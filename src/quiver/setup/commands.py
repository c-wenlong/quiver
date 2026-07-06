"""First-run setup wizard and domain subcommand routers."""

import json
import sys
from pathlib import Path

from quiver.console import c
from quiver.harness.discover import apply_findings, discover_harnesses
from quiver.harness.discover_commands import cmd_discover


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


def cmd_setup(args):
    apply = "--apply" in args
    json_out = "--json" in args
    if "-h" in args or "--help" in args:
        print(
            f"""
  {c('bold', 'swe setup')} — Onboarding wizard for new quiver installs

  Scans your machine for AI coding CLI harnesses and offers to register them.

  {c('cyan', 'swe setup')}              Scan and show recommendations (dry-run)
  {c('cyan', 'swe setup --apply')}      Add high-confidence harnesses to tools.json

{c('bold', 'Coming soon')}
  MCP server import and skills-root symlink hints in this wizard.
"""
        )
        return 0

    findings = discover_harnesses()
    new_findings = [f for f in findings if f.status == "new" and f.confidence == "high"]

    if json_out:
        print(
            json.dumps(
                {
                    "harness": [
                        {
                            "name": f.name,
                            "command": f.command,
                            "path": f.path,
                            "confidence": f.confidence,
                        }
                        for f in new_findings
                    ]
                },
                indent=2,
            )
        )
        if apply and new_findings:
            added = apply_findings(new_findings, min_confidence="high")
            print(json.dumps({"added": added}, indent=2))
        return 0

    print(f"\n{c('bold', 'quiver setup')}\n")
    print(c("dim", "  Step 1/1 — Discover AI coding CLI harnesses on PATH\n"))

    if not new_findings:
        print(c("green", "  ✓ No new high-confidence harnesses to register."))
        print(c("dim", "  Run `swe list` to see your registry, or `swe harness discover --all` for details.\n"))
        return 0

    for f in new_findings:
        path = f.path.replace(str(Path.home()), "~")
        print(f"  {c('green', '•')} {c('bold', f.name)} ({f.command})  {c('dim', path)}")

    print()
    if apply:
        added = apply_findings(new_findings, min_confidence="high")
        print(c("green", f"  ✓ Registered {len(added)} tool(s): {', '.join(added)}"))
        print(c("dim", "  Next: `swe list`  ·  `swe check`  ·  `swe mcp list`\n"))
        return 0

    if sys.stdin.isatty():
        try:
            answer = input("  Add these tools to your registry? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer in ("y", "yes"):
            added = apply_findings(new_findings, min_confidence="high")
            print(c("green", f"\n  ✓ Registered {len(added)} tool(s): {', '.join(added)}"))
            print(c("dim", "  Next: `swe list`  ·  `swe check`\n"))
            return 0

    print(c("dim", "  Dry-run only. Run: swe setup --apply\n"))
    return 0
