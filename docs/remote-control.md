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
  tail.py        # byte-offset transcript readers → events (read path)
  inject.py      # per-harness send/approval/interrupt registry (write path)
  supervise.py   # tmux lifecycle for attached sessions
  journal.py     # per-session seq-numbered event log (replay on reconnect)
  hub.py         # http.server ThreadingHTTPServer: REST + SSE, device auth
  agent.py       # satellite daemon: dial hub, run local tail/inject
  notify.py      # needs-you/finished/error fan-out via self-hosted ntfy
  commands.py    # swe remote serve|agent|pair|attach|status — argv + print
```

Layering: `remote` may import `sessions`, `reports`, `paths`, `console`,
`flags`; nothing imports `remote` back. State under
`~/.quiver/data/remote/` (`devices.json` 0600, `cursors/`, `journal/`) —
gitignored like the rest of `data/`. New commands run the usual
checklist: `completion.py`, `help_text.py`, `docs/json-contracts.md`.

## Read path — `tail.py`

Copy the `search_index.py` `files`-row pattern: byte-offset cursor per
transcript, stat → read appended bytes → decode new records → emit
events. ~1s poll while `sessions/status.py` says active, back off idle.

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
POST /v1/sessions                    new session {machine, harness, cwd, prompt}
GET  /v1/machines · /v1/usage · /v1/inbox · /v1/search?q=
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

Three tiers. The hub serves a static SPA (`remote/web/`): the whole
feature works over Tailscale with "add to home screen", zero store
friction. Then a thin WKWebView wrapper buys a real icon, a share-sheet
target, a Face ID launch gate and WidgetKit/Live Activities via app
extensions, without porting the UI. Native SwiftUI comes last, and only
if dictation (`SFSpeechRecognizer`), a real offline cache (service
workers in WKWebView are flaky) or general polish justify it. Push comes
from ntfy, not the app. One caveat: Safari's PWA storage and WKWebView's
data store are separate, so a device credential paired in the PWA does
not carry into the wrapper — re-scanning the QR once inside the app is
expected, not a bug. Share only protocol DTOs with QuiverCore — decoding
stays server-side and most of QuiverCore is AppKit-bound.

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
  The phone's job is unblocking agents, not being a terminal.
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
  Tailscale IP only, so no LAN/WAN port ever opens. Tailscale's OAuth
  login is how a device joins the tailnet; this feature does not
  re-implement it.
- Pairing: `swe remote pair` prints a QR (`quiver://pair?host&token`,
  one-time, short-lived) → phone exchanges it for a stored device
  credential in `devices.json` (0600), revocable per device — Trusted
  Devices minus biometrics. The token guards the pairing window, not
  the transport: only a tailnet device can even reach `/v1/pair`.
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
| R4 | iOS shell (WKWebView wrapper) + Inbox + ntfy push + approval cards | notifications |
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
