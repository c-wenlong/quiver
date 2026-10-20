"""First-run setup wizard and domain subcommand routers."""

import sys
from pathlib import Path

from quiver.console import c
from quiver.json_output import emit
from quiver.setup.models import HarnessPreview, McpPreview, SkillsPreview
from quiver.harness.discover import apply_findings, discover_harnesses
from quiver.harness.discover_commands import cmd_discover
from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers
from quiver.prompt import read_line
from quiver.skills.symlinks import apply_skills_symlink_hints, skills_symlink_hints
from quiver.setup.wizard import SECTION_ALIASES, run_setup_wizard
from quiver.init.commands import (
    adopt_instructions,
    agents_file_customised,
    check_adoptable,
    run_init,
)
from quiver.init.layout import agents_file, registry_name


def cmd_harness(args):
    """Registry-facing verbs for one harness, grouped under a single command.

    star and archive were top-level, which put two more names in a first
    layer that was already long. They act on a harness's standing in the
    registry, so they live with the rest of that.
    """
    if not args or args[0] in ("-h", "--help", "help"):
        print(
            f"""
  {c('bold', 'swe harness')} — everything about the harnesses you have  {c('dim', '(alias: swe hs)')}

  {c('cyan', 'swe harness list')}               List them  {c('dim', '(swe list is the shortcut)')}
  {c('cyan', 'swe harness edit')}               Review every harness at once
  {c('cyan', 'swe harness star <name>')}        Toggle a favourite (pins it to the top)
  {c('cyan', 'swe harness archive <name> [why]')}  Shelve one you have ruled out
  {c('cyan', 'swe harness archive')}            List what you archived, and why
  {c('cyan', 'swe harness discover [flags]')}   Scan PATH and home dirs for AI tools
  {c('cyan', 'swe harness usage <devin|cursor> --account-dir=<dir>')}  An extra account's rate limit
  {c('cyan', 'swe harness usage-events cursor [--since=<s>]')}  Cursor's per-event usage feed
  {c('cyan', 'swe harness usage-daily codex [--account-dir=<d>]')}  A Codex account's per-day usage

  {c('dim', 'Archiving hides it from swe list and records why — nothing is uninstalled;')}
  {c('dim', 'swe list --scope=all shows it again.')}
"""
        )
        return 0
    sub = args[0]
    rest = args[1:]
    if sub in ("list", "ls"):
        from quiver.harness.commands import cmd_list

        result = cmd_list(rest)
        return result if isinstance(result, int) else 0
    if sub == "discover":
        result = cmd_discover(rest)
        return result if isinstance(result, int) else 0
    if sub in ("star", "favourite", "favorite"):
        from quiver.harness.commands import cmd_star

        result = cmd_star(rest)
        return result if isinstance(result, int) else 0
    if sub in ("edit", "review"):
        from quiver.harness.commands import cmd_harness_edit

        result = cmd_harness_edit(rest)
        return result if isinstance(result, int) else 0
    if sub in ("archive", "shelve"):
        from quiver.harness.commands import cmd_archive

        result = cmd_archive(rest)
        return result if isinstance(result, int) else 0
    if sub == "usage":
        from quiver.harness.commands import cmd_harness_usage

        result = cmd_harness_usage(rest)
        return result if isinstance(result, int) else 0
    if sub == "usage-events":
        from quiver.harness.commands import cmd_harness_usage_events

        result = cmd_harness_usage_events(rest)
        return result if isinstance(result, int) else 0
    if sub == "usage-daily":
        from quiver.harness.commands import cmd_harness_usage_daily

        result = cmd_harness_usage_daily(rest)
        return result if isinstance(result, int) else 0
    print(c("red", f"  Unknown harness subcommand: '{sub}'"))
    print(c("dim", "  Try: swe harness list | edit | star | archive | discover | usage | usage-events | usage-daily"))
    return 1


def _setup_help():
    print(
        f"""
  {c('bold', 'swe setup')} — Onboarding wizard for new quiver installs

  {c('cyan', 'swe setup')}              Run the complete interactive setup wizard
  {c('cyan', 'swe setup --quick')}      Configure only missing or actionable stages
  {c('cyan', 'swe setup <section>')}    Run one section: harnesses, providers, mcp,
                                skills, report, or check
  {c('cyan', 'swe setup --apply')}      Non-interactively apply safe discovery changes,
                                then link AGENTS.md and skills into each harness
  {c('cyan', 'swe setup --apply --adopt=<file>')}  Start ~/.quiver/AGENTS.md from one of
                                your instruction files (only while it is the seed)
  {c('cyan', 'swe setup --apply --starred=a,b')}  Star exactly these harnesses (registers
                                catalog ones that aren't registered yet)
  {c('cyan', 'swe setup --json')}       Print the discovery preview as JSON
  {c('cyan', 'swe setup --non-interactive')}  Preview without prompts or writes

{c('bold', 'Steps')}
  1. Discover and register AI coding CLI harnesses
  2. Check LLM provider credential coverage without storing secrets
  3. Import discovered MCP servers into the source-of-truth
  4. Unify safe skills roots under ~/.quiver/skills
  5. Configure session summarizer and report writer models
  6. Verify the resulting setup and show next commands

  Existing values are shown as defaults. Files are backed up before changes.
  Ctrl+C stops the wizard without undoing stages that already completed.
"""
    )


def cmd_setup(args):
    adopt_args = [arg for arg in args if arg.startswith("--adopt=")]
    starred_args = [arg for arg in args if arg.startswith("--starred=")]
    args = [arg for arg in args
            if not arg.startswith(("--adopt=", "--starred="))]
    adopt = Path(adopt_args[-1].split("=", 1)[1]).expanduser() if adopt_args else None
    starred = None
    if starred_args:
        value = starred_args[-1].split("=", 1)[1]
        starred = [name.strip() for name in value.split(",") if name.strip()]
    apply = "--apply" in args
    json_out = "--json" in args
    quick = "--quick" in args
    non_interactive = "--non-interactive" in args
    if "-h" in args or "--help" in args:
        _setup_help()
        return 0

    known_flags = {"--apply", "--json", "--quick", "--non-interactive"}
    unknown_flags = [arg for arg in args if arg.startswith("-") and arg not in known_flags]
    positional = [arg for arg in args if not arg.startswith("-")]
    if unknown_flags:
        print(c("red", f"  Unknown setup option: {unknown_flags[0]}"))
        print(c("dim", "  Run: swe setup --help"))
        return 1
    if len(positional) > 1 or (positional and positional[0] not in SECTION_ALIASES):
        section = positional[0] if positional else ""
        print(c("red", f"  Unknown setup section: {section or 'too many arguments'}"))
        print(c("dim", "  Sections: harnesses, providers, mcp, skills, report, check"))
        return 1
    section = positional[0] if positional else None
    if section and (apply or json_out or non_interactive):
        print(c("red", "  Section setup is interactive; do not combine it with --apply, --json, or --non-interactive."))
        return 1
    if quick and (apply or json_out or non_interactive):
        print(c("red", "  --quick is for the interactive wizard and cannot be combined with non-interactive modes."))
        return 1

    refusal_stream = sys.stderr if json_out else sys.stdout
    if adopt is not None and not apply:
        print(c("red", "  --adopt only works with --apply."), file=refusal_stream)
        return 1
    if starred is not None and not apply:
        print(c("red", "  --starred only works with --apply."), file=refusal_stream)
        return 1
    if adopt is not None:
        reason = check_adoptable(Path.home(), adopt)
        if reason:
            print(c("red", f"  {reason}"), file=refusal_stream)
            return 1

    interactive = sys.stdin.isatty() and not non_interactive
    if not apply and not json_out and interactive:
        return run_setup_wizard(section=section, quick=quick)
    if section:
        print(c("red", "  This setup section requires an interactive terminal."))
        return 1
    if quick and not interactive:
        print(c("red", "  --quick requires an interactive terminal."))
        return 1

    home = Path.home()
    try:
        harness_findings = discover_harnesses()
        new_harness = [f for f in harness_findings if f.status == "new" and f.confidence == "high"]
        mcp_findings = discover_mcp_servers()
        new_mcp = [f for f in mcp_findings if f.status == "new"]
        skill_hints = skills_symlink_hints(home=home)
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception as exc:
        if not json_out:
            raise
        # The app's decoder contract holds on failed runs too: emit a
        # well-formed envelope with the error as data rather than a bare
        # traceback on stdout.
        print(c("red", f"  setup failed: {exc}"), file=sys.stderr)
        data = {"harness": [], "mcp": [], "skills": []}
        if apply:
            data["applied"] = {
                "harness": [],
                "mcp": [],
                "skills": [],
                "errors": [str(exc)],
                "links": {"adopted": None},
            }
        else:
            data["instructions"] = {
                "agents_file": str(agents_file(home)),
                "agents_file_exists": False,
                "agents_file_customised": False,
                "rows": [],
                "adopt_candidates": [],
                "error": str(exc),
            }
        emit(data)
        return 1
    actionable_skills = [h for h in skill_hints if h.action in ("create_shared", "symlink")]

    if json_out:
        # One document per --json invocation: --apply results ride inside
        # the same envelope instead of printing a second document.
        data = {
            "harness": [HarnessPreview.of(f).to_json() for f in new_harness],
            "mcp": [McpPreview.of(f).to_json() for f in new_mcp],
            "skills": [
                SkillsPreview.of(h).to_json()
                for h in skill_hints
                if h.action != "ok"
            ],
        }
        if apply:
            data["applied"] = _apply_all(new_harness, new_mcp, actionable_skills, home, adopt, starred)
        else:
            data["instructions"] = _instructions_preview(home)
        emit(data)
        # An apply step or a linking pass that stopped part way is not a
        # finished setup, even though what came before it was applied and
        # is reported above.
        applied = data.get("applied", {})
        failed = applied.get("errors") or applied.get("links", {}).get("error")
        return 1 if failed else 0

    print(f"\n{c('bold', 'quiver setup')}\n")

    # Step 1 — harnesses
    print(c("dim", "  Step 1/3 — AI coding CLI harnesses\n"))
    if new_harness:
        for f in new_harness:
            path = f.path.replace(str(home), "~")
            print(f"  {c('green', '•')} {c('bold', f.name)} ({f.command})  {c('dim', path)}")
    else:
        print(c("green", "  ✓ No new high-confidence harnesses to register."))
    print()

    # Step 2 — MCP
    print(c("dim", "  Step 2/3 — MCP servers\n"))
    if new_mcp:
        for f in new_mcp:
            tools = ", ".join(f.tools)
            print(f"  {c('green', '•')} {c('bold', f.name)}  {c('dim', f'({tools})')}")
    else:
        print(c("green", "  ✓ No new MCP servers outside ~/.quiver/mcp.json."))
    print()

    # Step 3 — skills
    print(c("dim", "  Step 3/3 — Skills roots\n"))
    shown_skills = False
    for hint in skill_hints:
        if hint.action == "ok":
            continue
        shown_skills = True
        if hint.action == "manual":
            print(f"  {c('yellow', '!')} {hint.label}: {hint.reason}")
            print(f"    {c('dim', hint.command)}")
        else:
            print(f"  {c('green', '•')} {hint.label}: {hint.reason}")
            print(f"    {c('dim', hint.command)}")
    if not shown_skills:
        print(c("green", "  ✓ Skills roots look good (shared tree linked)."))
    print()

    if apply:
        # Linking runs even when discovery found nothing new: a fresh
        # ~/.quiver still needs its AGENTS.md seeded and linked.
        applied = _apply_all(new_harness, new_mcp, actionable_skills, home, adopt, starred)
        parts = []
        if applied["harness"]:
            parts.append(f"{len(applied['harness'])} harness(es)")
        if applied["mcp"]:
            parts.append(f"{len(applied['mcp'])} MCP server(s)")
        if applied["skills"]:
            parts.append(f"{len(applied['skills'])} skills action(s)")
        links = applied["links"]
        linked = [r for r in links.get("rows", []) if r["result"] == "linked"]
        if linked:
            parts.append(f"{len(linked)} link(s)")
        print(c("green", f"  ✓ Applied: {', '.join(parts) or 'nothing'}"))
        apply_errors = applied.get("errors", [])
        for err in apply_errors:
            print(c("red", f"  ! {err}"))
        if links.get("adopted"):
            print(c("green", f"  ✓ AGENTS.md started from {links['adopted']}"))
        for row in links.get("rows", []):
            if row["result"] in ("blocked", "protected"):
                print(c("yellow", f"  ! left alone: {row['path']}  ") + c("dim", row["detail"]))
        if links.get("error"):
            print(c("red", f"  {links['error']}"))
            print(c("dim", "  Nothing was linked. Fix that, then run swe setup --apply again.\n"))
            return 1
        if apply_errors:
            print(c("dim", "  Fix the errors above, then run swe setup --apply again.\n"))
            return 1
        print(c("dim", "  Next: `swe list`  ·  `swe check`  ·  `swe mcp list`  ·  `swe skills`\n"))
        return 0

    has_work = bool(new_harness or new_mcp or actionable_skills)
    if not has_work:
        print(c("dim", "  Nothing to apply. Try `swe list`, `swe mcp list`, `swe skills scope list`.\n"))
        return 0

    if sys.stdin.isatty():
        try:
            answer = read_line("  Apply safe setup changes? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer in ("y", "yes"):
            try:
                added_h = apply_findings(new_harness, min_confidence="high")[0] if new_harness else []
                added_m = apply_mcp_findings(new_mcp).added if new_mcp else []
                added_s = apply_skills_symlink_hints(actionable_skills, home=home)
            except (EOFError, KeyboardInterrupt):
                raise
            except Exception as exc:
                print(c("red", f"\n  Apply stopped part way: {exc}"))
                print(c("dim", "  What already landed stays applied. Fix that, "
                              "then run swe setup --apply.\n"))
                return 1
            print(c("green", f"\n  ✓ Harness: {', '.join(added_h) or '—'}"))
            print(c("green", f"  ✓ MCP: {', '.join(added_m) or '—'}"))
            print(c("green", f"  ✓ Skills: {', '.join(added_s) or '—'}"))
            print(c("dim", "  Next: `swe list`  ·  `swe mcp list`\n"))
            return 0

    print(c("dim", "  Dry-run only. Run: swe setup --apply\n"))
    return 0


def _apply_all(new_harness, new_mcp, actionable_skills, home: Path,
               adopt: Path | None, starred: list[str] | None = None) -> dict:
    """Register, import and link everything setup found, without asking.

    Harnesses, MCP servers and skills roots first, so the stars and the
    linking pass after them see the freshly registered harnesses. Linking
    is `swe init` with nobody to ask: real files in the way stay blocked
    (no --force) and new skills folders are registered only when their
    owner has a command or catalog entry.
    """
    applied: dict = {"harness": [], "mcp": [], "skills": []}
    errors: list[str] = []
    # Every step reports into ``applied`` or ``errors`` rather than raising:
    # a failed write after earlier steps landed must still reach the caller
    # as data. Under --json the SetupApplied envelope is owed even (read:
    # especially) on the runs that went wrong, or the app's decoder sees a
    # bare traceback instead of a reply.
    steps = (
        ("harness", lambda: apply_findings(new_harness, min_confidence="high")[0]
         if new_harness else []),
        ("mcp", lambda: apply_mcp_findings(new_mcp).added if new_mcp else []),
        ("skills", lambda: apply_skills_symlink_hints(actionable_skills, home=home)),
    )
    for key, step in steps:
        try:
            applied[key] = step()
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as exc:
            errors.append(f"{key}: {exc}")
    if starred is not None:
        try:
            applied["starred"] = _set_starred(starred)
        except (EOFError, KeyboardInterrupt):
            raise
        except Exception as exc:
            errors.append(f"starred: {exc}")
    links: dict = {"adopted": None}
    try:
        if adopt is not None:
            adopt_instructions(home, adopt)
            links["adopted"] = str(adopt)
        run = run_init(home, evidence_only=True, quiet=True)
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception as exc:
        links["error"] = str(exc)
    else:
        links["scaffold"] = run.scaffold
        links["rows"] = run.rows_json()
    if errors:
        applied["errors"] = errors
    applied["links"] = links
    return applied


def _instructions_preview(home: Path) -> dict:
    """What linking the shared AGENTS.md would do, for the app's setup sheet.

    ``adopt_candidates`` are the real instruction files linking would leave
    blocked that --adopt would take: any one of them can start the shared
    file while it is still the seed. One too large or not text is left out
    rather than offered and then refused.
    """
    preview = {
        "agents_file": str(agents_file(home)),
        "agents_file_exists": agents_file(home).exists(),
        "agents_file_customised": agents_file_customised(home),
    }
    try:
        run = run_init(home, check_only=True, quiet=True)
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception as exc:
        # Any failure (unreadable linkignore, filesystem trouble) becomes a
        # preview error field, never a traceback: the caller still owes the
        # app its JSON envelope.
        preview.update(rows=[], adopt_candidates=[], error=str(exc))
        return preview
    preview["rows"] = run.rows_json()
    preview["adopt_candidates"] = [
        {"harness": registry_name(status.label), "path": str(status.path)}
        for status in run.instructions
        if status.state == "conflict" and status.path.is_file()
        and check_adoptable(home, status.path) is None
    ]
    return preview


def _set_starred(names: list[str]) -> dict:
    """Make the starred harnesses exactly ``names``, in that order.

    The app's setup sheet asks which harnesses you use and sends the whole
    ticked set, so anything starred before and now unticked is unstarred.
    A name the registry lacks but the catalog knows (one you picked before
    installing it) is registered from its catalog entry first; any other
    name is reported back as unknown rather than invented.
    """
    from datetime import datetime

    from quiver.harness.catalog import HARNESS_CATALOG
    from quiver.harness.registry import load_registry, resolve, save_registry
    from quiver.harness.stars import save_stars

    registry = load_registry()
    wanted: list[str] = []
    registered: list[str] = []
    unknown: list[str] = []
    for raw in names:
        name = resolve(registry, raw)
        if name is None and raw in HARNESS_CATALOG:
            entry = HARNESS_CATALOG[raw]
            registry[raw] = {
                "command": entry.get("command", raw),
                "description": entry.get("description", ""),
                "version": None,
                "tags": list(entry.get("tags") or ["agentic", "coding"]),
                "aliases": list(entry.get("aliases") or []),
                "added": datetime.now().isoformat(),
                "discovered_via": "setup",
            }
            if entry.get("primary_color"):
                registry[raw]["primary_color"] = entry["primary_color"]
            save_registry(registry)
            registered.append(raw)
            name = raw
        if name is None:
            unknown.append(raw)
        elif name not in wanted:
            wanted.append(name)
    save_stars(wanted)
    return {"starred": wanted, "registered": registered, "unknown": unknown}
