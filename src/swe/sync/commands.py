"""`swe sync` and `swe sync status`: presentation for ``sync/repo.py``."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from swe.console import c
from swe.json_output import emit, error_stream, take_json_flag
from swe.sync.repo import SyncError, status, sync

SUB_FLAGS = {
    "sync": {"--dry-run"},
    "status": {"--fetch"},
}


def _mode_line(doc: dict) -> str:
    if doc.get("mode") == "device":
        base = doc.get("base") or "origin/main (not on the remote yet)"
        return f"device branch {doc['branch']}, merges {base}, pushes only {doc['branch']}"
    return f"single branch {doc['branch']}, shared by every machine"


def _print_files(rows: list[dict], limit: int = 12) -> None:
    for row in rows[:limit]:
        print(f"    {c('dim', row['status'])} {row['path']}")
    if len(rows) > limit:
        print(c("dim", f"    ... and {len(rows) - limit} more"))


def _print_held_back(paths: list[str]) -> None:
    if not paths:
        return
    print(f"  {c('yellow', '!')} {len(paths)} changed file(s) under paths that never sync, kept out:")
    for path in paths[:5]:
        print(f"    {c('dim', path)}")
    print(c("dim", "    The hub's .gitignore is older than this swe; add them to ~/.swe/.gitignore."))


def _print_status(doc: dict) -> None:
    print(f"\n{c('bold', 'Hub sync')}  {c('dim', doc['path'])}")
    if doc.get("mode") is None:
        print(f"  {c('red', doc['problem'])}\n")
        return
    print(f"  {c('dim', 'mode')}     {_mode_line(doc)}")
    print(f"  {c('dim', 'remote')}   {doc.get('remote') or 'none'}")
    if doc.get("upstream"):
        print(f"  {c('dim', 'branch')}   {doc['ahead']} ahead, {doc['behind']} behind {doc['upstream']}")
    else:
        print(f"  {c('dim', 'branch')}   not on the remote yet, swe sync pushes it")
    if doc.get("base"):
        print(f"  {c('dim', 'main')}     {doc['base_behind']} new commit(s) on {doc['base']}")
    changes = doc.get("changes") or []
    if changes:
        groups = ", ".join(f"{k} {v}" for k, v in sorted(doc["groups"].items()))
        print(f"  {c('dim', 'changes')}  {len(changes)} file(s): {groups}")
        _print_files(changes)
    else:
        print(f"  {c('dim', 'changes')}  none")
    _print_held_back(doc.get("held_back") or [])
    if doc.get("problem"):
        print(f"  {c('red', doc['problem'])}")
    if doc.get("fetch_error"):
        print(f"  {c('yellow', 'fetch failed: ' + doc['fetch_error'])}")
    elif not doc.get("fetched"):
        print(c("dim", "  Remote counts are from the last fetch; --fetch checks now."))
    print()


def _conflict_help(doc: dict) -> list[str]:
    """How to resolve a conflict by hand, naming the real refs: the trial
    stops at the merge that conflicts, the last one planned, and that one
    can depend on the clean merges before it (a device branch, then
    main), so every planned merge is listed in order. Refs are shell-quoted
    so the command pastes as printed."""
    merge = ", then ".join(f"git -C ~/.swe merge {shlex.quote(m['ref'])}"
                           for m in doc["merges"])
    if doc.get("commit"):
        # git refuses to merge over uncommitted edits to the same files.
        # Only the user can commit them: a blanket `git add -A` could pick
        # up paths sync keeps out (secrets/, the data stores).
        return [f"To resolve it by hand: commit your changes in ~/.swe, run {merge},",
                "fix the files it marks and commit. Or undo one side. Then sync again."]
    return [f"To resolve it by hand: run {merge}, fix the files it marks",
            "and commit. Or undo one side. Then sync again."]


def _print_sync(doc: dict) -> None:
    dry = doc["dry_run"]
    # A conflict stops before anything is written: its commit and merges
    # are only the plan, worded as a dry run's.
    planned = dry or doc["result"] == "conflict"
    mark = "·" if planned else c("green", "✓")
    print(f"\n{c('bold', 'Hub sync')}  {c('dim', _mode_line(doc))}"
          + (c("dim", "  (dry run, nothing written)") if dry else ""))
    commit = doc.get("commit")
    if commit:
        verb = "would commit" if planned else "committed"
        print(f"  {mark} {verb} {len(commit['files'])} file(s): {commit['message']}")
        _print_files(commit["files"])
    for merge in doc.get("merges") or []:
        verb = "would merge" if planned else "merged"
        print(f"  {mark} {verb} {merge['commits']} commit(s) from {merge['ref']}")
    _print_held_back(doc.get("held_back") or [])
    result = doc["result"]
    if result == "conflict":
        print(f"  {c('red', '✗')} {c('red', 'Conflict: these files changed here and on the remote.')}")
        for path in doc["conflicts"]:
            print(f"    {path}")
        print(c("dim", "  Nothing was committed, merged or pushed; ~/.swe is as it was."))
        for line in _conflict_help(doc):
            print(c("dim", f"  {line}"))
    elif result == "up-to-date":
        print(f"  {c('green', '✓')} Already in sync.")
    elif result == "would-sync":
        if doc.get("would_push"):
            print(f"  · would push {doc['branch']}")
    elif doc.get("pushed"):
        print(f"  {c('green', '✓')} pushed {doc['branch']}")
    else:
        print(f"  {c('red', '✗')} push failed: {doc['push_error']}")
        print(c("dim", "  The commit and merges are kept here; sync again to retry."))
    relinked = doc.get("relinked")
    if relinked:
        print(f"  {c('green', '✓')} re-linked {relinked['changed']} incoming file(s) into every harness")
        for row in relinked.get("marketplaces") or []:
            mark = c("green", "✓") if not row["error"] else c("yellow", "!")
            print(f"    {mark} {row['marketplace']} in {row['harness']}"
                  + (f": {row['error']}" if row["error"] else ""))
        for note in relinked.get("notes") or []:
            print(f"    {c('yellow', '!')} {note}")
    print()


def print_sync_help() -> None:
    from swe.help_text import HELP
    print(HELP["sync"][1])


def cmd_sync(args, home: Path | None = None) -> int:
    home = home or Path.home()
    args, json_view = take_json_flag(list(args))
    sub = "sync"
    if args and args[0] == "status":
        sub, args = "status", args[1:]
    if args and args[0] in ("-h", "--help", "help"):
        print_sync_help()
        return 0
    unknown = [a for a in args if a not in SUB_FLAGS[sub]]
    if unknown:
        print(c("red", f"Unexpected args: {' '.join(unknown)}"), file=error_stream(json_view))
        return 1
    if sub == "status":
        doc = status(home, do_fetch="--fetch" in args)
        if json_view:
            return emit(doc)
        _print_status(doc)
        return 0 if doc.get("mode") else 1
    try:
        doc = sync(home, dry_run="--dry-run" in args)
    except SyncError as exc:
        print(c("red", f"swe sync: {exc}"), file=error_stream(json_view))
        return 1
    failed = doc["result"] in ("conflict", "push-failed")
    if json_view:
        emit(doc)
    else:
        _print_sync(doc)
    return 1 if failed else 0
