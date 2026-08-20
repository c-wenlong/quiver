"""Sectioned interactive setup wizard for Quiver."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from quiver.configuration import (
    CONFIG_FILE,
    ConfigurationError,
    check_config,
    get_value,
    interactive_report_setup,
    load_config,
    load_resolved_config,
    report_setup_complete,
    save_config,
)
from quiver.console import c
from quiver.harness.discover import apply_findings, discover_harnesses
from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers
from quiver.paths import MCP_SOURCE_FILE, REGISTRY_FILE
from quiver.prompt import read_line
from quiver.providers.discover import discover_provider_keys
from quiver.providers.keys import default_keys_dir
from quiver.providers.registry import load_registry as load_provider_registry
from quiver.skills.symlinks import (
    apply_skills_symlink_hints,
    skills_symlink_hints,
)


InputFn = Callable[[str], str]


@dataclass(frozen=True)
class StageOutcome:
    key: str
    label: str
    status: str
    detail: str
    changed: tuple[str, ...] = ()
    backups: tuple[str, ...] = ()


SECTION_ALIASES = {
    "harness": "harnesses",
    "harnesses": "harnesses",
    "provider": "providers",
    "providers": "providers",
    "mcp": "mcp",
    "skill": "skills",
    "skills": "skills",
    "report": "report",
    "reports": "report",
    "check": "check",
    "verify": "check",
}

SECTION_LABELS = {
    "harnesses": "AI coding harnesses",
    "providers": "LLM providers",
    "mcp": "MCP servers",
    "skills": "Shared skills",
    "report": "Coding-session reports",
    "check": "Verification",
}


def _ask_yes_no(
    question: str,
    *,
    default: bool,
    input_fn: InputFn = read_line,
) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input_fn(f"  {question} [{suffix}] ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print(c("yellow", "  Enter yes or no."))


def _display_path(path: Path, home: Path) -> str:
    try:
        relative = path.expanduser().relative_to(home.expanduser())
    except ValueError:
        return str(path)
    return "~" if str(relative) == "." else f"~/{relative}"


def backup_file(path: Path, *, now: datetime | None = None) -> Path | None:
    """Create a timestamped sibling backup without changing the source."""

    if not path.exists():
        return None
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
    backup = path.with_name(f"{path.name}.bak.{timestamp}")
    shutil.copy2(path, backup)
    return backup


def _stage_header(number: int, total: int, label: str) -> None:
    print()
    print(c("cyan", c("bold", f"  Step {number}/{total}  {label}")))
    print(c("dim", f"  {'-' * 56}"))


def _stage_harnesses(
    number: int,
    total: int,
    *,
    input_fn: InputFn,
    home: Path,
    quick: bool,
) -> StageOutcome:
    _stage_header(number, total, SECTION_LABELS["harnesses"])
    findings = discover_harnesses(include_registered=True, include_missing=True)
    registered = [item for item in findings if item.status == "registered" and item.path]
    missing = [item for item in findings if item.status == "missing"]
    new = [
        item
        for item in findings
        if item.status == "new" and item.confidence == "high"
    ]
    print(c("dim", f"  {len(registered)} registered and available; {len(missing)} registered but missing."))
    if not new:
        print(c("green", "  OK  No new high-confidence harnesses found."))
        return StageOutcome("harnesses", SECTION_LABELS["harnesses"], "ready", "registry is current")

    print(c("bold", f"  Found {len(new)} harness(es) ready to register:"))
    for item in new:
        print(f"    {c('green', '+')} {item.name:<16} {c('dim', _display_path(Path(item.path), home))}")
    if not _ask_yes_no("Register these harnesses?", default=True, input_fn=input_fn):
        return StageOutcome("harnesses", SECTION_LABELS["harnesses"], "skipped", f"{len(new)} available")

    backup = backup_file(REGISTRY_FILE)
    added = apply_findings(new, min_confidence="high")
    print(c("green", f"  Saved {len(added)} harness(es): {', '.join(added) or 'none'}"))
    return StageOutcome(
        "harnesses",
        SECTION_LABELS["harnesses"],
        "changed" if added else "ready",
        f"{len(added)} registered",
        tuple(added),
        (str(backup),) if backup else (),
    )


def _stage_providers(
    number: int,
    total: int,
    *,
    input_fn: InputFn,
    home: Path,
    quick: bool,
) -> StageOutcome:
    del input_fn, quick
    _stage_header(number, total, SECTION_LABELS["providers"])
    providers = load_provider_registry()
    keys_dir = default_keys_dir(home).expanduser()
    rows = discover_provider_keys(providers, keys_dir)
    configured = [row["name"] for row in rows if row["masked"] != "-"]
    print(c("dim", "  Quiver discovers provider credentials but never stores or rewrites them."))
    print(f"  Credential source: {c('cyan', _display_path(keys_dir, home))}")
    if configured:
        visible = ", ".join(configured[:8])
        if len(configured) > 8:
            visible += f", +{len(configured) - 8} more"
        print(c("green", f"  OK  {len(configured)}/{len(rows)} providers have discoverable keys: {visible}"))
        status = "ready"
        detail = f"{len(configured)} provider key(s) discovered"
    else:
        print(c("yellow", "  Attention  No provider API keys were discovered."))
        print(c("dim", "  Use `swe providers info <name>` for the expected file and environment variable."))
        status = "attention"
        detail = "no provider keys discovered"
    print(c("dim", "  Harness subscriptions and OAuth remain owned by Claude, Codex, and other CLIs."))
    return StageOutcome("providers", SECTION_LABELS["providers"], status, detail)


def _stage_mcp(
    number: int,
    total: int,
    *,
    input_fn: InputFn,
    home: Path,
    quick: bool,
) -> StageOutcome:
    del home, quick
    _stage_header(number, total, SECTION_LABELS["mcp"])
    new = [item for item in discover_mcp_servers() if item.status == "new"]
    if not new:
        print(c("green", "  OK  Source-of-truth already contains every discovered MCP server."))
        return StageOutcome("mcp", SECTION_LABELS["mcp"], "ready", "source-of-truth is current")

    print(c("bold", f"  Found {len(new)} server(s) outside {MCP_SOURCE_FILE}:"))
    for item in new[:12]:
        print(f"    {c('green', '+')} {item.name:<20} {c('dim', ', '.join(item.tools))}")
    if len(new) > 12:
        print(c("dim", f"    ... and {len(new) - 12} more; run `swe mcp discover` for the full list."))
    if not _ask_yes_no("Import them into the MCP source-of-truth?", default=True, input_fn=input_fn):
        return StageOutcome("mcp", SECTION_LABELS["mcp"], "skipped", f"{len(new)} available")

    backup = backup_file(MCP_SOURCE_FILE)
    added = apply_mcp_findings(new).added
    print(c("green", f"  Saved {len(added)} MCP server(s): {', '.join(added) or 'none'}"))
    return StageOutcome(
        "mcp",
        SECTION_LABELS["mcp"],
        "changed" if added else "ready",
        f"{len(added)} imported",
        tuple(added),
        (str(backup),) if backup else (),
    )


def _stage_skills(
    number: int,
    total: int,
    *,
    input_fn: InputFn,
    home: Path,
    quick: bool,
) -> StageOutcome:
    del quick
    _stage_header(number, total, SECTION_LABELS["skills"])
    hints = skills_symlink_hints(home=home)
    safe = [item for item in hints if item.action in {"create_shared", "symlink"}]
    manual = [item for item in hints if item.action == "manual"]
    if not safe and not manual:
        print(c("green", "  OK  Harness skill roots already share ~/.quiver/skills."))
        return StageOutcome("skills", SECTION_LABELS["skills"], "ready", "shared roots are linked")

    for item in safe:
        print(f"    {c('green', '+')} {item.label:<16} {item.reason}")
    for item in manual:
        print(f"    {c('yellow', '!')} {item.label:<16} {item.reason}")
        print(c("dim", f"      {item.command}"))
    if not safe:
        return StageOutcome("skills", SECTION_LABELS["skills"], "attention", f"{len(manual)} manual action(s)")
    if not _ask_yes_no("Apply the safe skills-root changes?", default=True, input_fn=input_fn):
        return StageOutcome("skills", SECTION_LABELS["skills"], "skipped", f"{len(safe)} available")

    applied = apply_skills_symlink_hints(safe, home=home)
    refreshed = skills_symlink_hints(home=home)
    follow_up_safe = [
        item for item in refreshed if item.action in {"create_shared", "symlink"}
    ]
    if follow_up_safe:
        applied.extend(apply_skills_symlink_hints(follow_up_safe, home=home))
    manual = [item for item in refreshed if item.action == "manual"]
    print(c("green", f"  Applied {len(applied)} skills action(s)."))
    status = "attention" if manual else ("changed" if applied else "ready")
    detail = f"{len(applied)} applied"
    if manual:
        detail += f"; {len(manual)} manual"
    return StageOutcome("skills", SECTION_LABELS["skills"], status, detail, tuple(applied))


def _runner_description(config: dict, role: str) -> str:
    harness = get_value(config, f"report.{role}.harness") or "not set"
    model = get_value(config, f"report.{role}.model") or "not set"
    return f"{harness}:{model}"


def _stage_report(
    number: int,
    total: int,
    *,
    input_fn: InputFn,
    home: Path,
    quick: bool,
) -> StageOutcome:
    del home
    _stage_header(number, total, SECTION_LABELS["report"])
    resolved = load_resolved_config()
    complete = report_setup_complete(resolved)
    print(f"  Session summarizer: {c('cyan', _runner_description(resolved, 'session'))}")
    print(f"  Final writer:       {c('cyan', _runner_description(resolved, 'writer'))}")
    print(c("dim", "  Models are explicit; authentication remains owned by each harness."))
    if complete and quick:
        print(c("green", "  OK  Report runners are already configured."))
        return StageOutcome("report", SECTION_LABELS["report"], "ready", "runners configured")

    question = "Reconfigure report runners?" if complete else "Configure report runners now?"
    if not _ask_yes_no(question, default=not complete, input_fn=input_fn):
        status = "ready" if complete else "attention"
        detail = "runners configured" if complete else "report setup incomplete"
        return StageOutcome("report", SECTION_LABELS["report"], status, detail)

    try:
        configured = interactive_report_setup(load_config(), input_fn=input_fn)
        backup = backup_file(CONFIG_FILE)
        save_config(configured)
    except (ConfigurationError, EOFError, KeyboardInterrupt) as exc:
        print(c("red", f"  Report configuration was not changed: {exc}"))
        return StageOutcome("report", SECTION_LABELS["report"], "attention", str(exc))
    print(c("green", f"  Saved report configuration to {CONFIG_FILE}"))
    return StageOutcome(
        "report",
        SECTION_LABELS["report"],
        "changed",
        "runners configured",
        ("report runners",),
        (str(backup),) if backup else (),
    )


def _stage_check(
    number: int,
    total: int,
    *,
    input_fn: InputFn,
    home: Path,
    quick: bool,
) -> StageOutcome:
    del input_fn, quick
    _stage_header(number, total, SECTION_LABELS["check"])
    findings = discover_harnesses(include_registered=True, include_missing=True)
    available = [item for item in findings if item.status == "registered" and item.path]
    missing = [item for item in findings if item.status == "missing"]
    provider_rows = discover_provider_keys(load_provider_registry(), default_keys_dir(home))
    provider_count = sum(row["masked"] != "-" for row in provider_rows)
    config_issues = check_config()
    report_ready = report_setup_complete(load_resolved_config())
    skill_attention = [item for item in skills_symlink_hints(home=home) if item.action == "manual"]

    checks = [
        (not missing and bool(available), f"Harnesses: {len(available)} available, {len(missing)} missing"),
        (provider_count > 0, f"Provider keys: {provider_count}/{len(provider_rows)} discovered"),
        (not config_issues, f"Configuration: {'valid' if not config_issues else f'{len(config_issues)} issue(s)'}"),
        (report_ready, f"Reports: {'configured' if report_ready else 'not configured'}"),
        (not skill_attention, f"Skills: {len(skill_attention)} manual action(s)"),
    ]
    for passed, label in checks:
        print(f"    {c('green', 'OK') if passed else c('yellow', '!!')}  {label}")
    attention = [label for passed, label in checks if not passed]
    if attention:
        print(c("yellow", "  Setup completed with items that may need attention."))
        return StageOutcome("check", SECTION_LABELS["check"], "attention", f"{len(attention)} item(s)")
    print(c("green", "  All setup checks passed."))
    return StageOutcome("check", SECTION_LABELS["check"], "ready", "all checks passed")


_STAGES = {
    "harnesses": _stage_harnesses,
    "providers": _stage_providers,
    "mcp": _stage_mcp,
    "skills": _stage_skills,
    "report": _stage_report,
    "check": _stage_check,
}


def _print_banner(section: str | None, quick: bool) -> None:
    title = "Quiver Setup Wizard" if section is None else f"Quiver Setup - {SECTION_LABELS[section]}"
    print()
    print(c("cyan", c("bold", f"  {title}")))
    print(c("dim", "  Configure coding harnesses and the context they share."))
    if quick:
        print(c("dim", "  Quick mode: configured sections keep their current values."))
    print(c("dim", "  Press Ctrl+C at any prompt to stop; completed stages stay saved."))


def _print_summary(outcomes: list[StageOutcome]) -> None:
    print()
    print(c("cyan", c("bold", "  Setup summary")))
    print(c("dim", f"  {'-' * 56}"))
    markers = {
        "ready": c("green", "OK"),
        "changed": c("green", "++"),
        "skipped": c("dim", "--"),
        "attention": c("yellow", "!!"),
    }
    for outcome in outcomes:
        print(f"    {markers[outcome.status]}  {outcome.label:<24} {outcome.detail}")
    backups = [path for outcome in outcomes for path in outcome.backups]
    if backups:
        print()
        print(c("dim", "  Backups created before changes:"))
        for path in backups:
            print(c("dim", f"    {path}"))
    print()
    print(c("bold", "  Next commands"))
    print(c("cyan", "    swe list             Review harnesses and usage"))
    print(c("cyan", "    swe check            Verify installed harnesses"))
    print(c("cyan", "    swe mcp validate     Validate MCP configuration"))
    print(c("cyan", "    swe setup <section>  Re-run one setup section"))
    print()


def run_setup_wizard(
    *,
    section: str | None = None,
    quick: bool = False,
    input_fn: InputFn = read_line,
    home: Path | None = None,
) -> int:
    """Run the full wizard or one independently runnable setup section."""

    home = home or Path.home()
    if section is not None:
        section = SECTION_ALIASES.get(section)
        if section is None:
            raise ValueError("unknown setup section")
        keys = [section]
    else:
        keys = ["harnesses", "providers", "mcp", "skills", "report", "check"]

    _print_banner(section, quick)
    outcomes: list[StageOutcome] = []
    try:
        for number, key in enumerate(keys, start=1):
            try:
                outcome = _STAGES[key](
                    number,
                    len(keys),
                    input_fn=input_fn,
                    home=home,
                    quick=quick,
                )
            except Exception as exc:
                print(c("red", f"  {SECTION_LABELS[key]} failed: {exc}"))
                outcome = StageOutcome(
                    key,
                    SECTION_LABELS[key],
                    "attention",
                    f"failed: {exc}",
                )
            outcomes.append(outcome)
    except (EOFError, KeyboardInterrupt):
        print()
        print(c("yellow", "  Setup stopped. Completed stages remain saved."))
        if outcomes:
            _print_summary(outcomes)
        return 130

    _print_summary(outcomes)
    return 0
