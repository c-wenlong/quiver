"""First-run setup wizard and domain subcommand routers."""

import sys
from pathlib import Path

from swe.console import c, tilde
from swe.json_output import emit
from swe.setup.models import HarnessPreview, McpPreview, SkillsPreview
from swe.harness.discover import apply_findings, discover_harnesses
from swe.harness.discover_commands import cmd_discover
from swe.mcp.discover import apply_mcp_findings, discover_mcp_servers
from swe.prompt import read_line
from swe.skills.symlinks import apply_skills_symlink_hints, skills_symlink_hints
from swe.setup.wizard import SECTION_ALIASES, run_setup_wizard
from swe.init.commands import (
    adopt_instructions,
    agents_file_customised,
    check_adoptable,
    run_init,
)
from swe.init.layout import agents_file, registry_name
from swe.paths import expand_user


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
        from swe.harness.commands import cmd_list

        result = cmd_list(rest)
        return result if isinstance(result, int) else 0
    if sub == "discover":
        result = cmd_discover(rest)
        return result if isinstance(result, int) else 0
    if sub in ("star", "favourite", "favorite"):
        from swe.harness.commands import cmd_star

        result = cmd_star(rest)
        return result if isinstance(result, int) else 0
    if sub in ("edit", "review"):
        from swe.harness.commands import cmd_harness_edit

        result = cmd_harness_edit(rest)
        return result if isinstance(result, int) else 0
    if sub in ("archive", "shelve"):
        from swe.harness.commands import cmd_archive

        result = cmd_archive(rest)
        return result if isinstance(result, int) else 0
    if sub == "usage":
        from swe.harness.commands import cmd_harness_usage

        result = cmd_harness_usage(rest)
        return result if isinstance(result, int) else 0
    if sub == "usage-events":
        from swe.harness.commands import cmd_harness_usage_events

        result = cmd_harness_usage_events(rest)
        return result if isinstance(result, int) else 0
    if sub == "usage-daily":
        from swe.harness.commands import cmd_harness_usage_daily

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
  {c('cyan', 'swe setup --hub=<git url>')}  Clone your hub repo as ~/.quiver first, then
                                carry on with any of the above. Only while
                                ~/.quiver has no hub yet; fork a hub repo first

{c('bold', 'Steps')}
  First, only while ~/.quiver has no hub yet: your hub repo's URL
  (a blank answer starts an empty hub, as before)
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


def _take_hub(args: list[str]) -> tuple[list[str], str | None]:
    """Pull ``--hub=<url>`` or ``--hub <url>`` out of ``args`` (the last
    one wins). Raises ValueError for a ``--hub`` with no URL after it."""
    rest: list[str] = []
    url = None
    at = 0
    while at < len(args):
        arg = args[at]
        if arg.startswith("--hub="):
            url = arg.split("=", 1)[1]
        elif arg == "--hub":
            if at + 1 >= len(args) or args[at + 1].startswith("-"):
                raise ValueError("--hub takes a git URL: swe setup --hub=<url>")
            url = args[at + 1]
            at += 1
        else:
            rest.append(arg)
        at += 1
    return rest, url


def _clone_hub(home: Path, url: str, *, quiet: bool) -> dict | None:
    """``--hub``: clone the hub as ``~/.quiver`` and register its plugin
    marketplaces. Returns what was cloned, or None after saying why not
    (on stderr when ``quiet``, so --json keeps stdout for its document)."""
    from swe.init.hub import HubError, clone_and_register, public_url

    if not quiet:
        print(f"\n  Cloning {public_url(url.strip())} into ~/.quiver…")
    try:
        cloned = clone_and_register(home, url)
    except HubError as exc:
        print(c("red", f"  {exc}"), file=sys.stderr if quiet else sys.stdout)
        return None
    if not quiet:
        print(c("green", "  Cloned."))
        _print_marketplaces(cloned["marketplaces"])
    return cloned


def _print_marketplaces(rows: list[dict]) -> None:
    if not rows:
        return
    print(f"  {c('bold', 'Plugin marketplaces')}")
    for row in rows:
        if row["error"] is None:
            print(f"    {c('green', '✓')} {row['marketplace']} registered in {row['harness']}")
        else:
            print(f"    {c('yellow', '!')} {row['marketplace']} not registered in "
                  f"{row['harness']}: {row['error']}")
    if any(row["error"] for row in rows):
        print(c("dim", "    swe find plugins lists what is left; --fix=<id> retries one."))


def _hub_data(home: Path, cloned: dict | None) -> dict:
    """The ``hub`` key of ``swe setup --json``: whether a hub can still be
    cloned as ``~/.quiver`` (the apps show their hub URL field only then),
    the demo hub to fork (null until it exists), and what this run cloned."""
    from swe.init.hub import DEMO_HUB_URL, can_clone

    return {"can_clone": can_clone(home), "demo_url": DEMO_HUB_URL or None,
            "cloned": cloned}


def _print_hub_hint(home: Path) -> None:
    """The dry run's pointer to --hub, while ~/.quiver has no hub yet."""
    from swe.init.hub import DEMO_HUB_URL, FORK_NOTE, can_clone

    if not can_clone(home):
        return
    print(c("dim", "  ~/.quiver has no hub yet. To start from your own hub repo:\n"))
    print(f"    {c('cyan', 'swe setup --hub=<git url>')}")
    print(c("dim", f"    {FORK_NOTE}"))
    if DEMO_HUB_URL:
        print(c("dim", f"    Demo hub: {DEMO_HUB_URL}"))
    print()


def cmd_setup(args):
    try:
        args, hub_url = _take_hub(list(args))
    except ValueError as exc:
        stream = sys.stderr if "--json" in args else sys.stdout
        print(c("red", f"  {exc}"), file=stream)
        return 1
    adopt_args = [arg for arg in args if arg.startswith("--adopt=")]
    starred_args = [arg for arg in args if arg.startswith("--starred=")]
    args = [arg for arg in args
            if not arg.startswith(("--adopt=", "--starred="))]
    adopt = expand_user(adopt_args[-1].split("=", 1)[1]) if adopt_args else None
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
    if hub_url is not None and not hub_url.strip():
        print(c("red", "  --hub needs a git URL: swe setup --hub=<url>"), file=refusal_stream)
        return 1
    if hub_url is not None and adopt is not None:
        # The hub brings its own AGENTS.md, so there is nothing to adopt into.
        print(c("red", "  --hub and --adopt both start AGENTS.md; pick one."),
              file=refusal_stream)
        return 1
    if adopt is not None:
        reason = check_adoptable(Path.home(), adopt)
        if reason:
            print(c("red", f"  {reason}"), file=refusal_stream)
            return 1

    interactive = sys.stdin.isatty() and not non_interactive
    wizard = not apply and not json_out and interactive
    if section and not wizard:
        print(c("red", "  This setup section requires an interactive terminal."))
        return 1
    if quick and not interactive:
        print(c("red", "  --quick requires an interactive terminal."))
        return 1

    home = Path.home()
    cloned = None
    if hub_url is not None:
        # The hub comes first, so everything after it (the wizard, the
        # preview, the apply) runs on the cloned ~/.quiver.
        cloned = _clone_hub(home, hub_url, quiet=json_out)
        if cloned is None:
            return 1
    if wizard:
        return run_setup_wizard(section=section, quick=quick)

    if json_out:
        # One document per --json invocation: --apply results ride inside
        # the same envelope instead of printing a second document.
        data, discovery_error = _setup_data(home, apply, adopt, starred, cloned)
        if discovery_error is not None:
            print(c("red", f"  setup failed: {discovery_error}"), file=sys.stderr)
            emit(data)
            return 1
        emit(data)
        # An apply step, a linking pass or the preview that stopped part
        # way is not a finished setup, even though what came before it
        # was applied and is reported above.
        applied = data.get("applied", {})
        preview_error = data.get("instructions", {}).get("error")
        if preview_error:
            print(c("red", f"  setup failed: {preview_error}"), file=sys.stderr)
        failed = (applied.get("errors") or applied.get("links", {}).get("error")
                  or preview_error)
        return 1 if failed else 0

    new_harness, new_mcp, skill_hints = _setup_findings(home)
    actionable_skills = _actionable(skill_hints)

    print(f"\n{c('bold', 'quiver setup')}\n")
    if not apply:
        _print_hub_hint(home)

    # Step 1 — harnesses
    print(c("dim", "  Step 1/3 — AI coding CLI harnesses\n"))
    if new_harness:
        for f in new_harness:
            path = tilde(f.path, home)
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


def _setup_findings(home: Path) -> tuple[list, list, list]:
    """What setup would apply: (new high-confidence harnesses, new MCP
    servers, skills-root hints)."""
    harness_findings = discover_harnesses()
    new_harness = [f for f in harness_findings if f.status == "new" and f.confidence == "high"]
    mcp_findings = discover_mcp_servers()
    new_mcp = [f for f in mcp_findings if f.status == "new"]
    skill_hints = skills_symlink_hints(home=home)
    return new_harness, new_mcp, skill_hints


def _actionable(skill_hints) -> list:
    return [h for h in skill_hints if h.action in ("create_shared", "symlink")]


def _setup_data(home: Path, apply: bool, adopt: Path | None = None,
                starred: list[str] | None = None,
                cloned: dict | None = None) -> tuple[dict, str | None]:
    """The ``swe setup --json`` document's ``data``, and the discovery
    error when discovery itself failed (the document then carries it as
    data, so the caller still gets a well-formed reply)."""
    data, error = _setup_findings_data(home, apply, adopt, starred)
    data["hub"] = _hub_data(home, cloned)
    return data, error


def _setup_findings_data(home: Path, apply: bool, adopt: Path | None,
                         starred: list[str] | None) -> tuple[dict, str | None]:
    try:
        new_harness, new_mcp, skill_hints = _setup_findings(home)
    except (EOFError, KeyboardInterrupt):
        raise
    except Exception as exc:
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
        return data, str(exc)
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
        data["applied"] = _apply_all(new_harness, new_mcp, _actionable(skill_hints),
                                     home, adopt, starred)
    else:
        data["instructions"] = _instructions_preview(home)
    return data, None


def setup_preview(home: Path) -> dict:
    """``swe setup --json``'s data, built in process: what setup would
    register, import and link. Writes nothing. The web app's setup page
    reads this."""
    return _setup_data(home, apply=False)[0]


def apply_setup(home: Path | None = None, adopt: Path | None = None,
                starred: list[str] | None = None) -> dict:
    """``swe setup --apply --json [--adopt=<file>] [--starred=a,b]``'s
    ``applied`` document, in process.

    The web app's Doctor page runs a ``setup`` fix through here with
    neither, the way the Mac app's Run calls ``swe setup --apply --json``,
    so the stars stay as they are. The Setup page passes the ticked
    harnesses and the file AGENTS.md starts from; checking that file
    (``check_adoptable``) is the caller's job, as ``cmd_setup`` does
    first. A step that fails is reported in ``errors`` or
    ``links.error``, as on the command line; a discovery that fails
    raises.
    """
    home = home or Path.home()
    new_harness, new_mcp, skill_hints = _setup_findings(home)
    return _apply_all(new_harness, new_mcp, _actionable(skill_hints), home, adopt, starred)


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

    from swe.harness.catalog import HARNESS_CATALOG
    from swe.harness.registry import load_registry, resolve, save_registry
    from swe.harness.stars import save_stars

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
