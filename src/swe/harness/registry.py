"""Tool registry load/save, alias resolution, and per-harness state.

Used to be three files: tools.json said what a harness is, stars.json said
which ones you favourite, archived.json said which ones you shelved. They
always moved together in practice — starring or archiving meant resolving an
alias against the registry and then writing a second file — and anything that
wanted the full picture had to load all three and join them by name. One file
with a `state` field on each entry says the same thing without the join.

load_registry() is the only thing that reads harness.json off disk. A fresh
install that still has tools.json (and maybe stars.json / archived.json)
gets lazily migrated into harness.json the first time it is loaded; after
that migration harness.json is authoritative and the legacy files are never
looked at again, even if they are still sitting on disk (see
`_migrate_from_legacy` for why).
"""

import json
import shutil
from datetime import datetime

from swe.configuration import CorruptConfigurationError, read_json_object
from swe.paths import (
    ARCHIVE_FILE,
    CONFIG_DIR,
    HARNESS_FILE,
    STARS_FILE,
    TOOLS_FILE,
    atomic_write_text,
)


def _validated_entries(tools: dict, source) -> dict:
    """Every registry value must be an object.

    The top-level shape is already checked by read_json_object, but a
    hand-edited entry like ``"claude": "oops"`` used to get through and
    crash the first caller that ran ``entry.get(...)`` on it — a bare
    AttributeError instead of a controlled error naming the bad row.
    """
    for name, entry in tools.items():
        if not isinstance(entry, dict):
            raise CorruptConfigurationError(
                f"{source}: entry {name!r} must be a JSON object,"
                f" got {type(entry).__name__}: fix or remove the file"
            )
    return tools


def load_registry() -> dict:
    if HARNESS_FILE.exists():
        # Strict: a malformed registry must not read as empty — every caller
        # that follows with save_registry would wipe whatever it held.
        return _validated_entries(read_json_object(HARNESS_FILE), HARNESS_FILE)
    if TOOLS_FILE.exists():
        return _migrate_from_legacy()
    # Empty, not seeded — and not written. This file used to be born
    # holding eleven hardcoded entries, so a machine with three harnesses
    # installed listed eight it did not have, each printing a version
    # constant as though it had been probed. Later the empty {} was still
    # saved here, which made a read (or a command that then failed, like
    # `swe --version` on an old release) create ~/.quiver: anything wanting
    # to place content at that path had to land before the first swe
    # invocation. The registry answers "what is on this machine", which
    # only `swe discover` can know; writers scaffold through
    # save_registry, which makes CONFIG_DIR itself.
    return {}


def load_registry_if_present() -> dict:
    """``load_registry()`` without the legacy migration write.

    A read-only caller — `swe find` and friends promise never to write
    anything — must not run the tools.json -> harness.json migration just
    by asking what state a harness is in. With no harness.json on disk a
    legacy install reads as empty here; ``load_registry_readonly()`` is
    the variant that still honours those old entries.
    """
    return load_registry() if HARNESS_FILE.exists() else {}


def load_registry_readonly() -> dict:
    """The registry for a lookup that must not write, legacy installs included.

    Like ``load_registry_if_present()``, but a machine that still has only
    tools.json gets its entries read as-is rather than {}: its aliases and
    custom commands are what a later migration would carry over, so a
    lookup that ignored them would resolve names differently from the
    write that follows.
    """
    if HARNESS_FILE.exists():
        return load_registry()
    if TOOLS_FILE.exists():
        return _validated_entries(read_json_object(TOOLS_FILE), TOOLS_FILE)
    return {}


def save_registry(tools: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Atomic: a crash mid-write used to leave a truncated harness.json that
    # then bricked every registry command with a bare JSONDecodeError.
    atomic_write_text(HARNESS_FILE, json.dumps(tools, indent=2) + "\n")


def alias_map(tools: dict) -> dict[str, str]:
    """Return {alias_or_name: canonical_name} for every tool."""
    mapping: dict[str, str] = {}
    for name, info in tools.items():
        mapping[name] = name
        aliases = info.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]    # hand-typed "cc": one alias, not c and c
        elif not isinstance(aliases, list):
            aliases = []
        for alias in aliases:
            # A list or object here is unhashable: every command resolving
            # a name went through this and raised TypeError.
            if isinstance(alias, str) and alias:
                mapping[alias] = name
    return mapping


def resolve(tools: dict, key: str) -> str | None:
    """Resolve a name or alias to canonical name, or None."""
    return alias_map(tools).get(key)


# ---------------------------------------------------------------------------
# State — the one field `swe list`, the tri-state editor, and every filter
# key off. Absent means active: most harnesses never set it, so treating a
# missing key as an error state would make every pre-consolidation entry
# archived by accident.
# ---------------------------------------------------------------------------


def state_of(entry: dict) -> str:
    """A harness's state: "active" (default), "starred", or "archived"."""
    return entry.get("state") or "active"


def is_active(entry: dict) -> bool:
    """Not archived. Starred still counts: it is in daily rotation, just
    pinned, so it belongs in the same "active" bucket as everything else
    that has not been ruled out."""
    return state_of(entry) != "archived"


def starred_names(reg: dict) -> list[str]:
    """Starred harness names in pin order (1 = top)."""
    def pin_of(entry: dict) -> float:
        pin = entry.get("pin")
        # A hand-edited pin could be a string; sorting "x" against 3 used
        # to raise TypeError. Non-numeric pins sort as unpinned.
        return pin if isinstance(pin, (int, float)) else 0

    starred = [(name, pin_of(entry)) for name, entry in reg.items()
               if state_of(entry) == "starred"]
    starred.sort(key=lambda pair: pair[1])
    return [name for name, _ in starred]


def archived_names(reg: dict) -> list[str]:
    return [name for name, entry in reg.items() if state_of(entry) == "archived"]


def active_names(reg: dict) -> list[str]:
    """Everything that is not archived — starred harnesses included."""
    return [name for name, entry in reg.items() if is_active(entry)]


# ---------------------------------------------------------------------------
# Lazy migration: tools.json + stars.json + archived.json -> harness.json
# ---------------------------------------------------------------------------


def _migrate_from_legacy() -> dict:
    """Merge the three legacy files into harness.json, once.

    Only runs when harness.json does not exist yet and tools.json does. A
    harness.json already on disk is never touched by this, even if the
    legacy files are still sitting beside it — that combination means the
    data plane migrated by hand already, and re-merging on top of it would
    silently clobber whatever it wrote.
    """
    tools = _validated_entries(read_json_object(TOOLS_FILE), TOOLS_FILE)

    stars = _read_legacy_stars()
    archived = _read_legacy_archive()

    reg = {name: dict(info) for name, info in tools.items()}
    for pin, name in enumerate(stars, start=1):
        # A star can name a harness tools.json doesn't know (the stars shim
        # deliberately supports state-only rows); keep the state, the
        # readers are the ones that must tolerate a missing command.
        reg.setdefault(name, {})
        reg[name]["state"] = "starred"
        reg[name]["pin"] = pin
    for name, entry in archived.items():
        if not isinstance(entry, dict):
            continue
        reg.setdefault(name, {})
        reg[name].pop("pin", None)  # archived and starred are exclusive
        reg[name]["state"] = "archived"
        reg[name]["archived"] = {
            "reason": str(entry.get("reason") or ""),
            "archived_at": str(entry.get("archived_at") or ""),
            "usage": str(entry.get("usage") or "unknown"),
        }

    save_registry(reg)
    _backup_legacy_files()
    return reg


def _read_legacy_stars() -> list[str]:
    if not STARS_FILE.exists():
        return []
    try:
        with open(STARS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, RecursionError) as exc:
        # The migration below retires this file to backup right after
        # reading it; reading a corrupt one as "no stars" would commit a
        # harness.json that drops every pin. Fail instead so the file is
        # still there to fix.
        raise CorruptConfigurationError(
            f"{STARS_FILE}: cannot migrate an unreadable stars file"
            f" ({exc}); fix or remove it and load the registry again"
        ) from exc
    if isinstance(data, list):
        return [str(x) for x in data if isinstance(x, str) and x]
    if isinstance(data, dict) and isinstance(data.get("stars"), list):
        return [str(x) for x in data["stars"] if isinstance(x, str) and x]
    return []


def _read_legacy_archive() -> dict:
    if not ARCHIVE_FILE.exists():
        return {}
    try:
        with open(ARCHIVE_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, RecursionError) as exc:
        # Same rule as the stars file: committing a migration on an
        # unreadable archive would silently re-activate shelved tools.
        raise CorruptConfigurationError(
            f"{ARCHIVE_FILE}: cannot migrate an unreadable archive file"
            f" ({exc}); fix or remove it and load the registry again"
        ) from exc
    return data if isinstance(data, dict) else {}


def _backup_legacy_files() -> None:
    """Move the legacy files out of the way. Never delete: a lazy migration
    that loses data on a bad merge is worse than one that leaves clutter.

    Derived from CONFIG_DIR rather than importing SWE_DIR directly, so
    that patching CONFIG_DIR (as every test here does, to keep this pointed
    at a tmp dir) also redirects the backup — it must never write under a
    real ~/.quiver during a test run.
    """
    stamp = datetime.now().strftime("%Y%m%d")
    dest_dir = CONFIG_DIR.parent / ".backup" / f"registry-migration-{stamp}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for legacy in (TOOLS_FILE, STARS_FILE, ARCHIVE_FILE):
        if legacy.exists():
            shutil.move(str(legacy), str(dest_dir / legacy.name))
