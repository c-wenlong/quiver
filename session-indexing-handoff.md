# Handoff: session indexing, lessons from Orca (2026-10-20)

Read [AGENTS.md](AGENTS.md) first. Its test, throwaway-HOME, reinstall-then-verify and PR-loop rules all apply. This file is a research snapshot and is not committed. Line numbers were correct on `main` at `d976f42`; re-check them before editing.

Orca lives at `../examples/orca` (relative to this repo). Its index code is in `src/main/ai-vault-search/`, and the transcript reader it reuses is in `src/main/ai-vault/`. Below, `orca:` means `../examples/orca/src/`.

## Why this exists

Orca's first run shows an "Indexing your agent sessions" dialog: "901 of 1,466 sessions · 296.4K messages searchable", done in about a minute, and search works while it runs. The user asked how it works, how it differs from Quiver, and what Quiver should copy.

## Short answer

Quiver has no full-text transcript index. Session search (`swe session --search`, and the Sessions page filter) is a substring match over title, path, agent, tool and id. The only SQLite index built from transcripts is the usage ledger, which stores token counts and no message text.

Orca builds a SQLite FTS5 index of message text. Its speed comes from four choices:

1. One read of each transcript feeds both the sidebar list and the search index.
2. Files are processed newest first and each file commits on its own, so recent sessions are searchable within seconds.
3. Tool output (80 to 97% of transcript bytes, per its own comment) is cut to the first 3 KB per tool message, and Claude `subagents/` files are skipped.
4. Progress has no counters. The UI polls every 2 s and the status call runs `count(*)` over the index tables.

The dialog is partly cosmetic. Its bar is indeterminate because the total grows as discovery runs, "sessions" are really transcript files, and "messages" are 8,000-character chunk rows, not conversation turns.

## Side by side

| Stage | Orca | Quiver today |
|---|---|---|
| Discover | Newest first across agents; skips Claude `subagents/` | Ledger walks path-sorted and includes 2,257 subagent files |
| Read | One streaming read per file, two consumers (sidebar list, index) | Two separate pipelines: Python `swe session` re-parses every file every time; Swift ledger reads incrementally for tokens |
| Store | FTS5 table of message text, commit per file | Ledger: tokens only. No message text anywhere |
| Incremental | Per-file cursor (dev, ino, offset, mtime, size, state) | Ledger cursor (inode, size, mtime, offset, 4 KB head and tail hash, parser state). Python side has none |
| Progress | `count(*)` per poll, UI polls every 2 s | Spinner until the scan finishes |
| Query | FTS5 MATCH, bm25 weights, snippets, typo repair | Case-insensitive substring over metadata |

Quiver's ledger cursor is already stronger than Orca's on one point. Orca accepts an append if the byte before the saved offset is `\n`; Quiver also checks a head and tail hash. Keep Quiver's check.

## Measurements on this Mac (read-only)

| What | Value |
|---|---|
| `~/.claude/projects` | 2,964 `.jsonl` files (707 top-level sessions, 2,257 subagent files), 8.5 GB. 9 files over 50 MB, largest 98 MB |
| `~/.codex/sessions` | 68 rollouts, 419 MB; `archived_sessions` 10 rollouts, 337 MB |
| Ledger DB | `~/.quiver/data/ledger/ledger.sqlite` 34 MB plus 6.4 MB WAL; 261,023 events; 3,812 cursors |
| `swe session 100000 --json` | 3.6 s wall, 870 KB JSON (this is what the app runs to list sessions) |
| `swe session --search=ledger` | 1.2 s |

Orca's code comment claims about 26 MB/s of transcript throughput and about 1 s per 32 MB commit (`orca:main/ai-vault-search/session-search-index-writer.ts:22-33`). Nobody has benchmarked either app head to head.

## Recommendations, in order

Each item is one PR. Items 1 and 2 need no product decision. Item 3 does (see Open decisions).

### 1. Ledger first-scan progress and partial results

This is already on the roadmap: [app/ROADMAP.md:153](app/ROADMAP.md) (M8 Phase 5), "First ledger scan: show files done out of total, and partial results. A heavy user waits minutes behind a spinner."

Copy Orca's approach:

- Expose a cheap status read on a read-only ledger connection: total candidate files known to the current scan, files whose cursor is current, and event count. Orca's version is three `GROUP BY` / `count(*)` queries (`orca:main/ai-vault-search/session-search-store.ts:317-359`, `session-search-indexer.ts:195-217`).
- Have the Overview card poll it every 2 s while a scan runs and show "N of M files". Keep M honest: it is files discovered so far, so either finish discovery before reading or show the bar as indeterminate, as Orca does (`orca:renderer/src/components/feature-tips/SessionSearchTipDialog.tsx:47-77`).
- Partial results already come for free because the ledger commits one `BEGIN IMMEDIATE` per file ([Ledger.swift:337-357](app/Sources/QuiverCore/Ledger/Ledger.swift)). The Overview just needs to re-read during the scan, not only at the end.
- Order the reads newest first by mtime so the last few days fill in first.

Gotcha: `jsonlFiles(under:)` sorts by path on purpose, "so dedup's keep-first is deterministic" ([Ledger.swift:377-387](app/Sources/QuiverCore/Ledger/Ledger.swift)). Changing the order changes which duplicate event survives. Either keep a deterministic tie-break that dedup can rely on, or make dedup order-independent (for example keep the earliest timestamp) before reordering. Write a test that scans the same fixture in two orders and gets identical totals.

Relevant code: scan loop [Ledger.swift:233-292](app/Sources/QuiverCore/Ledger/Ledger.swift), cursor logic `:312-374`, `ScanReport` `:130-143`, loading state [OverviewModel.swift:166](app/Sources/QuiverCore/Ledger/OverviewModel.swift), spinner copy [OverviewPage.swift:78](app/Sources/QuiverApp/Pages/OverviewPage.swift).

### 2. Stop re-parsing every transcript to list sessions

The app lists sessions by running `swe session 100000 --json` ([Sessions.swift:114-125](app/Sources/QuiverCore/Sessions/Sessions.swift)), which parses every file of 16 harnesses on every call (3.6 s here). The ledger already visits every JSONL file incrementally. Orca's lesson: one read, several consumers.

Options, cheapest first:

- Persist the Python listing per file keyed by (path, size, mtime) so unchanged files are skipped. The existing `session_cache.json` has a 60 s TTL and only `swe list` uses it ([aggregator.py:49-109](src/quiver/sessions/aggregator.py)).
- Or record session metadata (title, cwd, first and last timestamp) in the ledger as it scans, and let the Sessions page read it from SQLite. This moves listing into Swift, which is a bigger architectural change; check [ARCHITECTURE.md](ARCHITECTURE.md) for where the CLI/app boundary is meant to sit before choosing it.

Also fix [transcripts.py:371-387](src/quiver/reports/transcripts.py): `_find_session_file` globs `root/**/*.jsonl` for every session lookup. Build the id-to-path map once per call.

### 3. Full-text transcript search (needs a go-ahead)

If the user wants it, piggyback on the ledger scan so each file is still read once. Today `ClaudeLogParser` drops any line without `"usage"` before decoding ([ClaudeLogParser.swift:18-21](app/Sources/QuiverCore/Ledger/ClaudeLogParser.swift)), so a second consumer needs its own pass over the same chunk; keep the `"usage"` fast path for the ledger consumer.

Schema to start from (Orca, `orca:main/ai-vault-search/session-search-schema.ts:23-92`):

```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(
  user_text, assistant_text, tool_text, identifiers,
  tokenize="unicode61 tokenchars '_.-/+'", detail=full);
```

- Separate `sessions`, `files` (cursor) and `messages(id, session_row_id, role, ts)` tables; `messages.id` equals the FTS rowid. No sync triggers.
- One row per chunk, only the column for that role filled. Split user and assistant text at about 8,000 characters on a token boundary; cut tool text to its first 3,072 characters (`orca:main/ai-vault-search/session-search-message-rows.ts`).
- The `identifiers` column holds camelCase, snake_case and path pieces, up to 4,000 terms (`session-search-identifier-split.ts`). This is what makes searches like `jsonlFiles` or `Ledger.swift` hit.
- Query: quote every term, try exact phrase, then AND, then OR. bm25 weights 3/2/1/1 for user/assistant/tool/identifiers, group to one row per session before `LIMIT`, run `snippet()` only for the returned page (`session-search-retrieval.ts:102-137`, `:265-280`; `session-search-snippet.ts:63-90`).
- Snippet gotcha from Orca: use `rowid IN (SELECT ?)`; a bound `rowid = ?` next to MATCH was ignored by the planner.
- Keep reads short on their own connection. Orca saw the WAL grow from 10 MB to 266 MB when a read snapshot stayed open across an await (`session-search-store.ts:129-142`). Quiver's ledger already uses separate read-only connections; keep that.
- Re-reading a rewritten file: insert a new session row and delete the old `sessions` row only; queries join `sessions`, so stale message rows stop matching at once. A background drain deletes them 256 rows at a time with `PRAGMA incremental_vacuum(2000)` (`session-search-retention-delete.ts`, `session-search-index-writer.ts:243-258`).
- Hold out a file after 3 failures at the same mtime (`session-search-read-decision.ts:14`, `:86-92`). Skip any single record over 10 MB.
- Put the DB under `~/.quiver/data/` (never read Orca's or any other app's data), add an off switch and a "Clear search data" that deletes the DB plus `-wal`/`-shm`.
- Expose it to the CLI (`swe session --grep` today is metadata only, [help_text.py:209](src/quiver/help_text.py)) and the MCP server (`get_recent_sessions` has no search parameter, [server.py:22-58](src/quiver/mcp/server.py)), not just the app.

## What not to copy

- Orca's throttle (read for about 5 s out of every 25 s, `session-search-indexer-options.ts`). It suits a background child process in Electron. Quiver's ledger is a Swift actor that yields per file; keep that.
- Orca's weaker append check (see Side by side).
- The "sessions" label for file counts. Quiver should say files, or count real sessions.

## Open decisions for the user

1. Does Quiver want full-text transcript search at all (item 3)? There is no roadmap milestone for it; M4's "Search" ([app/ROADMAP.md:76](app/ROADMAP.md)) was metadata search.
2. Should subagent transcripts be listed or searched? Orca skips them; the ledger counts their tokens.
3. For item 2, keep listing in Python with a persistent per-file cache, or move it into the Swift ledger?
