"""``swe gh`` — GitHub surfaces over ``gh``: PRs, issues, actions, inbox.

Read-only on purpose: the agent inside a harness owns writes (create,
merge, comment). Quiver owns the map — which repos you watch, what state
they are in, and a fast way to jump to any of their GitHub pages.

Repo resolution is ``--repo=`` first, the working directory's remote
second, then every pinned repo — so a bare ``swe gh pr`` is the dashboard.
"""

import time
import webbrowser
from datetime import datetime

from quiver.console import c, sanitize, truncate
from quiver.flags import expand_value_flags
from quiver.github import gh, repos
from quiver.json_output import emit, error_stream, take_json_flag
from quiver.table import Table

_VALUE_FLAGS = {"--repo", "--state", "--limit"}

_PR_FIELDS = (
    "number,title,state,isDraft,author,headRefName,baseRefName,labels,"
    "mergeable,reviewDecision,statusCheckRollup,updatedAt,url"
)
_ISSUE_FIELDS = (
    "number,title,state,author,labels,assignees,comments,updatedAt,url"
)
_RUN_FIELDS = (
    "databaseId,displayTitle,workflowName,headBranch,event,status,"
    "conclusion,createdAt,updatedAt,url"
)
_RELEASE_FIELDS = (
    "tagName,name,isLatest,isDraft,isPrerelease,publishedAt"
)


# ------------------------------------------------------------------ shaping

def _parse_iso(text) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _rel(epoch: float | None) -> str:
    if epoch is None:
        return "-"
    diff = time.time() - epoch
    if diff < 60:
        return "just now"
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    return f"{int(diff / 86400)}d ago"


def _login(node) -> str:
    return (node or {}).get("login") or "-"


def _label_names(labels) -> list[str]:
    return [l.get("name", "") for l in (labels or []) if isinstance(l, dict)]


def rollup_checks(rollup) -> str:
    """A PR's ``statusCheckRollup`` list folded to one word.

    failing beats running beats passing: a finished failure matters more
    than a check still running, and an all-green rollup is the only state
    safe to merge on.
    """
    contexts = rollup if isinstance(rollup, list) else []
    if not contexts:
        return "none"
    states = set()
    for ctx in contexts:
        if not isinstance(ctx, dict):
            continue
        if ctx.get("__typename") == "StatusContext":
            states.add(str(ctx.get("state") or "").upper())
        else:
            status = str(ctx.get("status") or "").upper()
            conclusion = str(ctx.get("conclusion") or "").upper()
            states.add(conclusion or status)
    if states & {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED",
                 "ERROR", "STARTUP_FAILURE"}:
        return "failing"
    if states & {"QUEUED", "IN_PROGRESS", "PENDING", "EXPECTED", "WAITING"}:
        return "running"
    if states <= {"SUCCESS", "SKIPPED", "NEUTRAL"}:
        return "passing"
    return "unknown"


def _checks_glyph(checks: str) -> tuple[str, str]:
    return {
        "passing": ("✓", "green"),
        "failing": ("✗", "red"),
        "running": ("◐", "yellow"),
        "unknown": ("?", "yellow"),
        "none": ("-", "dim"),
    }.get(checks, ("-", "dim"))


def _pr_glyph(pr: dict) -> tuple[str, str]:
    state = str(pr.get("state") or "").upper()
    if state == "MERGED":
        return "●", "neon_pink"
    if state == "CLOSED":
        return "●", "red"
    if pr.get("isDraft") or pr.get("is_draft") or pr.get("draft"):
        return "○", "dim"
    return "●", "green"


def _review_cell(pr: dict) -> tuple[str, str]:
    """Merge-readiness column: conflict outranks every review verdict."""
    if str(pr.get("mergeable") or "").upper() == "CONFLICTING":
        return "conflict", "red"
    decision = str(pr.get("review_decision")
                   or pr.get("reviewDecision") or "").upper()
    return {
        "APPROVED": ("approved", "green"),
        "CHANGES_REQUESTED": ("changes", "red"),
        "REVIEW_REQUIRED": ("review", "dim"),
    }.get(decision, ("-", "dim"))


def _pr_row(pr: dict) -> dict:
    return {
        "number": pr.get("number"),
        "title": pr.get("title") or "",
        "state": str(pr.get("state") or "").lower(),
        "draft": bool(pr.get("isDraft") or pr.get("is_draft")),
        "author": _login(pr.get("author")),
        "branch": pr.get("headRefName") or "",
        "base": pr.get("baseRefName") or "",
        "labels": _label_names(pr.get("labels")),
        "mergeable": str(pr.get("mergeable") or "").lower() or "unknown",
        "review_decision":
            str(pr.get("reviewDecision") or "").lower() or "none",
        "checks": rollup_checks(pr.get("statusCheckRollup")),
        "updated_at": pr.get("updatedAt"),
        "url": pr.get("url") or "",
    }


def _issue_row(issue: dict) -> dict:
    return {
        "number": issue.get("number"),
        "title": issue.get("title") or "",
        "state": str(issue.get("state") or "").lower(),
        "author": _login(issue.get("author")),
        "labels": _label_names(issue.get("labels")),
        "assignees": [_login(a) for a in (issue.get("assignees") or [])],
        "comments": (issue.get("comments") or {}).get("totalCount", 0)
        if isinstance(issue.get("comments"), dict)
        else issue.get("comments") or 0,
        "updated_at": issue.get("updatedAt"),
        "url": issue.get("url") or "",
    }


def _run_row(run: dict) -> dict:
    return {
        "id": run.get("databaseId"),
        "workflow": run.get("workflowName") or run.get("name") or "",
        "title": run.get("displayTitle") or "",
        "branch": run.get("headBranch") or "",
        "event": run.get("event") or "",
        "status": str(run.get("status") or "").lower(),
        "conclusion": str(run.get("conclusion") or "").lower() or None,
        "created_at": run.get("createdAt"),
        "updated_at": run.get("updatedAt"),
        "url": run.get("url") or "",
    }


def _release_row(rel: dict, repo: str) -> dict:
    tag = rel.get("tagName") or ""
    return {
        "tag": tag,
        "name": rel.get("name") or "",
        "latest": bool(rel.get("isLatest")),
        "draft": bool(rel.get("isDraft")),
        "prerelease": bool(rel.get("isPrerelease")),
        "published_at": rel.get("publishedAt"),
        "url": f"https://github.com/{repo}/releases/tag/{tag}" if tag else "",
    }


def _notification_html_url(item: dict) -> str:
    """subject.url is an API URL; turn it into the page a human opens."""
    subject_url = (item.get("subject") or {}).get("url") or ""
    prefix = "https://api.github.com/repos/"
    if subject_url.startswith(prefix):
        rest = subject_url[len(prefix):]
        # API says /pulls/NNN and /issues/NNN; the site says /pull/NNN.
        rest = rest.replace("/pulls/", "/pull/", 1)
        return f"https://github.com/{rest}"
    return ((item.get("repository") or {}).get("html_url")
            or "https://github.com/notifications")


def _notification_row(item: dict) -> dict:
    subject = item.get("subject") or {}
    return {
        "id": item.get("id"),
        "repo": (item.get("repository") or {}).get("full_name") or "",
        "title": subject.get("title") or "",
        "type": subject.get("type") or "",
        "reason": item.get("reason") or "",
        "unread": bool(item.get("unread")),
        "updated_at": item.get("updated_at"),
        "url": _notification_html_url(item),
    }


def _pages_payload(repo: str, site, latest) -> dict:
    return {
        "repo": repo,
        "status": "ok",
        "pages": None if site is None else {
            "url": site.get("html_url") or site.get("url") or "",
            "cname": site.get("cname"),
            "status": site.get("status"),
            "source_branch": ((site.get("source") or {}).get("branch")),
            "latest_build": None if latest is None else {
                "status": latest.get("status"),
                "error": (latest.get("error") or {}).get("message")
                if isinstance(latest.get("error"), dict)
                else latest.get("error"),
                "commit": (latest.get("commit") or "")[:7],
                "updated_at": latest.get("updated_at"),
            },
        },
    }


# ------------------------------------------------------------------ fetching

def _gh_list(args: list[str]) -> gh.GhResult:
    return gh.run_json(args)


def _gh_paginated(path: str, limit: int) -> gh.GhResult:
    """``gh api`` a list endpoint, fetching just enough pages for ``limit``.

    GitHub caps ``per_page`` at 100, so a single request cannot serve
    ``--limit=200``. Pages are pulled explicitly — never
    ``--paginate``, which would fetch the endpoint's *entire* history —
    and a short page means the listing ended.
    """
    items: list = []
    per_page = min(limit, 100)
    page = 1
    sep = "&" if "?" in path else "?"
    while len(items) < limit:
        result = gh.run_json(
            ["api", f"{path}{sep}per_page={per_page}&page={page}"])
        if result.status != "ok":
            if items:
                break  # later page failed: keep what we already have
            return result
        batch = result.data if isinstance(result.data, list) else []
        items += batch
        if len(batch) < per_page:
            break
        page += 1
    return gh.GhResult("ok", data=items[:limit])


def _repo_result(repo: str, key: str, result: gh.GhResult,
                 shape) -> dict:
    """Uniform per-repo entry: ok carries shaped rows, errors stay typed."""
    if result.status == "ok":
        rows = [shape(item) for item in (result.data or [])
                if isinstance(item, dict)]
        return {"repo": repo, "status": "ok", key: rows}
    return {"repo": repo, "status": result.status, key: [],
            "error": result.error or result.status}


def _fetch_prs(repo: str, state: str, limit: int) -> dict:
    return _repo_result(repo, "pull_requests", _gh_list([
        "pr", "list", "--repo", repo, "--state", state,
        "--limit", str(limit), "--json", _PR_FIELDS]), _pr_row)


def _fetch_pr_status(repo: str) -> dict:
    result = _gh_list([
        "pr", "status", "--repo", repo, "--json", _PR_FIELDS])
    if result.status != "ok":
        return {"repo": repo, "status": result.status,
                "current_branch": None, "authored": [],
                "review_requested": [],
                "error": result.error or result.status}
    data = result.data or {}
    return {
        "repo": repo,
        "status": "ok",
        "current_branch": (_pr_row(data["currentBranch"])
                           if isinstance(data.get("currentBranch"), dict)
                           else None),
        "authored": [_pr_row(p) for p in (data.get("createdBy") or [])
                     if isinstance(p, dict)],
        "review_requested": [_pr_row(p) for p in (data.get("needsReview") or [])
                             if isinstance(p, dict)],
    }


def _fetch_issues(repo: str, state: str, limit: int) -> dict:
    return _repo_result(repo, "issues", _gh_list([
        "issue", "list", "--repo", repo, "--state", state,
        "--limit", str(limit), "--json", _ISSUE_FIELDS]), _issue_row)


def _fetch_runs(repo: str, limit: int) -> dict:
    return _repo_result(repo, "runs", _gh_list([
        "run", "list", "--repo", repo,
        "--limit", str(limit), "--json", _RUN_FIELDS]), _run_row)


def _fetch_pages(repo: str) -> dict:
    site = gh.run_json(["api", f"repos/{repo}/pages"])
    if site.status == "not-found":
        return _pages_payload(repo, None, None)
    if site.status != "ok":
        return {"repo": repo, "status": site.status, "pages": None,
                "error": site.error or site.status}
    latest = gh.run_json(["api", f"repos/{repo}/pages/builds/latest"])
    if latest.status == "ok":
        build = latest.data
    elif latest.status == "not-found":
        build = None
    else:
        # A failed build read is not a missing build — keep the error so
        # the UI can say "couldn't check" instead of rendering nothing.
        build = {"status": "fetch-failed",
                 "error": latest.error or latest.status}
    return _pages_payload(repo, site.data or {}, build)


def _fetch_releases(repo: str, limit: int) -> dict:
    result = _gh_list([
        "release", "list", "--repo", repo,
        "--limit", str(limit), "--json", _RELEASE_FIELDS])
    return _repo_result(repo, "releases", result,
                        lambda r: _release_row(r, repo))


def _fetch_tags(repo: str, limit: int) -> dict:
    result = _gh_paginated(f"repos/{repo}/tags", limit)
    def shape(tag: dict) -> dict:
        name = tag.get("name") or ""
        return {
            "name": name,
            "sha": ((tag.get("commit") or {}).get("sha") or "")[:7],
            "url": f"https://github.com/{repo}/releases/tag/{name}",
        }
    return _repo_result(repo, "tags", result, shape)


def _fetch_branches(repo: str, limit: int) -> dict:
    meta = gh.run_gh(["api", f"repos/{repo}", "--jq", ".default_branch"])
    if meta.status != "ok":
        return {"repo": repo, "status": meta.status, "branches": [],
                "default_branch": None,
                "error": meta.error or meta.status}
    default = str(meta.data or "").strip()
    result = _gh_paginated(f"repos/{repo}/branches", limit)
    def shape(branch: dict) -> dict:
        name = branch.get("name") or ""
        return {
            "name": name,
            "protected": bool(branch.get("protected")),
            "default": name == default,
            "sha": ((branch.get("commit") or {}).get("sha") or "")[:7],
            "url": f"https://github.com/{repo}/tree/{name}",
        }
    out = _repo_result(repo, "branches", result, shape)
    out["default_branch"] = default or None
    return out


def _fetch_inbox(limit: int, fetch_all: bool = False) -> dict:
    # Without all=true the API only returns unread notifications.
    path = "notifications?all=true" if fetch_all else "notifications"
    result = _gh_paginated(path, limit)
    if result.status != "ok":
        return {"status": result.status, "notifications": [],
                "error": result.error or result.status}
    return {
        "status": "ok",
        "notifications": [_notification_row(i)
                          for i in (result.data or [])
                          if isinstance(i, dict)],
    }


# ------------------------------------------------------------------ rendering

def _rel_at(iso) -> str:
    return _rel(_parse_iso(iso))


def _print_repo_error(entry: dict) -> None:
    print(f"  {c('bold', entry['repo'])}  "
          f"{c('red', entry.get('error') or entry['status'])}")


def _render_pr_table(results: list[dict]) -> None:
    for entry in results:
        if entry["status"] != "ok":
            _print_repo_error(entry)
            continue
        print(f"\n{c('bold', entry['repo'])}\n")
        pulls = entry["pull_requests"]
        if not pulls:
            print(c("dim", "  No pull requests.\n"))
            continue
        t = Table()
        t.add_column("num", "#", width=6, kind="text", fit="fixed")
        t.add_column("st", "ST", width=2, kind="preformatted", fit="fixed")
        t.add_column("title", "TITLE", width=50, kind="text", fit="shrink")
        t.add_column("branch", "BRANCH", width=30, kind="text", fit="shrink")
        t.add_column("author", "AUTHOR", width=14, kind="text", fit="shrink")
        t.add_column("ci", "CI", width=3, kind="preformatted", fit="fixed")
        t.add_column("review", "REVIEW", width=8, kind="preformatted",
                     fit="fixed")
        t.add_column("upd", "UPDATED", width=8, kind="text", fit="fixed")
        for pr in pulls:
            glyph, color = _pr_glyph(pr)
            ci_glyph, ci_color = _checks_glyph(pr["checks"])
            rev, rev_color = _review_cell(pr)
            t.add_row({
                "num": f"#{pr['number']}",
                "st": c(color, glyph),
                "title": pr["title"],
                "branch": pr["branch"],
                "author": pr["author"],
                "ci": c(ci_color, ci_glyph),
                "review": c(rev_color, rev),
                "upd": _rel_at(pr["updated_at"]),
            })
        for line in t.render():
            print(line)
    print()


def _render_pr_status(results: list[dict]) -> None:
    for entry in results:
        if entry["status"] != "ok":
            _print_repo_error(entry)
            continue
        print(f"\n{c('bold', entry['repo'])}\n")
        current = entry.get("current_branch")
        label = {
            "current_branch": "Current branch",
            "authored": "Authored by you",
            "review_requested": "Needs your review",
        }
        groups = [
            ("current_branch", [current] if current else []),
            ("authored", entry["authored"]),
            ("review_requested", entry["review_requested"]),
        ]
        if not any(pulls for _, pulls in groups):
            print(c("dim", "  Nothing open.\n"))
            continue
        for key, pulls in groups:
            print(c("dim", f"  {label[key]}"))
            for pr in pulls:
                glyph, color = _pr_glyph(pr)
                ci_glyph, ci_color = _checks_glyph(pr["checks"])
                print(f"    {c(color, glyph)} "
                      f"{c('cyan', '#' + str(pr['number']))} "
                      f"{sanitize(pr['title'])} "
                      f"{c(ci_color, ci_glyph)}")
            print()


def _render_issue_table(results: list[dict]) -> None:
    for entry in results:
        if entry["status"] != "ok":
            _print_repo_error(entry)
            continue
        print(f"\n{c('bold', entry['repo'])}\n")
        issues = entry["issues"]
        if not issues:
            print(c("dim", "  No issues.\n"))
            continue
        t = Table()
        t.add_column("num", "#", width=6, kind="text", fit="fixed")
        t.add_column("title", "TITLE", width=60, kind="text", fit="shrink")
        t.add_column("author", "AUTHOR", width=14, kind="text", fit="shrink")
        t.add_column("labels", "LABELS", width=24, kind="text", fit="shrink")
        t.add_column("upd", "UPDATED", width=8, kind="text", fit="fixed")
        for issue in issues:
            t.add_row({
                "num": f"#{issue['number']}",
                "title": issue["title"],
                "author": issue["author"],
                "labels": ",".join(issue["labels"]),
                "upd": _rel_at(issue["updated_at"]),
            })
        for line in t.render():
            print(line)
    print()


def _run_glyph(run: dict) -> tuple[str, str]:
    if run["status"] in ("queued", "in_progress", "waiting", "requested"):
        return "◐", "yellow"
    return {
        "success": ("✓", "green"),
        "failure": ("✗", "red"),
        "cancelled": ("■", "dim"),
        "skipped": ("-", "dim"),
    }.get(run["conclusion"] or "", ("-", "dim"))


def _render_run_table(results: list[dict]) -> None:
    for entry in results:
        if entry["status"] != "ok":
            _print_repo_error(entry)
            continue
        print(f"\n{c('bold', entry['repo'])}\n")
        runs = entry["runs"]
        if not runs:
            print(c("dim", "  No workflow runs.\n"))
            continue
        t = Table()
        t.add_column("st", "ST", width=3, kind="preformatted", fit="fixed")
        t.add_column("workflow", "WORKFLOW", width=20, kind="text",
                     fit="shrink")
        t.add_column("title", "TITLE", width=50, kind="text", fit="shrink")
        t.add_column("branch", "BRANCH", width=24, kind="text", fit="shrink")
        t.add_column("event", "EVENT", width=8, kind="text", fit="fixed")
        t.add_column("upd", "UPDATED", width=8, kind="text", fit="fixed")
        for run in runs:
            glyph, color = _run_glyph(run)
            t.add_row({
                "st": c(color, glyph),
                "workflow": run["workflow"],
                "title": run["title"],
                "branch": run["branch"],
                "event": run["event"],
                "upd": _rel_at(run["updated_at"]),
            })
        for line in t.render():
            print(line)
    print()


def _render_simple_table(results: list[dict], key: str,
                         columns: list[tuple[str, str, int]],
                         cells) -> None:
    """Releases, tags and branches share one plain-table shape."""
    for entry in results:
        if entry["status"] != "ok":
            _print_repo_error(entry)
            continue
        print(f"\n{c('bold', entry['repo'])}\n")
        items = entry[key]
        if not items:
            print(c("dim", "  Nothing here.\n"))
            continue
        t = Table()
        for name, header, width in columns:
            t.add_column(name, header, width=width, kind="text",
                         fit="shrink")
        for item in items:
            t.add_row({name: sanitize(str(cells[name](item)))
                       for name, _, _ in columns})
        for line in t.render():
            print(line)
    print()


def _render_releases(results: list[dict]) -> None:
    _render_simple_table(
        results, "releases",
        [("tag", "TAG", 20), ("name", "NAME", 40),
         ("kind", "KIND", 8), ("upd", "PUBLISHED", 9)],
        {"tag": lambda r: r["tag"], "name": lambda r: r["name"],
         "kind": lambda r: ("draft" if r["draft"] else
                            "pre" if r["prerelease"] else
                            "latest" if r["latest"] else ""),
         "upd": lambda r: _rel_at(r["published_at"])})


def _render_tags(results: list[dict]) -> None:
    _render_simple_table(
        results, "tags",
        [("name", "TAG", 40), ("sha", "SHA", 10)],
        {"name": lambda t: t["name"], "sha": lambda t: t["sha"]})


def _render_branches(results: list[dict]) -> None:
    _render_simple_table(
        results, "branches",
        [("name", "BRANCH", 50), ("sha", "SHA", 10), ("flags", "", 10)],
        {"name": lambda b: ("* " if b["default"] else "") + b["name"],
         "sha": lambda b: b["sha"],
         "flags": lambda b: "protected" if b["protected"] else ""})


def _render_pages(results: list[dict]) -> None:
    for entry in results:
        if entry["status"] != "ok":
            _print_repo_error(entry)
            continue
        pages = entry["pages"]
        if pages is None:
            print(f"  {c('bold', entry['repo'])}  "
                  f"{c('dim', 'no Pages site')}")
            continue
        parts = [c('cyan', pages['url'] or '-')]
        if pages.get('cname'):
            parts.append(c('dim', 'cname ' + pages['cname']))
        build = pages.get("latest_build") or {}
        if build:
            state = build.get("status") or "unknown"
            if build.get("error"):
                state = f"{state} ({build['error']})"
            parts.append(c('dim', 'build ' + state))
        print(f"  {c('bold', entry['repo'])}  " + "  ".join(parts))
    print()


_NOTIF_TYPES = {
    "PullRequest": ("PR", "cyan"),
    "Issue": ("issue", "green"),
    "CheckRun": ("check", "yellow"),
    "CheckSuite": ("check", "yellow"),
    "WorkflowRun": ("run", "yellow"),
    "Release": ("release", "blue"),
    "Discussion": ("disc", "blue"),
    "Commit": ("commit", "dim"),
    "RepositoryVulnerabilityAlert": ("security", "red"),
    "RepositoryInvitation": ("invite", "neon_pink"),
}


def _render_inbox(payload: dict) -> None:
    notes = payload["notifications"]
    if payload["status"] != "ok":
        print(c("red", f"  {payload.get('error') or payload['status']}"))
        return
    if not notes:
        print(c("dim", "\n  Inbox zero.\n"))
        return
    print()
    t = Table()
    t.add_column("un", " ", width=1, kind="preformatted", fit="fixed")
    t.add_column("repo", "REPO", width=24, kind="text", fit="shrink")
    t.add_column("kind", "KIND", width=8, kind="preformatted", fit="fixed")
    t.add_column("title", "TITLE", width=60, kind="text", fit="shrink")
    t.add_column("reason", "REASON", width=16, kind="text", fit="shrink")
    t.add_column("upd", "UPDATED", width=8, kind="text", fit="fixed")
    for note in notes:
        kind, color = _NOTIF_TYPES.get(note["type"],
                                       ((note["type"] or "?")[:8], "dim"))
        t.add_row({
            "un": c("cyan", "●") if note["unread"] else " ",
            "repo": note["repo"],
            "kind": c(color, kind),
            "title": note["title"],
            "reason": note["reason"].replace("_", " "),
            "upd": _rel_at(note["updated_at"]),
        })
    for line in t.render():
        print(line)
    print()


# ------------------------------------------------------------------ repo store

def _cmd_repo(args: list[str], json_view: bool) -> int:
    sub = args[0] if args else "list"
    rest = args[1:] if args else []
    err = error_stream(json_view)

    if sub in ("add", "remove", "rm", "search"):
        parsed = _parse_common(rest, json_view)
        if parsed is None:
            return 1
        opts, pos = parsed
        if not pos:
            print(c("red", f"Usage: swe gh repo {sub} <arg>"), file=err)
            return 1
    else:
        opts, pos = None, []

    if sub == "add":
        name = repos.normalize_repo_input(pos[0])
        if not name:
            print(c("red", f"Not a GitHub repo: {pos[0]}"), file=err)
            return 1
        env = gh.probe_env()
        if env.status != "ok":
            print(c("red", f"  {_env_error(env)}"), file=err)
            return 1
        check = gh.run_json(
            ["repo", "view", name, "--json", "nameWithOwner"])
        if check.status != "ok" or not isinstance(check.data, dict):
            print(c("red", f"Cannot reach {name}: "
                           f"{check.error or check.status}"), file=err)
            return 1
        name = check.data.get("nameWithOwner") or name
        pinned = repos.add_pinned(name)
        if json_view:
            return emit({"repo": name, "pinned": [r["name"] for r in pinned]})
        print(f"Pinned {c('cyan', name)}.")
        return 0

    if sub in ("remove", "rm"):
        name = repos.normalize_repo_input(pos[0]) or pos[0]
        pinned, removed = repos.remove_pinned(name)
        if json_view:
            return emit({"removed": removed, "repo": name,
                         "pinned": [r["name"] for r in pinned]})
        print(f"Unpinned {c('cyan', name)}." if removed
              else c("dim", f"{name} was not pinned."))
        return 0 if removed else 1

    if sub == "search":
        query = " ".join(pos)
        env = gh.probe_env()
        if env.status != "ok":
            print(c("red", f"  {_env_error(env)}"), file=err)
            return 1
        result = gh.run_json([
            "search", "repos", query, "--limit", str(_limit(opts, 15)),
            "--json", "fullName,description,stargazersCount,updatedAt"])
        if result.status != "ok":
            print(c("red", result.error or result.status), file=err)
            return 1
        found = [
            {"name": r.get("fullName"), "description": r.get("description"),
             "stars": r.get("stargazersCount"),
             "updated_at": r.get("updatedAt"),
             "url": f"https://github.com/{r.get('fullName')}"}
            for r in (result.data or []) if isinstance(r, dict)]
        if json_view:
            return emit({"query": query, "results": found})
        print()
        for r in found:
            stars = f"{r['stars']:,}" if isinstance(r["stars"], int) else "-"
            name = str(r["name"])
            print(f"  {c('cyan', name.ljust(40)[:40])} "
                  f"{c('dim', stars.rjust(8))}  "
                  f"{sanitize(truncate(r['description'] or '', 60))}")
        print(c("dim", f"\n  {len(found)} repos — add one with "
                       "`swe gh repo add <name>`\n"))
        return 0

    if sub != "list":
        print(c("red", f"Unknown: swe gh repo {sub}  "
                       "(list | add | remove | search)"), file=err)
        return 1

    pinned = repos.load_pinned()
    if json_view:
        return emit({"repos": pinned})
    if not pinned:
        print(c("dim", "\n  No repos pinned. "
                     "`swe gh repo add <owner/name>` to start.\n"))
        return 0
    print(f"\n{c('bold', 'Pinned repos')}\n")
    for r in pinned:
        print(f"  {c('cyan', r['name'])}  "
              f"{c('dim', _rel(r.get('added_at')))}")
    print()
    return 0


# ------------------------------------------------------------------ commands

_WEB_PAGES = {
    "pr": "pulls", "issue": "issues", "run": "actions",
    "pages": "settings/pages", "release": "releases",
    "tag": "tags", "branch": "branches",
}


def _open_web(kind: str, targets: list[str]) -> None:
    url = ("https://github.com/notifications" if kind == "inbox"
           else f"https://github.com/{targets[0]}/{_WEB_PAGES[kind]}"
           if targets else "https://github.com")
    webbrowser.open(url)


def _env_error(env: gh.GhEnv) -> str:
    if not env.installed:
        return "gh is not installed: brew install gh"
    if env.error:
        return f"gh is not responding ({env.error})"
    return "gh is not authenticated: run `gh auth login`"


def _env_payload(env: gh.GhEnv) -> dict:
    return {"status": env.status, "login": env.login or None,
            "error": env.error or None}


def _parse_common(rest: list[str],
                  json_view: bool) -> tuple[dict, list[str]] | None:
    """Split ``--repo=``/``--state=``/``--limit=``/``--web`` from positionals."""
    try:
        args = expand_value_flags(rest, _VALUE_FLAGS)
    except ValueError as exc:
        print(c("red", str(exc)), file=error_stream(json_view))
        return None
    opts = {"repo": None, "state": None, "limit": None,
            "web": False, "all": False}
    pos = []
    i = 0
    while i < len(args):
        if args[i] in _VALUE_FLAGS:
            opts[args[i][2:]] = args[i + 1]
            i += 2
        elif args[i] == "--web":
            opts["web"] = True
            i += 1
        elif args[i] == "--all":
            opts["all"] = True
            i += 1
        else:
            pos.append(args[i])
            i += 1
    return opts, pos


def _targets(opts: dict, json_view: bool) -> list[str] | None:
    """Resolve the repo list, or print why nothing can be queried."""
    if opts["repo"] and repos.normalize_repo_input(opts["repo"]) is None:
        msg = f"Not a GitHub repo: {opts['repo']}  (want owner/name or a URL)"
        if json_view:
            print(msg, file=error_stream(True))
        else:
            print(c("red", f"\n  {msg}\n"))
        return None
    names = repos.resolve(opts["repo"])
    if not names:
        msg = ("No repo to query: pass --repo=owner/name, run inside a "
               "checkout, or pin one with `swe gh repo add`.")
        if json_view:
            print(msg, file=error_stream(True))
        else:
            print(c("red", f"\n  {msg}\n"))
        return None
    return names


def _limit(opts: dict, default: int = 30) -> int:
    try:
        n = int(opts["limit"]) if opts["limit"] is not None else default
    except ValueError:
        return default
    return max(1, min(n, 200))


def _collect(cmd: str, opts: dict, json_view: bool, fetch):
    """Gate on gh + repo resolution, then fetch per repo.

    Returns ``(env, results)``: ``results`` is None when the run was
    blocked (message already printed), [] when ``--web`` opened instead
    of fetching.
    """
    env = gh.probe_env()
    if env.status != "ok":
        if json_view:
            print(_env_error(env), file=error_stream(True))
        else:
            print(c("red", f"\n  {_env_error(env)}\n"))
        return env, None
    names = _targets(opts, json_view)
    if names is None:
        return env, None
    if opts["web"]:
        _open_web(cmd, names)
        return env, []
    return env, [fetch(name, opts) for name in names]


def _emit_results(env: gh.GhEnv, results: list[dict]) -> int:
    return emit({"github": _env_payload(env), "results": results})


def _finish(env: gh.GhEnv, results, opts: dict,
            json_view: bool, render) -> int:
    """The tail every query shares: blocked → 1, --web → 0, else show."""
    if results is None:
        if json_view:
            _emit_results(env, [])
        return 1
    if opts["web"]:
        return 0
    if json_view:
        return _emit_results(env, results)
    render(results)
    return 0


def _list_command(cmd: str, fetch, render,
                  states: tuple[str, ...] = ()):
    """The bare/list shape shared by the one-fetch surfaces.

    ``fetch`` takes ``(repo_name, opts)`` so ``--state`` and ``--limit``
    reach it. ``states`` lists the legal ``--state`` values when the
    surface has them.
    """
    def handler(rest: list[str], json_view: bool) -> int:
        parsed = _parse_common(rest, json_view)
        if parsed is None:
            return 1
        opts, pos = parsed
        if pos and pos[0] == "list":
            pos = pos[1:]
        if pos:
            print(c("red", f"Unexpected args: {' '.join(pos)}"),
                  file=error_stream(json_view))
            return 1
        if states:
            state = (opts["state"] or "open").lower()
            if state not in states:
                print(c("red",
                        f"--state must be {', '.join(states[:-1])} or "
                        f"{states[-1]}"), file=error_stream(json_view))
                return 1
            opts["state"] = state
        env, results = _collect(cmd, opts, json_view, fetch)
        return _finish(env, results, opts, json_view, render)
    return handler


def _cmd_pr(rest: list[str], json_view: bool) -> int:
    parsed = _parse_common(rest, json_view)
    if parsed is None:
        return 1
    opts, pos = parsed
    sub = pos[0] if pos else "list"
    if len(pos) > 1:
        print(c("red", f"Unexpected args: {' '.join(pos[1:])}"),
              file=error_stream(json_view))
        return 1
    if sub == "status":
        env, results = _collect(
            "pr", opts, json_view, lambda name, _o: _fetch_pr_status(name))
        return _finish(env, results, opts, json_view, _render_pr_status)
    if sub != "list":
        print(c("red", f"Unknown: swe gh pr {sub}  (list | status)"),
              file=error_stream(json_view))
        return 1
    state = (opts["state"] or "open").lower()
    if state not in ("open", "closed", "merged", "all"):
        print(c("red", "--state must be open, closed, merged or all"),
              file=error_stream(json_view))
        return 1
    env, results = _collect(
        "pr", opts, json_view,
        lambda name, o: _fetch_prs(name, state, _limit(o)))
    return _finish(env, results, opts, json_view, _render_pr_table)


def _cmd_inbox(rest: list[str], json_view: bool) -> int:
    parsed = _parse_common(rest, json_view)
    if parsed is None:
        return 1
    opts, pos = parsed
    if pos:
        print(c("red", f"Unexpected args: {' '.join(pos)}"),
              file=error_stream(json_view))
        return 1
    env = gh.probe_env()
    if env.status != "ok":
        if json_view:
            print(_env_error(env), file=error_stream(True))
            emit({"github": _env_payload(env),
                  "status": env.status, "notifications": []})
        else:
            print(c("red", f"\n  {_env_error(env)}\n"))
        return 1
    if opts["web"]:
        _open_web("inbox", [])
        return 0
    payload = _fetch_inbox(_limit(opts, 50), fetch_all=opts["all"])
    if json_view:
        return emit({"github": _env_payload(env), **payload})
    _render_inbox(payload)
    return 0


def _print_overview(json_view: bool) -> int:
    env = gh.probe_env()
    pinned = repos.load_pinned()
    if json_view:
        return emit({"github": _env_payload(env), "repos": pinned})
    print(f"\n{c('bold', 'GitHub')}\n")
    if env.status == "ok":
        print(f"  {c('green', '●')} gh"
              f"{c('dim', f' as {env.login}') if env.login else ''}")
    else:
        print(f"  {c('red', '●')} "
              f"{'gh not installed: brew install gh' if not env.installed else 'not authenticated: gh auth login'}")
    print()
    if not pinned:
        print(c("dim", "  No repos pinned. "
                     "`swe gh repo add <owner/name>` to start.\n"))
    else:
        for r in pinned:
            print(f"  {c('cyan', r['name'])}")
        print()
    print(c("dim", "  swe gh pr | issue | run | pages | releases | tags | "
                   "branches | inbox | repo\n"))
    return 0


# ------------------------------------------------------------------ dispatch


def main(argv=None) -> int:
    args = list(argv or [])
    args, json_view = take_json_flag(args)
    if not args:
        return _print_overview(json_view)
    if args[0] in ("help", "-h", "--help"):
        from quiver.help_text import cmd_help
        return cmd_help(["gh"]) or 0

    sub, rest = args[0], args[1:]

    if sub == "repo":
        return _cmd_repo(rest, json_view)
    if sub in ("pr", "prs", "pulls"):
        return _cmd_pr(rest, json_view)
    if sub in ("issue", "issues"):
        return _list_command(
            "issue",
            lambda n, o: _fetch_issues(n, o["state"], _limit(o)),
            _render_issue_table,
            states=("open", "closed", "all"))(rest, json_view)
    if sub in ("run", "runs", "actions", "ci"):
        return _list_command(
            "run", lambda n, o: _fetch_runs(n, _limit(o, 15)),
            _render_run_table)(rest, json_view)
    if sub in ("pages", "page"):
        return _list_command(
            "pages", lambda n, _o: _fetch_pages(n),
            _render_pages)(rest, json_view)
    if sub in ("release", "releases"):
        return _list_command(
            "release", lambda n, o: _fetch_releases(n, _limit(o, 10)),
            _render_releases)(rest, json_view)
    if sub in ("tag", "tags"):
        return _list_command(
            "tag", lambda n, o: _fetch_tags(n, _limit(o, 20)),
            _render_tags)(rest, json_view)
    if sub in ("branch", "branches"):
        return _list_command(
            "branch", lambda n, o: _fetch_branches(n, _limit(o, 50)),
            _render_branches)(rest, json_view)
    if sub in ("inbox", "notifications", "notifs"):
        return _cmd_inbox(rest, json_view)

    print(c("red", f"Unknown: swe gh {sub}"), file=error_stream(json_view))
    print(c("dim", "  swe gh pr | issue | run | pages | releases | tags | "
                   "branches | inbox | repo"),
          file=error_stream(json_view))
    return 1
