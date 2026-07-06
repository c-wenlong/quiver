"""MCP discover CLI command."""

import json
import sys

from quiver.console import c
from quiver.mcp.cli import server_summary
from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers
from quiver.paths import MCP_SOURCE_FILE


def _parse_flags(args: list[str]) -> tuple[dict, list[str]]:
    opts = {"apply": False, "json": False, "all": False}
    rest = []
    for arg in args:
        if arg == "--apply":
            opts["apply"] = True
        elif arg == "--json":
            opts["json"] = True
        elif arg == "--all":
            opts["all"] = True
        elif arg in ("-h", "--help"):
            rest.append(arg)
        else:
            rest.append(arg)
    return opts, rest


def _print_help():
    print(
        f"""
  {c('bold', 'swe mcp discover')} — Find MCP servers across your AI tools

  {c('cyan', 'swe mcp discover')}              List servers in tool configs not in mcp.json (dry-run)
  {c('cyan', 'swe mcp discover --apply')}      Add discoveries to ~/.config/swe/mcp.json
  {c('cyan', 'swe mcp discover --json')}       Machine-readable output
  {c('cyan', 'swe mcp discover --all')}        Include servers already in source-of-truth

{c('bold', 'Source of truth')}  {MCP_SOURCE_FILE}
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

    findings = discover_mcp_servers(include_in_source=opts["all"])

    if opts["json"]:
        payload = [
            {
                "name": f.name,
                "tools": list(f.tools),
                "status": f.status,
                "source_tool": f.source_tool,
                "summary": server_summary(f.server),
            }
            for f in findings
        ]
        print(json.dumps(payload, indent=2))
    else:
        print(f"\n{c('bold', 'MCP Discover')}\n")
        if not findings:
            print(c("dim", "  No new MCP servers found outside source-of-truth.\n"))
        else:
            w_name, w_tools, w_stat = 22, 28, 10
            print(c("dim", f"  {'SERVER':<{w_name}} {'TOOLS':<{w_tools}} {'STATUS':<{w_stat}} SUMMARY"))
            print(c("dim", "  " + "─" * 90))
            for f in findings:
                tools = ", ".join(f.tools)
                stat = c("cyan", f.status) if f.status == "new" else c("dim", f.status)
                summary = server_summary(f.server)
                print(
                    f"  {c('bold', f.name):<{w_name + 9}} {tools:<{w_tools}} "
                    f"{stat:<{w_stat + 9}} {c('dim', summary)}"
                )
            print()
            print(c("dim", "  dry-run  ·  swe mcp discover --apply  │  swe setup --apply\n"))

    if opts["apply"]:
        added = apply_mcp_findings(findings)
        if opts["json"]:
            print(json.dumps({"added": added}, indent=2))
        elif added:
            print(c("green", f"  ✓ Added {len(added)} server(s) to {MCP_SOURCE_FILE}: {', '.join(added)}"))
        elif not opts["json"]:
            print(c("dim", "  Nothing to add.\n"))
    elif not opts["json"] and findings and sys.stdin.isatty() is False:
        new = [f for f in findings if f.status == "new"]
        if new:
            print(c("dim", "  Tip: pass --apply to write to mcp.json\n"))

    return 0
