# Sessions: the chat

The Sessions page (sidebar id `sessions`, drawn by `static/sessions.js`
from `GET /v1/app/sessions`, see docs/web-app.md) opens a session's chat
when you click its row. The chat is `static/pages/chat.js` (+ `chat.css`),
a port of the hub PWA's chat (`src/quiver/remote/web/app.js`): remote
control from the web app, with the same routes, ownership rules and event
handling as the phone. It has no page module and no `/v1/app` route of
its own; it talks to the hub's session routes (docs/remote-control.md,
docs/json-contracts.md "Remote control").

The chat lives at `#/sessions?chat=<key>`, so the sidebar keeps Sessions
selected, the browser's Back button returns to the list (scrolled where it
was), and a chat can be linked. `chat.js` is not a registered page: it
publishes one global, `SessionChat`, which `renderSessions` calls when the
hash names a chat. Should the script fail to load, a row opens the details
sheet as before.

## What it shows

| Part | What |
|---|---|
| Top bar | Back, harness logo, title, harness · folder (· machine for a satellite), the status badge, Attached or Observed, a Live / Reconnecting dot, and Details: the sheet the row used to open (harness, machine, folder, last active, id, models, tokens and spend, copy id, copy resume command). |
| Transcript | The last 500 messages from `GET /v1/sessions/{key}/messages?limit=500`, then live events. User text right in a bubble, assistant text left, both with the PWA's markdown (fenced code, inline code, bold; everything else escaped). Thinking folds under "Thought". Tool calls are cards ("Edit src/x.py") that open to their arguments and output; Edit, MultiEdit, Write, NotebookEdit and apply_patch cards show a diff instead. Compaction is a "Context compacted" rule; system notes are centred captions; errors are red. |
| Approval card | "Approval needed", the prompt, and Allow, Always, Deny. Once answered it dims and says Allowed, Always allowed or Denied. |
| Working dots | While the session is active, a send waits for its reply, or live messages are still being revealed. |
| Composer | Queue or Steer, the message box (Enter sends, Shift+Enter is a new line), Stop and Send. Off, reading "Read-only until this session is attached", unless the session is attached. |
| Observed banner | "Observed session: read-only until attached." with Attach when the hub says the session is `attachable` and it isn't mid-turn; otherwise why it can't be attached (mid-turn, or a harness that can't be resumed). |

## How it talks to the hub

1. `GET /v1/sessions` for the catalog row: `ownership`, `attachable` and
   `status` as the hub has them now (the list's copy can be minutes old),
   and the machine's name.
2. `GET /v1/sessions/{key}/messages?limit=500`, the backfill.
3. `GET /v1/inbox`, for an approval of this session the hub still waits
   on (its `request_id`).
4. `GET /v1/sessions/{key}/events?last=<seq of the last message shown>`,
   read with a fetch stream (EventSource can't send the bearer). Events
   are revealed one at a time with a short gap, as in the PWA; a replay of
   more than 24 lands at once. A dropped stream reconnects after 2 s from
   the last seq shown, and again when the tab becomes visible. Leaving the
   chat by any route aborts the stream, so it never keeps one of the
   browser's few connections to the hub. When the waiting approval was
   asked before that message (codex's app-server journals the ask before
   its tool call), the stream starts at 0 instead, takes only that ask
   from the part already shown, and puts its card back where it was
   asked.

Every request sends app.js's `authHeaders()`: the paired bearer when the
browser has one, and `X-Quiver-Pair: 1`, so the hub owner's own tailnet
devices work without pairing.

Events handled: `message.appended`, `session.status` (badge, dots, Attach
offer), `session.ownership` (composer and banner), `approval.requested`,
`approval.resolved`, `command.result` (settles a send by `client_id` or a
queued approval; an unmatched failure becomes a system line).

Writes, all with the device bearer:

| Control | Request | Answer |
|---|---|---|
| Send | `POST .../message {text, mode: queue\|steer, client_id}` | `202 {client_id, waiting}`: the bubble reads "Queued until the agent is idle" or "Sent", then settles on its `command.result` (dropped on success, "Sent (queued: …)" when degraded, red with the note on failure) or when the transcript echoes the message. |
| Allow, Always, Deny | `POST .../approval {request_id, decision}` | `200` resolves the card; `202` (a satellite's session) reads "Queued: waiting for the satellite" until its `command.result`; a refusal re-enables the buttons with the hub's message. |
| Stop | `POST .../control {op: interrupt}` | Off while the request runs; a refusal adds an error line. |
| Attach | `POST .../control {op: attach}` after a confirm in the banner: "Attach stops <harness> in the terminal running this session, then resumes the session under Quiver so you can send to it from here." | `200` opens the composer at once (the hub's `session.ownership` follows); `202` (a satellite) adds "Attach queued: waiting for the satellite"; `409 not_idle` and `422 not_attachable` show as an error line with the hub's reason. |

Any write answered `409 not_attached` means the session went back to
observed: the composer locks and the banner returns.

`/v1/app/sessions` rows also carry `ownership` (`attached` or `observed`)
and `attachable`, so the list marks attached sessions with an Attached
badge.

## Differences from the PWA

- Attach asks first. The PWA attaches on one tap; here it stops the
  harness in its terminal, so the banner names that before it happens.
- Opening a chat streams from the last message the backfill showed, not
  from a seq saved in `localStorage`, so an approval asked or a result
  reported after the last message is replayed into view, and a waiting
  approval asked before it is restored too. Within an open chat,
  reconnects resume from the last seq shown, as in the PWA.
- Codex's `apply_patch` (raw patch text) renders as a diff, and code
  blocks keep `**` and backticks as written.
- New messages scroll the page only when it is already at the bottom, so
  reading back through a long transcript isn't interrupted.
- Stop shows a refusal instead of ignoring it, and an answered approval
  says what was decided.
- Not ported: the PWA has no attachments, waiting-sends tray, slash
  commands or "load earlier" either, so neither does this (the hub's
  `queue` and `attachments` routes are unused here). Starting a new
  session (`POST /v1/sessions`) is not on the page.
