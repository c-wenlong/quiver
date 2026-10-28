"""Assemble ``swe doctor`` findings from the path-health probes."""

from __future__ import annotations

import json
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


def issue_findings(home: Path | None = None) -> list[DoctorFinding]:
    """Usage problems the app recorded in ``~/.quiver/data/issues.json``.

    The app's IssueStore is the source of truth for "this needs the
    user"; each entry becomes a finding whose fix text comes from the
    REMEDIES table, so ``swe doctor`` and the app describe the same fix.
    All of them are warnings or info — a stale sign-in must never fail
    doctor's exit status.
    """
    from quiver import paths
    from quiver.harness.remedies import remedy_for

    try:
        raw = json.loads(paths.issues_file_for(home or Path.home()).read_text())
    except (OSError, ValueError):
        return []
    issues = raw.get("issues") if isinstance(raw, dict) else None
    if not isinstance(issues, dict):
        return []
    findings: list[DoctorFinding] = []
    for issue_id, issue in sorted(issues.items()):
        if not isinstance(issue, dict) or issue.get("resolved"):
            continue
        name = issue.get("harness")
        status = issue.get("status")
        if not isinstance(name, str) or not isinstance(status, str):
            continue
        stored = issue.get("remedy") if isinstance(issue.get("remedy"), dict) else None
        # The table is authoritative — it may have learned a better fix
        # since the app wrote the issue; the stored remedy is the fallback.
        remedy = remedy_for(name, status) or stored or {}
        summary = (stored or {}).get("summary") or remedy.get("summary")
        message = summary or f"{name}: {status}"
        account = issue.get("account")
        if isinstance(account, str) and account:
            message = f"{message} ({account})"
        fix = remedy.get("command") or remedy.get("url")
        if fix is None:
            steps = remedy.get("steps")
            if isinstance(steps, list) and steps:
                fix = steps[0]
        severity = ("info" if status in ("unavailable", "timeout")
                    else "warning")
        findings.append(DoctorFinding(
            f"usage.{issue_id}", severity, message, fix, harness=name))
    return findings


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
    findings.extend(marketplace_findings(home, registry))
    return findings


# The file each plugin-capable harness registers marketplaces in.
_MARKETPLACE_CONFIG = {"claude": ".claude/settings.json", "codex": ".codex/config.toml"}


def marketplace_findings(home: Path, registry: dict | None = None) -> list[DoctorFinding]:
    """One warning per harness whose registered folder marketplaces are gone.

    A settings file synced from another machine can name
    ``~/.quiver/plugins/<name>`` folders that never reached this one (#696).
    Only ``directory`` marketplaces count: a git or GitHub one with no
    manifest yet is fetched by the harness itself. When the settings file
    is a Nix store link, editing it would fail, so the fix says where the
    change belongs instead.
    """
    import os

    from quiver.harness.models import display_name
    from quiver.plugins.catalog import read_views

    findings: list[DoctorFinding] = []
    for name, view in read_views(home, []).items():
        missing = sorted(m.name for m in view.markets.values()
                         if m.source_type == "directory" and m.root is not None
                         and not m.root.is_dir())
        if not missing:
            continue
        title = display_name((registry or {}).get(name) or {}) or name
        listed = ", ".join(missing)
        config = home / _MARKETPLACE_CONFIG.get(name, "")
        if name in _MARKETPLACE_CONFIG and os.path.realpath(config).startswith("/nix/store/"):
            fix = (f"~/{_MARKETPLACE_CONFIG[name]} is managed by Nix: copy the folders "
                   "onto this machine or drop them from your Nix config")
        elif name in _MARKETPLACE_CONFIG:
            fix = (f"copy the folders onto this machine, or drop them from "
                   f"~/{_MARKETPLACE_CONFIG[name]}")
        else:
            fix = "copy the folders onto this machine"
        findings.append(DoctorFinding(
            "setup.marketplace-missing", "warning",
            f"{title} registers {len(missing)} marketplace folder"
            f"{'s' if len(missing) != 1 else ''} missing on this machine: {listed}",
            fix, harness=name))
    return findings
