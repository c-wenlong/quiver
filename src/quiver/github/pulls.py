"""Pull-request reads and prompt building for the hub's ``/v1/pulls``
routes.

``fetch`` is ``swe gh pr view <n> --repo=<repo> --json`` shaped by
``cli._pr_detail`` plus the two fields a phone needs before it opens a
session: ``worktree`` (where ``manage.pr_worktree`` puts the checkout,
so the client can name the folder before it exists) and
``suggested_prompt`` (a review/fix brief the Open-in-session sheet
prefills and the owner edits).
"""

from __future__ import annotations

from pathlib import Path

from quiver.github import cli as ghcli
from quiver.github import gh, manage

# A PR body can be long; the prompt is capped (protocol.MAX_LAUNCH_PROMPT)
# and the harness reads the diff itself, so the body earns most of the
# room and the failing-checks line the rest.
BODY_LIMIT = 20_000

# The failing-checks line's cap: dozens of names joined could otherwise
# eat the body's room — or, worse, push the whole prompt past the
# launch cap the phone sends it back under.
CHECKS_LIMIT = 4_000

_FAILING = {"failing"}


def fetch(repo: str, number, home: Path | None = None,
          prompt_limit: int | None = None) -> gh.GhResult:
    """``gh pr view`` for ``number`` in ``repo``, shaped for the client.

    ``data`` is ``cli._pr_detail``'s row plus ``worktree`` and
    ``suggested_prompt``; failures keep ``gh``'s outcome word
    (``not-found``, ``upstream-error``, ``unavailable``).
    ``prompt_limit`` bounds ``suggested_prompt`` — callers pass
    ``protocol.MAX_LAUNCH_PROMPT`` so the phone can send it back
    verbatim.
    """
    result = gh.run_json(
        ["pr", "view", str(number), "--repo", repo,
         "--json", ghcli._PR_DETAIL_FIELDS], timeout=60)
    if result.status != "ok":
        return result
    if not isinstance(result.data, dict):
        return gh.GhResult(
            "upstream-error", error="gh returned an invalid pull request")
    return gh.GhResult(
        "ok", data=shape(result.data, repo, home, prompt_limit))


def shape(raw: dict, repo: str, home: Path | None = None,
          prompt_limit: int | None = None) -> dict:
    """The detail payload ``/v1/pulls/<o>/<r>/<n>`` answers with."""
    detail = ghcli._pr_detail(raw, repo)
    detail["worktree"] = worktree_path(repo, detail.get("number"), home)
    detail["suggested_prompt"] = build_prompt(
        repo, detail, detail["worktree"], limit=prompt_limit)
    return detail


# Where ``manage.pr_worktree`` puts the checkout: its own naming, so a
# pending launch's cwd is the folder the worktree call returns.
worktree_path = manage.pr_worktree_path


def build_prompt(repo: str, pr: dict | None, worktree: str,
                 limit: int | None = None) -> str:
    """The first prompt an "Open in session" launch starts with when the
    client sent none: review the PR and fix what needs fixing.

    ``pr`` is a ``_pr_detail`` row, or ``{"number": n}`` when the PR
    could not be read (the caller already refused that case unless a
    client prompt covers it). ``limit`` bounds the whole assembled
    prompt — ``suggested_prompt`` comes back as an open's ``prompt``,
    so it must fit the same cap the route validates against.
    """
    pr = pr or {}
    number = pr.get("number")
    url = pr.get("url") or f"https://github.com/{repo}/pull/{number}"
    head = f"Review {url}"
    title = (pr.get("title") or "").strip()
    if title:
        head += f" — {repo}#{number}: {title}"
    checks = _checks_line(pr)
    if len(checks) > CHECKS_LIMIT:
        checks = checks[:CHECKS_LIMIT].rstrip() + "…"
    closing = (
        f"You are in a detached checkout of the pull request's head "
        f"(refs/pull/{number}/head of {repo}) at {worktree}. The base "
        f"branch is {pr.get('base') or 'the default branch'}. Review "
        "the diff, run the tests if that helps, and fix what needs "
        "fixing. "
        f"When the head branch {pr.get('branch') or 'of the PR'} lives "
        "in this repo, commit on HEAD and push to it "
        "(git push origin HEAD:<branch>); for a fork PR keep the fix "
        "committed and say so in your reply.")
    body = (pr.get("body") or "").strip() or "(no description)"
    if len(body) > BODY_LIMIT:
        body = body[:BODY_LIMIT].rstrip() + "\n…"
    if limit is not None:
        # The body takes whatever the fixed parts leave. Layout:
        # head \n\n body [\n\n checks] \n\n closing.
        fixed = (len(head) + len(closing) + 4
                 + (len(checks) + 2 if checks else 0))
        room = max(0, limit - fixed)
        if len(body) > room:
            body = body[:max(0, room - 2)].rstrip() + "\n…"
    parts = [head, "", body]
    if checks:
        parts += ["", checks]
    parts += ["", closing]
    return "\n".join(parts)


def _checks_line(pr: dict) -> str:
    """One prompt line naming the failing checks, or the rollup's state."""
    runs = pr.get("check_runs") or []
    failing = [r.get("name") or "?"
               for r in runs
               if isinstance(r, dict) and r.get("state") in _FAILING]
    if failing:
        return "Failing checks: " + ", ".join(failing)
    return {
        "passing": "All checks pass.",
        "running": "Checks are still running.",
        "none": "No checks are configured.",
        "unknown": "Check state is unknown.",
    }.get(pr.get("checks") or "", "")
