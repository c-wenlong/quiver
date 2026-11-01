# Repositories and Inbox

The two GitHub pages: Repositories (sidebar id `repositories`, the Mac
app's `RepositoriesPage`, `RepoDetailPage` and `PrDetailSheet`) and Inbox
(id `inbox`, `InboxPage`). Repositories merges pull requests from the
PR sheet, opens a PR in a harness session, re-runs workflow runs from
Actions rows, and manages the pins: the Add repository sheet
(`AddRepoSheet`), Unpin in the rail's gear popover (`RepoManager`), and
drag to reorder. Inbox marks notifications read.

| File | What |
|---|---|
| `src/swe/webapp/pages/repositories.py` | `GET /v1/app/repositories`, `POST /v1/app/repositories/merge`, `/rerun`, `/worktree`, `/add`, `/remove` and `/reorder` |
| `src/swe/webapp/pages/inbox.py` | `GET /v1/app/inbox`, `POST /v1/app/inbox/read` |
| `src/swe/github/shared.py` | Shared with the hub's `/v1` routes: the `gh` cache, the write lock, the hub's pins, the env payload. |
| `src/swe/webapp/pages/_github.py` | Page-side: request flags, pin errors as page errors. The leading underscore keeps it off `/v1/app/`. |
| `src/swe/webapp/static/pages/github.js`, `github.css` | Shared by both scripts: `GhUI` (icons, the gh setup card, the minute poll, the writes' POST, the PR body's markdown) and the styles. |
| `src/swe/webapp/static/pages/repositories.js`, `inbox.js` | The pages. |

## Whose GitHub

Every read runs the `gh` CLI in the hub process, through the same
builders `swe gh … --json` uses (`swe.github.cli`). So the pages show
what **the hub machine's `gh` login** can see, and the pinned repos are
the hub machine's (`~/.swe/config/github.json`, the same file
`swe gh repo add` writes there).
The browser's own GitHub session plays no part. If `gh` is missing or
signed out on the hub, both pages show the app's setup card
(`brew install gh` / `gh auth login`) with a line saying to run it on the
hub machine. No GitHub token is stored by SWE.

## Cache

`gh` replies are kept in the hub process for 60 seconds (10 seconds for
a failed one), per hub home and per key: the env probe, each pin's
board, each focus surface and window, each PR, the inbox. Only one fetch
per key runs at a time; a second caller waits for its reply. The pages
poll every minute while open, like the app, so a poll costs at most one
round of `gh` calls whoever is looking. A tab in the background skips
its ticks; when it comes back a minute or more after the last read, it
reads at once rather than waiting for the next tick, as the app reloads
a page it shows again. The poll also waits while a sheet is open.
Refresh sends `refresh=1`, which skips the cache unless the reply is
under 5 seconds old.

A write drops what it changed: a merge forgets the repo's board, focus
surfaces and PR details; a mark-read forgets the inbox. A read that was
already in flight when the write landed still answers its own caller but
is not kept, so the next look reads GitHub again.

## Writes

They go through the functions `swe` itself calls, in the hub process
(`swe.github.cli.merge_pr`, `rerun_run`, `mark_threads_read`,
`mark_all_read`, `swe.github.manage.add_repo`, `pr_worktree`),
with the hub's `gh` login. Nothing a browser sends reaches a shell: the
repo must be a pin, the number and ids digits, the method one of three
words. Writes run one at a time per hub (`shared.WRITE_LOCK`), behind
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
the PR sheet and an issue row the issue sheet; every other row opens
GitHub. Below 960px the rail folds
into a strip of repo chips under the title.

**Mine / Review requested** (the iOS Pull requests page's filters). On
the Pull requests tab the page also reads the hub's `GET /v1/pulls`,
whose open PRs carry `mine` and `review_requested` (`gh pr status`),
and shows All, Mine and Review requested with counts under the tabs. A
pick narrows the list and the rail's counts, and repos with nothing left
fold under "N quiet". A filter shows only while its flag is known for
every PR the list and the rail show: a `null` flag (a failed `gh pr
status`), a PR newer than that answer, `gh` blocked or a failed read
hides it, and the list falls back to All rather than hide PRs on a guess. The focus page,
which lists PRs in every state, has no such filter, since `/v1/pulls`
flags only open ones.

**Issue sheet** (the Mac app's IssueDetailSheet), from an issue row on
the dashboard or the focus page. It reads the hub's
`GET /v1/issues/<owner>/<repo>/<n>` (`remote/issues.py`: `gh issue view`
behind the same one-minute cache) and shows the title, number, author
and age, labels and assignees, the body and every comment as Markdown,
each comment with a link to it on GitHub. The footer has Resolve with
agent (off for a closed issue), Open on GitHub, Fetch again and Done.

Under the thread sits the Mac's comment box: Return breaks the line,
Cmd or Ctrl+Return or Comment posts (Comment is off while the box is
blank). It posts `POST /v1/issues/comment {repo, number, body}`, which
the hub sends through its own `gh` login (the caption names that login
and the hub machine), then reads the issue again, so the thread lists
the new comment. A refusal shows under the box and keeps the draft.
The box hides while the Resolve panel is open.

Resolve with agent opens the Mac's ResolveIssueSheet as a panel in the
sheet: where it starts (`quiver/issue-<n>`, a worktree off the default
branch on the hub), the harness, the shared Model menu
(`static/model-menu.js`), Your context (optional) and the first prompt,
prefilled with the hub's `suggested_prompt` (emptied, the hub writes
it). Start posts `POST /v1/issues/resolve` with `{repo, number, harness,
prompt, context?, model?}` and follows the launch as Open in session
does: the chat opens once the session shows up. "Also post as a
comment on #<n>", ticked by default as on the Mac, posts the context
through `POST /v1/issues/comment` first. A refused comment shows as a
warning ("The agent still gets it in the prompt") and the launch goes
ahead, and a Start retried after a failed launch doesn't post the same
context twice.

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

**Open in session** (the PR sheet's terminal button, the app's "Open in
session" menu). The panel names the PR and the hub machine, offers the
harnesses the hub can start (`GET /v1/app/repositories?harnesses=1`:
`{"harnesses": [{"name", "display_name", "tracked", "launch_model",
"launch_effort", "last_model", "recent_models"}]}`, what the phone's
new-session sheet offers, in Provider visibility order) and a first
message, prefilled ("Review pull request #7, "…", in owner/name. Its
head is checked out in this folder.") and editable: the hub's launch
needs one, since a harness under tmux writes nothing until it gets one.
A Model menu sits under the harness while the picked one reports
`launch_model`: the shared `static/model-menu.js` control described in
[Tasks](tasks.md#the-model-menu).
Start then:

1. `POST /v1/app/repositories/worktree` `{"repo", "number"}`: `swe gh
   pr worktree N --repo=R --json` in process
   (`swe.github.manage.pr_worktree`). A bare mirror per repo under
   `~/.swe/data/github/mirrors`, the head fetched from GitHub's
   `refs/pull/N/head` (fork PRs too), a detached checkout at
   `~/.swe/worktrees/<owner>__<name>-prN`, reused on the next ask.
   Answers `{"repo", "number", "path", "head", "created", "machine"}`.
   It holds no lock the other writes wait on, since a first clone can
   take minutes. Checkouts of one repo run one after another: its PRs
   share a mirror and its `FETCH_HEAD`, so two at once could hand one
   session the other PR's code. A failure is a 502 `worktree_failed` naming the step ("Fetch failed:
   …"); a missing or signed-out `gh` a 409.
2. `POST /v1/sessions` `{"harness", "cwd": path, "prompt"}`, the hub's
   own launch route (docs/remote-control.md), then `GET /v1/launches/<id>` once a
   second until the session shows up, at most a minute.
3. The sheet closes and the session's chat opens
   (`#/sessions?chat=<key>`). A harness swe can't read sessions of says
   it started with no chat to open; a failed launch shows its note; one
   that hasn't shown up after a minute says it will appear in Sessions.

The Mac app opens the checkout in a terminal with the harness's own
command and no first message; the web app has no terminal on the hub to
open, so it runs the harness under tmux there, as the phone does.

**Re-run** (`POST /v1/app/repositories/rerun`), `swe gh run rerun <id>
--repo=R [--failed] --json` in process: `{"repo": "owner/name", "run":
4242, "failed": true}`. `failed` re-runs only the failed jobs (default
false: all jobs). GitHub decides what can re-run: a run still going, or
one with nothing failed, comes back as a 502 `rerun_failed` in gh's own
words; an id GitHub doesn't know is a 404 `unknown_run`, a repo that
isn't a pin a 404 `not_pinned`, a bad id or flag a 422, a missing or
signed-out `gh` a 409. Success drops the repo's cached views. Answers
`{"repo", "run", "failed_only", "rerun": true, "machine"}`.

An Actions row has a ⋯ button at its end (the app's context menu;
shown on hover or focus, always on touch screens) with Re-run failed
jobs (failed runs only, as in the app), Re-run all jobs and Open on
GitHub. While the request runs the row's badge is a spinner; then the
view reads the repo again, and the run's row changes once GitHub starts
it. A refusal shows in an error card above the list.

**Search** (`search=<text>`), the Add repository sheet's droplist.
`swe gh repo search <text> --json` in process: the hub's synced catalog
(`swe gh sync`, Settings › Integrations › GitHub › Sync now) first, and
when it has no match, or no catalog exists yet, a live `gh search repos`
scoped to the hub login and its orgs. Up to 15 hits:

```json
{"github": GhEnv | null, "status": "ok", "error": null, "query": "quiv",
 "results": [{"name", "description", "stars", "updated_at", "url"}],
 "source": "catalog" | "github" | null}
```

A catalog hit runs no `gh` at all (`github` is then `null`). A live
search that can't run answers 200 with `status` (`auth-required`,
`upstream-error`, …) and `error`, which the sheet shows above the last
good list. Replies are cached like the other reads, per lower-cased
query; text over 200 characters is a 400 `bad_search`.

**Add** (`POST /v1/app/repositories/add`), `swe gh repo add <repo>
--json` in process: `{"repo": "owner/name"}` (a GitHub URL or SSH remote
works too, as in swe). The hub asks GitHub for the repo (`gh repo view`),
which proves the hub login can see it and gives its own spelling, then
pins that spelling. Pinning a pinned repo changes nothing. Answers
`{"repo", "repos": [PinnedRepo], "machine"}`, the pin and every pin
after it, and drops cached searches. Refusals: 422 `bad_repo` (not
`owner/name` or a GitHub URL), 409 `gh_unavailable` /
`gh_auth_required`, 404 `unknown_repo` (GitHub can't find it, or this
login can't see it; the message is "Cannot reach owner/name: …"), 502
`add_failed`.

The sheet follows the app's: a "Pin a repository" title and caption, one
field ("owner/repo, GitHub URL, or search terms"), a search after a
350 ms pause (Enter searches at once; an emptied field clears the list),
"Searching your repos…" above the hits while one runs, each hit's name,
stars and description with Pin or a Pinned badge, and a "Pin
owner/repo" button once the text is `owner/repo` or a github.com URL. A
line under the hits says where they came from. A pin closes the sheet
and the dashboard reads again. The sheet opens from the rail's last row
(Add repository) and the empty page's Add repository button. A focus
link to a repo the hub hasn't pinned (`not_pinned`) shows "owner/name
is not pinned on the hub." with a Pin it button, which pins it and
opens the repo.

**Unpin** (`POST /v1/app/repositories/remove`), `swe gh repo remove
<repo> --json`: `{"repo": "owner/name"}`, matched as pinned whatever its
case. No `gh` call; the pin list is the hub's own file. The repo's cached
board, focus surfaces and PR details are dropped, so a re-pin reads
GitHub again. Answers `{"repo", "repos", "machine"}`. A repo that isn't
pinned is a 404 `not_pinned` (the page then reads the pins again), a
malformed one a 422.

**Reorder** (`POST /v1/app/repositories/reorder`), `swe gh repo reorder
--json`: `{"order": ["owner/name", …]}` (at most 500). The named pins
take the first slots in that order; a pin it doesn't name keeps its
place after them, and a name that isn't pinned is ignored, so a pin added
from another tab meanwhile is never lost. Answers `{"repos", "machine"}`;
anything but a list of names is a 422 `bad_order`.

The rail's gear opens the app's popover: "Pinned repositories", the pins
grouped by owner (owners sorted, an owner line only when there are
several), each with Open on GitHub and an Unpin button, then "Add
repository…". Unpin acts on the first click, as in the app, and its row
spins while it runs. The popover closes on a click outside or Escape.
Rail rows drag to reorder (a grip shows on hover): a drop moves the repo
within its run, an owner's active repos or its quiet ones, and every
other pin keeps its slot (`PinOrder.move`). The new order shows at once
and is saved behind it; a drop while a save runs is sent once that one
answers, and a failed save shows its error and reads the stored order
back. A repaint that arrives mid-drag waits for the drop. One pin write
runs at a time from the page, as in the app. Touch screens have no drag;
the narrow layout's chip strip drags sideways.

## Inbox

**Needs you** sits above the GitHub list: the iOS Inbox's session
sections from the hub's own `GET /v1/inbox?done=1`, in its order (Waiting
on you, Errors, Finished today; an unknown status counts as waiting). A
row shows the status dot, title, harness logo, what it waits on and age,
and opens the session's chat (`#/sessions?chat=<key>`); a pending approval
says Review. Clear, on a row (on hover or focus) or a section's head,
posts `POST /v1/inbox/dismiss` with the row's `last_active_at`, so the
hub keeps it off this browser's inbox until the session moves again. A
pending approval has no Clear and a section's Clear skips it. A row
leaves at once; a refused clear says so and the next read brings it back.
The hub's GitHub rows are left out, since the list below shows them. It
reads again with the GitHub list: on open, Refresh and the minute poll.

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
is the listing with the marked rows' `unread` off (GitHub can take a
moment to show a mark-all). The page turns off only the rows the answer
marked (for a mark-all, the rows in the answer's listing), so two marks
answering out of order, or a notification a refresh brought in since,
keep their own state; after a failed read the answer's listing becomes
the page's rows. A failed mark-all is a 502.

An unread row has a Mark as read button at its end (the app's context
menu item; it shows on hover or focus, always on touch screens). The
header's envelope, shown while anything is unread, marks every
notification read after an inline confirm, since it reaches past the
rows on screen. A row's button spins while its request runs; partial
failures list each id with GitHub's error in an error card, as the app
does.

## Differences from the Mac app

- Merge and mark all read ask an inline confirm first; the Mac app's
  buttons act on the first click. Marking one row read does not ask.
- The hub refuses a merge the sheet would not offer on a fresh read of
  the PR, so a stale sheet can't merge a PR that was merged, closed or
  turned draft since it loaded.
- Mark as read is a row button, not a context menu. Re-run is a row's ⋯
  menu, and Open in session a button in the PR sheet, not on the row.
- Open in session starts the harness under tmux on the hub with a first
  message, then opens its chat here; the app opens a terminal on the Mac.

- The app keeps a history store, so the focus page's lists keep rows
  older than the fetch window across refreshes and offline starts. The
  web page shows what the current fetch returned.
- The app warm-starts from a snapshot of the last session; the web page
  starts empty and waits for the first reply.
- Refresh sits in the rail as in the app (in the header when there is no
  rail). ⌘K focuses the search; ⌘R and ⌘[ stay the browser's.
- Drag to reorder needs a mouse or trackpad (HTML drag and drop); a
  touch screen reorders nowhere but the Mac app and `swe gh repo
  reorder`.
