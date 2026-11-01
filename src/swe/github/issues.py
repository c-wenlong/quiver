"""One issue in full, a comment on it, and the prompt that resolves it.

``swe gh issue view|comment|worktree`` and the hub's
``POST /v1/issues/resolve`` share these, so the ``suggested_prompt`` a
client shows is the prompt the hub would build itself. Nothing here
prints.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from swe.github import gh, manage

DETAIL_FIELDS = ("number,title,state,author,labels,assignees,url,body,"
                 "createdAt,updatedAt,comments")

# The whole prompt stays under the hub's launch cap (MAX_LAUNCH_PROMPT,
# 32k) with room for a client's edits. A body or one comment past its
# own cap is cut; past the total, the oldest comments go first.
PROMPT_LIMIT = 30_000
BODY_LIMIT = 20_000
COMMENT_LIMIT = 8_000
# The smallest body a prompt over the limit is cut down to.
_BODY_FLOOR = 1_000

_ISSUE_URL_RE = re.compile(r"/issues/(\d+)")


def issue_number(ref) -> int | None:
    """``12``, ``#12`` or a ``…/issues/12`` URL -> 12; anything else None."""
    text = str(ref or "").strip()
    if text.startswith("#"):
        text = text[1:]
    if text.isdigit():
        return int(text) or None
    match = _ISSUE_URL_RE.search(text)
    return int(match.group(1)) if match else None


def _login(node) -> str:
    if not isinstance(node, dict):
        return "-"
    return node.get("login") or "-"


def shape(raw: dict, repo: str, home: Path | None = None) -> dict:
    """``gh issue view --json DETAIL_FIELDS`` -> the ``issue`` object of
    ``swe gh issue view --json``, ``suggested_prompt`` included."""
    number = int(raw.get("number") or 0)
    issue = {
        "number": number,
        "title": raw.get("title") or "",
        "state": str(raw.get("state") or "").lower(),
        "author": _login(raw.get("author")),
        "labels": [str(l.get("name") or "") for l in (raw.get("labels") or [])
                   if isinstance(l, dict)],
        "assignees": [_login(a) for a in (raw.get("assignees") or [])],
        "url": raw.get("url") or "",
        "body": raw.get("body") or "",
        "created_at": raw.get("createdAt"),
        "updated_at": raw.get("updatedAt"),
        "comments": [
            {"author": _login(c.get("author")),
             "body": c.get("body") or "",
             "created_at": c.get("createdAt"),
             "url": c.get("url") or ""}
            for c in (raw.get("comments") or []) if isinstance(c, dict)],
        "branch": manage.issue_branch(number),
        "worktree": manage.issue_worktree_path(repo, number, home),
    }
    issue["suggested_prompt"] = build_prompt(repo, issue, issue["worktree"])
    return issue


def fetch(repo: str, number: int, home: Path | None = None) -> gh.GhResult:
    """``gh issue view`` for one issue; ``data`` is ``shape``'s dict."""
    result = gh.run_json(["issue", "view", str(number), "--repo", repo,
                          "--json", DETAIL_FIELDS], timeout=60)
    if result.status != "ok":
        return result
    if not isinstance(result.data, dict):
        return gh.GhResult("upstream-error",
                           error="gh returned an invalid issue")
    return gh.GhResult("ok", data=shape(result.data, repo, home))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def post_comment(repo: str, number: int, body: str,
                 login: str = "") -> gh.GhResult:
    """``gh issue comment`` with the body on stdin (``--body-file -``),
    so any text, flags and newlines included, arrives as typed.

    ``data`` is ``{author, body, created_at, url}``: ``author`` is the
    signed-in ``login`` the caller probed ("" when unknown), ``url`` the
    new comment's, which gh prints on success.
    """
    result = gh.run_gh(["issue", "comment", str(number), "--repo", repo,
                        "--body-file", "-"], timeout=60, stdin=body)
    if result.status != "ok":
        return result
    url = ""
    for line in (result.data or "").splitlines():
        if line.strip().startswith("http"):
            url = line.strip()
    return gh.GhResult("ok", data={"author": login, "body": body,
                                   "created_at": _now_iso(), "url": url})


# ------------------------------------------------------------------ prompt

def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = len(text) - limit
    return (text[:limit].rstrip()
            + f"\n…({cut} more characters cut; read the rest on GitHub)")


def context_block(context: str, author: str = "") -> str:
    """The "context for this run" paragraph, or "" for no context."""
    context = (context or "").strip()
    if not context:
        return ""
    return f"Context for this run, from {author or 'the user'}:\n{context}"


def _comment_line(comment: dict) -> str:
    day = str(comment.get("created_at") or "")[:10] or "undated"
    body = _clip((comment.get("body") or "").strip(), COMMENT_LIMIT)
    return f"- {comment.get('author') or '-'}, {day}: {body}"


def build_prompt(repo: str, issue: dict, worktree_path: str,
                 context: str = "", context_author: str = "",
                 limit: int = PROMPT_LIMIT) -> str:
    """The first prompt of an agent resolving ``issue`` (``shape``'s
    dict) in the checkout at ``worktree_path``.

    The Comments block is left out when there are none, the Context
    block when ``context`` is blank. Past ``limit`` the oldest comments
    are dropped first, with a line saying how many; the body is cut
    last.
    """
    number = issue.get("number")
    head = f"Resolve {repo}#{number} — {issue.get('title') or ''}".rstrip()
    if issue.get("url"):
        head += f"\n{issue['url']}"
    body = _clip((issue.get("body") or "").strip() or "(no description)",
                 BODY_LIMIT)
    comments = [_comment_line(c) for c in (issue.get("comments") or [])
                if isinstance(c, dict)]
    ctx = context_block(context, context_author)
    foot = (f"You are on branch {manage.issue_branch(number)} in a worktree "
            f"of {repo}\nat {worktree_path}. Implement the change, run the "
            "tests, commit, push the\nbranch, and open a PR that closes this "
            f'issue ("Closes #{number}").')

    def assemble(body_text: str, kept: list[str]) -> str:
        blocks = [head, body_text]
        if comments:
            lines = [f"Comments ({len(comments)}):"]
            omitted = len(comments) - len(kept)
            if omitted:
                lines.append(f"({omitted} earlier comment"
                             f"{'s' if omitted != 1 else ''} omitted)")
            blocks.append("\n".join(lines + kept))
        if ctx:
            blocks.append(ctx)
        blocks.append(foot)
        return "\n\n".join(blocks)

    kept = list(comments)
    text = assemble(body, kept)
    while len(text) > limit and kept:
        kept.pop(0)
        text = assemble(body, kept)
    if len(text) > limit:
        room = max(_BODY_FLOOR, len(body) - (len(text) - limit) - 80)
        text = assemble(_clip(body, room), kept)
    return text
