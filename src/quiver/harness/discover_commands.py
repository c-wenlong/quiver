"""Harness discover CLI command."""

import json
import sys
from pathlib import Path

from quiver.console import c, elide
from quiver.multiselect import Choice, _supported, multiselect
from quiver.harness.discover import apply_findings, discover_harnesses


def _parse_flags(args: list[str]) -> tuple[dict, list[str]]:
    opts = {
        "apply": False,
        "apply_all": False,
        "json": False,
        "include_registered": False,
        "include_missing": False,
        "pick": False,
        "names": [],
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
        elif arg == "--pick":
            opts["pick"] = True
        elif arg in ("-h", "--help"):
            rest.append(arg)
        elif not arg.startswith("-"):
            opts["names"].append(arg)
        else:
            rest.append(arg)
    return opts, rest


def _print_help():
    print(
        f"""
  {c('bold', 'swe harness discover')} — Find AI coding CLIs on this machine

  {c('cyan', 'swe harness discover')}              List tools not in the registry (dry-run)
  {c('cyan', 'swe harness discover --apply')}      Add high-confidence matches to harness.json
  {c('cyan', 'swe harness discover --apply-all')}  Add every match, including home-scan finds
  {c('cyan', 'swe harness discover --apply')} {c('dim', 'NAME…')} Add only the named matches, any confidence
  {c('cyan', 'swe harness discover --pick')}       Tick the matches to add (terminal only)
  {c('cyan', 'swe harness discover --json')}       Machine-readable output
  {c('cyan', 'swe harness discover --all')}        Include already-registered and missing entries

{c('bold', 'How it works')}
  Two scans. PATH (plus ~/.local/bin, /opt/homebrew/bin, …) is checked against
  a catalog of known AI coding CLIs, then pattern-matched for other likely
  agent binaries. Then ~ and ~/.config are swept for agent-shaped homes —
  dotdirs holding a skills/ or agents/ dir or an AGENTS.md — which catches
  desktop apps and IDE tools that never put a binary on PATH. Home-scan finds
  are low confidence and register as archived: known, hidden from swe list,
  one swe hs star away if they matter. A few known apps that keep a skills/
  dir but are not harnesses (Aside, Pinokio, TokenTracker) are never listed.

{c('bold', 'See also')}  {c('cyan', 'swe setup')} — interactive onboarding wizard
"""
    )


def _pick(findings) -> set[str] | None:
    """Multiselect over the new findings; None when cancelled.

    High-confidence matches (catalog hits) start ticked. Path and home-scan
    finds start unticked: they are guesses until someone says otherwise.
    """
    new = [f for f in findings if f.status == "new"]
    if not new:
        return set()
    home = str(Path.home())
    choices = [
        Choice(
            key=f.name,
            label=f.name,
            about=f"{f.confidence} · {(f.path or '').replace(home, '~') or f.description}",
        )
        for f in new
    ]
    chosen = multiselect(
        choices,
        selected=[f.name for f in new if f.confidence == "high"],
        title="Tick the tools that are coding harnesses",
    )
    return None if chosen is None else set(chosen)


def cmd_discover(args):
    opts, rest = _parse_flags(args)
    if rest and rest[0] in ("-h", "--help"):
        _print_help()
        return 0
    if rest:
        print(c("red", f"  Unknown argument(s): {' '.join(rest)}"))
        _print_help()
        return 1

    if opts["names"] and not opts["apply"]:
        print(c("red", "  Names only go with --apply: swe harness discover --apply NAME…"))
        return 1
    if opts["pick"] and (opts["apply"] or opts["apply_all"]):
        print(c("red", "  --pick chooses for you; drop --apply/--apply-all"))
        return 1
    if opts["pick"] and not _supported():
        print(c("red", "  --pick needs a terminal. Use --apply NAME… to choose by name."))
        return 1

    findings = discover_harnesses(
        include_registered=opts["include_registered"],
        include_missing=opts["include_missing"],
    )

    names: set[str] | None = None
    if opts["names"]:
        new_names = {f.name for f in findings if f.status == "new"}
        unknown = [n for n in opts["names"] if n not in new_names]
        if unknown:
            print(c("red", f"  Not a new match: {', '.join(unknown)}"))
            if new_names:
                print(c("dim", f"  New matches: {', '.join(sorted(new_names))}"))
            return 1
        names = set(opts["names"])
    elif opts["pick"]:
        names = _pick(findings)
        if names is None:
            print(c("dim", "  cancelled, nothing added\n"))
            return 0

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
                path = elide(f.path.replace(home, "~") if f.path else "—", 48)
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

    if opts["apply"] or opts["apply_all"] or names is not None:
        # --apply-all means every tier, including the home scan's "low";
        # the floor map in apply_findings treats "low" as accept-everything.
        # Named or picked findings skip the floor entirely.
        min_conf = "low" if opts["apply_all"] else "high"
        added = apply_findings(findings, min_confidence=min_conf, names=names)
        if opts["json"]:
            print(json.dumps({"added": added}, indent=2))
        elif added:
            print(c("green", f"  ✓ Added {len(added)} tool(s) to registry: {', '.join(added)}"))
            print(c("dim", "  Run `swe list` to verify.\n"))
        elif not opts["json"]:
            reason = "nothing ticked" if names is not None else "no new high-confidence matches"
            print(c("dim", f"  Nothing to add ({reason}).\n"))
    elif not opts["json"] and findings and findings[0].status == "new":
        actionable = [f for f in findings if f.status == "new"]
        if actionable and not sys.stdin.isatty():
            print(c("dim", "  Tip: pass --apply to write matches to harness.json\n"))

    return 0
