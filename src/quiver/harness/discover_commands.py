"""Harness discover CLI command."""

import json
import sys
from pathlib import Path

from quiver.console import c, truncate
from quiver.harness.discover import apply_findings, discover_harnesses


def _parse_flags(args: list[str]) -> tuple[dict, list[str]]:
    opts = {
        "apply": False,
        "apply_all": False,
        "json": False,
        "include_registered": False,
        "include_missing": False,
    }
    rest = []
    for arg in args:
        if arg == "--apply":
            opts["apply"] = True
        elif arg == "--apply-all":
            opts["apply_all"] = True
        elif arg == "--json":
            opts["json"] = True
        elif arg == "--all":
            opts["include_registered"] = True
            opts["include_missing"] = True
        elif arg in ("-h", "--help"):
            rest.append(arg)
        else:
            rest.append(arg)
    return opts, rest


def _print_help():
    print(
        f"""
  {c('bold', 'swe harness discover')} — Find AI coding CLIs on this machine

  {c('cyan', 'swe harness discover')}              List installable tools not in registry (dry-run)
  {c('cyan', 'swe harness discover --apply')}      Add high-confidence matches to tools.json
  {c('cyan', 'swe harness discover --apply-all')}  Add high + medium confidence matches
  {c('cyan', 'swe harness discover --json')}       Machine-readable output
  {c('cyan', 'swe harness discover --all')}        Include already-registered and missing entries

{c('bold', 'How it works')}
  Scans your PATH (plus ~/.local/bin, /opt/homebrew/bin, …) against a catalog of
  known AI coding CLIs, then pattern-matches other likely agent binaries.

{c('bold', 'See also')}  {c('cyan', 'swe setup')} — interactive onboarding wizard
"""
    )


def cmd_discover(args):
    opts, rest = _parse_flags(args)
    if rest and rest[0] in ("-h", "--help"):
        _print_help()
        return 0
    if rest:
        print(c("red", f"  Unknown argument(s): {' '.join(rest)}"))
        _print_help()
        return 1

    findings = discover_harnesses(
        include_registered=opts["include_registered"],
        include_missing=opts["include_missing"],
    )

    if opts["json"]:
        payload = [
            {
                "name": f.name,
                "command": f.command,
                "path": f.path,
                "confidence": f.confidence,
                "source": f.source,
                "status": f.status,
                "description": f.description,
                "tags": list(f.tags),
                "aliases": list(f.aliases),
            }
            for f in findings
        ]
        print(json.dumps(payload, indent=2))
    else:
        print(f"\n{c('bold', 'Harness Discover')}\n")
        if not findings:
            print(c("dim", "  No new harnesses found. Registry looks up to date.\n"))
        else:
            w_name, w_cmd, w_conf, w_stat = 18, 14, 10, 10
            hdr = (
                f"  {'NAME':<{w_name}} {'COMMAND':<{w_cmd}} {'CONF':<{w_conf}}"
                f" {'STATUS':<{w_stat}} PATH"
            )
            print(c("dim", hdr))
            print(c("dim", "  " + "─" * 90))
            home = str(Path.home())
            for f in findings:
                path = truncate(f.path.replace(home, "~") if f.path else "—", 48)
                conf = c("green", f.confidence) if f.confidence == "high" else c("yellow", f.confidence)
                stat = c("cyan", f.status) if f.status == "new" else c("dim", f.status)
                print(
                    f"  {c('bold', f.name):<{w_name + 9}} {f.command:<{w_cmd}} "
                    f"{conf:<{w_conf + 9}} {stat:<{w_stat + 9}} {c('dim', path)}"
                )
            print()
            print(
                c(
                    "dim",
                    "  dry-run  ·  swe harness discover --apply  │  --apply-all  │  swe setup --apply",
                )
            )
            print()

    if opts["apply"] or opts["apply_all"]:
        min_conf = "medium" if opts["apply_all"] else "high"
        added = apply_findings(findings, min_confidence=min_conf)
        if opts["json"]:
            print(json.dumps({"added": added}, indent=2))
        elif added:
            print(c("green", f"  ✓ Added {len(added)} tool(s) to registry: {', '.join(added)}"))
            print(c("dim", "  Run `swe list` to verify.\n"))
        elif not opts["json"]:
            print(c("dim", "  Nothing to add (no new high-confidence matches).\n"))
    elif not opts["json"] and findings and findings[0].status == "new":
        actionable = [f for f in findings if f.status == "new"]
        if actionable and not sys.stdin.isatty():
            print(c("dim", "  Tip: pass --apply to write matches to tools.json\n"))

    return 0
