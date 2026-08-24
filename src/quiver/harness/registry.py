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

import copy
import json
import shutil
from datetime import datetime

from quiver.harness.defaults import DEFAULT_TOOLS
from quiver.paths import ARCHIVE_FILE, CONFIG_DIR, HARNESS_FILE, STARS_FILE, TOOLS_FILE


def load_registry() -> dict:
    if HARNESS_FILE.exists():
        with open(HARNESS_FILE) as f:
            return json.load(f)
    if TOOLS_FILE.exists():
        return _migrate_from_legacy()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # Deep, not shallow: callers (star/archive) set state straight onto the
    # entry dicts they get back, and a shallow dict(DEFAULT_TOOLS) hands out
    # the very same nested dicts DEFAULT_TOOLS holds — mutating one in place
    # would permanently star or archive a harness in the module-level
    # default for the rest of the process.
    defaults = copy.deepcopy(DEFAULT_TOOLS)
    save_registry(defaults)
    return defaults


def save_registry(tools: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(HARNESS_FILE, "w") as f:
        json.dump(tools, f, indent=2)


def alias_map(tools: dict) -> dict[str, str]:
    """Return {alias_or_name: canonical_name} for every tool."""
    mapping: dict[str, str] = {}
    for name, info in tools.items():
        mapping[name] = name
        for alias in info.get("aliases", []):
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
    starred = [(name, entry.get("pin") or 0) for name, entry in reg.items()
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
    with open(TOOLS_FILE) as f:
        tools = json.load(f)

    stars = _read_legacy_stars()
    archived = _read_legacy_archive()

    reg = {name: dict(info) for name, info in tools.items()}
    for pin, name in enumerate(stars, start=1):
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
    except (OSError, json.JSONDecodeError):
        return []
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
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _backup_legacy_files() -> None:
    """Move the legacy files out of the way. Never delete: a lazy migration
    that loses data on a bad merge is worse than one that leaves clutter.

    Derived from CONFIG_DIR rather than importing QUIVER_DIR directly, so
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
