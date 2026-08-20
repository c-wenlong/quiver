"""MCP discover CLI command."""

import json
import sys

from quiver.console import c
from quiver.mcp.cli import server_summary
from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers
from quiver.paths import MCP_SOURCE_FILE


def _parse_flags(args: list[str]) -> tuple[dict, list[str]]:
    opts = {"apply": False, "json": False, "all": False, "prune": False}
    rest = []
    for arg in args:
        if arg == "--prune":
            opts["prune"] = True
        elif arg == "--apply":
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
  {c('cyan', 'swe mcp discover --apply')}      Add discoveries to ~/.quiver/mcp.json
  {c('cyan', 'swe mcp discover --json')}       Machine-readable output
  {c('cyan', 'swe mcp discover --all')}        Include servers already in source-of-truth
  {c('cyan', 'swe mcp discover --prune')}      Also remove hub servers no harness configures

{c('bold', 'Where it looks')}
  Both the configs quiver has registered and the harness directories it
  scans for ones it has not, since a server in an unregistered config is
  the one worth telling you about. Vendored configs (editor extensions)
  are skipped.

{c('bold', 'Source of truth')}  {MCP_SOURCE_FILE}
"""
    )


def _warn_version_pinned(findings) -> None:
    """Name any discovery that launches from a versioned directory.

    Adopting one records a path the next extension upgrade deletes, so the
    hub would keep a server that no longer starts.
    """
    from quiver.find.mcps import version_pinned

    pinned = [(f.name, version_pinned(f.server))
              for f in findings if f.status == "new"]
    pinned = [(n, path) for n, path in pinned if path]
    if not pinned:
        return
    print(c("yellow", "  Launches from a versioned directory, so the path "
                      "breaks on upgrade:"))
    for name, path in pinned:
        print(f"    {c('yellow', name)}  {c('dim', path)}")
    print()


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
                colour = {"new": "cyan", "changed": "yellow",
                          "orphaned": "red"}.get(f.status, "dim")
                stat = c(colour, f.status)
                summary = server_summary(f.server)
                print(
                    f"  {c('bold', f.name):<{w_name + 9}} {tools:<{w_tools}} "
                    f"{stat:<{w_stat + 9}} {c('dim', summary)}"
                )
            print()
            _warn_version_pinned(findings)
            print(c("dim", "  dry-run  ·  swe mcp discover --apply  │  swe setup --apply\n"))

    if opts["apply"]:
        result = apply_mcp_findings(findings, prune=opts["prune"])
        if opts["json"]:
            print(json.dumps({
                "added": result.added,
                "updated": result.updated,
                "orphaned": result.orphaned,
                "pruned": result.pruned,
            }, indent=2))
        else:
            if result.added:
                print(c("green", f"  ✓ Added {len(result.added)} server(s) to "
                                 f"{MCP_SOURCE_FILE}: {', '.join(result.added)}"))
            if result.updated:
                print(c("cyan", f"  ↻ Updated {len(result.updated)} server(s) from "
                                f"their harness config: {', '.join(result.updated)}"))
            if result.pruned:
                print(c("red", f"  ✗ Pruned {len(result.pruned)} orphan(s): "
                               f"{', '.join(result.pruned)}"))
            elif result.orphaned:
                print(c("yellow", f"  ! {len(result.orphaned)} server(s) in the hub that "
                                  f"no harness configures: {', '.join(result.orphaned)}"))
                print(c("dim", "    Left in place. Pass --prune to remove them.\n"))
            if not result.wrote and not result.orphaned:
                print(c("dim", "  Hub already matches every harness.\n"))
    elif not opts["json"] and findings and sys.stdin.isatty() is False:
        new = [f for f in findings if f.status == "new"]
        if new:
            print(c("dim", "  Tip: pass --apply to write to mcp.json\n"))

    return 0
