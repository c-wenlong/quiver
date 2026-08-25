"""Discover AI coding CLI harnesses on the local machine."""

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quiver.harness.catalog import EXCLUDE_BASENAMES, EXTRA_BIN_DIRS, HARNESS_CATALOG
from quiver.harness.registry import load_registry, save_registry
from quiver.harness.tools import live_version

# Uncatalogued agent binaries and *-code suffixes (avoid generic *-cli false positives).
_UNCATALOGUED_BINARIES = frozenset({"aider", "warp", "factory"})
_PATH_CODE_SUFFIX_RE = re.compile(r"^.+-code$", re.I)

# Home-directory scan. A PATH scan misses every harness that ships as a
# desktop app or IDE extension: of ten such tools found on this machine in
# one audit (traycer, pane, conductor, aside, …), only three had a binary
# anywhere on PATH. Their homes still look alike — a dotdir holding agent
# furniture — so scan for that shape instead.
#
# The markers are agent-specific on purpose. Every second dotdir has a
# config.json (~/.docker does); almost none that aren't agents keep a
# skills/ or agents/ dir or an AGENTS.md within a few levels. "sessions"
# was tried and dropped: pgadmin and mutagen both keep one.
_HOME_MARKER_DIRS = frozenset({"skills", "agents"})
_HOME_MARKER_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "SKILL.md", "SOUL.md"})
# Dotdirs that are never harness homes, and child dirs too big or too
# generic to descend into.
_HOME_SCAN_SKIP = frozenset({
    ".Trash", ".cache", ".cargo", ".cups", ".docker", ".dropbox", ".gem",
    ".git", ".gnupg", ".gradle", ".local", ".m2", ".npm", ".nvm", ".ollama",
    ".orbstack", ".pyenv", ".quiver", ".rustup", ".ssh", ".venv", ".vim",
    ".vscode", ".yarn", "node_modules", "logs", "cache", "venv", "dist",
    "build",
})
_HOME_SCAN_DEPTH = 3
_HOME_SCAN_ENTRY_CAP = 500


@dataclass(frozen=True)
class HarnessFinding:
    name: str
    command: str
    path: str
    confidence: str  # high | medium | low
    source: str  # catalog | path_scan
    status: str  # new | registered | missing
    description: str = ""
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


def _expand_path(path: str, home: Path) -> Path:
    return Path(os.path.expanduser(path)).expanduser()


def _path_dirs(path_env: str | None, home: Path) -> list[Path]:
    dirs: list[Path] = []
    seen: set[Path] = set()
    raw = (path_env if path_env is not None else os.environ.get("PATH", "")).split(os.pathsep)
    for extra in EXTRA_BIN_DIRS:
        raw.append(str(_expand_path(extra, home)))
    for entry in raw:
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except Exception:
            continue
        if resolved not in seen and resolved.is_dir():
            seen.add(resolved)
            dirs.append(resolved)
    return dirs


def _registered_commands(registry: dict) -> dict[str, str]:
    """Map command basename → registry name."""
    mapping: dict[str, str] = {}
    for name, info in registry.items():
        cmd = info.get("command")
        if cmd:
            mapping[cmd] = name
    return mapping


def _command_candidates(name: str, command: str) -> list[str]:
    candidates = [command, name, f"{command}-cli", f"{name}-cli"]
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _resolve_executable(
    name: str,
    command: str,
    path_env: str | None,
    path_dirs: list[Path],
) -> tuple[str | None, str | None]:
    for candidate in _command_candidates(name, command):
        path = shutil.which(candidate, path=path_env)
        if path:
            return path, command
    for directory in path_dirs:
        for candidate in _command_candidates(name, command):
            entry = directory / candidate
            try:
                if entry.is_file() and os.access(entry, os.X_OK):
                    return str(entry.resolve()), command
            except OSError:
                continue
    return None, None


def _catalog_findings(
    registry: dict,
    path_env: str | None,
    home: Path,
) -> list[HarnessFinding]:
    findings: list[HarnessFinding] = []
    registered_names = set(registry)
    reg_by_command = _registered_commands(registry)
    path_dirs = _path_dirs(path_env, home)

    for name, meta in HARNESS_CATALOG.items():
        command = meta["command"]
        path, resolved_command = _resolve_executable(name, command, path_env, path_dirs)
        in_registry = name in registered_names
        if path and resolved_command:
            if in_registry:
                status = "registered"
                confidence = "high"
            elif resolved_command in reg_by_command:
                status = "registered"
                confidence = "high"
            else:
                status = "new"
                confidence = "high"
            findings.append(
                HarnessFinding(
                    name=name,
                    command=resolved_command,
                    path=path,
                    confidence=confidence,
                    source="catalog",
                    status=status,
                    description=meta.get("description", ""),
                    tags=tuple(meta.get("tags", [])),
                    aliases=tuple(meta.get("aliases", [])),
                )
            )
        elif in_registry:
            findings.append(
                HarnessFinding(
                    name=name,
                    command=command,
                    path="",
                    confidence="high",
                    source="catalog",
                    status="missing",
                    description=meta.get("description", ""),
                    tags=tuple(meta.get("tags", [])),
                    aliases=tuple(meta.get("aliases", [])),
                )
            )
    return findings


def _path_scan_findings(
    registry: dict,
    path_env: str | None,
    home: Path,
    existing: list[HarnessFinding],
) -> list[HarnessFinding]:
    known_commands = {f.command for f in existing}
    known_names = {f.name for f in existing}
    reg_by_command = _registered_commands(registry)
    catalog_commands = {m["command"] for m in HARNESS_CATALOG.values()}
    catalog_names = set(HARNESS_CATALOG)
    extra: list[HarnessFinding] = []

    for directory in _path_dirs(path_env, home):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if not os.access(entry, os.X_OK):
                    continue
            except OSError:
                continue
            basename = entry.name
            if basename in EXCLUDE_BASENAMES:
                continue
            if (
                basename in known_commands
                or basename in catalog_commands
                or basename in catalog_names
                or basename in known_names
            ):
                continue
            if basename not in _UNCATALOGUED_BINARIES and not _PATH_CODE_SUFFIX_RE.match(basename):
                continue
            if basename in reg_by_command:
                status = "registered"
            else:
                status = "new"
            extra.append(
                HarnessFinding(
                    name=basename,
                    command=basename,
                    path=str(entry),
                    confidence="medium",
                    source="path_scan",
                    status=status,
                    description=f"Discovered on PATH ({basename})",
                    tags=("agentic", "coding"),
                    aliases=(),
                )
            )
            known_commands.add(basename)
    return extra


def _registered_homes(registry: dict) -> set[str]:
    """Every top-level dotdir a registered harness is known to own.

    Joins three sources, because names and directories disagree: the
    registry name itself (traycer -> ~/.traycer), the ~/.config variant,
    and the first component of every capabilities root — which is how
    droid maps to ~/.factory and qwen-code to ~/.qwen without a table.
    """
    owned: set[str] = set()
    for name, info in registry.items():
        owned.add(f".{name}")
        owned.add(f"config/{name}")
        caps = info.get("capabilities") if isinstance(info, dict) else None
        for cap in (caps or {}).values():
            root = cap.get("root") if isinstance(cap, dict) else None
            if not root or not root.startswith("~/"):
                continue
            parts = Path(root[2:]).parts
            if not parts:
                continue
            if parts[0] == ".config" and len(parts) > 1:
                owned.add(f"config/{parts[1]}")
            else:
                owned.add(parts[0])
    return owned


def _looks_like_agent_home(candidate: Path) -> str | None:
    """Return the marker that makes this dir agent-shaped, or None.

    Bounded walk: three levels, a few hundred entries, noise dirs skipped.
    Deliberately shallow — a tool that buries its skills four projects deep
    (superset does) is invisible here, and that is the accepted trade
    against descending into every dotdir on the machine.
    """
    seen = 0
    frontier = [(candidate, 0)]
    while frontier:
        directory, depth = frontier.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if seen > _HOME_SCAN_ENTRY_CAP:
                return None
            name = entry.name
            try:
                if entry.is_dir():
                    if name in _HOME_MARKER_DIRS:
                        return f"{name}/ dir"
                    if name not in _HOME_SCAN_SKIP and depth + 1 < _HOME_SCAN_DEPTH:
                        frontier.append((entry, depth + 1))
                elif name in _HOME_MARKER_FILES:
                    return name
            except OSError:
                continue
    return None


def _home_scan_findings(
    registry: dict,
    home: Path,
    existing: list[HarnessFinding],
) -> list[HarnessFinding]:
    """Find harness-shaped homes under ~ and ~/.config that nothing owns."""
    owned = _registered_homes(registry)
    owned |= {f".{f.name}" for f in existing} | {f"config/{f.name}" for f in existing}
    catalog_names = set(HARNESS_CATALOG)

    candidates: list[tuple[str, Path]] = []
    # A harness home is named like a product: one clean slug. This drops
    # timestamped backups (.hermes.pre-bootstrap-20260730-…) outright.
    slug = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
    try:
        for entry in home.iterdir():
            name = entry.name.lstrip(".")
            if (entry.name.startswith(".") and entry.is_dir()
                    and not entry.is_symlink()
                    and entry.name not in _HOME_SCAN_SKIP
                    # .config is scanned per-child below; .agents is
                    # quiver's own pre-0.2.7 shared root, not a harness.
                    and entry.name not in (".config", ".agents")
                    and slug.match(name)
                    and entry.name not in owned):
                candidates.append((name, entry))
    except OSError:
        pass
    config_dir = home / ".config"
    try:
        for entry in config_dir.iterdir():
            if (entry.is_dir() and not entry.is_symlink()
                    and entry.name not in _HOME_SCAN_SKIP
                    and f"config/{entry.name}" not in owned
                    and f".{entry.name}" not in owned):
                candidates.append((entry.name, entry))
    except OSError:
        pass

    findings: list[HarnessFinding] = []
    taken = {f.name for f in existing}
    for name, path in sorted(candidates):
        if name in taken or name in catalog_names:
            continue
        marker = _looks_like_agent_home(path)
        if marker is None:
            continue
        command = name if shutil.which(name) else ""
        findings.append(HarnessFinding(
            name=name,
            command=command,
            path=str(path),
            confidence="low",
            source="home_scan",
            status="new",
            description=f"Agent-shaped home ({marker} under {path.name})",
            tags=("registered-from-disk",),
            aliases=(),
        ))
        taken.add(name)
    return findings


def discover_harnesses(
    *,
    path_env: str | None = None,
    home: Path | None = None,
    include_registered: bool = False,
    include_missing: bool = False,
) -> list[HarnessFinding]:
    """Scan PATH and catalog for AI coding CLI harnesses."""
    home = home or Path.home()
    registry = load_registry()
    findings = _catalog_findings(registry, path_env, home)
    findings.extend(_path_scan_findings(registry, path_env, home, findings))
    findings.extend(_home_scan_findings(registry, home, findings))

    if not include_registered:
        findings = [f for f in findings if f.status != "registered"]
    if not include_missing:
        findings = [f for f in findings if f.status != "missing"]

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f.confidence, 9), f.name))
    return findings


def apply_findings(findings: list[HarnessFinding], *, min_confidence: str = "high") -> list[str]:
    """Add findings to harness.json; returns names added or updated."""
    # min_confidence is a floor: "high" applies only sure things, "low"
    # applies everything. This map used to be inverted — --apply (high)
    # accepted every tier while --apply-all accepted fewer — which went
    # unnoticed only because no finding source produced "low" until the
    # home scan did.
    allowed = {"high": {"high"}, "medium": {"high", "medium"},
               "low": {"high", "medium", "low"}}
    conf_ok = allowed.get(min_confidence, {"high"})

    registry = load_registry()
    added: list[str] = []
    for finding in findings:
        if finding.status != "new":
            continue
        if finding.confidence not in conf_ok:
            continue
        version = live_version(finding.command) if finding.command else None
        entry = {
            "command": finding.command,
            "description": finding.description,
            "version": version,
            "tags": list(finding.tags) or ["agentic", "coding"],
            "aliases": list(finding.aliases),
            "added": datetime.now().isoformat(),
            "discovered_via": finding.source,
        }
        if finding.source == "home_scan":
            # A home-scan find has no usage history and often no binary, so
            # it enters shelved: known to the registry, absent from swe
            # list, one `swe hs star` away if it turns out to matter.
            entry["state"] = "archived"
            entry["archived"] = {
                "reason": "registered by the home-directory scan; usage unknown",
                "archived_at": datetime.now().isoformat(),
                "usage": "none",
            }
        registry[finding.name] = entry
        added.append(finding.name)
    if added:
        save_registry(registry)
    return added
