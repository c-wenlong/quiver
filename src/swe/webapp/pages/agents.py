"""The AGENTS.md page: the shared ``~/.swe/AGENTS.md`` with where
``swe init --check --json`` says it is linked, and one project folder's
AGENTS.md and CLAUDE.md.

Saving stays in ``swe`` and the Mac app; see docs/web-app/agents.md.
``POST /v1/app/agents/link`` is Link with swe init and
``POST /v1/app/agents/replace`` is Replace and back up (``swe init
--force``), both run in process as the Doctor page's fixes are and one at
a time with them. Each answers the Global tab read again, plus ``ran``
and ``error``.

``GET /v1/app/agents`` is the Global tab. ``?folder=<path>`` is the
Project tab for that folder, which must be a folder under the hub's home;
only its AGENTS.md and CLAUDE.md are read, and only when they resolve
under that home too.
"""

from __future__ import annotations

import os
from pathlib import Path

from swe.webapp.pages import PageError, home_path
from swe.webapp.pages.skills import read_text

# AgentsModel.projectFileNames, in the order the page lists them.
PROJECT_FILES = ("AGENTS.md", "CLAUDE.md")
# Recent session folders offered for the Project tab, newest first.
MAX_SUGGESTIONS = 12


def get(ctx, args: dict) -> dict:
    home = home_path(ctx)
    if "folder" in args:
        return project(args["folder"], home)
    return global_view(ctx, home)


def post(ctx, action: str, body: dict) -> dict:
    from swe.webapp.pages.doctor import run_init_now

    force = {"link": False, "replace": True}.get(action)
    if force is None:
        raise PageError(404, "unknown_action", f"unknown AGENTS.md action: {action}")
    home = home_path(ctx)
    error = run_init_now(home, force=force)
    return {"ran": action, "error": error, **global_view(ctx, home)}


def global_view(ctx, home: Path) -> dict:
    from swe.init.commands import check_document, run_init
    from swe.webapp.pages.doctor import blocked_paths

    run = run_init(home, check_only=True, quiet=True)
    report = check_document(home, run)
    agents = Path(report["agents_file"])
    return {
        **report,
        # Every path Replace and back up would replace: instructions,
        # skills and hooks alike, not only the rows the strip shows.
        "blocked": blocked_paths(run, home),
        "machine": ctx.machine.get("name"),
        "home": str(home),
        "text": read_text(agents) if report["agents_file_exists"] else None,
        "suggestions": suggestions(ctx, home),
    }


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def suggestions(ctx, home: Path) -> list[str]:
    """Folders the hub's own sessions ran in, newest first, that still exist
    under its home: the web stand-in for the Mac app's folder picker."""
    try:
        rows = ctx.state.catalog.rows()
        machine = ctx.state.machine_id
    except AttributeError:
        return []
    real_home = os.path.realpath(home)
    out: list[str] = []
    for row in rows:
        cwd = row.get("cwd")
        if not cwd or row.get("machine_id") != machine or cwd in out:
            continue
        real = os.path.realpath(cwd)
        if real == real_home or not _under(real, real_home) or not os.path.isdir(cwd):
            continue
        out.append(cwd)
        if len(out) == MAX_SUGGESTIONS:
            break
    return out


def project(folder: str, home: Path) -> dict:
    """AgentsModel.rescanProject plus each file's text. ``links_to`` names
    the other listed file a symlink ends at (CLAUDE.md -> AGENTS.md)."""
    folder = folder.strip()
    if folder == "~" or folder.startswith("~/"):
        folder = str(home) + folder[1:]
    if not folder or not os.path.isabs(folder):
        raise PageError(400, "bad_folder", "Give the project folder's full path on the hub.")
    real_home = os.path.realpath(home)
    real = os.path.realpath(folder)
    if not _under(real, real_home):
        raise PageError(403, "outside_home", "Only folders under the hub's home can be read.")
    if not os.path.isdir(real):
        raise PageError(404, "no_folder", "No such folder on the hub.")
    paths = [Path(folder) / name for name in PROJECT_FILES]
    # Where each file would sit with only the folder resolved: a link
    # ending at one of these points at that sibling.
    spots = [os.path.join(real, name) for name in PROJECT_FILES]
    files = []
    for i, path in enumerate(paths):
        target = os.path.realpath(path)
        other = next((PROJECT_FILES[j] for j in range(len(paths))
                      if j != i and target == spots[j]), None) if path.is_symlink() else None
        exists = path.exists()
        inside = _under(target, real_home)
        files.append({
            "name": path.name,
            "path": str(path),
            "exists": exists,
            "links_to": other,
            "outside": exists and not inside,
            "text": read_text(Path(target)) if exists and inside else None,
        })
    return {"folder": folder, "home": str(home), "files": files}
