# Tasks

The Tasks page (sidebar id `tasks`, hash route `#/tasks`, a task's
detail at `#/tasks?task=<id>`): the work items the hub's agents are on —
the task store (`~/.swe/tasks.json`) overlaid on the sources' open
items. It is the web half of the work-items surface the iOS app mirrors;
GitHub is the first source; Linear and Notion ride along when
configured.

| File | What |
|---|---|
| `src/swe/webapp/pages/tasks.py` | `GET /v1/app/tasks` — `swe.remote.tasks.list_view` in process, plus the page's session and launch joins; `?harnesses=1` answers what the hub can start |
| `src/swe/webapp/static/pages/tasks.js` | The page. Shares `GhUI` (icons, setup card, poll, POST helper, `hashParams`, `keepFocus`) with the other GitHub pages |
| `src/swe/webapp/static/pages/tasks.css` | The state stepper, chips, rail mark and detail layout on the `gh-*`/`styles.css` vocabulary |

Reads stay in process: the module calls `tasks.list_view(ctx.state,
args)` — the same builder `GET /v1/tasks` serves — so the page and the
phone share one aggregation, one 60-second `gh` cache and one derived
state ladder. Writes never get a page route: they ride the frozen remote
protocol — `POST`/`DELETE /v1/sessions/{key}/task` to link and unlink,
`POST /v1/issues/resolve` or `POST /v1/pulls/<o>/<r>/<n>/open` to start
an agent.

## Payload

```json
{"github": GhEnv, "linear": LinearEnv, "notion": NotionEnv,
 "env_blocked"?: "…",
 "repos"?: [{"name", "status", "error"}],
 "tasks": [TaskRow], "machine": "hub name", "machine_id": "…",
 "fetched_at": epoch}
```

Everything except the two joins is `GET /v1/tasks`'s answer verbatim
(docs/json-contracts.md): `TaskRow` is the `TaskRef` fields plus `id`
(`"github:issue:o/r:5"`), the derived `state`, `sessions` (session
keys), `launches` (launch ids), `linked`, `agent_running`,
`last_active_at`, `created_at`, `updated_at`, and `pr` — an issue's
`quiver/issue-<n>` PR (open or merged) or a pull's own. `?refresh=1`
bypasses the cache, exactly as on `/v1/tasks`.

The joins, always present:

- `session_rows`: each key in `sessions` joined against the catalog,
  trimmed to what a row draws (`key`, `harness`, `agent`, `title`,
  `status`, `cwd`, `machine`/`machine_id`/`remote`, `ownership`,
  `attachable`, `last_active_at`, `trivial`). A satellite row's
  `machine` is its name joined from `machines.json`, the same join
  `/v1/app/sessions` makes. A key the catalog no longer lists answers
  `{"key", "gone": true}` — it still counts as linked and the row shows
  the key as its title.
- `launch_rows`: each id in `launches` joined against the hub's live
  launch table: `{id, state (pending|started|failed|untracked),
  harness, note, key, cwd, machine}`. A launch the hub forgot (TTL, or a
  restart) is left off — `launches` still says it was linked, and
  `linked` still counts it.

`GET /v1/app/tasks?harnesses=1` does not touch the task list at all: it
answers `{"harnesses": [{name, display_name, tracked, launch_model,
launch_effort, last_model, recent_models}], machine}` —
`catalog.launchable_harnesses`, the same list the Repositories page's
PR sheet and the phone's new-session sheet offer. `launch_model` says
the harness takes a `model` pick at launch, `recent_models` lists the
ledger's recent models for it (newest first, at most five) and
`last_model` is the head of that list (`""` when none); together they
fill the sheet's Model menu. `launch_effort` rides along, unused here
so far.

### The Model menu

`static/model-menu.js` draws one control for every web launch sheet:
this page's New agent session, the Repositories page's Open in session
and the Sessions page's New session. It shows only while the picked
harness can take a model at launch, and offers, in order:

- **Harness default**: sends no `model` key.
- Each of the harness's `recent_models`, newest first. An old hub that
  sends only `last_model` (or `last_models` on a machine row) gives a
  one-item list.
- **Other…**: shows a monospace box for a typed model id; its trimmed
  value is sent, or nothing when it is blank.

It opens on the newest recent model, else Harness default. A harness or
machine switch re-defaults an untouched menu to the new harness's
newest model. Any pick the user made, Harness default and Other…
included, survives the switch, except a recent model the new harness
has never used: that one re-defaults, so a sheet never sends another
harness's model.

## The page

**List.** Rows group under a repo header (`owner / name`, with links to
GitHub and to the Repositories page's focus view) or, for other
sources, `Source / project` (or the source's name alone), all in one
box as on Repositories; order is the payload's last-activity-first.
Each group shows five rows, then the Repositories dashboard's "Show N
more" / "Show less" fold; an unfolded group stays open per group and
tab across repaints and polls while the page is open. Each row is the
kind's icon (open/merged/closed, draft), title, ref and state word,
when it last moved, a `PR #<n>` badge, a linked-session count, and an
"Agent running" chip. Tabs filter without another fetch: **All**,
**Running** (`agent_running`), **PR open** (the `pr_open` state, or an
open pull task), **Done** (`merged` or `closed`).

**Rail.** Beside the list, whenever there are rows, sits the
Repositories rail (same classes, same narrow-width chip strip), headed
"Sources" with Refresh: "All tasks" with the tab's count, then GitHub
repos under their owner, Linear projects under "Linear", Notion
databases under "Notion", any other source under its name, each with its count on the current tab; a group
with nothing on the tab folds under "N quiet". Groups and rows sort by
name so the rail holds still while the list reorders. Its dot lights
while an agent runs on one of the group's tasks. A tap narrows the list
to that group, unfolded; a group that leaves the payload drops the
narrowing back to All tasks. No gear, Add repository or drag order:
nothing here is pinned.

**Detail.** `#/tasks?task=<id>` — the state ladder as a stepper (Open →
In progress → PR open → Merged/Closed; the PR rung exists only where a
PR can close the gap, a GitHub issue), then a Sessions card, then the
PR card when the task has one. A session row deep-links into the chat
(`#/sessions?chat=<key>`) and carries an unlink button
(`DELETE /v1/sessions/{key}/task` with the task's ref); a `gone` row is
the key alone. A pending launch shows as its own row — "Starting <h>…"
with the launch's note — so a session that is on its way is visible
before adoption lands, and a failed or untracked launch says so. The PR
card deep-links into the Repositories page's PR sheet
(`#/repositories?repo=<repo>&pr=<n>`, the `pr` parameter this page
added to that route).

**Actions.** On a GitHub issue or pull, "New agent session" asks for a
harness (`?harnesses=1`), an optional model (the Model menu above,
shown only when the picked harness reports `launch_model`) and an
optional first
message, then posts to
`POST /v1/issues/resolve` (issues) or `POST /v1/pulls/<o>/<r>/<n>/open`
(pulls) — both stamp the launch's `origin`, so the session that lands
links itself to the task on adoption. The panel polls
`GET /v1/launches/<id>` once a second like the other launch flows, then
opens the chat on the session key; an untracked harness says it started
with no chat to open, a failure shows the launch's note. "Link session"
filters `/v1/sessions` (active first, then last-active) and posts
`{task_ref}` to `POST /v1/sessions/{key}/task`; a session already
linked to this task is left out, one linked to another task is still
offerable. Both writes use the same bearer and Origin checks as every
`/v1` route.

**Blocked and stale.** A blocked source still serves its stored rows
under a "Last known state" note: GitHub shows the shared setup card
(`gh auth login`/`brew install gh`, run on the hub machine), a failing
Linear or Notion (`auth-required`, `bad-config`, `upstream-error`) a
small offer card each ("Notion: bad config") with its reason and a
refresh; `disabled` means off, so no card.
A per-repo fetch failure renders its own group with the error. A hub
that answers nothing on a refresh keeps the last rows under an error
card, as the other pages do. An empty store on a working env links to
Repositories; an empty filter says which filter. A task id that left
the list (closed, unpinned) gets a "isn't listed any more" card with a
way back.

The page polls every minute while open — one shared cache, so a poll
costs at most one round of source reads — and skips ticks while a panel
or a write is open, so a reply can't yank the prompt mid-edit.

## Differences from the native surfaces

- This page only renders the store; it cannot create, edit or dismiss
  tasks. The store's writes stay on the remote routes above, which the
  phone shares.
- "Open on GitHub" ("Open in Linear", "Open in Notion" for those
  sources) is an external link; issue and PR bodies are not shown
  here — the Repositories page's PR sheet is the detail surface for a
  PR.
- Linear and Notion tasks can link and unlink sessions and show their
  state in the source's own words (a Notion page's Status property),
  but have no launch route: "New agent session" is GitHub-only, since
  the launch needs a repo worktree.
- The remote protocol is frozen: nothing here changes `GET /v1/tasks`
  or the link routes; `session_rows`/`launch_rows` exist only on the
  page's own payload.
