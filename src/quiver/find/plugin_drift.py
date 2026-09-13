"""Whether the plugins in ``~/.quiver/plugins`` actually reached each harness.

``~/.quiver/plugins`` holds the user's own marketplaces: one directory each,
with a ``.claude-plugin/marketplace.json`` naming its plugins. Claude Code and
Codex both register such a directory in place, but neither runs the plugin
from it. Each installs a cached copy and runs that, so an edit under
``~/.quiver`` changes nothing until the harness reinstalls. This module finds
the gap, per harness, in four kinds:

  unregistered   the marketplace directory is not registered in the harness
  not-installed  registered, but the plugin has no installed copy
  stale          the installed copy's content differs from the source
  disabled-here  installed but disabled here, while enabled in the other one

Every finding carries a copy-pasteable ``fix``. Nothing here writes, runs a
harness CLI, or prints. A record this module cannot parse yields no findings
for that harness rather than a guess: reporting every plugin as unregistered
because ``config.toml`` has a typo would be worse than reporting nothing.
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

from quiver.find.plugins import _expand_root, _load_json
from quiver.paths import plugins_dir_for

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

KINDS = ("unregistered", "not-installed", "stale", "disabled-here")


@dataclass(frozen=True)
class Finding:
    harness: str          # "claude" | "codex"
    marketplace: str      # the name this harness knows it by
    plugin: str           # "" for unregistered: that finding is marketplace-wide
    kind: str             # one of KINDS
    detail: str
    fix: str              # a shell command, or a one-line edit where no verb exists
    paths: tuple[str, ...] = field(default=())  # stale only: differing files, relative

    @property
    def ref(self) -> str:
        return f"{self.plugin}@{self.marketplace}" if self.plugin else self.marketplace


class SourcePlugin(NamedTuple):
    marketplace: str      # marketplace.json "name"
    marketplace_dir: Path
    name: str
    path: Path            # the plugin directory inside the marketplace
    version: str          # plugin.json "version", "" when it declares none


# --------------------------------------------------------------------------
# The source side: ~/.quiver/plugins


def quiver_plugins(home: Path) -> list[SourcePlugin]:
    """Every locally sourced plugin in every marketplace under ~/.quiver/plugins."""
    root = plugins_dir_for(home)
    try:
        market_dirs = sorted(p for p in root.iterdir()
                             if not p.name.startswith(".") and p.is_dir())
    except OSError:
        return []

    found: list[SourcePlugin] = []
    for mdir in market_dirs:
        data = _load_json(mdir / ".claude-plugin" / "marketplace.json")
        if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
            continue
        mname = data.get("name")
        if not isinstance(mname, str) or not mname:
            mname = mdir.name
        for entry in data["plugins"]:
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
    in ``~/.quiver``. Anything else is fetched from elsewhere at install time:
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
    fix_register: Callable[[Path], str]
    fix_install: Callable[[str], str]
    fix_update: Callable[[str], str]
    fix_enable: Callable[[str], str]


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
        fix_register=lambda d: f"claude plugin marketplace add {shlex.quote(str(d))}",
        fix_install=lambda ref: f"claude plugin install {ref}",
        fix_update=lambda ref: f"claude plugin update {ref}",
        fix_enable=lambda ref: f"claude plugin enable {ref}",
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
        # be a directory under ~/.quiver.
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
        fix_register=lambda d: f"codex plugin marketplace add {shlex.quote(str(d))}",
        fix_install=lambda ref: f"codex plugin add {ref}",
        # codex plugin has add, list, marketplace and remove, and no update
        # verb, so a refresh is a reinstall.
        fix_update=lambda ref: f"codex plugin remove {ref} && codex plugin add {ref}",
        # Nor is there an enable verb; the flag lives only in config.toml.
        fix_enable=lambda ref: f'set [plugins."{ref}"] enabled = true in ~/.codex/config.toml',
    )


_READERS = {"claude": _claude, "codex": _codex}


# --------------------------------------------------------------------------
# Comparing a copy with its source


def _snapshot(root: Path) -> dict | None:
    """relative path -> (kind, value, absolute path) for every file under root.

    Symlinks are followed, so a tree is compared by what a reader of it gets:
    claude copies ``.codex-plugin -> .claude-plugin`` as a link, codex copies
    the file it points at, and both are the same content. A directory whose
    real path is already one of its own ancestors is a loop; it is recorded
    as a leaf and not entered. A dangling link is recorded with its target so
    two identical dangling links still compare equal. Empty directories are
    not recorded, the same as git. Returns None when the tree is too large to
    be a plugin (see MAX_ENTRIES).
    """
    out: dict = {}
    stack = [(str(root), "", frozenset({_real(root)}))]
    seen = 0
    while stack:
        directory, prefix, ancestors = stack.pop()
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
            rel = prefix + entry.name
            try:
                st = os.stat(entry.path)          # follows the link
            except OSError:
                try:
                    target = os.readlink(entry.path)
                except OSError:
                    target = ""
                out[rel] = ("dangling", target, entry.path)
                continue
            if stat.S_ISDIR(st.st_mode):
                real = _real(entry.path)
                if real in ancestors:
                    out[rel] = ("loop", "", entry.path)
                else:
                    stack.append((entry.path, rel + "/", ancestors | {real}))
            elif stat.S_ISREG(st.st_mode):
                out[rel] = ("file", st.st_size, entry.path)
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
        if a is None or b is None or a[:2] != b[:2]:
            out.append(rel)
        elif a[0] == "file" and not _same_bytes(a[2], b[2]):
            out.append(rel)
    return out


# --------------------------------------------------------------------------
# The report


def plugin_drift(home: Path, harnesses=HARNESSES) -> list[Finding]:
    """Every way the ~/.quiver plugins have not reached the given harnesses.

    Findings for one plugin stop at the first gap that blocks the rest: an
    unregistered marketplace is not also reported as N uninstalled plugins,
    and an uninstalled plugin cannot be stale. ``stale`` and
    ``disabled-here`` are independent and can both appear.

    ``disabled-here`` compares against every harness this module reads, not
    just the ones asked for, so ``harnesses=("codex",)`` still says a plugin
    codex has disabled is enabled in claude.
    """
    sources = quiver_plugins(home)
    if not sources:
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
        flagged_markets: set = set()
        for sp in sources:
            ref, inst = state(view, sp)
            if ref is None:
                if sp.marketplace_dir not in flagged_markets:
                    flagged_markets.add(sp.marketplace_dir)
                    findings.append(Finding(
                        hname, sp.marketplace, "", "unregistered",
                        f"{sp.marketplace_dir} is not a {hname} marketplace",
                        view.fix_register(sp.marketplace_dir)))
                continue
            mname = ref.rpartition("@")[2]
            if inst is None:
                findings.append(Finding(
                    hname, mname, sp.name, "not-installed",
                    f"registered in {hname} but never installed",
                    view.fix_install(ref)))
                continue

            src = source_snapshot(sp)
            copy = _snapshot(inst.path) if src is not None else None
            if src is not None and copy is not None:
                diff = _differing(src, copy)
                if diff:
                    installed = inst.version or "unversioned"
                    source_v = sp.version or "unversioned"
                    noun = "file differs" if len(diff) == 1 else "files differ"
                    findings.append(Finding(
                        hname, mname, sp.name, "stale",
                        f"installed {installed} vs source {source_v}, "
                        f"{len(diff)} {noun}",
                        view.fix_update(ref), tuple(diff)))

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
                        view.fix_enable(ref)))
    return findings
