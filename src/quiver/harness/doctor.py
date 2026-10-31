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
            which_dir = str(Path(which_npm).parent)
            preferred_dir = str(Path(preferred).parent)
            # Removing the PATH's npm is only safe when the preferred one
            # is already reachable; otherwise it is the only npm there is.
            preferred_on_path = is_dir_on_path(Path(preferred_dir))
            # A shell-init edit does not reach the process already
            # serving the web app: the check clears on the next hub start.
            note = " A shell edit only counts from a new shell or a hub restart."
            if preferred_on_path:
                fix = (f"reorder PATH so {preferred_dir} wins, or remove "
                       f"the npm at {which_npm}")
                steps = ("Put {0} ahead of {1}, or remove npm from {1}."
                         .format(preferred_dir, which_dir) + note)
                prompt = (f"`which npm` resolves to {which_npm} but swe "
                          f"prefers {preferred}. Inspect the shell init "
                          f"files and reorder PATH so the preferred npm "
                          f"wins, or remove npm from {which_dir}. Verify "
                          f"with `swe doctor` from a new shell: the "
                          f"npm.preferred finding should be gone.")
            else:
                fix = f"add {preferred_dir} to PATH ahead of {which_dir}"
                steps = (f"{preferred_dir} is not on PATH, so removing "
                         f"{which_dir}'s npm would leave none. Add "
                         f"{preferred_dir} to PATH in your shell init."
                         + note)
                prompt = (f"`which npm` resolves to {which_npm} but swe "
                          f"prefers {preferred}, and {preferred_dir} is "
                          f"not on PATH at all. Inspect the shell init "
                          f"files and add {preferred_dir} to PATH. Do "
                          f"NOT remove {which_dir}'s npm: it is the only "
                          f"one on PATH. Verify with `swe doctor` from a "
                          f"new shell: the npm.preferred finding should "
                          f"be gone.")
            findings.append(DoctorFinding(
                "npm.preferred", "info",
                f"which npm is {which_npm}; swe prefers {preferred}",
                fix,
                source=f"PATH order: {which_dir} precedes {preferred_dir}",
                remedies=(
                    {"kind": "steps",
                     "title": "Reorder PATH in your shell init",
                     "detail": steps},
                    {"kind": "agent", "title": "Fix with agent",
                     "detail": "An agent inspects your shell init files "
                              "and makes the edit itself.",
                     "prompt": prompt},
                )))
    if nvm_dirs and not any(is_dir_on_path(d) for d in nvm_dirs):
        findings.append(DoctorFinding(
            "nvm", "warning",
            "nvm node bins are not on PATH, so nvm globals are invisible "
            "to swe and non-interactive shells",
            "Avoid `npm install -g` under nvm unless nvm is always on PATH. "
            "Prefer: swe install <name>"))
    for name, command, hit in orphans:
        via = install_method(name)
        fix = (f"swe install {name} --via={via}" if via
               else f"swe install {name}")
        findings.append(DoctorFinding(
            "off-path", "error",
            f"{name} ({command}) is at {hit.path} [{hit.source}], not on PATH",
            fix,
            harness=name, action="install" if via else None, via=via,
            source=f"found at {hit.path} [{hit.source}]",
            remedies=({"kind": "action", "action": "install", "via": via,
                       "title": "Reinstall with swe",
                       "command": fix,
                       "detail": f"{fix}: reinstalls the way the stray "
                                f"copy was installed, so it lands on PATH."},)
            if via else
            ({"kind": "steps", "title": "Put it on PATH yourself",
              "detail": fix},)))
    for finding in drift:
        findings.append(DoctorFinding(
            f"drift.{finding.area}",
            "error" if finding.severity == "error" else "warning",
            finding.message, finding.fix,
            harness=finding.harness, action=finding.action,
            source=finding.source, remedies=finding.remedies))
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
    doctor's exit status. An outage is listed only once it has failed
    ``PERSISTENT_POLLS`` polls in a row; a single blip stays on its card.
    """
    from quiver import paths
    from quiver.harness.remedies import (
        AUTH_REQUIRED,
        PERSISTENT_POLLS,
        SIGNED_OUT,
        remedy_for,
    )

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
        outage = status in ("unavailable", "timeout")
        occurrences = issue.get("occurrences")
        if outage and not (isinstance(occurrences, int)
                           and occurrences >= PERSISTENT_POLLS):
            continue
        stored = issue.get("remedy") if isinstance(issue.get("remedy"), dict) else None
        # The table is authoritative — it may have learned a better fix
        # since the app wrote the issue. The one exception is an extra
        # account's login fix: ``remedy_for_account`` aimed it at that
        # account's own files, which this file can't redo without the
        # account's directory. Every other status has the same fix for
        # every account, so the table wins there too.
        account_login = (bool(issue.get("accountKey") or issue.get("account"))
                         and status in (SIGNED_OUT, AUTH_REQUIRED))
        remedy = ((stored if account_login else None)
                  or remedy_for(name, status) or stored or {})
        summary = remedy.get("summary") or (stored or {}).get("summary")
        message = summary or f"{name}: {status}"
        account = issue.get("account")
        if isinstance(account, str) and account:
            message = f"{message} ({account})"
        fix = remedy.get("command") or remedy.get("url")
        if fix is None:
            steps = remedy.get("steps")
            if isinstance(steps, list) and steps:
                fix = steps[0]
        severity = "info" if outage else "warning"
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
            "swe setup --apply", action="setup", source="~/.quiver",
            remedies=({"kind": "action", "action": "setup",
                       "title": "swe setup --apply",
                       "detail": "Registers the harnesses it finds, imports "
                                "their MCP servers, and links the shared "
                                "AGENTS.md and skills into each."},))]
    if not (paths.quiver_dir_for(home) / "AGENTS.md").is_file():
        return [DoctorFinding(
            "setup.agents-md", "warning",
            "No shared AGENTS.md in ~/.quiver",
            "swe setup --apply", action="setup", source="~/.quiver",
            remedies=({"kind": "action", "action": "setup",
                       "title": "swe setup --apply",
                       "detail": "Registers the harnesses it finds, imports "
                                "their MCP servers, and links the shared "
                                "AGENTS.md and skills into each."},))]

    registry = load_registry_readonly()
    if not registry:
        return [DoctorFinding(
            "setup.registry", "warning",
            "No harnesses registered",
            "swe harness discover --apply", action="discover",
            remedies=({"kind": "action", "action": "discover",
                       "title": "swe harness discover --apply",
                       "detail": "Adds the high-confidence matches it finds "
                                "to harness.json."},))]

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
                "swe init", harness=name, action="init",
                remedies=({"kind": "action", "action": "init",
                           "title": "Link it with swe init",
                           "detail": "A real file in the way is left alone "
                                    "and listed for Replace and back up."},)))
    findings.extend(marketplace_findings(home, registry))
    return findings


# The file each plugin-capable harness declares marketplaces in. Claude
# also keeps the ones `claude plugin marketplace add` registered in
# plugins/known_marketplaces.json, which its own remove command edits.
_MARKETPLACE_CONFIG = {"claude": ".claude/settings.json", "codex": ".codex/config.toml"}


def _declared_in_settings(home: Path, harness: str) -> set[str]:
    """Names Claude's settings.json declares; the rest came from the CLI."""
    if harness != "claude":
        return set()
    try:
        data = json.loads((home / _MARKETPLACE_CONFIG["claude"]).read_text())
    except (OSError, ValueError):
        return set()
    extra = data.get("extraKnownMarketplaces") if isinstance(data, dict) else None
    return set(extra) if isinstance(extra, dict) else set()


def marketplace_findings(home: Path, registry: dict | None = None) -> list[DoctorFinding]:
    """One warning per harness whose registered folder marketplaces are gone.

    A settings file synced from another machine can name
    ``~/.quiver/plugins/<name>`` folders that never reached this one (#696).
    Only ``directory`` marketplaces count: a git or GitHub one with no
    manifest yet is fetched by the harness itself. When the settings file
    is a Nix store link, editing it would fail, so the fix says where the
    change belongs instead. A harness whose files could not be read gets a
    warning saying the check did not finish, rather than silence.
    """
    import os

    from quiver.harness.models import display_name
    from quiver.plugins.catalog import read_views

    def title(name: str) -> str:
        return display_name((registry or {}).get(name) or {}) or name

    errors: list = []
    views = read_views(home, errors)
    findings: list[DoctorFinding] = []
    for name in sorted({e["harness"] for e in errors} - set(views)):
        detail = next(e["detail"] for e in errors if e["harness"] == name)
        findings.append(DoctorFinding(
            "setup.marketplace-unchecked", "warning",
            f"Couldn't check {title(name)}'s marketplaces: {detail}", harness=name))
    for name, view in views.items():
        missing = sorted(m.name for m in view.markets.values()
                         if m.source_type == "directory" and m.root is not None
                         and not m.root.is_dir())
        if not missing:
            continue
        listed = ", ".join(missing)
        declared = _declared_in_settings(home, name) if name == "claude" else set(missing)
        in_file = [m for m in missing if m in declared]
        by_cli = [m for m in missing if m not in declared]
        config = home / _MARKETPLACE_CONFIG.get(name, "")
        nix = name in _MARKETPLACE_CONFIG and in_file and \
            os.path.realpath(config).startswith("/nix/store/")
        if nix:
            fix = (f"~/{_MARKETPLACE_CONFIG[name]} is managed by Nix: copy the folders "
                   "onto this machine or drop them from your Nix config")
        elif name in _MARKETPLACE_CONFIG:
            ways = []
            if in_file:
                ways.append(f"drop {', '.join(in_file)} from ~/{_MARKETPLACE_CONFIG[name]}")
            ways += [f"{name} plugin marketplace remove {m}" for m in by_cli]
            fix = "copy the folders onto this machine, or " + "; ".join(ways)
        else:
            fix = "copy the folders onto this machine"
        findings.append(DoctorFinding(
            "setup.marketplace-missing", "warning",
            f"{title(name)} registers {len(missing)} marketplace folder"
            f"{'s' if len(missing) != 1 else ''} missing on this machine: {listed}",
            fix, harness=name,
            source=_marketplace_source(name, in_file, by_cli),
            remedies=_marketplace_remedies(
                name, missing, in_file, by_cli, nix)))
    return findings


# Claude keeps CLI-added marketplaces beside its settings file, in
# plugins/known_marketplaces.json.
_KNOWN_MARKETPLACES = ".claude/plugins/known_marketplaces.json"


def _marketplace_source(
    name: str, in_file: list[str], by_cli: list[str]
) -> str | None:
    """The file(s) registering the missing folders, for the source line."""
    files = []
    if name in _MARKETPLACE_CONFIG and in_file:
        files.append(f"~/{_MARKETPLACE_CONFIG[name]}")
    if name == "claude" and by_cli:
        files.append(f"~/{_KNOWN_MARKETPLACES}")
    if not files:
        return None
    return "registered in " + " · ".join(files)


def _marketplace_remedies(
    name: str, missing: list[str], in_file: list[str],
    by_cli: list[str], nix: bool,
) -> tuple:
    """Ways out of a missing-marketplace finding, best first.

    Restoring the folders beats dropping the entries, since a synced
    settings file usually means the folders live on the user's other
    machine. An agent stays the escape for mixed keep/repoint/remove
    calls; a Nix-managed settings file narrows the manual side to the
    Nix config itself.
    """
    listed = ", ".join(missing)
    remedies: list[dict] = [{
        "kind": "steps",
        "title": "Copy the folders onto this machine",
        "detail": "The settings came from a machine that has them; "
                  "bring them over and the entries work again.",
    }]
    # CLI-registered entries live in known_marketplaces.json, which Nix
    # does not manage: they come out through the harness's own remove
    # command under either management style.
    cli_steps = [f"{name} plugin marketplace remove {m}" for m in by_cli]
    if nix:
        drop_steps = []
        if in_file:
            drop_steps.append(
                f"drop {', '.join(in_file)} from your Nix config "
                f"(~/{_MARKETPLACE_CONFIG[name]} is generated)")
        drop_steps += cli_steps
        remedies.append({
            "kind": "steps",
            "title": f"Or drop the {len(missing)} entr"
                     f"{'y' if len(missing) == 1 else 'ies'}",
            "steps": drop_steps,
        })
    else:
        drop_steps = []
        if in_file:
            drop_steps.append(
                f"drop {', '.join(in_file)} from "
                f"~/{_MARKETPLACE_CONFIG.get(name, 'settings')}")
        drop_steps += cli_steps
        if drop_steps:
            remedies.append({
                "kind": "steps",
                "title": f"Or drop the {len(missing)} entr"
                         f"{'y' if len(missing) == 1 else 'ies'}",
                "steps": drop_steps,
            })
    if name in _MARKETPLACE_CONFIG:
        config = f"~/{_MARKETPLACE_CONFIG[name]}"
        places = [config] + ([f"~/{_KNOWN_MARKETPLACES}"]
                             if name == "claude" and by_cli else [])
        if nix:
            # The settings file resolves into the store: its entries go
            # in the Nix config that generates it; CLI-registered ones
            # still leave through the harness's own remove command.
            prompt = (f"{config} is a symlink into /nix/store and "
                      f"registers {len(missing)} marketplace "
                      f"folder{'s' if len(missing) != 1 else ''} missing "
                      f"on this machine ({listed}). Do NOT edit {config}. "
                      + (f"For {', '.join(in_file)}: find the Nix or "
                         "home-manager config that generates the file "
                         "and drop the entries there. " if in_file else "")
                      + (f"For {', '.join(by_cli)}: run `{name} plugin "
                         "marketplace remove <name>` per entry, which "
                         f"edits ~/{_KNOWN_MARKETPLACES}, or remove "
                         "them there by hand. " if by_cli else "")
                      + "Restore a folder instead when it should exist "
                      "here. Verify with `swe doctor`: the "
                      "setup.marketplace-missing finding should be gone.")
        else:
            prompt = (f"{config} registers {len(missing)} marketplace "
                      f"folder{'s' if len(missing) != 1 else ''} that do "
                      f"not exist on this machine ({listed}). Decide per "
                      f"entry: restore the folder if it should exist here, "
                      f"repoint the entry at the real location, or remove "
                      f"it from {' and '.join(places)}, backing up each "
                      f"file first. Verify with `swe doctor`: the "
                      f"setup.marketplace-missing finding should be gone.")
        remedies.append({
            "kind": "agent",
            "title": "Fix with agent",
            "detail": "An agent decides per entry: keep, repoint, or remove.",
            "prompt": prompt,
        })
    return tuple(remedies)
