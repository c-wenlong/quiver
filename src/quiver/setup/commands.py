"""First-run setup wizard and domain subcommand routers."""

import json
import sys
from pathlib import Path

from quiver.console import c
from quiver.harness.discover import apply_findings, discover_harnesses
from quiver.harness.discover_commands import cmd_discover
from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers
from quiver.prompt import read_line
from quiver.skills.symlinks import apply_skills_symlink_hints, skills_symlink_hints
from quiver.setup.wizard import SECTION_ALIASES, run_setup_wizard


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
  {c('cyan', 'swe harness discover [flags]')}   Scan PATH for AI coding CLIs

  {c('dim', 'Archived harnesses drop out of swe list; --scope=all brings them back.')}
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
    print(c("red", f"  Unknown harness subcommand: '{sub}'"))
    print(c("dim", "  Try: swe harness list | edit | star | archive | discover"))
    return 1


def _setup_help():
    print(
        f"""
  {c('bold', 'swe setup')} — Onboarding wizard for new quiver installs

  {c('cyan', 'swe setup')}              Run the complete interactive setup wizard
  {c('cyan', 'swe setup --quick')}      Configure only missing or actionable stages
  {c('cyan', 'swe setup <section>')}    Run one section: harnesses, providers, mcp,
                                skills, report, or check
  {c('cyan', 'swe setup --apply')}      Non-interactively apply safe discovery changes
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
    harness_findings = discover_harnesses()
    new_harness = [f for f in harness_findings if f.status == "new" and f.confidence == "high"]
    mcp_findings = discover_mcp_servers()
    new_mcp = [f for f in mcp_findings if f.status == "new"]
    skill_hints = skills_symlink_hints(home=home)
    actionable_skills = [h for h in skill_hints if h.action in ("create_shared", "symlink")]

    if json_out:
        print(
            json.dumps(
                {
                    "harness": [
                        {"name": f.name, "command": f.command, "path": f.path}
                        for f in new_harness
                    ],
                    "mcp": [{"name": f.name, "tools": list(f.tools)} for f in new_mcp],
                    "skills": [
                        {
                            "label": h.label,
                            "action": h.action,
                            "command": h.command,
                            "reason": h.reason,
                        }
                        for h in skill_hints
                        if h.action != "ok"
                    ],
                },
                indent=2,
            )
        )
        if apply:
            result = {
                "applied": {
                    "harness": apply_findings(new_harness, min_confidence="high") if new_harness else [],
                    "mcp": apply_mcp_findings(new_mcp).added if new_mcp else [],
                    "skills": apply_skills_symlink_hints(actionable_skills, home=home),
                }
            }
            print(json.dumps(result, indent=2))
        return 0

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

    has_work = bool(new_harness or new_mcp or actionable_skills)
    if not has_work:
        print(c("dim", "  Nothing to apply. Try `swe list`, `swe mcp list`, `swe skills scope list`.\n"))
        return 0

    if apply:
        added_h = apply_findings(new_harness, min_confidence="high") if new_harness else []
        added_m = apply_mcp_findings(new_mcp).added if new_mcp else []
        added_s = apply_skills_symlink_hints(actionable_skills, home=home)
        parts = []
        if added_h:
            parts.append(f"{len(added_h)} harness(es)")
        if added_m:
            parts.append(f"{len(added_m)} MCP server(s)")
        if added_s:
            parts.append(f"{len(added_s)} skills action(s)")
        print(c("green", f"  ✓ Applied: {', '.join(parts) or 'nothing'}"))
        print(c("dim", "  Next: `swe list`  ·  `swe check`  ·  `swe mcp list`  ·  `swe skills`\n"))
        return 0

    if sys.stdin.isatty():
        try:
            answer = read_line("  Apply safe setup changes? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer in ("y", "yes"):
            added_h = apply_findings(new_harness, min_confidence="high") if new_harness else []
            added_m = apply_mcp_findings(new_mcp).added if new_mcp else []
            added_s = apply_skills_symlink_hints(actionable_skills, home=home)
            print(c("green", f"\n  ✓ Harness: {', '.join(added_h) or '—'}"))
            print(c("green", f"  ✓ MCP: {', '.join(added_m) or '—'}"))
            print(c("green", f"  ✓ Skills: {', '.join(added_s) or '—'}"))
            print(c("dim", "  Next: `swe list`  ·  `swe mcp list`\n"))
            return 0

    print(c("dim", "  Dry-run only. Run: swe setup --apply\n"))
    return 0
