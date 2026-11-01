"""Run the fixes ``swe doctor`` names: ``swe doctor --fix`` and the Run
buttons on the web and Mac Doctor pages.

A finding names its fix as an ``action`` (the finding's own, or an
``action`` remedy carrying the targets its runner edits). This module
owns one runner per action, so the CLI, the web page and the Mac app
(which calls ``swe doctor --fix``) all run the same code.

Two kinds of action:

- **Batch** actions are local repairs that only add or relink, never
  delete anything that could still be wanted: ``setup``, ``discover``,
  ``init``, ``plugin-fix`` (register a marketplace folder, install a
  plugin from it). ``swe doctor --fix`` and Fix all run every one of them, in
  ``ORDER``, re-reading the report between steps because an earlier fix
  changes what the later checks find (no hub hides every link check).
- **Click** actions need a person to pick them for one finding:
  ``install`` (network and package managers), and the drops
  (``undeclare``, ``marketplace-drop``, ``dangling-link``), which delete
  entries or links a synced file may still want on another machine, and
  ``plugin-reinstall``, which removes Codex's copy before adding it back. They run only when
  one finding is named (``--fix=<check> --message=<text>``, or a row's
  Run), and the batch lists them as skipped.

Every runner returns what went wrong, or None, and never prints. A fix
that raises is reported and the rest still run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

# The order a batch runs in. setup makes the hub everything else reads;
# discover fills the registry the link checks walk; init links what the
# registry names. The click actions close the list for a named finding.
ORDER = ("setup", "discover", "init", "plugin-fix", "plugin-reinstall",
         "dangling-link", "marketplace-drop", "undeclare", "install")

BATCH = frozenset({"setup", "discover", "init", "plugin-fix"})
CLICK = frozenset({"plugin-reinstall", "dangling-link", "marketplace-drop",
                   "undeclare", "install"})

# How many times a batch walks ORDER: enough for setup to reveal the
# link checks and a registered marketplace its plugins, and bounded.
MAX_PASSES = 3

# One run fixes every finding naming these, so a batch runs each once.
GLOBAL = frozenset({"setup", "discover", "init"})


class FixRefused(Exception):
    """A fix that cannot be run as asked: the finding or its targets are
    gone, or two findings match. Nothing ran."""


# ------------------------------------------------------------------ runners
# Each takes (home, finding, remedy) and returns what went wrong, or None.

def _run_setup(home: Path, finding: dict, remedy: dict | None) -> str | None:
    from quiver.setup.commands import apply_setup

    applied = apply_setup(home)
    return (applied.get("links", {}).get("error")
            or "; ".join(applied.get("errors") or []) or None)


def _run_discover(home: Path, finding: dict, remedy: dict | None) -> str | None:
    from quiver.harness.discover import apply_findings, discover_harnesses

    apply_findings(discover_harnesses(), min_confidence="high")
    return None


def _run_init(home: Path, finding: dict, remedy: dict | None) -> str | None:
    """``swe init`` with nobody to ask: a real file in the way stays, and
    is not an error (``swe init --force`` is its own button)."""
    from quiver.init.commands import run_init

    run_init(home, quiet=True, ask=False)
    return None


def _run_install(home: Path, finding: dict, remedy: dict | None) -> str | None:
    from quiver.harness.commands import install_harness

    name = finding.get("harness")
    via = (remedy or finding).get("via")
    if not name or not via:
        raise FixRefused("this install names no harness or method")
    return install_problem(install_harness(name, via), name)


def _run_undeclare(home: Path, finding: dict, remedy: dict | None) -> str | None:
    from quiver.harness.registry import load_registry
    from quiver.hooks.settings import drop_script_declarations

    settings = (remedy or {}).get("settings")
    targets = (remedy or {}).get("targets")
    if (not isinstance(settings, list) or not settings
            or not isinstance(targets, list) or not targets):
        raise FixRefused("that fix names no settings file or script")
    registry = load_registry()
    errors = []
    for raw in settings:
        try:
            drop_script_declarations(Path(raw), targets, registry,
                                     finding.get("harness"), home)
        except Exception as exc:  # noqa: BLE001 - per-file errors all show
            errors.append(str(exc) or type(exc).__name__)
    return "; ".join(errors) or None


def _run_marketplace_drop(home: Path, finding: dict, remedy: dict | None) -> str | None:
    from quiver.harness.doctor import drop_marketplace_entries

    in_file = (remedy or {}).get("settings_entries") or []
    cli = (remedy or {}).get("cli_entries") or []
    if not in_file and not cli:
        raise FixRefused("that fix names no marketplace entries")
    return drop_marketplace_entries(home, in_file, cli)


def _run_plugin_fix(home: Path, finding: dict, remedy: dict | None) -> str | None:
    """``swe find plugins --fix=<id>``: drift is computed again and only
    the commands it builds run, never text from the report."""
    from quiver.find.plugin_drift import FixError, apply_fix

    drift_id = (remedy or {}).get("id")
    if not isinstance(drift_id, str) or not drift_id:
        raise FixRefused("that fix names no plugin drift id")
    try:
        apply_fix(home, drift_id)
    except FixError as exc:
        return str(exc)
    return None


def _run_dangling_link(home: Path, finding: dict, remedy: dict | None) -> str | None:
    """Remove one broken top-level symlink in ~/.quiver or its skills
    folder, after copying the link itself to ~/.quiver/backups.

    Refuses anything that is not still a broken link sitting directly in
    one of those two folders, so a report gone stale never removes a
    real file or a link that works again."""
    from quiver.paths import backup_tree, quiver_dir_for, skills_dir_for

    raw = (remedy or {}).get("path")
    if not isinstance(raw, str) or not raw:
        raise FixRefused("that fix names no link")
    path = Path(raw)
    allowed = set()
    for folder in (quiver_dir_for(home), skills_dir_for(home)):
        try:
            allowed.add(folder.resolve())
        except OSError:
            continue
    try:
        parent = path.parent.resolve()
    except OSError:
        parent = None
    if parent not in allowed:
        raise FixRefused(f"{raw} is not directly in ~/.quiver or its skills folder")
    if not path.is_symlink():
        raise FixRefused(f"{raw} is not a symlink any more")
    if path.exists():
        raise FixRefused(f"{raw} points at something again; left alone")
    backup_tree(path, home)
    path.unlink()
    return None


def install_problem(result, name: str) -> str | None:
    """What a captured install says went wrong, with the end of its output,
    or None once the command is on PATH."""
    if result.action is not None:
        return None if result.path else f"Installed, but {name}'s command is still not on PATH."
    tail = "\n".join(line for line in (result.output or "").strip().splitlines()[-8:])
    return result.error + ("\n" + tail if tail else "")


Runner = Callable[[Path, dict, "dict | None"], "str | None"]

RUNNERS: dict[str, Runner] = {
    "setup": _run_setup,
    "discover": _run_discover,
    "init": _run_init,
    "marketplace-drop": _run_marketplace_drop,
    "undeclare": _run_undeclare,
    "install": _run_install,
    "plugin-fix": _run_plugin_fix,
    "plugin-reinstall": _run_plugin_fix,
    "dangling-link": _run_dangling_link,
}


# ------------------------------------------------------------------ choosing

def resolve(finding: dict, action: str | None = None) -> tuple[str, dict | None]:
    """The action a Run for ``finding`` means, and the remedy carrying its
    targets. ``action`` names a remedy; None means the finding's own.
    Refuses an action the finding no longer offers, since the report
    changed under the click."""
    remedy = _remedy_named(finding, action) if action is not None else None
    if action is not None and remedy is None:
        raise FixRefused("That fix isn't in the report any more. Refresh to see the current one.")
    name = remedy["action"] if remedy else finding.get("action")
    if name not in RUNNERS:
        raise FixRefused("swe can't run this fix itself.")
    # The finding's own action: its remedy, when it has one, carries the
    # targets (an off-PATH install's ``via`` sits on both).
    return name, remedy or _remedy_named(finding, name)


def _remedy_named(finding: dict, name: str) -> dict | None:
    return next((r for r in finding.get("remedies") or []
                 if r.get("kind") == "action" and r.get("action") == name), None)


def actions_of(finding: dict) -> list[str]:
    """Every action a finding offers: its own, then its action remedies."""
    names = [finding.get("action")] + [
        r.get("action") for r in finding.get("remedies") or []
        if r.get("kind") == "action"]
    seen: list[str] = []
    for name in names:
        if name in RUNNERS and name not in seen:
            seen.append(name)
    return seen


def batch_action(finding: dict) -> str | None:
    """The action a batch runs for ``finding``: the first batch-safe one."""
    return next((a for a in actions_of(finding) if a in BATCH), None)


def in_scope(finding: dict, check: str | None) -> bool:
    """``--fix=<check>``: that check, or every check under it
    (``--fix=setup`` covers ``setup.unlinked``)."""
    if not check:
        return True
    name = finding.get("check") or ""
    return name == check or name.startswith(check + ".")


def _key(finding: dict) -> tuple:
    return finding.get("check"), finding.get("message")


def _brief(finding: dict) -> dict:
    out = {"check": finding.get("check"), "message": finding.get("message")}
    if finding.get("harness"):
        out["harness"] = finding["harness"]
    return out


# ------------------------------------------------------------------ running

def _call(action: str, run: Callable[[], "str | None"]) -> str | None:
    return run()


def run_fixes(
    home: Path,
    report: Callable[[], list],
    *,
    check: str | None = None,
    message: str | None = None,
    action: str | None = None,
    dry_run: bool = False,
    guard: Callable[[str, Callable[[], "str | None"]], "str | None"] = _call,
) -> dict:
    """Run doctor's fixes and say what changed.

    ``report`` returns the current findings (the ``--json`` dicts); it is
    read before, between steps that ran something, and after. With
    ``message`` one finding is named (it must match ``check`` and
    ``message`` exactly) and its fix runs, a click action included.
    Without it every batch fix in scope runs, in ``ORDER``; a finding
    whose only fixes are click actions is listed in ``skipped``.
    ``dry_run`` reads the report once and runs nothing: what a batch
    would run first (a later step may find more once an earlier one ran).

    Returns ``{dry_run, ran, fixed, still_failing, skipped}``: each step
    as ``{action, fix, findings, error}``, the findings gone after the
    run, the findings in scope still there, and the click-only ones.
    """
    before = report()
    ran: list[dict] = []
    skipped: list[dict] = []

    def step(name: str, findings: list, remedy: dict | None) -> None:
        entry = {"action": name, "fix": findings[0].get("fix"),
                 "findings": [_brief(f) for f in findings], "error": None}
        if not dry_run:
            try:
                entry["error"] = guard(name, lambda: RUNNERS[name](home, findings[0], remedy))
            except FixRefused as exc:
                entry["error"] = str(exc)
            except Exception as exc:  # noqa: BLE001 - one failure never stops the rest
                entry["error"] = str(exc) or type(exc).__name__
        ran.append(entry)

    if message is not None:
        matches = [f for f in before
                   if f.get("check") == check and f.get("message") == message]
        if not matches:
            raise FixRefused("That finding isn't in the report any more. Refresh to see the current one.")
        if len(matches) > 1:
            raise FixRefused(f"{len(matches)} findings match; fix them one at a time from the report.")
        name, remedy = resolve(matches[0], action)
        step(name, matches, remedy)
    else:
        # A fix can reveal more (a registered marketplace shows its
        # uninstalled plugins), so passes repeat while something new ran.
        # Nothing runs twice: a fix that did not clear its finding is
        # reported once, not retried.
        current, tried = before, set()
        for _ in range(MAX_PASSES):
            progressed = False
            for name in ORDER:
                if name not in BATCH:
                    continue
                todo = [f for f in current
                        if in_scope(f, check) and batch_action(f) == name
                        and (name if name in GLOBAL else (name, *_key(f))) not in tried]
                if not todo:
                    continue
                groups = [todo] if name in GLOBAL else [[f] for f in todo]
                for group in groups:
                    tried.add(name if name in GLOBAL else (name, *_key(group[0])))
                    step(name, group, _remedy_named(group[0], name))
                progressed = True
                if not dry_run:
                    current = report()
            if dry_run or not progressed:
                break

    after = before if dry_run else report()
    if message is None:
        skipped = [dict(_brief(f), actions=actions_of(f))
                   for f in after
                   if in_scope(f, check) and actions_of(f) and batch_action(f) is None]
    gone = {_key(f) for f in before} - {_key(f) for f in after}
    return {
        "dry_run": dry_run,
        "ran": ran,
        "fixed": [_brief(f) for f in before if _key(f) in gone],
        "still_failing": [f for f in after if in_scope(f, check)],
        "skipped": skipped,
    }


def fixable_left(result: dict) -> list[dict]:
    """The findings still in scope that a batch could run a fix for."""
    return [f for f in result["still_failing"] if batch_action(f) is not None]
