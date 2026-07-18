"""PATH / Node environment health helpers for doctor + install."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from quiver.harness.catalog import EXTRA_BIN_DIRS


@dataclass(frozen=True)
class NodeEnv:
    node: str | None
    npm: str | None
    node_version: str | None
    npm_version: str | None
    global_prefix: str | None
    global_bin: str | None
    global_bin_on_path: bool


@dataclass(frozen=True)
class OffPathHit:
    command: str
    path: str
    source: str  # nvm | extra | which-miss


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path)).expanduser()


def path_entries() -> list[str]:
    return [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]


def is_dir_on_path(directory: str | Path) -> bool:
    try:
        target = Path(directory).resolve()
    except Exception:
        return False
    for entry in path_entries():
        try:
            if Path(entry).resolve() == target:
                return True
        except Exception:
            continue
    return False


def _run(cmd: list[str], timeout: float = 3.0) -> str | None:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    out = (result.stdout or result.stderr or "").strip()
    return out or None


def probe_node_env() -> NodeEnv:
    node = shutil.which("node")
    npm = preferred_npm_bin()
    node_version = None
    npm_version = None
    global_prefix = None
    global_bin = None

    if node:
        raw = _run([node, "-v"])
        node_version = raw.lstrip("v") if raw else None
    if npm:
        npm_version = _run([npm, "-v"])
        global_prefix = _run([npm, "prefix", "-g"])
        if global_prefix:
            # npm bin -g is deprecated; convention is <prefix>/bin
            candidate = Path(global_prefix) / "bin"
            global_bin = str(candidate) if candidate.is_dir() else global_prefix

    return NodeEnv(
        node=node,
        npm=npm,
        node_version=node_version,
        npm_version=npm_version,
        global_prefix=global_prefix,
        global_bin=global_bin,
        global_bin_on_path=bool(global_bin and is_dir_on_path(global_bin)),
    )


def preferred_npm_bin() -> str | None:
    """Prefer a PATH-visible npm that is NOT under nvm, else any npm on PATH."""
    which_npm = shutil.which("npm")
    homebrew = Path("/opt/homebrew/bin/npm")
    usr_local = Path("/usr/local/bin/npm")

    for candidate in (homebrew, usr_local):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            # Prefer if this npm's bin dir is on PATH
            if is_dir_on_path(candidate.parent) or which_npm:
                return str(candidate)

    if which_npm and "nvm" not in which_npm:
        return which_npm
    if which_npm:
        return which_npm
    return None


def nvm_bin_dirs(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    nvm_dir = Path(os.environ.get("NVM_DIR", home / ".nvm"))
    versions = nvm_dir / "versions" / "node"
    if not versions.is_dir():
        return []
    dirs: list[Path] = []
    for child in sorted(versions.iterdir()):
        bin_dir = child / "bin"
        if bin_dir.is_dir():
            dirs.append(bin_dir)
    return dirs


def search_dirs_for_command(command: str, home: Path | None = None) -> list[OffPathHit]:
    """Find executables named `command` outside current PATH resolution."""
    home = home or Path.home()
    hits: list[OffPathHit] = []
    seen: set[str] = set()

    # Already on PATH?
    on_path = shutil.which(command)
    if on_path:
        return [OffPathHit(command=command, path=on_path, source="path")]

    candidates: list[tuple[str, Path]] = []
    for d in nvm_bin_dirs(home):
        candidates.append(("nvm", d / command))
    for extra in EXTRA_BIN_DIRS:
        candidates.append(("extra", _expand(extra) / command))
    # npm-global style
    candidates.append(("extra", home / ".npm-global" / "bin" / command))

    for source, path in candidates:
        try:
            if path.is_file() and os.access(path, os.X_OK):
                resolved = str(path.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    hits.append(OffPathHit(command=command, path=resolved, source=source))
        except OSError:
            continue
    return hits


def find_off_path_tools(registry: dict) -> list[tuple[str, str, OffPathHit]]:
    """Return (registry_name, command, hit) for tools missing from PATH but found elsewhere."""
    results: list[tuple[str, str, OffPathHit]] = []
    for name, info in sorted(registry.items()):
        command = info.get("command") or name
        if shutil.which(command):
            continue
        hits = search_dirs_for_command(command)
        off = [h for h in hits if h.source != "path"]
        if off:
            results.append((name, command, off[0]))
    return results


# Known npm package names when they differ from the registry/command name.
NPM_PACKAGE_MAP: dict[str, str] = {
    "jules": "@google/jules",
    "claude": "@anthropic-ai/claude-code",
    "codex": "@openai/codex",
    "copilot": "@github/copilot",
    "continue": "@continuedev/cli",
    "cline": "cline",
    "qwen-code": "@qwen-code/qwen-code",
    "mistral-vibe": "mistral-vibe",
    "augment": "@augmentcode/auggie",
    "cursor": "@cursor/agent",  # best-effort; may vary
    "mastracode": "mastracode",
    "pi": "@earendil-works/pi-coding-agent",
}


def resolve_npm_package(name: str, package: str | None = None) -> str:
    if package:
        return package
    return NPM_PACKAGE_MAP.get(name, name)
