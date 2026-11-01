# Calendar

The Mac app's Calendar page (`CalendarPage.swift`, model in
`SWECore/Calendar/`): when each harness was actually working on the
hub machine, drawn as blocks of activity per project on a day grid.

Files: `src/swe/webapp/pages/calendar.py` (data),
`src/swe/webapp/pages/_activity.py` (the session-file and Cursor
app sources),
`src/swe/webapp/static/pages/calendar.js` and `calendar.css` (page),
`LedgerStore.activity_events` and `activity_session_stats` (the store
reads it adds), `tests/test_webapp_calendar.py` and
`tests/test_webapp_calendar_sources.py`.

## What it shows

- **Header**: the anchor's month ("October 2026") and, outside month
  mode, "Week N" (ISO weeks for a Monday-first locale, the week holding
  1 January as week 1 otherwise). Controls: the view menu (Day, Week,
  Month, and "N days" while one is active), Today, previous and next,
  the side panel toggle, and refresh (rescans the ledger).
- **Day, week and N-day grid**: one column per day, an hour axis, and
  each block placed by clock time in the harness's colour (15% fill, a
  full-colour bar, the project name, and its time range once the slice
  is 30 minutes or longer, plus the account when the harness has more
  than one). Overlapping blocks share lanes; a column too narrow for
  them (under 56px a lane) keeps the longest and folds the rest into a
  "+N" chip. Today's header has the red pill and its column the red
  now-line with the time on the axis. Each range opens at its earliest
  block (08:00 when empty); stepping sideways keeps the hour.
- **Month grid**: six week rows from the first weekday on or before the
  1st, ISO week numbers down the edge, one row per project per day
  (start time and name), "N more" and a click on the cell opening that
  day.
- **Side panel**: the mini month (the visible range lit, today red, a
  click jumps there) and the Harnesses legend: a checkbox per harness
  that hides or shows its blocks, the logo and name, and its wall-clock
  time in the range (in month mode, the month itself). A harness with
  more than one account lists them under it, each with its own toggle
  and time; the chevron folds them.
- **Block details**: hovering a block shows its card (project, account,
  range, active time, responses, tokens and spend, and up to eight
  sessions with their own range, responses, tokens and spend). Blocks
  from session files (Pi, Factory Droid, Freebuff) and the Cursor app
  have no tokens, so their card stops at responses. A click
  opens the session's detail (the Sessions page's sheet) when the block
  has one session, else the project's session list, where each row
  opens its detail. The "+N" chip's card lists the hidden blocks with
  the hovered one's card beside it.
- **Keys**: D, W, M, 1 to 9 (N days), T (today), S (side panel), and
  the left and right arrows.

## The blocks

Ported from `Activity.swift`, `ActivityBlocks.swift` and
`ProjectResolver.swift`:

1. The ledger's responses in the range, a day past each edge, for the
   harnesses the Mac calendar reads from the ledger (Claude Code, Codex,
   OpenCode, Devin, Grok Bot).
2. Per session, a block until more than 30 minutes (`GAP_S`) pass
   between two responses. Each keeps its responses, tokens, priced
   cost, and the folder most of its responses used (a Claude
   side-session notes folder only when nothing else is known).
3. Sessions whose whole history is only helper calls (Devin's
   `summarizer` and `compactor`) or that are accidental (the Sessions
   page's rule) draw nothing.
4. Pi and Factory Droid come from their own session files instead
   (`FileActivitySources.swift`): every `*.jsonl` under
   `~/.pi/agent/sessions` and `~/.factory/sessions`, hidden files and
   folders skipped. Each `message` line's ISO `timestamp` is a
   response; the first `session` (Pi) or `session_start` (Droid) line
   with a string `cwd` names the folder. A Pi session's id is the file's
   full path, a Droid one's the file stem, as swe names them. The whole
   history is read, so a block keeps its real ends, and the step 2 rule
   makes the blocks. A session whose ledger history (under its own
   account) is accidental draws nothing; one the ledger has never seen
   stays. The account is `~/.pi` or `~/.factory`. The files are listed
   again at most every 30 seconds (refresh does it at once), and parsed
   again only when their mtime or size changes.

   Freebuff (`FreebuffActivity`): every
   `<project>/chats/<chat>/run-state.json` under
   `~/.config/manicode/projects`, account `~/.config/manicode`. The
   chat folder's name is the session id; each entry of
   `sessionState.mainAgentState.messageHistory` with a numeric `sentAt`
   (epoch milliseconds) is a response, and a history holding anything
   but objects is no session. The folder is the agent's `cwd` or
   `initialCwd`, else `sessionState.initialCwd`, else the file's own
   `initialCwd`. The ledger doesn't log Freebuff, so nothing is dropped
   as accidental.

   The Cursor app (`CursorAppActivity`): each account root's
   `User/globalStorage/state.vscdb`, opened read-only. The roots are
   `~/Library/Application Support/Cursor` plus each extra Cursor
   profile's `~/.swe/accounts/cursor/<id>/desktop`, and the root is
   the block's account ("Cursor", or "Cursor: <profile>"). Every
   `cursorDiskKV` row keyed `bubbleId:<composer>:<bubble>` is a response
   at its `createdAt` (an ISO time or epoch milliseconds), grouped by
   composer; `task-` composers (subagents) are left out. A chat has no
   folder, so its blocks read "No folder". The ledger's Cursor feed
   events stay off the calendar, as on the Mac.
5. Blocks regroup by harness, account and project, the key being the
   repository root, so worktrees of one repository merge and same-named
   folders of different ones stay apart, and merge while the next
   starts within the gap. Every Grok Bot session is the one "Grok Bot"
   project.

## Payload

`GET /v1/app/calendar?mode=week&anchor=2026-10-24&tz=Asia/Singapore&first_weekday=1`

| Parameter | Meaning |
|---|---|
| `mode` | `day`, `week`, `month` or `days` (with `n`, 2 to 9; 1 reads as `day`). Anything else is `week`. |
| `anchor` | The day the range is built around, `YYYY-MM-DD`; today in `tz` when missing or bad. |
| `tz` | The browser's IANA zone; the hub's own when missing or unknown. |
| `first_weekday` | ISO weekday the week starts on (1 Monday to 7 Sunday), from the browser's locale. |
| `rescan` | `1` rescans the ledger first. |

```json
{
  "mode": "week", "n": 7, "anchor": "2026-10-24", "today": "2026-10-24",
  "first_weekday": 1, "time_zone": "Asia/Singapore",
  "title": "October 2026", "subtitle": "Week 43",
  "empty": "No harness activity this week",
  "previous": "2026-10-17", "next": "2026-10-31",
  "range": {"start": 1792339200.0, "end": 1792944000.0},
  "totals_range": {"start": 1792339200.0, "end": 1792944000.0},
  "days": [{"date": "2026-10-19", "start": 1792339200.0, "end": 1792425600.0}],
  "gap_s": 1800,
  "blocks": [{
    "id": "claude|/home/me/.claude|/home/me/dev/swe|1792755710.7",
    "harness": "claude", "account": "/home/me/.claude", "account_label": "Claude: Default",
    "project": "swe", "project_key": "/home/me/dev/swe",
    "start": 1792755710.7, "end": 1792765400.1, "responses": 369,
    "tokens": 101928159, "cost_usd": 33.59, "unpriced_tokens": 0,
    "sessions": [{"id": "8bccd4fc-…", "row": "<catalog key>", "title": "Deploy the hub",
                  "cwd": "/home/me/dev/swe", "start": 1792755710.7, "end": 1792765400.1,
                  "responses": 167, "tokens": 41800000, "cost_usd": 13.69, "unpriced_tokens": 0,
                  "days": {"2026-10-23": {"responses": 167, "tokens": 41800000,
                                          "cost_usd": 13.69, "unpriced_tokens": 0}}}]
  }],
  "legend": [{"harness": "claude", "total_s": 29460.0,
              "accounts": [{"home": "/home/me/.claude", "label": "Claude: Default", "total_s": 29460.0}]}],
  "sessions": {"<catalog key>": {"…": "a /v1/app/sessions row"}},
  "scanned_at": 1792780645.9, "notes": [], "machine": "home-server"
}
```

`days` holds each column's local midnights, so the page never does
time-zone arithmetic. A session's `days` splits its counts by local day,
so a month cell counts only its own share of a block that runs past
midnight. `sessions` is a Sessions page row for every
session a block holds, keyed by its catalog key (a session gone from the
listing gets a stand-in row from the ledger, as the app's
`CalendarSessionIndex` does).

## How it differs from the Mac app

- **Sources**: the ledger's harnesses, Pi, Factory Droid and Freebuff
  session files and the Cursor app's database, on the hub machine. Devin
  cloud sessions stay Mac-only: the Mac app syncs them from Devin's API
  with the token in its Settings into its own store, which the hub
  doesn't share. A satellite's activity is not shown either.
  The hub keeps its file index in memory; the Mac's copy in
  `~/.swe/cache/calendar` is its own.
- **Grok Bot titles** come from the names the Mac app remembered in
  `~/.swe/data/grok-bot-names.json` (read only); the hub has no Bot
  roster, so a deleted Bot reads as its short id, not "Deleted Bot".
- **Tokens and spend** are added to the block card and the project
  sheet for ledger harnesses; the app shows time and responses only.
- **No swiping**: the grid steps a whole range with the chevrons or
  arrows instead of sliding a day at a time with buffer columns.
- **Side panel**: collapsing hides it (the toggle or S brings it back);
  the app floats it back over the grid on hover. On a phone it sits
  under the grid.
- **Accidental sessions** are always hidden; the app follows its "Hide
  accidental sessions" setting.
- **Hidden toggles, the view and the panel** are kept per browser
  (localStorage), not in the Mac app's defaults.
- **Week start** follows the browser's locale (`Intl.Locale` week
  info), Monday when it has none.
- **Resume** is "Copy resume command" in the session sheet, as on the
  Sessions page.
