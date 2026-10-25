"""Assemble ``swe doctor`` findings from the path-health probes."""

from __future__ import annotations

from pathlib import Path

from quiver.harness.models import DoctorFinding
from quiver.harness.path_health import is_dir_on_path


def collect_findings(env, preferred, which_npm, nvm_dirs, orphans, drift) -> list:
    """Every problem ``swe doctor`` knows about, as finding models.

    Severity ``error`` is exactly what makes the exit status 1: an off-PATH
    harness, an npm global bin missing from PATH, or an error-level drift
    finding. A missing node or npm, nvm bins off PATH and drift warnings
    are warnings, and a differing preferred npm is info, so ``ok`` and the
    exit status always agree.
    """
    findings = []
    if not env.node:
        findings.append(DoctorFinding(
            "node", "warning", "node not found on PATH"))
    if not env.npm:
        findings.append(DoctorFinding(
            "npm", "warning", "npm not found on PATH",
            "Install Node/npm, or put npm on PATH."))
    if env.global_bin and not env.global_bin_on_path:
        findings.append(DoctorFinding(
            "npm.global-bin", "error",
            f"npm global bin {env.global_bin} is not on PATH",
            f'export PATH="{env.global_bin}:$PATH"'))
    if preferred and which_npm:
        try:
            differs = (Path(preferred).resolve()
                       != Path(which_npm).resolve())
        except (OSError, RuntimeError):
            # A symlink loop or hostile PATH entry must not kill the
            # report — compare the raw strings instead.
            differs = preferred != which_npm
        if differs:
            findings.append(DoctorFinding(
                "npm.preferred", "info",
                f"which npm is {which_npm}; swe prefers {preferred}"))
    if nvm_dirs and not any(is_dir_on_path(d) for d in nvm_dirs):
        findings.append(DoctorFinding(
            "nvm", "warning",
            "nvm node bins are not on PATH, so nvm globals are invisible "
            "to swe and non-interactive shells",
            "Avoid `npm install -g` under nvm unless nvm is always on PATH. "
            "Prefer: swe install <name>"))
    for name, command, hit in orphans:
        via = install_method(name)
        findings.append(DoctorFinding(
            "off-path", "error",
            f"{name} ({command}) is at {hit.path} [{hit.source}], not on PATH",
            f"swe install {name} --via={via}" if via else f"swe install {name}",
            harness=name, action="install" if via else None, via=via))
    for finding in drift:
        findings.append(DoctorFinding(
            f"drift.{finding.area}",
            "error" if finding.severity == "error" else "warning",
            finding.message))
    return findings


def install_method(name: str) -> str | None:
    """The method a Run reinstalls an off-PATH harness with: the one that
    put the stray copy there (an nvm npm install comes back through the
    npm on PATH), else the first whose tool is on PATH. None when no
    installer's tool is on PATH, so there is nothing to run."""
    import os
    import shutil

    from quiver.harness.commands import _install_command_name
    from quiver.harness.installers import METHOD_TOOL, installers_for, uninstaller_for
    from quiver.harness.path_health import search_dirs_for_command
    from quiver.harness.registry import load_registry_readonly

    usable = [inst.via for inst in installers_for(name) if shutil.which(METHOD_TOOL[inst.via])]
    if not usable:
        return None
    command = _install_command_name(name, load_registry_readonly())
    for hit in search_dirs_for_command(command):
        if hit.source == "path":
            continue
        remover = uninstaller_for(name, os.path.realpath(hit.path))
        if remover is not None and remover.via in usable:
            return remover.via
    return usable[0]


def setup_findings(home: Path | None = None) -> list[DoctorFinding]:
    """Setup-state checks ``swe doctor`` runs after the path probes.

    They short-circuit so the user fixes the root cause first: no hub, no
    shared AGENTS.md, an empty registry (which makes the per-harness link
    check meaningless). Every one is a warning, so ``ok`` and the exit
    status stay untouched.
    """
    from quiver import paths
    from quiver.harness.registry import is_active, load_registry_readonly
    from quiver.init.layout import link_states

    home = home or Path.home()
    if not paths.quiver_dir_for(home).is_dir():
        return [DoctorFinding(
            "setup.hub", "warning",
            "~/.quiver doesn't exist yet, so nothing is shared between harnesses",
            "swe setup --apply", action="setup")]
    if not (paths.quiver_dir_for(home) / "AGENTS.md").is_file():
        return [DoctorFinding(
            "setup.agents-md", "warning",
            "No shared AGENTS.md in ~/.quiver",
            "swe setup --apply", action="setup")]

    registry = load_registry_readonly()
    if not registry:
        return [DoctorFinding(
            "setup.registry", "warning",
            "No harnesses registered",
            "swe harness discover --apply", action="discover")]

    findings: list[DoctorFinding] = []
    states = link_states(home)
    for name, entry in registry.items():
        if not is_active(entry):
            continue
        wanted = []
        per = states.get(name, {})
        if per.get("agents") in ("create", "relink"):
            wanted.append("the shared AGENTS.md")
        if per.get("skills") in ("create", "relink"):
            wanted.append("skills")
        if wanted:
            from quiver.harness.models import display_name
            title = display_name(entry) or name
            findings.append(DoctorFinding(
                "setup.unlinked", "warning",
                f"{title} doesn't read {' or '.join(wanted)} yet",
                "swe init", harness=name, action="init"))
    return findings
