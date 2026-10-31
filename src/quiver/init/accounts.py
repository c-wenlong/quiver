"""Shared content synced into Quiver's extra account homes (#888).

An extra account (a profile in ``~/.quiver/accounts/profiles.json``) runs its
harness with ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` pointed at
``~/.quiver/accounts/<vendor>/<id>/home``. That folder is config-dir shaped,
so each account target is the default target minus the vendor's config-dir
prefix: ``~/.claude/skills`` becomes ``<home>/skills``, ``~/.codex/AGENTS.md``
becomes ``<home>/AGENTS.md``, ``~/.claude/hooks/x.py`` becomes
``<home>/hooks/x.py``.

One switch per primitive, ``accounts.sync.<primitive>`` in config.json, all
off by default. Only Claude and Codex profiles take part: Cursor has no CLI
home variable, and Devin's account homes are not mapped yet.

The rules are narrower than the default targets', because an account home
lives inside ``~/.quiver`` and holds that account's own login:

- Create and relink only, never absorb. A real file or directory in the
  account home is reported ``conflict`` and left alone, ``--force`` or not.
  A symlink is only repointed when it is dangling or already points into
  the shared roots; anyone else's link is a conflict too.
- Turning a switch off removes only the links swe itself made. Each one is
  recorded in ``~/.quiver/data/account-links.json`` when it is created, and
  the unlink pass removes a recorded link only while it still points at the
  source swe gave it. A link the user made by hand is never in the record.
- Anything unreadable (config.json, profiles.json, the record) stops the
  whole pass rather than reading as "nothing is on", which would unlink.
"""

from __future__ import annotations

import contextlib
try:
    import fcntl
except ImportError:  # Windows: no flock, and no Quiver.app to race with
    fcntl = None
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from quiver import paths as _paths
from quiver.configuration import (
    ACCOUNT_SYNC_PRIMITIVES,
    ConfigurationError,
    read_json_object,
)
from quiver.init.layout import (
    ARCHIVED_DETAIL,
    HARNESS_SIGNATURES,
    IGNORED_DETAIL,
    LinkStatus,
    _instruction_targets,
    _registry_skill_roots,
    _skills_disabled,
    agents_file,
    archived_names,
    hooks_dir,
    is_linkignored,
    registry_name,
    skills_dir,
)

SYNC_PRIMITIVES: tuple[str, ...] = ACCOUNT_SYNC_PRIMITIVES
SYNC_KEY = "accounts.sync"

# Vendor -> the config dir its account home stands in for.
ACCOUNT_VENDORS: dict[str, Path] = {
    "claude": Path(".claude"),
    "codex": Path(".codex"),
}

# ProfileStore.isSafeId: ASCII letters, digits, - and _.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")

MISSING_HOME_DETAIL = "account home missing"
OUTSIDE_DETAIL = "resolves outside the account home, left alone"
OFF_DETAIL = "sync switched off"
GONE_DETAIL = "no longer synced"


class AccountSyncUnavailable(Exception):
    """A file the pass depends on can't be read; nothing is touched."""


@dataclass(frozen=True)
class AccountHome:
    vendor: str
    id: str
    path: Path

    @property
    def label(self) -> str:
        return f"{self.vendor}/{self.id}"


@dataclass
class AccountRow:
    """One account target and what the pass did (or would do) with it."""

    primitive: str
    status: LinkStatus
    result: str
    # The MCP server a row is about (accounts.sync.mcp); None for a link.
    name: str | None = None

    def to_json(self) -> dict:
        out = {
            "account": self.status.label,
            "primitive": self.primitive,
            "path": str(self.status.path),
            "state": self.result,
            "detail": self.status.detail,
        }
        if self.primitive == "mcp":
            out["name"] = self.name
        return out


def accounts_root(home: Path) -> Path:
    return _paths.quiver_dir_for(home) / "accounts"


def manifest_file(home: Path) -> Path:
    return _paths.data_dir_for(home) / "account-links.json"


def sync_flags(home: Path) -> dict[str, bool]:
    """``accounts.sync.*`` from config.json; only a literal ``true`` is on.

    Raises AccountSyncUnavailable when config.json exists but can't be
    read: an unreadable file must not read as "every switch off".
    """
    try:
        config = read_json_object(_paths.config_file_for(home))
    except ConfigurationError as exc:
        raise AccountSyncUnavailable(str(exc)) from exc
    section = (config.get("accounts") or {}) if isinstance(config.get("accounts"), dict) else {}
    sync = section.get("sync") if isinstance(section.get("sync"), dict) else {}
    return {p: sync.get(p) is True for p in SYNC_PRIMITIVES}


def load_account_homes(home: Path) -> list[AccountHome]:
    """Every non-default Claude or Codex profile, in file order."""
    path = accounts_root(home) / "profiles.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError) as exc:
        raise AccountSyncUnavailable(f"cannot read {path}: {exc}") from exc
    raw = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise AccountSyncUnavailable(f"{path}: no profiles list")
    out: list[AccountHome] = []
    for p in raw:
        if not isinstance(p, dict) or p.get("isDefault"):
            continue
        vendor, pid = p.get("vendor"), p.get("id")
        if vendor not in ACCOUNT_VENDORS or not isinstance(pid, str) \
                or not _SAFE_ID.match(pid):
            continue
        out.append(AccountHome(vendor, pid, accounts_root(home) / vendor / pid / "home"))
    return out


def _strip(rel: Path | None, prefix: Path) -> Path | None:
    """``.claude/skills`` -> ``skills``; None when not under the prefix."""
    if rel is None:
        return None
    try:
        out = rel.relative_to(prefix)
    except ValueError:
        return None
    return out if out.parts else None


def _instruction_rel(vendor: str, registry: dict, home: Path) -> Path | None:
    for label, rel, _installed in _instruction_targets(registry, home):
        if registry_name(label) == vendor:
            return _strip(rel, ACCOUNT_VENDORS[vendor])
    return None


def _skills_rel(vendor: str, registry: dict, home: Path) -> Path | None:
    if vendor in _skills_disabled(registry):
        return None
    for path, key in _registry_skill_roots(registry, home).items():
        if key == vendor:
            try:
                return _strip(path.relative_to(home), ACCOUNT_VENDORS[vendor])
            except ValueError:
                return None
    sig = HARNESS_SIGNATURES.get(vendor)
    return _strip(sig.skills if sig else None, ACCOUNT_VENDORS[vendor])


def _hook_targets(vendor: str, registry: dict, home: Path) -> tuple[Path | None, list[Path]]:
    """(account-relative hooks root, hub scripts) for one vendor."""
    from quiver.init.hooks import _disabled_hook_names, hook_root, hook_sources

    harness_dir = hooks_dir(home) / vendor
    if not harness_dir.is_dir():
        return None, []
    root, _why = hook_root(registry, vendor, home)
    if root is None:
        return None, []
    try:
        rel = _strip(root.relative_to(home), ACCOUNT_VENDORS[vendor])
    except ValueError:
        return None, []
    disabled = _disabled_hook_names(harness_dir)
    return rel, [s for s in hook_sources(harness_dir) if s.name not in disabled]


def _targets(primitive: str, acct: AccountHome, registry: dict,
             home: Path) -> list[tuple[Path, Path]]:
    """(destination, source) pairs for one primitive in one account home."""
    vendor = acct.vendor
    if primitive == "instructions":
        rel = _instruction_rel(vendor, registry, home)
        return [(acct.path / rel, agents_file(home))] if rel else []
    if primitive == "skills":
        rel = _skills_rel(vendor, registry, home)
        return [(acct.path / rel, skills_dir(home))] if rel else []
    if primitive == "hooks":
        rel, sources = _hook_targets(vendor, registry, home)
        if rel is None:
            return []
        return [(acct.path / rel / s.name, s) for s in sources]
    return []


def _plain(path: Path, home: Path) -> bool:
    """``path`` sits under ~/.quiver/accounts with no folder on the way
    that is a link: what it resolves to is what it says. The one rule both
    linking and unlinking use, so swe never records a link it would later
    refuse to remove."""
    root = accounts_root(home)
    try:
        rel = path.relative_to(root)
        return path.resolve() == root.resolve() / rel
    except (OSError, RuntimeError, ValueError):
        return False


def _nearest_existing(path: Path) -> Path:
    while not os.path.lexists(path) and path.parent != path:
        path = path.parent
    return path


def _shared_roots(home: Path) -> tuple[Path, ...]:
    return (agents_file(home), skills_dir(home), hooks_dir(home))


def _points_into_shared(dest: Path, home: Path) -> bool:
    """A link swe could have made: its target (literal or resolved) is in
    the shared AGENTS.md, skills/ or hooks/."""
    try:
        literal = Path(os.readlink(dest))
    except OSError:
        return False
    if not literal.is_absolute():
        literal = dest.parent / literal
    # Lexically, so ~/.quiver/skills/../private reads as private.
    literal = Path(os.path.normpath(literal))
    try:
        resolved = dest.resolve()
    except (OSError, RuntimeError):
        resolved = None
    for root in _shared_roots(home):
        for candidate in (literal, resolved):
            if candidate is not None and (candidate == root or candidate.is_relative_to(root)):
                return True
    return False


def classify(label: str, acct: AccountHome, dest: Path, source: Path,
             home: Path) -> LinkStatus:
    """Classify one account target without touching the filesystem."""
    if acct.path.is_symlink() or not acct.path.is_dir():
        return LinkStatus(label, dest, "skipped", MISSING_HOME_DETAIL, source)
    ancestor = _nearest_existing(dest.parent)
    if not ancestor.is_dir():
        return LinkStatus(label, dest, "conflict",
                          f"{ancestor.name} is the account's own file, left alone", source)
    if not _plain(ancestor, home):
        # A home or a hooks/ folder that is a link out (to ~/.claude/hooks,
        # say) would put this link somewhere that is not the account's.
        return LinkStatus(label, dest, "conflict", OUTSIDE_DETAIL, source)
    if dest.is_symlink():
        try:
            current = os.readlink(dest)
        except OSError:
            return LinkStatus(label, dest, "conflict", "unreadable symlink", source)
        try:
            lands = dest.resolve() == source.resolve()
        except (OSError, RuntimeError):
            lands = False
        if Path(current) == source or lands:
            return LinkStatus(label, dest, "linked", "", source)
        if not dest.exists() or _points_into_shared(dest, home):
            return LinkStatus(label, dest, "relink", f"points at {current}", source)
        return LinkStatus(label, dest, "conflict",
                          f"the account's own link to {current}, left alone", source)
    if dest.exists():
        kind = "directory" if dest.is_dir() else "file"
        return LinkStatus(label, dest, "conflict",
                          f"the account's own {kind}, left alone", source)
    return LinkStatus(label, dest, "create", "", source)


def plan_accounts(home: Path, patterns: list[str], registry: dict,
                  flags: dict[str, bool],
                  accounts: list[AccountHome]) -> list[tuple[str, LinkStatus]]:
    """(primitive, status) for every target of every switched-on primitive."""
    archived = archived_names(registry)
    out: list[tuple[str, LinkStatus]] = []
    for acct in accounts:
        for primitive in SYNC_PRIMITIVES:
            if not flags.get(primitive):
                continue
            for dest, source in _targets(primitive, acct, registry, home):
                if is_linkignored(dest, home, patterns):
                    status = LinkStatus(acct.label, dest, "ignored", IGNORED_DETAIL, source)
                else:
                    status = classify(acct.label, acct, dest, source, home)
                    if acct.vendor in archived and status.state in ("create", "relink", "conflict"):
                        status.state, status.detail = "ignored", ARCHIVED_DETAIL
                out.append((primitive, status))
    return out


# --------------------------------------------------------------- record

def load_manifest(home: Path) -> list[dict]:
    """The links swe made in account homes; raises when unreadable."""
    path = manifest_file(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError) as exc:
        raise AccountSyncUnavailable(f"cannot read {path}: {exc}") from exc
    links = data.get("links") if isinstance(data, dict) else None
    if not isinstance(links, list):
        raise AccountSyncUnavailable(f"{path}: no links list")
    for entry in links:
        # One damaged entry stops the pass: dropping it would lose the only
        # note that swe made that link, so switching off could never remove it.
        if not isinstance(entry, dict) or not all(
                isinstance(entry.get(k), str) for k in ("path", "source", "primitive", "account")):
            raise AccountSyncUnavailable(f"{path}: a damaged entry, fix or remove the file")
    return links


def save_manifest(home: Path, links: list[dict]) -> None:
    path = manifest_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": 1, "links": sorted(links, key=lambda e: e["path"])}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(body, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _locked(home: Path):
    """One account pass at a time (the app and a terminal can overlap):
    the record is read, acted on and saved under one flock."""
    lock = _paths.cache_dir_for(home) / "account-links.lock"  # cache/: never versioned
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as fh:
        if fcntl is None:
            yield
            return
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _still_ours(entry: dict, home: Path) -> bool:
    """The recorded link is still there, still says what swe wrote, and
    still sits in its account home: no folder on the way was swapped for a
    link out (an account's hooks/ pointed at ~/.claude/hooks would make the
    default home's own link look like this one)."""
    path = Path(entry["path"])
    root = accounts_root(home)
    try:
        rel = path.parent.relative_to(root)
        if len(rel.parts) < 3 or rel.parts[2] != "home" or not _plain(path.parent, home):
            return False
        return path.is_symlink() and os.readlink(path) == entry["source"]
    except (OSError, RuntimeError, ValueError):
        return False


# ---------------------------------------------------------------- apply

def _link(status: LinkStatus, home: Path) -> str:
    """Create or repoint one link; re-checks the disk first."""
    path = status.path
    if status.source is None:
        return "conflict"
    if not _plain(_nearest_existing(path.parent), home):
        return "conflict"
    try:
        if status.state == "relink":
            if not path.is_symlink():
                return "conflict"
            path.unlink()
        elif os.path.lexists(path):
            return "conflict"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(status.source)
    except OSError as exc:
        status.detail = f"could not link: {exc.strerror or exc}"
        return "conflict"
    return "linked"


def sync_accounts(home: Path, patterns: list[str], registry: dict, *,
                  check_only: bool) -> tuple[list[AccountRow], list[str]]:
    """Link every switched-on primitive into every account home, and unlink
    what swe made for a switched-off one. Returns (rows, notes)."""
    if check_only:
        return _sync_accounts(home, patterns, registry, check_only=True)
    with _locked(home):
        return _sync_accounts(home, patterns, registry, check_only=False)


def _sync_accounts(home: Path, patterns: list[str], registry: dict, *,
                   check_only: bool) -> tuple[list[AccountRow], list[str]]:
    from quiver.init.account_mcp import load_record, sync_mcp

    try:
        flags = sync_flags(home)
        manifest = load_manifest(home)
        # An unreadable MCP record is the MCP pass's to report, not a reason
        # to skip the links; treat it as "maybe something to do" here.
        try:
            mcp_record = bool(load_record(home))
        except AccountSyncUnavailable:
            mcp_record = True
        if not any(flags.values()) and not manifest and not mcp_record:
            return [], []
        accounts = load_account_homes(home)
    except AccountSyncUnavailable as exc:
        return [], [f"account sync skipped: {exc}"]

    rows, notes = _sync_links(home, patterns, registry, flags, manifest, accounts,
                              check_only=check_only)
    mcp_rows, mcp_notes = sync_mcp(home, patterns, registry, flags.get("mcp", False), accounts,
                                   check_only=check_only)
    return rows + mcp_rows, notes + mcp_notes


def _sync_links(home: Path, patterns: list[str], registry: dict, flags: dict[str, bool],
                manifest: list[dict], accounts: list[AccountHome], *,
                check_only: bool) -> tuple[list[AccountRow], list[str]]:
    if not any(v for k, v in flags.items() if k != "mcp") and not manifest:
        return [], []

    planned = plan_accounts(home, patterns, registry, flags, accounts)
    wanted = {str(status.path) for _, status in planned}

    rows: list[AccountRow] = []
    keep: list[dict] = []
    for entry in manifest:
        if entry["path"] in wanted:
            if _still_ours(entry, home):
                keep.append(entry)
            continue
        if not _still_ours(entry, home):
            continue  # gone, or no longer the link swe made: forget it
        detail = OFF_DETAIL if not flags.get(entry["primitive"]) else GONE_DETAIL
        status = LinkStatus(entry["account"], Path(entry["path"]), "unlink", detail,
                            Path(entry["source"]))
        if check_only:
            rows.append(AccountRow(entry["primitive"], status, "would-unlink"))
            keep.append(entry)
            continue
        try:
            Path(entry["path"]).unlink()
            rows.append(AccountRow(entry["primitive"], status, "unlinked"))
        except OSError as exc:
            status.detail = f"could not unlink: {exc.strerror or exc}"
            rows.append(AccountRow(entry["primitive"], status, "conflict"))
            keep.append(entry)

    record = {e["path"]: e for e in keep}
    for primitive, status in planned:
        if status.state in ("create", "relink"):
            if check_only:
                rows.append(AccountRow(primitive, status, "would-" + status.state))
                continue
            result = _link(status, home)
            rows.append(AccountRow(primitive, status, result))
            if result == "linked":
                record[str(status.path)] = {
                    "path": str(status.path), "source": str(status.source),
                    "primitive": primitive, "account": status.label}
        else:
            rows.append(AccountRow(primitive, status, status.state))

    links = sorted(record.values(), key=lambda e: e["path"])
    if not check_only and links != manifest:
        save_manifest(home, links)
    return rows, []
