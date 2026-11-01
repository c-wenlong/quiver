"""Whether the plugins in ``~/.swe/plugins`` actually reached each harness.

``~/.swe/plugins`` holds the user's own marketplaces: one directory each,
with a ``.claude-plugin/marketplace.json`` naming its plugins. Claude Code and
Codex both register such a directory in place. Codex installs a cached copy
and runs that, so an edit under ``~/.swe`` changes nothing in Codex until
it reinstalls. Claude Code reads a directory-marketplace plugin from the
folder itself (``claude plugin update`` says "read from its folder ... nothing
to update"); the copy it leaves under ``~/.claude/plugins/cache`` is never
loaded, so it is not compared. This module finds the gap, per harness, in
five kinds:

  unregistered     the marketplace directory is not registered in the harness
  not-installed    registered, but the plugin has no install record or copy
  linked-manifest  the source's ``.codex-plugin`` is a symlink, which Codex
                   skips when it copies, writing a manifest of its own with
                   no version instead (Codex only)
  stale            the installed copy's content differs from the source
                   (only for a harness that runs its own copy: Codex)
  disabled-here    installed but disabled here, while enabled in the other one

Codex follows no symlink when it copies a plugin: a linked file, folder or
manifest is left out of the copy. So the source is compared the way Codex
copies it, without its symlinks, and a reinstall is never offered for a
difference no reinstall could clear.

Every finding carries a copy-pasteable ``fix``, and ``argv`` when that fix is
commands rather than an edit. Nothing here writes or prints except
``apply_fix``, which runs one finding's commands when asked to by id: a
harness CLI, or for ``linked-manifest`` ``rm``, ``cp`` and ``mv`` in the source.
A record this module cannot parse yields no findings for that harness rather
than a guess: reporting every plugin as unregistered because
``config.toml`` has a typo would be worse than reporting nothing.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, NamedTuple

from swe.find.plugins import _expand_root, _load_json
from swe.paths import plugins_dir_for

HARNESSES = ("claude", "codex")

# Files a harness drops into its own copy that were never in the source, so a
# copy carrying them has not drifted. ``.in_use`` is claude's lock directory
# inside a live install; ``.orphaned_at`` is the marker claude leaves in the
# old version directories it keeps after an update; the other two are the OS
# and the interpreter. Matched by basename at any depth, on both sides.
LITTER = frozenset({".in_use", ".orphaned_at", ".DS_Store", "__pycache__"})

# A plugin is a handful of Markdown files. A tree this large is a symlink
# pointing somewhere it should not (``skills -> ~``), and walking it would
# make a read-only report hang, so the comparison gives up instead.
MAX_ENTRIES = 20_000

# Worst first: the skills hub shows a plugin's first gap in this order, and
# a linked manifest has to be fixed in the source before a reinstall helps.
KINDS = ("unregistered", "not-installed", "linked-manifest", "stale", "disabled-here")


@dataclass(frozen=True)
class Finding:
    harness: str          # "claude" | "codex"
    marketplace: str      # the name this harness knows it by
    plugin: str           # "" for unregistered: that finding is marketplace-wide
    kind: str             # one of KINDS
    detail: str
    fix: str              # a shell command, or a one-line edit where no verb exists
    paths: tuple[str, ...] = field(default=())  # stale only: differing files, relative
    # ``fix`` as argv lists, run one after another; empty when no verb exists
    # and ``fix`` describes an edit instead. ``apply_fix`` runs these without
    # a shell, so nothing in a marketplace path or plugin name is interpreted.
    argv: tuple[tuple[str, ...], ...] = field(default=())
    # unregistered only: the source directory. Two directories can declare
    # one marketplace name, and the id has to tell their fixes apart.
    marketplace_dir: str = ""

    @property
    def ref(self) -> str:
        return f"{self.plugin}@{self.marketplace}" if self.plugin else self.marketplace

    @property
    def id(self) -> str:
        """Names this finding across runs, for ``swe find plugins --fix``."""
        return f"{self.harness}:{self.kind}:{self.marketplace_dir or self.ref}"


class Fix(NamedTuple):
    text: str
    argv: tuple[tuple[str, ...], ...]


def _commands(*argvs: tuple[str, ...]) -> Fix:
    """A fix that is one or more commands; the text is their shell form."""
    return Fix(" && ".join(shlex.join(a) for a in argvs), tuple(argvs))


class SourcePlugin(NamedTuple):
    marketplace: str      # marketplace.json "name"
    marketplace_dir: Path
    name: str
    path: Path            # the plugin directory inside the marketplace
    version: str          # plugin.json "version", "" when it declares none


# --------------------------------------------------------------------------
# The source side: ~/.swe/plugins


def _hub_manifests(home: Path) -> list[tuple[str, Path, list]]:
    """``(name, dir, plugins)`` per ~/.swe/plugins marketplace whose
    manifest lists its plugins; the name falls back to the folder's."""
    root = plugins_dir_for(home)
    try:
        market_dirs = sorted(p for p in root.iterdir()
                             if not p.name.startswith(".") and p.is_dir())
    except OSError:
        return []
    found = []
    for mdir in market_dirs:
        data = _load_json(mdir / ".claude-plugin" / "marketplace.json")
        if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
            continue
        mname = data.get("name")
        found.append((mname if isinstance(mname, str) and mname else mdir.name,
                      mdir, data["plugins"]))
    return found


def hub_marketplaces(home: Path) -> list[tuple[str, Path]]:
    """``(name, dir)`` for each ~/.swe/plugins marketplace with a manifest.

    Independent of the plugins inside: a marketplace whose plugins all come
    from GitHub or a URL has no local folder for ``swe_plugins`` to find,
    and it still needs registering.
    """
    return [(mname, mdir) for mname, mdir, _plugins in _hub_manifests(home)]


def swe_plugins(home: Path) -> list[SourcePlugin]:
    """Every locally sourced plugin in every marketplace under ~/.swe/plugins."""
    found: list[SourcePlugin] = []
    for mname, mdir, entries in _hub_manifests(home):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            rel = _local_source(entry.get("source"))
            if not isinstance(name, str) or not name or rel is None:
                continue
            pdir = mdir / rel
            if not pdir.is_dir():
                continue
            found.append(SourcePlugin(mname, mdir, name, pdir,
                                      _source_version(pdir, entry)))
    return found


def _local_source(source) -> PurePosixPath | None:
    """The plugin's directory relative to its marketplace, or None.

    Only a plain relative path string (``"./eng"``) names a directory sitting
    in ``~/.swe``. Anything else is fetched from elsewhere at install time:
    ``{"source": "git-subdir", "url": ...}`` (composio in ``development``),
    a github or url object, or a URL string. There is no local source to
    compare such a copy against, so it is skipped rather than reported. A
    path that climbs out with ``..`` is not a plugin the marketplace holds.
    """
    if not isinstance(source, str) or not source or "://" in source:
        return None
    rel = PurePosixPath(source)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    return rel


def _source_version(pdir: Path, entry: dict) -> str:
    for manifest in (".claude-plugin", ".codex-plugin"):
        data = _load_json(pdir / manifest / "plugin.json")
        if isinstance(data, dict) and data.get("version"):
            return str(data["version"])
    return str(entry.get("version") or "")


# --------------------------------------------------------------------------
# The harness side


_BROKEN = object()   # present but unreadable or the wrong shape


def _read_json(path: Path):
    """{} when the file is absent, _BROKEN when it exists but cannot be used.

    ``_load_json`` folds both into None, and the difference matters here: no
    known_marketplaces.json means nothing is registered, an unparseable one
    means we cannot tell.
    """
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except OSError:
        return _BROKEN
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return _BROKEN
    return data if isinstance(data, dict) else _BROKEN


def _read_toml(path: Path):
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except OSError:
        return _BROKEN
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):   # TOMLDecodeError and bad UTF-8
        return _BROKEN


def _real(path) -> str:
    """Canonical form for comparing a registered path to a marketplace dir.

    realpath, not Path.resolve(strict=True): a registration can point at a
    directory that no longer exists, and a symlink loop must come back as a
    string rather than raise.
    """
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return str(path)


class _Install(NamedTuple):
    version: str
    path: Path


@dataclass
class _Harness:
    name: str
    registered: dict            # realpath of marketplace dir -> registered name
    enabled: dict               # "plugin@marketplace" -> bool
    install: Callable[[str, str], _Install | None]   # (marketplace, plugin)
    fix_register: Callable[[Path], Fix]
    fix_install: Callable[[str], Fix]
    fix_update: Callable[[str], Fix]
    fix_enable: Callable[[str], Fix]
    # Whether the harness runs its own copy of a plugin. When it reads the
    # marketplace folder live there is no copy to fall behind, so a "stale"
    # check would compare against a leftover it never loads.
    copies_source: bool = True
    # The manifest folder the harness reads from its own copy, which it
    # replaces with a generated one when the source's is a symlink.
    own_manifest: str = ""


def _claude(home: Path) -> _Harness | None:
    base = home / ".claude"
    if not base.is_dir():
        return None
    known = _read_json(base / "plugins" / "known_marketplaces.json")
    installed = _read_json(base / "plugins" / "installed_plugins.json")
    if known is _BROKEN or installed is _BROKEN:
        return None
    records = installed.get("plugins", {})
    if not isinstance(records, dict):
        return None
    # settings.json is often a read-only symlink into the Nix store; reading
    # it is fine. Unparseable settings only cost the enabled flags.
    settings = _read_json(base / "settings.json")
    if settings is _BROKEN:
        settings = {}

    registered: dict = {}
    extra = settings.get("extraKnownMarketplaces")
    # known_marketplaces.json is what claude has materialised; settings'
    # extraKnownMarketplaces is declared and gets materialised at startup.
    # Either one means the directory is registered.
    for table in (extra if isinstance(extra, dict) else {}, known):
        for mname, entry in table.items():
            src = entry.get("source") if isinstance(entry, dict) else None
            if (isinstance(src, dict) and src.get("source") == "directory"
                    and isinstance(src.get("path"), str) and src["path"]):
                registered[_real(_expand_root(src["path"], home))] = mname

    flags = settings.get("enabledPlugins")
    enabled = {ref: flag for ref, flag in
               (flags if isinstance(flags, dict) else {}).items()
               if isinstance(flag, bool)}

    def install(market: str, plugin: str) -> _Install | None:
        entries = records.get(f"{plugin}@{market}")
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list):
            return None
        rows = [e for e in entries if isinstance(e, dict)
                and isinstance(e.get("installPath"), str) and e["installPath"]]
        if not rows:
            return None
        # One record per scope. The user scope is the one that loads
        # everywhere, so it wins over a project install when both exist.
        row = next((e for e in rows if e.get("scope") == "user"), rows[0])
        return _Install(str(row.get("version") or ""),
                        _expand_root(row["installPath"], home))

    return _Harness(
        name="claude", registered=registered, enabled=enabled, install=install,
        fix_register=lambda d: _commands(("claude", "plugin", "marketplace", "add", str(d))),
        fix_install=lambda ref: _commands(("claude", "plugin", "install", ref)),
        fix_update=lambda ref: _commands(("claude", "plugin", "update", ref)),
        fix_enable=lambda ref: _commands(("claude", "plugin", "enable", ref)),
        # Claude Code loads a directory-marketplace plugin from its folder
        # (`claude plugin update` says "read from its folder ... nothing to
        # update"), and every marketplace checked here is a directory.
        copies_source=False,
    )


def _codex(home: Path) -> _Harness | None:
    base = home / ".codex"
    if not base.is_dir():
        return None
    config = _read_toml(base / "config.toml")
    if config is _BROKEN:
        return None

    registered: dict = {}
    markets = config.get("marketplaces")
    for mname, entry in (markets if isinstance(markets, dict) else {}).items():
        if not isinstance(entry, dict):
            continue
        src = entry.get("source")
        # git marketplaces carry a URL in the same key; only local ones can
        # be a directory under ~/.swe.
        if entry.get("source_type", "local") == "local" and isinstance(src, str) and src:
            registered[_real(_expand_root(src, home))] = mname

    plugins = config.get("plugins")
    enabled = {ref: entry["enabled"] for ref, entry in
               (plugins if isinstance(plugins, dict) else {}).items()
               if isinstance(entry, dict) and isinstance(entry.get("enabled"), bool)}

    cache = base / "plugins" / "cache"

    def install(market: str, plugin: str) -> _Install | None:
        # codex writes no install record: `codex plugin add` copies the plugin
        # to cache/<marketplace>/<plugin>/<version>, "local" when the manifest
        # declares no version, and `remove` deletes that directory. So the
        # directory is the evidence. More than one version dir can survive an
        # upgrade; the most recently written is the one codex just installed,
        # so pick by mtime (name breaks a tie) rather than by version order,
        # which "local" and hash-named versions would defeat.
        root = cache / market / plugin
        try:
            candidates = []
            for p in root.iterdir():
                if p.name.startswith(".") or p.name in LITTER or not p.is_dir():
                    continue
                candidates.append((p.stat().st_mtime, p.name, p))
        except OSError:
            return None
        if not candidates:
            return None
        _, vname, path = max(candidates)
        return _Install(vname, path)

    return _Harness(
        name="codex", registered=registered, enabled=enabled, install=install,
        fix_register=lambda d: _commands(("codex", "plugin", "marketplace", "add", str(d))),
        fix_install=lambda ref: _commands(("codex", "plugin", "add", ref)),
        # codex plugin has add, list, marketplace and remove, and no update
        # verb, so a refresh is a reinstall.
        fix_update=lambda ref: _commands(("codex", "plugin", "remove", ref),
                                         ("codex", "plugin", "add", ref)),
        # Nor is there an enable verb; the flag lives only in config.toml, so
        # this fix is an edit with nothing to run. json.dumps yields a valid
        # TOML basic-string key for any name.
        fix_enable=lambda ref: Fix(
            f"set [plugins.{json.dumps(ref)}] enabled = true in ~/.codex/config.toml", ()),
        # `codex plugin add` skips a symlinked .codex-plugin and writes its
        # own plugin.json from the marketplace entry, without the version,
        # into a copy named "local" (seen with codex-cli 0.162.1).
        own_manifest=".codex-plugin",
    )


_READERS = {"claude": _claude, "codex": _codex}


def _fix_fields(fix: Fix, paths: tuple[str, ...] = ()) -> tuple:
    """``fix``, ``paths`` and ``argv`` in Finding's field order."""
    return fix.text, paths, fix.argv


# --------------------------------------------------------------------------
# Comparing a copy with its source


def _snapshot(root: Path) -> dict | None:
    """relative path -> (size, absolute path) for every regular file under root.

    Symlinks below the root are neither followed nor recorded, because that
    is how codex copies a plugin: a linked file, folder or manifest, and a
    dangling link, are all left out of its copy. Comparing the source by
    what its links point at reported differences no reinstall could clear.
    Only real directories are entered, so no link can loop the walk. Empty
    directories are not recorded, the same as git. Returns None when the
    tree is too large to be a plugin (see MAX_ENTRIES).
    """
    out: dict = {}
    stack = [(str(root), "")]
    seen = 0
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = list(it)
        except OSError:
            continue
        for entry in entries:
            if entry.name in LITTER:
                continue
            seen += 1
            if seen > MAX_ENTRIES:
                return None
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            rel = prefix + entry.name
            if stat.S_ISDIR(st.st_mode):
                stack.append((entry.path, rel + "/"))
            elif stat.S_ISREG(st.st_mode):
                out[rel] = (st.st_size, entry.path)
    return out


def _same_bytes(a: str, b: str) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                ca, cb = fa.read(65536), fb.read(65536)
                if ca != cb:
                    return False
                if not ca:
                    return True
    except OSError:
        return False


def _differing(source: dict, copy: dict) -> list[str]:
    """Relative paths present on one side only, or whose content differs.

    Sizes come from the stat the walk already did, so bytes are read only
    for a pair of files that are the same size.
    """
    out = []
    for rel in sorted(source.keys() | copy.keys()):
        a, b = source.get(rel), copy.get(rel)
        if a is None or b is None or a[0] != b[0] or not _same_bytes(a[1], b[1]):
            out.append(rel)
    return out


class _LinkedManifest(NamedTuple):
    detail: str
    fix: Fix


def _linked_manifest(harness: str, manifest: str, sp: SourcePlugin) -> _LinkedManifest | None:
    """The finding for a source whose ``manifest`` folder is a symlink, or None.

    The fix swaps the link for a real copy of its target, by resolved path.
    It copies first, into a new folder beside the link, and only then
    removes the link and moves the copy into its place: a copy that fails
    leaves the link, so the finding is still there. The only thing it ever
    deletes is the link.

    It is a command only when that target is a folder inside the plugin's
    own marketplace that does not contain the link, and nothing is at the
    staging path yet. A link out of the marketplace could pull any folder
    on the machine into the plugin, copying a folder into itself never
    ends, and ``cp`` into something already there would write through any
    link it holds, while clearing it first could delete a real folder.
    Anything else is left to do by hand.
    """
    link = sp.path / manifest
    try:
        target = os.readlink(link)
    except OSError:          # absent, or a real folder
        return None
    detail = (f"{manifest} is a symlink to {target}; {harness} doesn't follow "
              f"symlinks, so it installs a generated manifest without the version")
    plugin_dir = _real(sp.path)
    real_link = os.path.join(plugin_dir, manifest)
    resolved = _real(link)
    market = _real(sp.marketplace_dir)
    staged = real_link + ".swe-tmp"
    by_hand = f"replace the {manifest} symlink in {plugin_dir} with a folder holding plugin.json"
    if os.path.lexists(staged):
        # most likely a copy an earlier run of this fix left when it failed
        return _LinkedManifest(detail, Fix(f"{by_hand}; {staged} is in the way", ()))
    if (os.path.isdir(resolved)
            and os.path.commonpath([resolved, market]) == market
            and os.path.commonpath([resolved, real_link]) != resolved):
        return _LinkedManifest(detail, _commands(
            ("cp", "-R", resolved, staged),
            ("rm", real_link),
            ("mv", staged, real_link)))
    return _LinkedManifest(detail, Fix(by_hand, ()))


# --------------------------------------------------------------------------
# The report


def plugin_drift(home: Path, harnesses=HARNESSES) -> list[Finding]:
    """Every way the ~/.swe plugins have not reached the given harnesses.

    Findings for one plugin stop at the first gap that blocks the rest: an
    unregistered marketplace is not also reported as N uninstalled plugins,
    and an uninstalled plugin cannot be stale. ``linked-manifest``,
    ``stale`` and ``disabled-here`` are independent and can all appear, but
    each cause is reported once: the manifest codex generates for a linked
    one is not also a stale path.

    ``disabled-here`` compares against every harness this module reads, not
    just the ones asked for, so ``harnesses=("codex",)`` still says a plugin
    codex has disabled is enabled in claude.
    """
    sources = swe_plugins(home)
    markets = hub_marketplaces(home)
    if not sources and not markets:
        return []
    views = {}
    for name, reader in _READERS.items():
        view = reader(home)
        if view is not None:
            views[name] = view

    snapshots: dict = {}

    def source_snapshot(sp: SourcePlugin):
        if sp.path not in snapshots:
            snapshots[sp.path] = _snapshot(sp.path)
        return snapshots[sp.path]

    def state(view: _Harness, sp: SourcePlugin):
        """(ref, install or None) for sp in view; ref is None if unregistered."""
        mname = view.registered.get(_real(sp.marketplace_dir))
        if mname is None:
            return None, None
        inst = view.install(mname, sp.name)
        if inst is not None and not inst.path.is_dir():
            inst = None
        return f"{sp.name}@{mname}", inst

    findings: list[Finding] = []
    for hname in harnesses:
        view = views.get(hname)
        if view is None:
            continue
        # One finding per unregistered marketplace, its plugins aside: the
        # plugins of one are not also reported as N uninstalled plugins.
        for mname, mdir in markets:
            if _real(mdir) not in view.registered:
                findings.append(Finding(
                    hname, mname, "", "unregistered",
                    f"{mdir} is not a {hname} marketplace",
                    *_fix_fields(view.fix_register(mdir)),
                    marketplace_dir=str(mdir)))
        for sp in sources:
            ref, inst = state(view, sp)
            if ref is None:
                continue
            mname = ref.rpartition("@")[2]
            if inst is None:
                findings.append(Finding(
                    hname, mname, sp.name, "not-installed",
                    f"registered in {hname} but never installed",
                    *_fix_fields(view.fix_install(ref))))
                continue

            linked = (_linked_manifest(hname, view.own_manifest, sp)
                      if view.copies_source and view.own_manifest else None)
            if linked is not None:
                findings.append(Finding(
                    hname, mname, sp.name, "linked-manifest", linked.detail,
                    *_fix_fields(linked.fix)))

            src = source_snapshot(sp) if view.copies_source else None
            copy = _snapshot(inst.path) if src is not None else None
            if src is not None and copy is not None:
                if linked is not None:
                    # The harness generated this manifest because of the
                    # link, which the finding above already reports.
                    copy = {rel: v for rel, v in copy.items()
                            if not rel.startswith(view.own_manifest + "/")}
                diff = _differing(src, copy)
                if diff:
                    installed = inst.version or "unversioned"
                    source_v = sp.version or "unversioned"
                    noun = "file differs" if len(diff) == 1 else "files differ"
                    findings.append(Finding(
                        hname, mname, sp.name, "stale",
                        f"installed {installed} vs source {source_v}, "
                        f"{len(diff)} {noun}",
                        *_fix_fields(view.fix_update(ref), tuple(diff))))

            if view.enabled.get(ref) is False:
                elsewhere = []
                for other_name, other in views.items():
                    if other_name == hname:
                        continue
                    oref, oinst = state(other, sp)
                    if oref and oinst is not None and other.enabled.get(oref) is True:
                        elsewhere.append(other_name)
                if elsewhere:
                    findings.append(Finding(
                        hname, mname, sp.name, "disabled-here",
                        f"disabled in {hname}, enabled in {', '.join(elsewhere)}",
                        *_fix_fields(view.fix_enable(ref))))
    return findings


# --------------------------------------------------------------------------
# Running a fix


class FixError(Exception):
    """A fix that could not run, or ran and failed; the message says which."""


# Variables that move a harness away from its default home. Drift is read
# from ``home/.claude`` and ``home/.codex``, so a fix must act on those too,
# not on whichever account the caller's shell happens to point at.
_HOME_OVERRIDES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")


def _harness_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _HOME_OVERRIDES}
    env["HOME"] = str(home)
    return env


def apply_fix(home: Path, finding_id: str, run=None, harnesses=HARNESSES) -> Finding:
    """Run the commands of the finding named ``finding_id``; return it.

    Drift is computed again first, so only a fix for a gap that exists right
    now runs, and the commands are the ones this module builds, never text
    a caller supplied. Commands run in order without a shell and stop at the
    first failure, so ``codex plugin remove && add`` never adds after a
    failed remove.
    """
    import subprocess

    run = run or subprocess.run
    matches = [f for f in plugin_drift(home, harnesses) if f.id == finding_id]
    if not matches:
        raise FixError(f"no current plugin drift has id {finding_id}; refresh and try again")
    if len(matches) > 1:
        # Never guess which of two findings was meant.
        raise FixError(f"{len(matches)} findings share id {finding_id}; fix them by hand")
    finding = matches[0]
    if not finding.argv:
        raise FixError(f"nothing to run for {finding_id}: {finding.fix}")
    for argv in finding.argv:
        try:
            done = run(list(argv), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=300, env=_harness_env(home))
        except FileNotFoundError:
            raise FixError(f"{argv[0]} is not on PATH") from None
        except OSError as exc:
            raise FixError(f"couldn't run {argv[0]}: {exc.strerror or exc}") from None
        except subprocess.TimeoutExpired:
            raise FixError(f"{shlex.join(argv)} did not finish in 5 minutes") from None
        if done.returncode != 0:
            said = (done.stderr or done.stdout or "").strip().splitlines()
            tail = said[-1] if said else f"exit status {done.returncode}"
            raise FixError(f"{shlex.join(argv)} failed: {tail}")
    return finding
