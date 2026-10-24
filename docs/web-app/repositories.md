# Repositories and Inbox

The two GitHub pages: Repositories (sidebar id `repositories`, the Mac
app's `RepositoriesPage`, `RepoDetailPage` and `PrDetailSheet`) and Inbox
(id `inbox`, `InboxPage`). Each has one write: merging a pull request
from the PR sheet, and marking notifications read.

| File | What |
|---|---|
| `src/quiver/webapp/pages/repositories.py` | `GET /v1/app/repositories`, `POST /v1/app/repositories/merge` |
| `src/quiver/webapp/pages/inbox.py` | `GET /v1/app/inbox`, `POST /v1/app/inbox/read` |
| `src/quiver/webapp/pages/_github.py` | Shared: the `gh` cache, the write lock, the hub's pins, the env payload. The leading underscore keeps it off `/v1/app/`. |
| `src/quiver/webapp/static/pages/github.js`, `github.css` | Shared by both scripts: `GhUI` (icons, the gh setup card, the minute poll, the writes' POST, the PR body's markdown) and the styles. |
| `src/quiver/webapp/static/pages/repositories.js`, `inbox.js` | The pages. |

## Whose GitHub

Every read runs the `gh` CLI in the hub process, through the same
builders `swe gh … --json` uses (`quiver.github.cli`). So the pages show
what **the hub machine's `gh` login** can see, and the pinned repos are
the hub machine's (`~/.quiver/config/github.json`, `swe gh repo add`).
The browser's own GitHub session plays no part. If `gh` is missing or
signed out on the hub, both pages show the app's setup card
(`brew install gh` / `gh auth login`) with a line saying to run it on the
hub machine. No GitHub token is stored by Quiver.

## Cache

`gh` replies are kept in the hub process for 60 seconds (10 seconds for
a failed one), per hub home and per key: the env probe, each pin's
board, each focus surface and window, each PR, the inbox. Only one fetch
per key runs at a time; a second caller waits for its reply. The pages
poll every minute while open, like the app, so a poll costs at most one
round of `gh` calls whoever is looking. Refresh sends `refresh=1`, which
skips the cache unless the reply is under 5 seconds old.

A write drops what it changed: a merge forgets the repo's board, focus
surfaces and PR details; a mark-read forgets the inbox. A read that was
already in flight when the write landed still answers its own caller but
is not kept, so the next look reads GitHub again.

## Writes

Both go through the functions `swe` itself calls, in the hub process
(`quiver.github.cli.merge_pr`, `mark_threads_read`, `mark_all_read`),
with the hub's `gh` login. Nothing a browser sends reaches a shell: the
repo must be a pin, the number and ids digits, the method one of three
words. Writes run one at a time per hub (`_github.WRITE_LOCK`), behind
the device bearer and the hub's Origin check like every page POST.
Refusals are `PageError`s: 404 for something the page does not list,
409 for a state conflict (and for a missing or signed-out `gh`, with the
setup words), 422 for bad input, 502 when GitHub refuses.

## Repositories

`GET /v1/app/repositories` has three views. Only pinned repos are read:
any other `repo` is a 404 (`not_pinned`), a malformed one a 400.

**Dashboard** (no `repo`). `swe gh --json` plus `swe gh board --json` per
pin:

```json
{"github": GhEnv, "repos": [PinnedRepo],
 "boards": {"owner/name": {"surfaces": {"pull_requests": …, "issues": …, "runs": …,
   "pages": …, "releases": …, "tags": …, "branches": …}, "fetched_at": epoch}},
 "machine": "hub name"}
```

A blocked env (`unavailable`, `auth-required`) or no pins returns before
any board fetch. Each surface is the `GhResults` entry shape in
docs/json-contracts.md ("swe gh shapes"): open pull requests and issues
(50), runs (30), Pages, releases (20), tags (30), branches (50).

The page matches the app: tabs Pull requests, Issues, Actions across all
repos, all seven once the rail narrows to one repo; the search matches
repo names (a named repo shows its whole board) and row text including
state words (`failing`, `draft`, `conflict`, `approved`, `bot`); one box
with a header per repo and five rows before "Show N more"; repos with
nothing on the tab drop out of the list and fold under "N quiet" in the
rail; the rail's CI dot is the newest run on the default branch; the
same empty texts and row layouts (PR state, review and check icons with
their hover words, issue comments and assignees, run conclusion badges,
Pages facts, release, tag and branch badges and shas). A PR row opens
the PR sheet; every other row opens GitHub. Below 960px the rail folds
into a strip of repo chips under the title.

**Focus page** (`repo=owner/name`, the hash route
`#/repositories?repo=owner/name`, opened from a repo header's expand
icon). Every surface in every state, fetched together at the app's
first windows:

```json
{"github": GhEnv, "repo": "owner/name", "surfaces": {…seven entries…},
 "limits": {"pull_requests": 50, "issues": 50, "runs": 30, "pages": 0,
            "releases": 20, "tags": 30, "branches": 50},
 "fetched_at": epoch, "machine": "…"}
```

`surface=<key>&limit=N` (N capped at 200, `pages` refused) refetches one
surface at a wider window: `{"github", "repo", "surface", "entry",
"limit", "fetched_at", "machine"}`. The list reveals ten rows at a
time; the "Keep scrolling for more" row fills while it stays in view,
then shows ten more or asks for 50 more rows, as the app's scroll loader
does. It also advances on click.

**Pull request** (`repo=owner/name&pr=N`). `swe gh pr view N
--repo=owner/name --json`: `{"github", "repo", "status", "pr":
GhPrDetail?, "error"?, "fetched_at", "machine"}`. The sheet shows the
header (state, branches, author, size, mergeable and merge state, CI and
review marks, auto-merge, labels), the folded Checks list with its pass
count, Reviews and requested reviewers, and the body as markdown
(`<details>` folds, HTML converted, fences, headings, quotes, lists,
tasks, rules; links open only http(s) and mailto).

**Merge** (`POST /v1/app/repositories/merge`), `swe gh pr merge N
--repo=R [--method=…] [--auto] --json` in process:

```json
{"repo": "owner/name", "number": 7, "method": "squash", "auto": false}
```

`method` is `squash` (default), `rebase` or `merge`; `auto` arms
auto-merge instead of merging now. The hub first reads the PR again
(`gh pr view`, never the cache: the sheet may be a minute old), then
refuses what the Mac app's sheet would not offer, in its words:

| Case | Answer |
|---|---|
| PR not open (merged or closed) | 409 `not_open`, "#7 is merged, so it can't merge." |
| Draft | 409 `draft`, "Draft PRs can't merge" (merge and auto-merge) |
| Conflicting, merge now | 409 `conflicting`, "Resolve the conflicts first" |
| Auto-merge already armed, `auto` | 409 `auto_merge_armed`, "Auto-merge is already armed" |
| No such PR in the repo | 404 `unknown_pr` |
| Not a pin | 404 `not_pinned` |
| Bad repo, number, method or auto | 422 |
| `gh` refused (branch protection, required checks, method not allowed) | 502 `merge_failed`, gh's own message |

The app does not gate on checks or review state, so neither does the
hub: GitHub's own rules refuse through `gh`. Arming auto-merge on a
conflicting PR is allowed, as in the app. The answer is
`{"github", "repo", "number", "method", "auto", "merged", "pr",
"fetched_at", "machine"}`; `merged` is false under `auto`, and `pr` is
the `GhPrDetail` read again after the write (`null` if that read
failed, and the sheet fetches it itself).

In the sheet an open PR shows the app's strategy menu (Squash and merge,
Rebase and merge, Create a merge commit), Merge and Auto-merge, off with
the app's hover words in the same cases. Either button opens an inline
confirm above the buttons that names the PR number and title, the repo,
the base branch and the strategy ("Merge #7 Fix the thing into `main` of
owner/name with Squash and merge?"); only its second button posts. While
it runs the buttons are off. The sheet repaints from the answer (the PR
shows merged, or the Auto-merge badge), and the page behind it reads the
repo again so a merged row leaves the list. A refusal shows in an error
card in the sheet, with the confirm still open to retry or cancel.

## Inbox

`GET /v1/app/inbox` is `swe gh inbox --all --limit=50 --json`: `{"github",
"status", "notifications": [GhNotification], "error"?, "fetched_at",
"machine"}`. Read rows come back too, so Unread only (on by default)
filters in the browser, as in the app. A row shows the unread dot, the
subject type's icon tinted by reason (CI red; review requests and
mentions amber), title, repo, reason and age, and opens the subject on
github.com. "Updated Xm ago" sits under the list. A failed read says
"GitHub returned <status>".

**Mark read** (`POST /v1/app/inbox/read`), `swe gh inbox read --json` in
process. `{"ids": ["123", …]}` marks those threads (`PATCH
notifications/threads/<id>` each, at most 50, each one the inbox's own
listing has, else 404 `unknown_notification`); `{"all": true}` marks
everything (`PUT notifications` with `last_read_at` now). Not both, not
neither (422). The answer is `{"github", "all", "marked", "failed",
"notifications", "machine"}`: `marked` is the ids GitHub accepted
(`["*"]` for all), `failed` is `[{"id", "error"}]`, and `notifications`
is the listing with the marked rows' `unread` off, which the page
repaints from (GitHub can take a moment to show a mark-all). A failed
mark-all is a 502.

An unread row has a Mark as read button at its end (the app's context
menu item; it shows on hover or focus, always on touch screens). The
header's envelope, shown while anything is unread, marks every
notification read after an inline confirm, since it reaches past the
rows on screen. A row's button spins while its request runs; partial
failures list each id with GitHub's error in an error card, as the app
does.

## Differences from the Mac app

Left out because they write (they stay in `swe` and the Mac app):

| Write | Where it lives |
|---|---|
| Re-run failed or all jobs of a workflow run | `swe gh run rerun`, Actions row menu in the Mac app |
| Pin, unpin, add a repository (with catalog search and sync), reorder pins by drag | `swe gh repo add/remove/reorder`, `swe gh sync`, the rail's gear and Add repository |
| Open a PR in a session (PR worktree) | `swe gh pr worktree`, the PR row's context menu |

Also different:

- Merge and mark all read ask an inline confirm first; the Mac app's
  buttons act on the first click. Marking one row read does not ask.
- The hub refuses a merge the sheet would not offer on a fresh read of
  the PR, so a stale sheet can't merge a PR that was merged, closed or
  turned draft since it loaded.
- Mark as read is a row button, not a context menu.

- The app keeps a history store, so the focus page's lists keep rows
  older than the fetch window across refreshes and offline starts. The
  web page shows what the current fetch returned.
- The app warm-starts from a snapshot of the last session; the web page
  starts empty and waits for the first reply.
- Refresh sits in the rail as in the app (in the header when there is no
  rail). ⌘K focuses the search; ⌘R and ⌘[ stay the browser's.
- Drag to reorder and the rail's gear popover are not there (writes).
