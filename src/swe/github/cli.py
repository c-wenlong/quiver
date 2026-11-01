"""``swe gh`` — GitHub surfaces over ``gh``: PRs, issues, actions, inbox.

Reads cover the dashboard; a few deliberate writes exist too — merging
a PR, re-running a workflow, marking notifications read — all explicit
commands, never side effects of a read.

Repo resolution is ``--repo=`` first, the working directory's remote
second, then every pinned repo — so a bare ``swe gh pr`` is the dashboard.
"""

import re
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from swe.console import c, sanitize, sanitize_document, truncate
from swe.flags import expand_value_flags
from swe.github import catalog, gh, manage, repos
from swe.github import issues as issue_mod
from swe.json_output import emit, error_stream, take_json_flag
from swe.table import Table

_VALUE_FLAGS = {"--repo", "--state", "--limit", "--method"}

_PR_FIELDS = (
    "number,title,state,isDraft,author,headRefName,headRepositoryOwner,"
    "baseRefName,labels,mergeable,reviewDecision,statusCheckRollup,"
    "updatedAt,url"
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
_PR_DETAIL_FIELDS = (
    "number,title,state,isDraft,author,headRefName,baseRefName,body,url,"
    "mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,"
    "latestReviews,reviewRequests,additions,deletions,changedFiles,"
    "labels,createdAt,updatedAt,mergedAt,autoMergeRequest"
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
        # Whose fork the head branch lives on; "" when gh can't say.
        "head_owner":
            (pr.get("headRepositoryOwner") or {}).get("login") or "",
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


def _check_row(item: dict) -> dict:
    """One statusCheckRollup entry, normalized across the two shapes:
    CheckRun carries status/conclusion/detailsUrl + workflowName, while
    a StatusContext carries state/targetUrl + context for the name. The
    ``state`` field folds the entry to rollup_checks' vocabulary so
    pending contexts and odd conclusions still draw a warning sign."""
    workflow = item.get("workflowName")
    if not workflow and isinstance(item.get("workflow"), dict):
        workflow = item["workflow"].get("name")
    is_context = (item.get("__typename") == "StatusContext"
                  or ("state" in item and "status" not in item))
    if is_context:
        ctx = str(item.get("state") or "").lower()
        status = "completed" if ctx in ("success", "failure", "error") \
            else "queued"
        conclusion = ctx if status == "completed" else None
    else:
        status = str(item.get("status") or "").lower() or "completed"
        conclusion = str(item.get("conclusion") or "").lower() or None
    state = rollup_checks([item])
    return {
        "name": item.get("name") or item.get("context") or "",
        "workflow": workflow or "",
        "status": status,
        "conclusion": conclusion,
        "state": "none" if state == "unknown" else state,
        "url": item.get("detailsUrl") or item.get("targetUrl") or "",
    }


def _pr_detail(pr: dict, repo: str) -> dict:
    """The list row plus everything the detail pane draws: body, the
    per-check rows behind the rollup, reviews, and merge state."""
    requested = []
    for r in pr.get("reviewRequests") or []:
        if isinstance(r, dict):
            # A requested reviewer can be a user (login) or a team
            # (slug/name) — whichever name it carries is the label.
            name = r.get("login") or r.get("slug") or r.get("name")
            if name:
                requested.append(str(name))
    automerge = pr.get("autoMergeRequest")
    return {
        **_pr_row(pr),
        "repo": repo,
        "body": pr.get("body") or "",
        "merge_state": str(pr.get("mergeStateStatus")
                           or "").lower() or "unknown",
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changedFiles"),
        "check_runs": [_check_row(x)
                       for x in (pr.get("statusCheckRollup") or [])
                       if isinstance(x, dict)],
        "reviews": [
            {"author": _login(r.get("author")),
             "state": str(r.get("state") or "").lower(),
             "submitted_at": r.get("submittedAt")}
            for r in (pr.get("latestReviews") or [])
            if isinstance(r, dict)],
        "review_requests": requested,
        "auto_merge": ({
            "method": str(automerge.get("mergeMethod") or "").lower(),
            "enabled_at": automerge.get("enabledAt"),
        } if isinstance(automerge, dict) else None),
        "created_at": pr.get("createdAt"),
        "merged_at": pr.get("mergedAt"),
    }


def _comment_count(value) -> int:
    """``comments`` across the two gh shapes: ``gh issue list`` returns
    the comment objects themselves, GraphQL search returns
    ``{totalCount}`` — the row always carries the number."""
    if isinstance(value, dict):
        return int(value.get("totalCount") or 0)
    if isinstance(value, list):
        return len(value)
    return int(value or 0)


def _issue_row(issue: dict) -> dict:
    return {
        "number": issue.get("number"),
        "title": issue.get("title") or "",
        "state": str(issue.get("state") or "").lower(),
        "author": _login(issue.get("author")),
        "labels": _label_names(issue.get("labels")),
        "assignees": [_login(a) for a in (issue.get("assignees") or [])],
        "comments": _comment_count(issue.get("comments")),
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


# ``subject.type`` folded to the inbox row's vocabulary; anything else
# (discussions, commits, advisories, invitations) lands on ``other``.
_INBOX_SUBJECTS = {
    "PullRequest": "pr",
    "Issue": "issue",
    "CheckRun": "ci",
    "CheckSuite": "ci",
    "WorkflowRun": "ci",
    "Release": "release",
}

# gh's ``reason`` names folded to the inbox row's vocabulary. Reasons
# outside the map pass through verbatim — a new GitHub reason reads
# truthfully instead of pretending to be a comment.
_INBOX_REASONS = {
    "review_requested": "review_requested",
    "assign": "assignee",
    "mention": "mention",
    "team_mention": "mention",
    "ci_activity": "ci_fail",
    "comment": "comment",
    "author": "comment",
    "state_change": "comment",
    "subscribed": "comment",
    "manual": "comment",
}

# The reasons worth a push: review requests, assignments, mentions and
# red CI need the owner; plain comments on subscribed threads stay
# inbox-only.
PUSH_REASONS = frozenset(
    {"review_requested", "assignee", "mention", "ci_fail"})


def inbox_row(note: dict) -> dict | None:
    """One ``_notification_row`` as the ``/v1/inbox`` ``github`` item.

    ``key`` is what ``POST /v1/inbox/dismiss`` takes; ``last_active_at``
    the epoch seconds its age comparisons run on. A notification with
    no id has no thread to mark read or dismiss, so it yields no row.
    """
    tid = str(note.get("id") or "")
    if not tid:
        return None
    reason = str(note.get("reason") or "")
    return {
        "kind": "github",
        "key": f"gh:{tid}",
        "harness": "github",
        "agent": "GitHub",
        "status": "github",
        "repo": str(note.get("repo") or ""),
        "subject_type": _INBOX_SUBJECTS.get(note.get("type"), "other"),
        "title": str(note.get("title") or ""),
        "reason": _INBOX_REASONS.get(reason, reason or "comment"),
        "url": str(note.get("url") or ""),
        "gh_thread_id": tid,
        "updated_at": note.get("updated_at"),
        "last_active_at": _parse_iso(note.get("updated_at")) or 0.0,
        "unread": bool(note.get("unread", True)),
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

    A later page that fails keeps the rows already read, but the answer
    says so: ``ok`` with ``error`` set means partial, and the callers
    mark the listing ``partial`` rather than pass it off as complete.
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
                # A later page failed: keep what we already have.
                return gh.GhResult("ok", data=items[:limit], error=(
                    f"page {page} failed: {result.error or result.status}"))
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
        # A non-list reply on a clean exit is an anomaly, not an empty
        # listing — an eaten `gh` reply must read as a failure, not as
        # "no rows", or a real transient blip lies to the board.
        if not isinstance(result.data, list):
            return {"repo": repo, "status": "upstream-error", key: [],
                    "error": "gh returned an unexpected reply"}
        rows = [shape(item) for item in result.data
                if isinstance(item, dict)]
        out = {"repo": repo, "status": "ok", key: rows}
        if result.error:  # rows from the pages that answered
            out.update(partial=True, error=result.error)
        return out
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
    out = {
        "status": "ok",
        "notifications": [_notification_row(i)
                          for i in (result.data or [])
                          if isinstance(i, dict)],
    }
    if result.error:  # rows from the pages that answered
        out.update(partial=True, error=result.error)
    return out


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


def _render_pr_detail(pr: dict) -> None:
    glyph, color = _pr_glyph(pr)
    print(f"\n  {c(color, glyph)} {c('bold', sanitize(pr['title']))}")
    print(f"  {c('cyan', '#' + str(pr['number']))} "
          f"{c('dim', pr['repo'])}  "
          f"{c('cyan', pr['branch'])} → {pr['base']}  "
          f"{c('dim', _rel(_parse_iso(pr.get('updated_at'))))}")
    print(f"  {c('dim', pr['url'])}")
    meta = []
    if pr.get("author") and pr["author"] != "-":
        meta.append(f"by {pr['author']}")
    if pr.get("additions") is not None:
        meta.append(f"+{pr['additions']}/-{pr['deletions']}"
                    f" in {pr.get('changed_files') or 0} files")
    if pr.get("mergeable"):
        meta.append(pr["mergeable"])
    if pr.get("merge_state") and pr["merge_state"] != "unknown":
        meta.append(pr["merge_state"])
    if pr.get("auto_merge"):
        meta.append(f"auto-merge: {pr['auto_merge']['method']}")
    if meta:
        print(f"  {c('dim', ' · '.join(meta))}")
    checks = pr.get("check_runs") or []
    if checks:
        print(f"\n  {c('dim', 'Checks')}")
        for chk in checks:
            ci_glyph, ci_color = _checks_glyph(chk.get("state") or "none")
            workflow = f"  {chk['workflow']}" if chk.get("workflow") else ""
            print(f"    {c(ci_color, ci_glyph)} "
                  f"{sanitize(chk['name'])}{c('dim', workflow)}")
    reviews = pr.get("reviews") or []
    requested = pr.get("review_requests") or []
    if reviews or requested:
        print(f"\n  {c('dim', 'Reviews')}")
        for r in reviews:
            print(f"    {r['author']}  {c('dim', r['state'])}")
        if requested:
            print(f"    {c('dim', 'requested: ' + ', '.join(requested))}")
    body = (pr.get("body") or "").strip()
    if body:
        print(f"\n  {c('dim', truncate(sanitize(body), 400))}")
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

    if sub in ("add", "remove", "rm", "search", "reorder"):
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
        added = manage.add_repo(name)
        if added.status != "ok":
            print(c("red", added.error), file=err)
            return 1
        name = added.data["repo"]
        if json_view:
            return emit(added.data)
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

    if sub == "reorder":
        names = [repos.normalize_repo_input(p) or p for p in pos]
        pinned = repos.reorder_pinned(names)
        if json_view:
            return emit({"pinned": [r["name"] for r in pinned]})
        print("Pin order: "
              + ", ".join(c("cyan", r["name"]) for r in pinned))
        return 0

    if sub == "search":
        query = " ".join(pos)
        found = manage.search_repos(query, _limit(opts, 15), gh.probe_env,
                                    everywhere=bool(opts["all"]))
        if found["status"] != "ok":
            indent = "  " if found["env_blocked"] else ""
            print(c("red", indent + found["error"]), file=err)
            return 1
        if json_view:
            return emit({"query": query, "results": found["results"],
                         "source": found["source"]})
        if found["source"] == "catalog":
            hint = ("repos — sync in Settings refreshes the catalog, or "
                    "search all of GitHub with --all")
        elif found["scoped"]:
            hint = ("repos — add one with `swe gh repo add <name>`, or "
                    "search all of GitHub with --all")
        else:
            hint = "repos — add one with `swe gh repo add <name>`"
        _print_repo_hits(found["results"], hint)
        return 0

    if sub != "list":
        print(c("red", f"Unknown: swe gh repo {sub}  "
                       "(list | add | remove | reorder | search)"), file=err)
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


def _print_repo_hits(found: list[dict], hint: str) -> None:
    print()
    for r in found:
        stars = f"{r['stars']:,}" if isinstance(r["stars"], int) else "-"
        name = str(r["name"])
        print(f"  {c('cyan', name.ljust(40)[:40])} "
              f"{c('dim', stars.rjust(8))}  "
              f"{sanitize(truncate(r['description'] or '', 60))}")
    print(c("dim", f"\n  {len(found)} {hint}\n"))


# -------------------------------------------------------------- gh sync

def _cmd_sync(rest: list[str], json_view: bool) -> int:
    err = error_stream(json_view)
    if rest:
        print(c("red", "Usage: swe gh sync [--json]"), file=err)
        return 1
    env = gh.probe_env()
    if env.status != "ok":
        print(c("red", f"  {_env_error(env)}"), file=err)
        return 1
    summary = manage.sync_catalog(env)
    if summary["error"]:
        print(c("red", f"  {summary['error']}"), file=err)
        return 1
    failed = summary["failed"]
    if json_view:
        return emit({"repos": summary["repos"],
                     "synced_at": summary["synced_at"],
                     "owners": summary["owners"],
                     "failed": failed})
    print()
    if failed and failed[0]["owner"] == "<orgs>":
        print(f"  {c('red', '●')} {c('cyan', '<orgs>'.ljust(40)[:40])} "
              f"{failed[0]['error']}")
    for e in summary["listed"]:
        fine = e["status"] == "ok"
        detail = (f"{e['repos']} repos" if fine
                  else e["error"] or e["status"])
        dot = c("green" if fine else "red", "●")
        print(f"  {dot} {c('cyan', e['owner'])}  {c('dim', detail)}")
    print(c("dim", f"\n  {summary['repos']} repos in the catalog"
                   " — `swe gh repo search` reads it now.\n"))
    return 0 if not failed else 1


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


_env_error = gh.env_error


def _env_payload(env: gh.GhEnv) -> dict:
    return {"status": env.status, "login": env.login or None,
            "error": env.error or None}


def _parse_common(rest: list[str], json_view: bool,
                  extra: frozenset[str] = frozenset(),
                  ) -> tuple[dict, list[str]] | None:
    """Split ``--repo=``/``--state=``/``--limit=``/``--web`` from positionals.

    ``extra`` adds value flags one command takes (``--body`` for
    ``issue comment``); each lands in ``opts`` under its bare name. A
    value is everything after the first ``=`` of its one argv token, so
    free text such as ``--body=a --x=y`` arrives whole.
    """
    names = _VALUE_FLAGS | extra
    try:
        args = expand_value_flags(rest, names)
    except ValueError as exc:
        print(c("red", str(exc)), file=error_stream(json_view))
        return None
    opts = {"repo": None, "state": None, "limit": None, "method": None,
            "web": False, "all": False, "auto": False, "failed": False}
    opts.update({name[2:]: None for name in extra})
    pos = []
    i = 0
    while i < len(args):
        if args[i] in names:
            opts[args[i][2:]] = args[i + 1]
            i += 2
        elif args[i] == "--web":
            opts["web"] = True
            i += 1
        elif args[i] == "--all":
            opts["all"] = True
            i += 1
        elif args[i] == "--auto":
            opts["auto"] = True
            i += 1
        elif args[i] == "--failed":
            opts["failed"] = True
            i += 1
        else:
            pos.append(args[i])
            i += 1
    return opts, pos


def _targets(opts: dict,
             json_view: bool) -> tuple[list[str] | None, str | None]:
    """Resolve the repo list, or print why nothing can be queried.
    The message comes back too so --json callers can emit it."""
    if opts["repo"] and not all(
            repos.normalize_repo_input(part)
            for part in opts["repo"].split(",")):
        msg = f"Not a GitHub repo: {opts['repo']}  (want owner/name or a URL)"
        if json_view:
            print(msg, file=error_stream(True))
        else:
            print(c("red", f"\n  {msg}\n"))
        return None, msg
    names = repos.resolve(opts["repo"])
    if not names:
        msg = ("No repo to query: pass --repo=owner/name, run inside a "
               "checkout, or pin one with `swe gh repo add`.")
        if json_view:
            print(msg, file=error_stream(True))
        else:
            print(c("red", f"\n  {msg}\n"))
        return None, msg
    return names, None


def _resolve_one(opts: dict,
                 json_view: bool) -> tuple[str | None, str | None]:
    """One repo for one-object commands (view/merge/rerun): --repo or
    the checkout wins; several pins is ambiguous rather than wrong."""
    names, res_err = _targets(opts, json_view)
    if names is None:
        return None, res_err
    if len(names) > 1:
        msg = ("This command takes one repo — pass --repo=owner/name "
               f"(resolved {len(names)} repos).")
        if json_view:
            print(msg, file=error_stream(True))
        else:
            print(c("red", f"\n  {msg}\n"))
        return None, msg
    return names[0], None


def _limit(opts: dict, default: int = 30) -> int:
    try:
        n = int(opts["limit"]) if opts["limit"] is not None else default
    except ValueError:
        return default
    return max(1, min(n, 200))


def _fan(names: list[str], fetch) -> list:
    """One ``gh`` subprocess per repo, overlapped, at most eight at once
    — GitHub latency dominates, so serial fetches cost pins × per-call
    seconds. The hub's GitHub surfaces fan out through this too
    (``shared.fan``). No names, no pool: an empty pool can't be built."""
    if len(names) <= 1:
        return [fetch(n) for n in names]
    with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
        return list(pool.map(fetch, names))


def _env_after(results: list[dict]) -> gh.GhEnv:
    """The probe exists to classify failures; a clean sweep already
    proves gh worked, so it costs no subprocess at all."""
    if all(r["status"] == "ok" for r in results):
        # Successful fetches prove gh is installed and authed — spelling
        # it out keeps `installed` off a live `shutil.which`.
        return gh.GhEnv(installed=True, authenticated=True)
    return gh.probe_env()


def _collect_json(cmd: str, opts: dict, fetch):
    """The app's path: fetch first, probe only to classify a failure.

    A successful fetch is itself the auth proof — paying `auth status`
    on every poll made a one-repo board cost ~1s of probe per surface.
    """
    names, _res_err = _targets(opts, True)
    if names is None:
        return gh.probe_env(), None
    if opts["web"]:
        _open_web(cmd, names)
        return gh.probe_env(), []
    results = _fan(names, lambda name: fetch(name, opts))
    env = _env_after(results)
    if env.status != "ok":
        print(_env_error(env), file=error_stream(True))
        return env, None
    return env, results


def _collect(cmd: str, opts: dict, json_view: bool, fetch):
    """Gate on gh + repo resolution, then fetch per repo.

    Returns ``(env, results)``: ``results`` is None when the run was
    blocked (message already printed), [] when ``--web`` opened instead
    of fetching.
    """
    if json_view:
        return _collect_json(cmd, opts, fetch)
    env = gh.probe_env()
    if env.status != "ok":
        print(c("red", f"\n  {_env_error(env)}\n"))
        return env, None
    names, _res_err = _targets(opts, json_view)
    if names is None:
        return env, None
    if opts["web"]:
        _open_web(cmd, names)
        return env, []
    return env, _fan(names, lambda name: fetch(name, opts))


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
    if sub in ("view", "merge", "worktree", "checkout"):
        if len(pos) != 2:
            print(c("red", f"Usage: swe gh pr {sub} <number|url>"),
                  file=error_stream(json_view))
            return 1
        if sub == "view":
            return _cmd_pr_view(pos[1], opts, json_view)
        if sub == "merge":
            return _cmd_pr_merge(pos[1], opts, json_view)
        return _cmd_pr_worktree(pos[1], opts, json_view)
    if len(pos) > 1:
        print(c("red", f"Unexpected args: {' '.join(pos[1:])}"),
              file=error_stream(json_view))
        return 1
    if sub == "status":
        env, results = _collect(
            "pr", opts, json_view, lambda name, _o: _fetch_pr_status(name))
        return _finish(env, results, opts, json_view, _render_pr_status)
    if sub != "list":
        print(c("red",
                f"Unknown: swe gh pr {sub}  (list | status | view | merge "
                "| worktree)"),
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


def _env_emit(env: gh.GhEnv) -> dict:
    """Every one-object payload carries the probe result so the app can
    tell a blocked env from a fetch failure without a second call."""
    return {"github": _env_payload(env)}


def _cmd_pr_view(ref: str, opts: dict, json_view: bool) -> int:
    err = error_stream(json_view)
    env = gh.probe_env()
    if env.status != "ok":
        msg = _env_error(env)
        print(c("red", f"  {msg}"), file=err)
        if json_view:
            emit({**_env_emit(env), "repo": None, "status": env.status,
                  "pr": None, "error": msg})
        return 1
    repo, res_err = _resolve_one(opts, json_view)
    if repo is None:
        if json_view:
            emit({**_env_emit(env), "repo": None, "status": "error",
                  "pr": None, "error": res_err})
        return 1
    result = gh.run_json(
        ["pr", "view", ref, "--repo", repo, "--json", _PR_DETAIL_FIELDS],
        timeout=60)
    if result.status != "ok" or not isinstance(result.data, dict):
        msg = result.error or "gh returned an invalid pull request"
        print(c("red", f"  {msg}"), file=err)
        if json_view:
            emit({**_env_emit(env), "repo": repo, "status": result.status,
                  "pr": None, "error": msg})
        return 1
    detail = _pr_detail(result.data, repo)
    if json_view:
        return emit({**_env_emit(env), "repo": repo, "status": "ok",
                     "pr": detail})
    _render_pr_detail(detail)
    return 0


_MERGE_METHODS = {"squash": "--squash", "merge": "--merge",
                  "rebase": "--rebase"}


def merge_pr(repo: str, ref: str, method: str, auto: bool) -> gh.GhResult:
    """``gh pr merge <ref> --repo <repo> --<method> [--auto]``: the one
    merge call, shared by ``swe gh pr merge`` and the web app's PR
    sheet. ``method`` must be a ``_MERGE_METHODS`` key."""
    args = ["pr", "merge", ref, "--repo", repo, _MERGE_METHODS[method]]
    if auto:
        args.append("--auto")
    return gh.run_gh(args, timeout=60)


def _cmd_pr_merge(ref: str, opts: dict, json_view: bool) -> int:
    err = error_stream(json_view)
    method = (opts["method"] or "squash").lower()
    if method not in _MERGE_METHODS:
        print(c("red", "--method must be squash, merge or rebase"),
              file=err)
        return 1
    env = gh.probe_env()
    if env.status != "ok":
        msg = _env_error(env)
        print(c("red", f"  {msg}"), file=err)
        if json_view:
            emit({**_env_emit(env), "repo": None, "ref": ref,
                  "method": method, "auto": bool(opts["auto"]),
                  "merged": False, "error": msg})
        return 1
    repo, res_err = _resolve_one(opts, json_view)
    if repo is None:
        if json_view:
            emit({**_env_emit(env), "repo": None, "ref": ref,
                  "method": method, "auto": bool(opts["auto"]),
                  "merged": False, "error": res_err})
        return 1
    result = merge_pr(repo, ref, method, bool(opts["auto"]))
    if result.status != "ok":
        msg = result.error or result.status
        if json_view:
            emit({**_env_emit(env), "repo": repo, "ref": ref,
                  "method": method, "auto": bool(opts["auto"]),
                  "merged": False, "error": msg})
        else:
            print(c("red", f"  Merge failed: {msg}\n"))
        return 1
    # --auto arm's the merge for later; it does not merge now.
    merged = not opts["auto"]
    if json_view:
        return emit({**_env_emit(env), "repo": repo, "ref": ref,
                     "method": method, "auto": bool(opts["auto"]),
                     "merged": merged})
    done = (f"Auto-merge armed for {ref} ({method})." if opts["auto"]
            else f"Merged {ref} in {repo} ({method}).")
    print(c("green", f"  {done}\n"))
    return 0


_run_git = manage.run_git


def _pr_number(ref: str) -> str | None:
    """The numeric part of a PR ref: bare digits or a /pull/N URL."""
    if ref.isdigit():
        return ref
    match = re.search(r"/pull/(\d+)", ref)
    return match.group(1) if match else None


def _cmd_pr_worktree(ref: str, opts: dict, json_view: bool) -> int:
    """A detached checkout of the PR's head (``manage.pr_worktree``)."""
    err = error_stream(json_view)
    number = _pr_number(ref)
    if number is None:
        print(c("red", "Want a PR number or its /pull/<N> URL"), file=err)
        return 1
    env = gh.probe_env()
    if env.status != "ok":
        if json_view:
            emit({**_env_emit(env), "repo": None, "pr": int(number),
                  "path": None, "created": False, "error": _env_error(env)})
        else:
            print(c("red", f"  {_env_error(env)}"), file=err)
        return 1
    repo, res_err = _resolve_one(opts, json_view)
    if repo is None:
        if json_view:
            emit({**_env_emit(env), "repo": None, "pr": int(number),
                  "path": None, "created": False, "error": res_err})
        return 1

    made = manage.pr_worktree(repo, number)
    if made.status != "ok":
        if json_view:
            emit({**_env_emit(env), "repo": repo, "pr": int(number),
                  "path": None, "created": False, "error": made.error})
        else:
            print(c("red", f"  {made.error}\n"))
        return 1
    path, head, created = made.data["path"], made.data["head"], made.data["created"]

    if json_view:
        return emit({**_env_emit(env), "repo": repo, "pr": int(number),
                     "path": path, "head": head, "created": created})
    state = "checked out" if created else "already at"
    print(f"  {c('green', repo + '#' + number)} {state} "
          f"{c('cyan', path)}"
          + (f"  {c('dim', '@ ' + head)}" if head else "") + "\n")
    return 0


# ------------------------------------------------------------------ issue


_ISSUE_ONE = ("view", "comment", "worktree")


def _cmd_issue(rest: list[str], json_view: bool) -> int:
    """``swe gh issue [list]`` lists; ``view|comment|worktree <N|url>``
    act on one issue (``issues.py`` holds the shared shaping)."""
    parsed = _parse_common(rest, json_view,
                           extra=frozenset({"--body", "--body-file"}))
    if parsed is None:
        return 1
    opts, pos = parsed
    if not pos or pos[0] not in _ISSUE_ONE:
        # The list keeps its own parse, so it reads exactly as before.
        return _list_command(
            "issue",
            lambda n, o: _fetch_issues(n, o["state"], _limit(o)),
            _render_issue_table,
            states=("open", "closed", "all"))(rest, json_view)
    sub = pos[0]
    err = error_stream(json_view)
    if len(pos) != 2:
        extra = (" --body=<text> | --body-file=<path|->"
                 if sub == "comment" else "")
        print(c("red", f"Usage: swe gh issue {sub} <number|url>{extra}"),
              file=err)
        return 1
    number = issue_mod.issue_number(pos[1])
    if number is None:
        print(c("red", "Want an issue number or its /issues/<N> URL"),
              file=err)
        return 1
    if not opts["repo"] and "/issues/" in pos[1]:
        # The URL names its repo; --repo still wins when given.
        opts["repo"] = pos[1]
    if sub != "comment" and (opts["body"] is not None
                             or opts["body-file"] is not None):
        print(c("red", "--body goes with swe gh issue comment"), file=err)
        return 1
    if sub == "view":
        return _cmd_issue_view(number, opts, json_view)
    if sub == "comment":
        return _cmd_issue_comment(number, opts, json_view)
    return _cmd_issue_worktree(number, opts, json_view)


def _issue_gate(opts: dict, json_view: bool, blocked) -> tuple:
    """Probe ``gh`` and resolve the one repo. Returns ``(env, repo)``;
    ``repo`` is None when blocked, after ``blocked(env, repo, msg)`` has
    emitted the command's own failure envelope under ``--json``."""
    err = error_stream(json_view)
    env = gh.probe_env()
    if env.status != "ok":
        msg = _env_error(env)
        print(c("red", f"  {msg}"), file=err)
        if json_view:
            blocked(env, None, msg)
        return env, None
    repo, res_err = _resolve_one(opts, json_view)
    if repo is None and json_view:
        blocked(env, None, res_err)
    return env, repo


def _render_issue_detail(issue: dict, repo: str) -> None:
    open_ = issue["state"] == "open"
    glyph, color = ("●", "green") if open_ else ("✓", "dim")
    print(f"\n  {c(color, glyph)} {c('bold', sanitize(issue['title']))}")
    meta = [issue["state"] or "-"]
    if issue.get("author") and issue["author"] != "-":
        meta.append(f"by {issue['author']}")
    meta.append(_rel_at(issue.get("updated_at")))
    print(f"  {c('cyan', '#' + str(issue['number']))} {c('dim', repo)}  "
          f"{c('dim', ' · '.join(meta))}")
    print(f"  {c('dim', issue['url'])}")
    if issue["labels"]:
        print(f"  {c('dim', 'labels:')} {', '.join(issue['labels'])}")
    if issue["assignees"]:
        print(f"  {c('dim', 'assignees:')} {', '.join(issue['assignees'])}")
    body = (issue.get("body") or "").strip()
    print()
    for line in (sanitize_document(body) if body
                 else c("dim", "(no description)")).splitlines():
        print(f"  {line}")
    comments = issue.get("comments") or []
    if comments:
        print(f"\n  {c('dim', f'Comments ({len(comments)})')}")
        for item in comments:
            print(f"\n    {c('cyan', item['author'])}  "
                  f"{c('dim', _rel_at(item.get('created_at')))}")
            for line in truncate(sanitize_document(item["body"].strip()),
                                 600).splitlines():
                print(f"    {line}")
    print(f"\n  {c('dim', 'resolve on ' + issue['branch'] + ': swe gh issue worktree ' + str(issue['number']))}\n")


def _cmd_issue_view(number: int, opts: dict, json_view: bool) -> int:
    def blocked(env, repo, msg):
        emit({**_env_emit(env), "repo": repo, "status": env.status
              if env.status != "ok" else "error", "issue": None,
              "error": msg})

    env, repo = _issue_gate(opts, json_view, blocked)
    if repo is None:
        return 1
    result = issue_mod.fetch(repo, number)
    if result.status != "ok":
        msg = result.error or "gh returned an invalid issue"
        print(c("red", f"  {msg}"), file=error_stream(json_view))
        if json_view:
            emit({**_env_emit(env), "repo": repo, "status": result.status,
                  "issue": None, "error": msg})
        return 1
    if json_view:
        return emit({**_env_emit(env), "repo": repo, "status": "ok",
                     "issue": result.data})
    _render_issue_detail(result.data, repo)
    return 0


def _cmd_issue_comment(number: int, opts: dict, json_view: bool) -> int:
    """Post ``--body`` as a comment. gh reads the body from stdin, so any
    text survives; ``--body=`` itself keeps everything after its ``=``.
    ``--body-file=-`` reads the body from our own stdin instead (a path
    reads that file), so a caller never puts the text on argv, where
    every process on the machine can read it."""
    def blocked(env, repo, msg):
        emit({**_env_emit(env), "repo": repo, "status": env.status
              if env.status != "ok" else "error", "issue": number,
              "comment": None, "error": msg})

    env, repo = _issue_gate(opts, json_view, blocked)
    if repo is None:
        return 1
    body = opts["body"] or ""
    body_file = opts.get("body-file")
    if body_file is not None:
        msg = None
        if opts["body"] is not None:
            msg = "Pass --body or --body-file, not both"
        else:
            try:
                body = (sys.stdin.read() if body_file == "-" else
                        Path(body_file).expanduser().read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                where = "stdin" if body_file == "-" else body_file
                msg = f"Can't read {where}: {exc}"
        if msg:
            print(c("red", f"  {msg}"), file=error_stream(json_view))
            if json_view:
                blocked(env, repo, msg)
            return 1
    if not body.strip():
        msg = "The comment is empty: pass --body=<text>"
        print(c("red", f"  {msg}"), file=error_stream(json_view))
        if json_view:
            blocked(env, repo, msg)
        return 1
    result = issue_mod.post_comment(repo, number, body, env.login)
    if result.status != "ok":
        msg = result.error or result.status
        print(c("red", f"  Comment failed: {msg}"),
              file=error_stream(json_view))
        if json_view:
            emit({**_env_emit(env), "repo": repo, "status": result.status,
                  "issue": number, "comment": None, "error": msg})
        return 1
    if json_view:
        return emit({**_env_emit(env), "repo": repo, "status": "ok",
                     "issue": number, "comment": result.data})
    where = result.data["url"] or f"{repo}#{number}"
    print(c("green", f"  Commented on {repo}#{number}: ") + where + "\n")
    return 0


def _cmd_issue_worktree(number: int, opts: dict, json_view: bool) -> int:
    """A branch ``quiver/issue-<n>`` off the default branch, checked out
    under ``~/.quiver/worktrees`` (``manage.issue_worktree``)."""
    def blocked(env, repo, msg):
        emit({**_env_emit(env), "repo": repo, "issue": number,
              "path": None, "branch": manage.issue_branch(number),
              "created": False, "error": msg})

    env, repo = _issue_gate(opts, json_view, blocked)
    if repo is None:
        return 1
    made = manage.issue_worktree(repo, number)
    if made.status != "ok":
        if json_view:
            blocked(env, repo, made.error)
        else:
            print(c("red", f"  {made.error}\n"))
        return 1
    data = made.data
    if json_view:
        return emit({**_env_emit(env), "repo": repo, "issue": number,
                     **data})
    state = "checked out" if data["created"] else "already at"
    print(f"  {c('green', f'{repo}#{number}')} {c('cyan', data['branch'])} "
          f"{state} {c('cyan', data['path'])}  "
          f"{c('dim', 'off ' + data['base'])}\n")
    return 0


def _cmd_run(rest: list[str], json_view: bool) -> int:
    parsed = _parse_common(rest, json_view)
    if parsed is None:
        return 1
    opts, pos = parsed
    if pos and pos[0] == "rerun":
        if len(pos) != 2:
            print(c("red", "Usage: swe gh run rerun <run-id>"),
                  file=error_stream(json_view))
            return 1
        return _cmd_run_rerun(pos[1], opts, json_view)
    if pos and pos[0] == "list":
        pos = pos[1:]
    if pos:
        print(c("red", f"Unexpected args: {' '.join(pos)}"),
              file=error_stream(json_view))
        return 1
    env, results = _collect(
        "run", opts, json_view, lambda n, o: _fetch_runs(n, _limit(o, 15)))
    return _finish(env, results, opts, json_view, _render_run_table)


def rerun_run(repo: str, run_id: str, failed_only: bool) -> gh.GhResult:
    """``gh run rerun <id> --repo <repo> [--failed]``: the one re-run
    call, shared by ``swe gh run rerun`` and the web app's Actions rows.
    ``run_id`` must be digits."""
    args = ["run", "rerun", run_id, "--repo", repo]
    if failed_only:
        args.append("--failed")
    return gh.run_gh(args, timeout=60)


def _cmd_run_rerun(run_id: str, opts: dict, json_view: bool) -> int:
    err = error_stream(json_view)
    if not run_id.isdigit():
        print(c("red", "Want the numeric run id from `swe gh run`"),
              file=err)
        return 1
    env = gh.probe_env()
    if env.status != "ok":
        msg = _env_error(env)
        print(c("red", f"  {msg}"), file=err)
        if json_view:
            emit({**_env_emit(env), "repo": None, "run": int(run_id),
                  "rerun": False, "error": msg})
        return 1
    repo, res_err = _resolve_one(opts, json_view)
    if repo is None:
        if json_view:
            emit({**_env_emit(env), "repo": None, "run": int(run_id),
                  "rerun": False, "error": res_err})
        return 1
    result = rerun_run(repo, run_id, bool(opts["failed"]))
    if result.status != "ok":
        msg = result.error or result.status
        if json_view:
            emit({**_env_emit(env), "repo": repo, "run": int(run_id),
                  "rerun": False, "error": msg})
        else:
            print(c("red", f"  Re-run failed: {msg}\n"))
        return 1
    if json_view:
        return emit({**_env_emit(env), "repo": repo, "run": int(run_id),
                     "rerun": True,
                     "failed_only": bool(opts["failed"])})
    which = "failed jobs of" if opts["failed"] else "all jobs of"
    print(c("green", f"  Re-running {which} run {run_id} in {repo}.\n"))
    return 0


def mark_all_read() -> gh.GhResult:
    """Every notification read: one ``PUT notifications`` with
    ``last_read_at`` now. Shared by ``swe gh inbox read --all`` and the
    web app's Inbox."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return gh.run_gh(
        ["api", "--method", "PUT", "notifications",
         "-f", f"last_read_at={stamp}"], timeout=30)


def mark_threads_read(ids: list[str]) -> list[dict]:
    """``PATCH notifications/threads/<id>`` per id, overlapped:
    ``[{"id", "status", "error"}]`` in ``ids`` order. Callers pass only
    digit ids. Shared by ``swe gh inbox read`` and the web app."""
    def mark(thread: str) -> dict:
        r = gh.run_gh(["api", "--method", "PATCH",
                       f"notifications/threads/{thread}"], timeout=30)
        return {"id": thread, "status": r.status,
                "error": r.error or r.status}

    return _fan(ids, mark)


def _cmd_inbox_read(ids: list[str], opts: dict, json_view: bool) -> int:
    """Mark notification threads read: `gh api PATCH threads/<id>` per
    id, or one PUT notifications with last_read_at for --all."""
    err = error_stream(json_view)
    if not ids and not opts["all"]:
        print(c("red", "Usage: swe gh inbox read <id>... | --all"),
              file=err)
        return 1
    if ids and opts["all"]:
        print(c("red", "Pick ids or --all, not both"), file=err)
        return 1
    bad = [i for i in ids if not i.isdigit()]
    if bad:
        print(c("red", f"Not a notification id: {' '.join(bad)}"), file=err)
        return 1
    env = gh.probe_env()
    if env.status != "ok":
        msg = _env_error(env)
        print(c("red", f"  {msg}"), file=err)
        if json_view:
            emit({**_env_emit(env), "all": bool(opts["all"]),
                  "marked": [], "error": msg})
        return 1
    if opts["all"]:
        result = mark_all_read()
        if result.status != "ok":
            msg = result.error or result.status
            if json_view:
                emit({**_env_emit(env), "all": True, "marked": [],
                      "error": msg})
            else:
                print(c("red", f"  Marking read failed: {msg}\n"))
            return 1
        if json_view:
            return emit({**_env_emit(env), "all": True, "marked": ["*"]})
        print(c("green", "  All notifications marked read.\n"))
        return 0

    entries = mark_threads_read(ids)
    marked = [e["id"] for e in entries if e["status"] == "ok"]
    failed = [{"id": e["id"], "error": e["error"]}
              for e in entries if e["status"] != "ok"]
    if json_view:
        return emit({**_env_emit(env), "all": False, "marked": marked,
                     "failed": failed})
    for e in entries:
        if e["status"] == "ok":
            print(f"  {c('green', '●')} {e['id']} marked read")
        else:
            print(f"  {c('red', '●')} {e['id']}  {c('dim', e['error'])}")
    print()
    return 0 if not failed else 1


def _cmd_inbox(rest: list[str], json_view: bool) -> int:
    parsed = _parse_common(rest, json_view)
    if parsed is None:
        return 1
    opts, pos = parsed
    if pos and pos[0] == "read":
        return _cmd_inbox_read(pos[1:], opts, json_view)
    if pos:
        print(c("red", f"Unexpected args: {' '.join(pos)}"),
              file=error_stream(json_view))
        return 1
    if opts["web"]:
        _open_web("inbox", [])
        return 0
    if json_view:
        # Same fetch-first path as _collect_json — the poll is every
        # minute, so a success must not pay for a probe.
        payload = _fetch_inbox(_limit(opts, 50), fetch_all=opts["all"])
        env = _env_after([payload])
        if env.status != "ok":
            print(_env_error(env), file=error_stream(True))
            emit({"github": _env_payload(env), "status": env.status,
                  "notifications": []})
            return 1
        return emit({"github": _env_payload(env), **payload})
    env = gh.probe_env()
    if env.status != "ok":
        print(c("red", f"\n  {_env_error(env)}\n"))
        return 1
    payload = _fetch_inbox(_limit(opts, 50), fetch_all=opts["all"])
    _render_inbox(payload)
    return 0


# The app's cold load: every surface of a repo in one reply, so the
# Repositories page fills all its tabs for the price of the slowest
# single fetch instead of one call per tab.
_BOARD_FETCHERS = {
    "pull_requests": lambda n: _fetch_prs(n, "open", 50),
    "issues": lambda n: _fetch_issues(n, "open", 50),
    "runs": lambda n: _fetch_runs(n, 30),
    "pages": _fetch_pages,
    "releases": lambda n: _fetch_releases(n, 20),
    "tags": lambda n: _fetch_tags(n, 30),
    "branches": lambda n: _fetch_branches(n, 50),
}


def _fetch_board(name: str) -> dict:
    with ThreadPoolExecutor(max_workers=len(_BOARD_FETCHERS)) as pool:
        futures = {key: pool.submit(fetch, name)
                   for key, fetch in _BOARD_FETCHERS.items()}
        return {"repo": name,
                "surfaces": {key: fut.result() for key, fut in futures.items()}}


def _render_board(boards: list[dict]) -> None:
    counts = {"pull_requests": "PRs", "issues": "issues", "runs": "runs",
              "pages": "pages", "releases": "releases", "tags": "tags",
              "branches": "branches"}
    print()
    for board in boards:
        print(f"  {c('cyan', board['repo'])}")
        for key, label in counts.items():
            entry = board["surfaces"].get(key) or {}
            if entry.get("status") != "ok":
                print(f"    {label:10} {c('red', entry.get('status') or 'missing')}")
            elif key == "pages":
                print(f"    {label:10} {'site' if entry.get('pages') else '—'}")
            else:
                print(f"    {label:10} {len(entry.get(key) or [])}")
    print()


def _cmd_board(rest: list[str], json_view: bool) -> int:
    parsed = _parse_common(rest, json_view)
    if parsed is None:
        return 1
    opts, pos = parsed
    if pos:
        print(c("red", f"Unexpected args: {' '.join(pos)}"),
              file=error_stream(json_view))
        return 1
    names, _res_err = _targets(opts, json_view)
    if names is None:
        if json_view:
            emit({"github": _env_payload(gh.probe_env()),
                  "boards": []})
        return 1
    if opts["web"]:
        _open_web("pr", names)
        return 0
    boards = _fan(names, _fetch_board)
    env = _env_after(
        [s for b in boards for s in b["surfaces"].values()])
    if json_view:
        rc = emit({"github": _env_payload(env), "boards": boards})
        return 1 if env.status != "ok" else rc
    _render_board(boards)
    return 0


def _print_overview(json_view: bool) -> int:
    env = gh.probe_env()
    pinned = repos.load_pinned()
    if json_view:
        return emit({"github": _env_payload(env), "repos": pinned,
                     "catalog": catalog.status()})
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
        from swe.help_text import cmd_help
        return cmd_help(["gh"]) or 0

    sub, rest = args[0], args[1:]

    if sub == "sync":
        return _cmd_sync(rest, json_view)
    if sub == "repo":
        return _cmd_repo(rest, json_view)
    if sub == "board":
        return _cmd_board(rest, json_view)
    if sub in ("pr", "prs", "pulls"):
        return _cmd_pr(rest, json_view)
    if sub in ("issue", "issues"):
        return _cmd_issue(rest, json_view)
    if sub in ("run", "runs", "actions", "ci"):
        return _cmd_run(rest, json_view)
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
