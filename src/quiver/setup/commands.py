"""First-run setup wizard and domain subcommand routers."""

import json
import sys
from pathlib import Path

from quiver.console import c
from quiver.harness.discover import apply_findings, discover_harnesses
from quiver.harness.discover_commands import cmd_discover
from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers
from quiver.skills.symlinks import apply_skills_symlink_hints, skills_symlink_hints


def cmd_harness(args):
    if not args or args[0] in ("-h", "--help"):
        print(
            f"""
  {c('bold', 'swe harness')} — Harness registry utilities

  {c('cyan', 'swe harness discover [flags]')}   Scan PATH for AI coding CLIs

  Run {c('cyan', 'swe harness discover --help')} for flags.
"""
        )
        return 0
    sub = args[0]
    rest = args[1:]
    if sub == "discover":
        result = cmd_discover(rest)
        return result if isinstance(result, int) else 0
    print(c("red", f"  Unknown harness subcommand: '{sub}'"))
    print(c("dim", "  Try: swe harness discover"))
    return 1


def _setup_help():
    print(
        f"""
  {c('bold', 'swe setup')} — Onboarding wizard for new quiver installs

  {c('cyan', 'swe setup')}              Scan harnesses, MCP servers, and skills roots (dry-run)
  {c('cyan', 'swe setup --apply')}      Apply safe changes (harness registry + mcp.json + symlinks)
  {c('cyan', 'swe setup --json')}       Machine-readable output

{c('bold', 'Steps')}
  1. Discover AI coding CLI harnesses on PATH
  2. Discover MCP servers across tool configs
  3. Recommend skills-root symlinks (~/.agents/skills)
"""
    )


def cmd_setup(args):
    apply = "--apply" in args
    json_out = "--json" in args
    if "-h" in args or "--help" in args:
        _setup_help()
        return 0

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
                    "mcp": apply_mcp_findings(new_mcp) if new_mcp else [],
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
        print(c("green", "  ✓ No new MCP servers outside ~/.config/swe/mcp.json."))
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
        added_m = apply_mcp_findings(new_mcp) if new_mcp else []
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
            answer = input("  Apply safe setup changes? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer in ("y", "yes"):
            added_h = apply_findings(new_harness, min_confidence="high") if new_harness else []
            added_m = apply_mcp_findings(new_mcp) if new_mcp else []
            added_s = apply_skills_symlink_hints(actionable_skills, home=home)
            print(c("green", f"\n  ✓ Harness: {', '.join(added_h) or '—'}"))
            print(c("green", f"  ✓ MCP: {', '.join(added_m) or '—'}"))
            print(c("green", f"  ✓ Skills: {', '.join(added_s) or '—'}"))
            print(c("dim", "  Next: `swe list`  ·  `swe mcp list`\n"))
            return 0

    print(c("dim", "  Dry-run only. Run: swe setup --apply\n"))
    return 0
