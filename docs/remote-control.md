# Remote control

Status: design proposal. Nothing here exists yet.

Watch and steer CLI coding sessions (Claude Code, Codex, opencode and the
rest of the registry) from a phone. Read the chat, send prompts, see new
messages stream in, answer approvals, get notified when an agent needs a
decision. The Claude Code Remote Control and ChatGPT Remote experience,
generalised across every harness `swe` already understands.

## Topology

```
              ┌─────────────┐
              │ phone       │
              │ (PWA → iOS) │
              └──────┬──────┘
                     │ HTTPS + SSE over Tailscale
                     ▼
   ┌────────────────────────────────┐
   │ home server (Linux, always on) │
   │ `swe remote serve` — hub       │
   │ catalog · journal · tailers ·  │
   │ injectors · notify fan-out     │
   └──┬──────────────────┬──────────┘
      │ local tail/inject           │ outbound persistent conn (Tailscale)
      ▼                             ▼
 sessions on this box      ┌──────────────────┐
                           │ MacBook          │
                           │ `swe remote agent│
                           │ — satellite      │
                           └──────────────────┘
```

The phone only ever talks to the hub. Satellites dial *out* to the hub —
no inbound ports anywhere, the same shape Claude Remote Control uses
("registers with the API and polls for work" / streaming connection). A
sleeping satellite is normal: the hub marks its sessions offline and holds
queued commands until it reconnects.

Tailscale is the transport because the relay is our own server: nothing
third-party stores transcripts (Claude RC stores them on Anthropic
servers; we don't need to). v1 is tailnet-only: the hub binds its
Tailscale IP, never `0.0.0.0`. A TLS fallback with a cert pinned at
pairing is deferred until a non-tailnet device actually needs in.

### Behind `tailscale serve`

`swe remote serve --public-url=https://box.tailnet.ts.net:8477` runs the
hub on `127.0.0.1` with `tailscale serve` in front: serve terminates TLS
with the node's Let's Encrypt cert and dials the hub on loopback. Every
request then arrives from `127.0.0.1`, so with `--public-url` on a
loopback bind the hub reads the client from the headers serve sets:

| Header | Used for |
|---|---|
| `X-Forwarded-For` | the tailnet client: `tailscale whois` for pairing and approval, the pair-request rate limit |
| `X-Forwarded-Proto` | the scheme the `Origin` check expects (`https://<Host>`) |
| `Host` | kept as the public `host:port`; the approval path's DNS-rebinding check allows exactly it |

Serve replaces any `X-Forwarded-For` / `Tailscale-User-*` the client sent
(checked on a live node, 2026-10-23), and the hub reads the headers only
from loopback peers while `--public-url` is set; on a tailnet-IP bind
they are ignored. A local process can still forge them, which is no
worse than today: it can already read `devices.json`. Owner identity
comes from `whois` of the node's own tailnet IP. The public URL also
becomes the ntfy action-button base, and `serve` saves
`{host, port, public_url}` to `data/remote/hub.json` so `swe remote
pair` prints `https://` links (the `quiver://` deep link gains
`scheme=https`) and `swe remote status` probes the loopback bind.
Satellites dial the `https://` URL; when Python's default store has no
CA certificates (python.org builds on macOS), the agent retries once
against the OS bundle and keeps it, never skipping verification.
On a Mac, `swe remote serve --install --public-url=URL` writes a
LaunchAgent that runs the hub on `127.0.0.1` with that URL instead of
`--tailnet`; on Linux, run the same `serve --public-url=URL` under a
systemd user unit.

## Precedents

| System | Transport | Write path | What we take |
|---|---|---|---|
| Claude `remote-control` / `/rc` | outbound HTTPS to Anthropic relay, transcript stored server-side | same-process channel | ownership semantics, QR pairing, reconnect journal, Queue semantics, compaction/diff/model controls |
| ChatGPT Remote (Codex) | desktop app as host, account relay | app-server internally | Queue vs Steer follow-ups; "phone as control plane" framing; approvals + diffs + notify |
| `codex app-server` | JSON-RPC 2.0 over stdio | `thread/resume` + `turn/start`, server-initiated approval requests, `command/exec` pty | our tier-A Codex inject channel |
| `opencode serve` | HTTP + one SSE `/event` | `POST /session/{id}/prompt_async`, `permission.asked`/`replied` over HTTP | our tier-A opencode channel; API-driven-seat model |
| Happy Coder | encrypted blob relay, QR key exchange, offline queue | pty wrapper | pairing + offline-first queue shape (no E2E needed — relay is ours) |
| VibeTunnel | pty → WebSocket, server-side VT emulation | pty write | pty is an *input* fallback, never the display format |

## Ownership

Two writers on one transcript is the failure mode the open Codex issue
reports (CLI session invisible to mobile; write-lock conflict). Rule:

- `attached` — the session runs under our supervisor (tmux, `opencode
  serve`, `codex app-server`, headless turns). Read **and** write.
- `observed` — a human terminal owns it, or the harness has no inject
  path. Read-only plus notifications.

Composer renders only on `attached` sessions. `observed` sessions offer
**Attach**: relaunch under the supervisor via the resume flags
`sessions/resume.py` already knows (`claude --resume`, `codex resume`,
`--session`, `--continue`, …). Attach is offered only while the session
looks idle (no transcript append in ~5s, status not running), and the
hub re-checks at execution time: killing a terminal mid-keystroke is
the failure mode. `LIMITED_RESUME` harnesses stay observed.

## New domain: `src/quiver/remote/`

Stdlib-only — SSE is a held HTTP GET, no WebSocket dependency needed
(POST upstream + SSE downstream covers the protocol). If a later phase
wants WS it goes behind a new extra; `server` is taken by the mcp extra.

```
remote/
  protocol.py    # wire schema: events, messages, commands (versioned)
  pairing.py     # QR payload, one-time token → device credential
  catalog.py     # session registry: local + satellite rows, machine-scoped
  dismissals.py  # per-device inbox dismissals (inbox_dismissals.json, 0600)
  tail.py        # byte-offset transcript readers → events (read path)
  inject.py      # per-harness send/approval/interrupt registry (write path)
  supervise.py   # tmux lifecycle for attached sessions
  journal.py     # per-session seq-numbered event log (replay on reconnect)
  hub.py         # http.server ThreadingHTTPServer: REST + SSE, device auth
  agent.py       # satellite daemon: dial hub, run local tail/inject
  notify.py      # needs-you/finished/error fan-out via self-hosted ntfy
  launchd.py     # serve --install: the hub as a per-user LaunchAgent
  commands.py    # swe remote serve|agent|pair|attach|status — argv + print
```

Layering: `remote` may import `sessions`, `reports`, `paths`, `console`,
`flags`; nothing imports `remote` back. State under
`~/.quiver/data/remote/` (`devices.json` 0600, `inbox_dismissals.json`
0600, `cursors/`, `journal/`) —
gitignored like the rest of `data/`. New commands run the usual
checklist: `completion.py`, `help_text.py`, `docs/json-contracts.md`.

## Read path — `tail.py`

Copy the `search_index.py` `files`-row pattern: byte-offset cursor per
transcript, stat → read appended bytes → decode new records → emit
events. ~1s poll while `sessions/status.py` says active, back off idle.

Before a message leaves the hub, `injected.py` removes what a harness
wrote into the transcript for itself rather than for you: Claude Code's
`<system-reminder>` and `<task-notification>` envelopes (a finished task
becomes one system line), Cursor's `<timestamp>`/`<user_query>` wrapper,
Codex's developer turns, `AGENTS.md` and environment injections and its
`<oai-mem-citation>` footer. It runs at decode and again when journal
events are served, so history journaled before it existed is cleaned too.

One upgrade over `reports/transcripts.py`: that layer *strips* thinking
and collapses tool calls — right for reports, wrong for a chat UI that
auto-collapses them. `tail.py` keeps the block detail the raw records
already carry (Claude jsonl thinking/redacted_thinking blocks and
compact boundaries, Codex reasoning items and `compacted` payloads,
opencode part types). Message model:

```python
RemoteMessage:
  seq, session_id, machine_id, ts
  kind: user|assistant|thinking|tool_use|tool_result
      | compaction|system|approval|error
  text                          # markdown for user/assistant
  tool:  {name, call_id, args_summary, args_full}
  result_ref                    # tool_result → tool_use call_id
```

`_redact_secrets` runs before anything leaves the process — prompt text
is user-originated, transcript text is not.

Granularity is **record append**, not token delta: transcripts only
materialize completed records. True deltas come free on tier-A channels
(opencode SSE, codex notifications, `claude -p --output-format
stream-json`) where the harness emits them; `kind` survives both paths.

`needs_you` detection is tiered the same way: tier A gets it free from
structured events (approval requests, `permission.asked`); for tail-only
sessions the tailer infers "waiting for input" from transcript state,
reusing the `reports/followups.py` classification where it applies
rather than a second heuristic.

## Write path — `inject.py`

Registry keyed by `tool_name`, same shape as `PARSER_REGISTRY` /
`READER_REGISTRY`:

| Tier | Harness | Channel | Gets |
|---|---|---|---|
| A | opencode | `opencode serve` REST + SSE (`prompt_async`, permission replies) | structured msgs, approvals, interrupt, multi-client |
| A | codex | `codex app-server` JSON-RPC (`thread/resume`+`turn/start`, approval reqs) | structured items, approvals, deltas |
| A | claude | headless `claude -p --resume <id> --output-format stream-json` per message | one appended turn per call, deltas; permissions preset via `--permission-mode`/`--allowedTools` (no mid-turn prompt in print mode — flagged limitation) |
| B | resumable TUIs (gemini, cursor, crush, cline, droid, pi…) | `supervise.py` tmux: send = `send-keys`, prompt/approval = `capture-pane` parse, display still transcript tail | universal write; approvals are buttons that send menu keys — best effort |
| C | neither resumable nor headless | tail only | read + notify |

## Protocol — `protocol.py`

One schema for phone↔hub and satellite↔hub (satellites forward the same
events upstream and receive command envelopes downstream):

```
GET  /v1/sessions                    catalog: SessionRow shape + machine + ownership
GET  /v1/sessions/{id}/events        SSE stream; ?last=seq replays journal
POST /v1/sessions/{id}/message       {text, mode: queue|steer, attachments[]}
POST /v1/sessions/{id}/attachments   multipart upload → staged file, returns ref
POST /v1/sessions/{id}/approval      {request_id, decision}
POST /v1/sessions/{id}/control       interrupt|compact|clear|model|effort|attach|kill
POST /v1/sessions                    new session {machine, harness, cwd, prompt} → launch_id
GET  /v1/folders?path=                subfolders of one folder on the hub machine (new-session browser)
GET  /v1/launches/{launch_id}        pending|started|failed; started carries the session key
GET  /v1/machines · /v1/usage · /v1/search?q=
GET  /v1/inbox                       pending decisions; ?done=1 adds finished-today
GET  /v1/app/overview                the web app's Overview (docs/web-app.md)
GET  /app/                           the web app's pages, static
POST /v1/inbox/dismiss               {key, last_active_at} → cleared for this device
POST /v1/pair                        QR payload → device credential
```

Events: `message.appended`, `message.delta` (tier A only),
`session.status`, `approval.requested|resolved`, `compaction`,
`machine.online|offline`, `notify` (needs_you|finished|error|limit).

`queue|steer` is the OpenAI Remote distinction: queue waits for idle;
steer injects mid-turn where the channel allows (codex turn input, tmux
send-keys), else degrades to queue with a UI hint. `SessionRow.to_json`
is reused verbatim so phone and macOS app share semantics —
`docs/json-contracts.md` gains a section rather than a parallel dialect.

`POST /v1/sessions` starts a harness under tmux (`supervise.launch_new`)
on the hub, or queues a `launch` command for the satellite named in
`machine` (empty means the hub). The harnesses a machine can start come
from `catalog.launchable_harnesses`, never a hardcoded list: every
harness `swe list` shows there (not archived) whose registry `command`
is on PATH, minus the ones switched off in the Mac app's Settings ›
Provider visibility, in that setting's order (read from the app's
preferences, `providerOrder` and `providerDisabled`; a machine without
the app keeps registry order). The launch runs the registry command, so
`cursor` starts `cursor-agent`. Providers with no CLI, like Antigravity,
can't start. A harness swe has no session parser for (qwen-code) still
starts, but nothing can ever name its session: the machine row lists it
in `untracked_harnesses`, and its launch settles `untracked` with a note
instead of staying pending. Adoption knows a new session by its JSONL transcript's birth
time; harnesses that keep sessions in a database (opencode, kilo, devin,
cline) are matched by the session's own activity time instead, with every
session that existed at launch excluded (the catalog's rows plus a fresh
run of that harness's parser, since the catalog can be a minute old). `cwd` must be absolute and,
on the hub, an existing folder
(a satellite checks its own disk and fails the command otherwise);
`prompt` is required, since a harness writes no transcript until its
first message, and is typed into the pane once the TUI is up. Each
machine row in `/v1/machines` carries `launchable` (in order) and
`harness_names`, and the hub refuses a launch outside it
(`422 not_launchable`). Errors: `422 bad_command`
(body), `422 bad_cwd` (no such folder on the hub), `422 launch_failed`
(tmux missing), `429 backlog_full` (satellite too far behind).

The answer is `202 {launch_id, state: "pending", note}` (plus
`queued: true` for a satellite). On the hub it comes before the pane is
primed, which can take 30 s; a launch that fails after that reads
`failed` at the route below. The harness picks its own session id,
so the key is not known yet: the launch id rides the pending marker into
the supervised entry when `adopt_pending` claims the new transcript, and
the adopting machine emits `session.launched {launch_id}` naming the
new session (the hub puts it on the global stream: an event in the
session's own journal before its tailer starts would make the tailer
skip the transcript as history already sent). `GET /v1/launches/{launch_id}` then answers
`{state: "started", key}`, only once the catalog has that row, so the
phone can open the chat straight away; a satellite's failed `launch`
command (`command.result` echoing `launch_id`) turns it `failed` with the
note. The hub keeps launches in memory for a day.

`GET /v1/folders?path=` lists one folder's subfolders on the hub machine
for the phone's folder browser: `{path, parent, folders: [{name, path}],
truncated}`, names only, dot-folders hidden, at most 500. No `path` means
the hub user's home; a relative or missing folder is `422 bad_cwd`, an
unreadable one `403 not_readable`, and another `machine` is `422
browse_unavailable`, since a satellite's disk isn't readable from the
hub.

`GET /v1/search?q=&limit=` (`search.py`) answers from two sources. When
the Quiver app's FTS5 index (`~/.quiver/data/search/search.sqlite`)
exists on the hub machine, transcript hits come first, ranked the way
`swe session --search` ranks them (phrase, then AND, then OR, BM25),
each with its best snippet. Titles, harness and folder always match too,
as case-insensitive text (the Mac app's Sessions match; never a regex,
which could backtrack without bound), newest first. Results are catalog rows, so each
opens through the usual session routes; satellite sessions match on
metadata only, since the index is per machine.

`GET /v1/usage[?refresh=1]` (`usage.py`) returns the hub machine's
`swe list --usage --json` rows as `harnesses`, plus `window_days` (100),
`machine` and `server_time`: every active harness with its 100-day
session count, and a `rate_limit` reading for each starred one. Readings
come through the rate-limit disk cache with swe's default 300 s TTL,
pinned so a `SWE_RATE_LIMITS_TTL` override in the shell that started the
hub can't freeze them (the Mac app strips it for the same reason), and
concurrent requests share one fetch. `refresh=1` refetches only readings older than 60 s and
failed ones, so a refresh button can't hammer providers that rate-limit.
Each reading carries `fetched_at`; one older than the cache window is
the 24 h outage fallback (newer fetches failed), and the PWA marks it
stale. An unreadable registry answers 503 `usage_unavailable`.

Satellites run the same snapshot every 300 s and post it to
`POST /v1/agent/usage` (`{machine_id, harnesses, window_days,
server_time}`); a failed push retries after 60 s. The hub keeps each
machine's last report in `data/remote/satellite-usage/<machine>.json`,
so a sleeping MacBook still shows its last figures after a hub restart.
`GET /v1/usage` lists every machine under `machines`, hub first:
`{machine, harnesses, window_days, reported_at, received_at, hub}`.
`reported_at` is the reporting machine's clock when it took the
snapshot and `received_at` the hub's when the report arrived, so a
client can age a satellite's `fetched_at` stamps without trusting the
two clocks to agree. The top-level `harnesses` stay the hub's own rows.
The PWA's Usage tab reads this route, with a machine switcher once more
than one machine reports.

`journal.py` is the reconnect story: seq-numbered per-session ring on
disk; client resumes with `?last=seq`. "Open the app, catch up on 30 min
of agent work" replays the journal instead of refetching a transcript —
this is what Claude RC's server-side transcript buys them.

Attachments stage through the hub: `POST .../attachments` lands the
bytes under `remote/inbox/<session>/` on the machine running the session
(a satellite writes them via its own connection when the session is
remote), and the prompt references the path: `@`-ref syntax where the
harness supports it, an inlined path otherwise. Caps: 20 MB per file,
image/pdf/text only, swept after 7 days or at session end, whichever
comes first.

## Keeping the hub up — `launchd.py`

A hub started from a shell dies with the login session, and nothing
restarts it. `swe remote serve --install` writes
`~/Library/LaunchAgents/com.cwenlong.quiver.remote-hub.plist` with
`RunAtLoad` and `KeepAlive`, so the hub starts at login and launchd
restarts it 15 s after it exits. The agent runs Quiver.app's bundled
`swe` when the app is installed, and otherwise the `swe` that installed
it. macOS privacy protection blocks a launchd agent from reading
`~/Desktop`, `~/Documents` and `~/Downloads`, so a checkout's
`.venv/bin/swe` there would fail at every start: `--install` refuses it
and says to install the app. `install-app.sh` restarts the agent on the
new bundle. The agent gets the installing shell's `PATH` and logs to
`~/.quiver/data/remote/serve.log` with timestamped start and stop lines.
It runs `serve --tailnet`: with no Tailscale address yet (early in a
login) the hub exits instead of binding localhost, and launchd retries.
`--install` again restarts it; `--uninstall` stops and removes it. On
Linux, run `swe remote serve` under a systemd unit instead.

## Satellite — `agent.py`

Same `tail.py`/`inject.py`, no listener: persistent outbound connection
to the hub over Tailscale (long-lived HTTPS stream or long-poll command
fetch + POST events; stdlib covers both). Registers machine metadata —
hostname, specs, harnesses detected by the existing `discover`
machinery. Hub stamps every session `machine_id` + reachability; queued
commands wait in the hub journal until reconnect.

The macOS app side is a Settings toggle + menu-bar status managing
`swe remote agent` as a LaunchAgent ("Remote access: online as
macbook"). All session data still flows through bundled `swe` — the app
never writes transcripts itself, same as the
writes-go-through-tested-Python rule.

## Phone client

[remote/ROADMAP.md](../remote/ROADMAP.md) tracks the client redesign:
every page, component and feature, and the hub work each needs.

Two clients. The hub serves a static SPA (`src/quiver/remote/web/`),
so the whole feature works in any browser over Tailscale with "add to
home screen". On iPhone the client is native SwiftUI (`remote/ios/`):
it reads the same REST routes and SSE streams, keeps its credential in
the Keychain, and leaves room for widgets, Live Activities, Face ID and
APNs. It replaced an earlier WKWebView wrapper around the SPA once the
webview's scrolling, keyboard handling and blank offline launch showed.
Push comes from ntfy for now. A device paired in Safari's PWA and the
app pair separately; each holds its own credential. Share only protocol
DTOs with QuiverCore: decoding stays server-side and most of QuiverCore
is AppKit-bound.

Screens:

- **Sessions** — machine → project groups; status dot
  (active/needs-you/error/offline); unread badges; new-session sheet
  (machine → dir → harness → prompt).
- **Chat** — `kind` drives rendering: `thinking` auto-collapses to a
  "Thought for 12s" card; `tool_use`/`tool_result` pair into collapsible
  cards (`Edit · src/foo.py` → args + output), runs fold to "used 6
  tools"; `compaction` renders an inline divider; `approval` renders an
  action card (allow/deny/always); composer carries Queue/Steer, `/`
  shortcuts, `@` file-complete proxied through the hub, photo/file
  attach landing as `@`-refs on the machine.
- **Inbox** — the control-plane screen: every pending decision across
  both machines (approvals, `needs_followup` hits, errors, finished).
  The phone's job is unblocking agents, not being a terminal. The hub
  keeps it bounded: follow-ups, errors and interrupts drop out three
  days after the session's last activity (`catalog.INBOX_MAX_AGE_S`),
  while pending approvals stay until answered. `POST /v1/inbox/dismiss`
  clears a row for the calling device only; the hub stores
  `key → last_active_at` per device id in `inbox_dismissals.json` and
  hides the row until its `last_active_at` moves past that, so new
  activity brings it back. Dismissals prune 30 days after they were
  made and never hide an approval. `?done=1` adds sessions that
  finished since the hub's local midnight as `done` items.
- **Machine** — per-device strip; the MacBook satellite can ride
  QuiverCore's Compute samplers (CPU/GPU/temps) along this view.

Beyond remote control, nearly free on this architecture: push for
needs-you/finished/error/limit via the self-hosted ntfy (iOS background
delivery needs `upstream-base-url: https://ntfy.sh` set, and ntfy `http`
action buttons give approve/deny straight from the lock screen: a POST
to `/v1/sessions/{id}/approval` over the tailnet, no app code); a
usage/limits dashboard (`swe list` data); transcript FTS across machines
(the search index normalizes already); diff viewer + commit/push via
`swe gh`; `$ swe report` cards; voice dictation; offline prompt queue.

## Security

- Tailnet membership is the transport boundary: the hub binds its
  Tailscale IP only (or loopback behind `tailscale serve`), so no
  LAN/WAN port ever opens. Tailscale's OAuth
  login is how a device joins the tailnet; this feature does not
  re-implement it.
- Pairing: the phone posts `POST /v1/pair/request`; the hub resolves
  its Tailscale node with `tailscale whois <peer ip>` and hands back a
  6-digit code + a poll secret. A second device — or the hub's own
  shell — approves it (`swe remote devices --approve=<code>`, the
  `#approve` page on another Mac's browser, or any already-paired
  device). Approving mints a stored credential in `devices.json`
  (0600), revocable per device — Trusted Devices minus biometrics. A
  device can't approve its own request; five wrong codes deny it. The
  QR/token path (`swe remote pair`, `http://hub/#pair=token`; `--app`
  swaps in the `quiver://` deep link) stays as the fallback for devices
  `whois` can't identify. Either way, only a tailnet device can reach
  the pairing routes at all.
- Threat model for the README: the hub executes on your machines by
  design; the credential gates the app layer on top of the tailnet —
  per-device revocation is what "revoke my lost phone" means without
  touching the tailnet.

## Milestones

| # | Scope | Proves |
|---|---|---|
| R1 | hub + tail + catalog + SSE + PWA read-only chat, Linux only | streaming, rendering on real transcripts |
| R2 | inject A-tier claude headless + tmux supervisor + attach | phone can prompt |
| R3 | satellite + machine registry + offline queue | MacBook joins |
| R4 | iOS app (a WKWebView shell, since replaced by SwiftUI) + Inbox + ntfy push + approval cards | notifications |
| R5 | opencode serve adapter, codex app-server adapter, diff pane, Queue/Steer polish | tier-A depth |

## Risks

- **Two writers on a transcript** — exactly the Codex write-lock
  complaint; the ownership rule is the fix, never inject into `observed`.
- **Approval fidelity** — only tier A is structured; tmux approvals are
  capture-pane heuristics.
- **Claude headless ≠ Claude RC** — no mid-turn approval prompts in
  `-p` mode, permissions fixed per invocation; true RC parity would need
  Anthropic's relay, which we deliberately don't use.
- **Not all harnesses persist thinking** — the card simply never
  appears there.
- **Push rides ntfy.sh's APNs relay** — a self-hosted ntfy still needs
  `upstream-base-url: https://ntfy.sh` for iOS background delivery, so
  notification metadata transits ntfy.sh unless bodies are encrypted.
- **Reverses the "cloud sync: out of scope" roadmap line** — scoped to
  session access, not app-state sync; already recorded as M9 in
  `app/ROADMAP.md`.
