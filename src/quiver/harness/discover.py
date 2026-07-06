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

    if not include_registered:
        findings = [f for f in findings if f.status != "registered"]
    if not include_missing:
        findings = [f for f in findings if f.status != "missing"]

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order.get(f.confidence, 9), f.name))
    return findings


def apply_findings(findings: list[HarnessFinding], *, min_confidence: str = "high") -> list[str]:
    """Add findings to tools.json; returns names added or updated."""
    allowed = {"high": {"high", "medium", "low"}, "medium": {"medium", "low"}, "low": {"low"}}
    conf_ok = allowed.get(min_confidence, {"high"})

    registry = load_registry()
    added: list[str] = []
    for finding in findings:
        if finding.status != "new":
            continue
        if finding.confidence not in conf_ok:
            continue
        version = live_version(finding.command) if finding.path else None
        registry[finding.name] = {
            "command": finding.command,
            "description": finding.description,
            "version": version,
            "tags": list(finding.tags) or ["agentic", "coding"],
            "aliases": list(finding.aliases),
            "added": datetime.now().isoformat(),
            "discovered_via": finding.source,
        }
        added.append(finding.name)
    if added:
        save_registry(registry)
    return added
