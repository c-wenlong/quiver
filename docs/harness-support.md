# Harness support

SWE knows about more harnesses than it has features for. This page shows,
per harness, what actually works today. **Yes** means fully supported.
**Partial** means it works with a limitation, spelled out in the notes under
each table. **No** means not implemented, usually because the harness writes
no data SWE can read.

Only harnesses `swe discover` can find are listed, plus Jules and Mastracode,
which `swe install` knows but discovery does not.

## Setup

| Harness | Install | Instructions | Skills | Plugins | MCP sync |
|---|---|---|---|---|---|
| Claude Code | Yes | Yes | Yes | Yes | Yes |
| Codex | Yes | Yes | Yes | Yes | Yes |
| Cursor | Yes | Yes | Yes | Partial | Yes |
| Gemini | Yes | Yes | Yes | No | Yes |
| Copilot | Yes | No | Yes | No | Yes |
| Cline | Yes | Yes | Yes | No | Yes |
| Kilo | Yes | Yes | Yes | No | Yes |
| OpenCode | Yes | Yes | Yes | No | Yes |
| Droid | Yes | Yes | Yes | No | Yes |
| Devin | Yes | Yes | Yes | No | Partial |
| Crush | Yes | Yes | Yes | No | Partial |
| Qwen Code | Yes | Yes | Yes | No | Partial |
| Mistral Vibe | Yes | No | No | No | Partial |
| Goose | Yes | No | No | No | Partial |
| Aider | Yes | No | No | No | Partial |
| Continue | Yes | No | No | No | Partial |
| Pi | Yes | No | No | No | Partial |
| Forge | Yes | No | No | No | Partial |
| Augment | Yes | No | No | No | Partial |
| Kiro | Yes | No | No | No | Partial |
| Blackbox | Yes | No | No | No | Partial |
| Freebuff | Yes | No | No | No | Partial |
| Jules | Yes | No | No | No | No |
| Mastracode | Yes | No | No | No | No |

Notes:

- **Cursor, plugins:** `swe plugins` lists Cursor's installed plugins, but
  install, uninstall, enable and disable only run through the Claude and
  Codex CLIs.
- **Copilot, instructions:** SWE links its shared skills directory but
  has no confirmed instructions file for Copilot, so `AGENTS.md` is not
  linked there.
- **Cline, instructions:** linked at `~/.agents/AGENTS.md`, the shared
  location Cline reads.
- **MCP, Partial:** these harnesses sync through the conventional
  `~/.<name>/mcp.json` path, which SWE cannot confirm the harness
  actually reads. Claude, Cursor, Copilot, Cline, Gemini, OpenCode, Kilo,
  Codex and Droid have verified config locations instead.
- **Jules and Mastracode:** install-only. They are not in the discovery
  catalog, so MCP sync never sees them.

## Sessions

Session listing is the same in `swe session` and the app's Sessions page:
the app reads swe's output. Resume differs only for the Partial rows below.

| Harness | Sessions list | Resume a session |
|---|---|---|
| Claude Code | Yes | Yes |
| Codex | Yes | Yes |
| Cursor | Yes | Yes |
| OpenCode | Yes | Yes |
| Kilo | Yes | Yes |
| Pi | Yes | Yes |
| Droid | Yes | Yes |
| Copilot | Yes | Yes |
| Devin | Yes | Yes |
| Freebuff | Yes | Yes |
| Cline | Yes | Yes |
| Gemini | Yes | Partial |
| Continue | Yes | Partial |
| Crush | Yes | Partial |
| Forge | Yes | Partial |
| Qwen Code | No | No |
| Mistral Vibe | No | No |
| Goose | No | No |
| Aider | No | No |
| Augment | No | No |
| Kiro | No | No |
| Blackbox | No | No |
| Jules | No | No |
| Mastracode | No | No |

Notes:

- **Resume, Partial:** Gemini, Continue, Crush and Forge take no session id
  on their command lines. `swe session` can only reopen the harness in the
  session's directory, not the session itself, and the app offers no
  Resume button for them.
- **Codex:** resumes by thread id; sessions whose ids carry no thread uuid
  offer no resume.

## Usage in the app

| Harness | Tokens and cost | Calendar activity | Usage limits |
|---|---|---|---|
| Claude Code | Yes | Yes | Yes |
| Codex | Yes | Yes | Yes |
| OpenCode | Yes | Yes | No |
| Devin | Yes | Yes | Yes |
| Droid | Partial | Yes | Yes |
| Pi | Yes | Yes | No |
| Freebuff | No | Yes | Yes |
| Cursor | Yes | Yes | Yes |
| Copilot | No | No | Yes |
| Gemini | No | No | No |
| Cline | Partial | No | No |
| Kilo | No | No | No |
| Crush | No | No | No |
| Continue | No | No | No |
| Forge | No | No | No |
| Qwen Code | No | No | No |
| Mistral Vibe | No | No | No |
| Goose | No | No | No |
| Aider | No | No | No |
| Augment | No | No | No |
| Kiro | No | No | No |
| Blackbox | No | No | No |
| Jules | No | No | No |
| Mastracode | No | No | No |

Notes:

- **Tokens and cost:** Claude, Codex, OpenCode, Devin and Pi write logs
  SWE prices per request. Droid and Cline give SWE each session's
  totals instead, so their spend shows as a lump at the session's end. Every
  other harness shows no spend on the Overview and no cost next to its
  sessions.
- **Cursor:** tokens and cost come from Cursor's own usage feed, which
  covers the editor, `cursor-agent` and cloud agents; the cost is Cursor's
  figure, not SWE's price list. Calendar activity still comes from the
  editor's own database, so feed events never draw a second calendar row.
- **Grok Bot:** uses the Cursor account, so the same feed gives it tokens
  and cost (listed as Grok Bot) and a calendar row; it has no limits meter
  of its own.
- **Droid and Cline, tokens and cost:** both keep token totals per
  session rather than per request, so SWE counts the whole session at
  its last activity (Droid) or its end (Cline). Droid reports credits,
  not dollars, so its cost comes from SWE's price list.
- **Freebuff:** the calendar reads its session files, but SWE doesn't
  import its token counts yet, so it contributes time, not spend.
- **Usage limits:** meters exist for Claude, Codex, Copilot, Cursor, Devin,
  Droid and Freebuff. Devin and Cursor report several quota windows; the
  rest report their main limits.

## Accounts and processes

| Harness | Multiple accounts | Account-aware launch | Compute attribution |
|---|---|---|---|
| Claude Code | Yes | Yes | Yes |
| Codex | Yes | Yes | Yes |
| Devin | Partial | Yes | Yes |
| Cursor | Partial | Yes | Yes |
| Every other discovered harness | No | No | Yes |

Notes:

- **Multiple accounts:** only Claude, Codex, Devin and Cursor can hold extra
  profiles. Claude and Codex extras are full accounts: own sign-in, own
  usage, own cost.
- **Cursor:** the `cursor-agent` CLI keeps one global sign-in, so extra
  accounts are desktop-only (each gets its own app instance and data
  directory).
- **Devin:** extra accounts show usage limits, but tokens and cost come
  from the default account only.
- **Compute:** the Compute page attributes CPU and memory for every starred
  harness by matching its command names. Harnesses whose config lives
  outside `~/.<name>` (Droid, Qwen Code, Mistral Vibe) are matched by
  process name only.

## Known gaps

- **Tokens and cost exist for eight harnesses only** (Claude, Codex,
  OpenCode, Devin, Pi, Droid, Cline and Cursor, plus Grok Bot through the
  Cursor account; Droid and Cline as session totals). For everything else
  the Overview shows time and limits, never spend.
- **Copilot gets shared skills but no instructions link**; no confirmed
  file exists to link.
- **Devin extra profiles are not counted in the ledger**, so their spend is
  invisible even though their limits show.
- **Grok** has a session reader, but no discovery or install entry. Its
  sessions appear in `swe session` output and the app's Sessions page,
  but discovery cannot add it to the harness list.
- **Mistral Vibe, Goose, Aider, Augment, Kiro and Blackbox** are
  discoverable and installable but have no instructions, skills, sessions
  or usage support yet.
