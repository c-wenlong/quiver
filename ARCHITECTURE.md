# Architecture

Quiver (`swe`) is a manager for AI coding CLIs. It unifies the things those
CLIs each invented separately: instruction files, skills, plugins, MCP servers,
session history and rate limits.

The CLI is stdlib-only. `tomli` is the single dependency, and only below
Python 3.11.

## The shape

```
                         cli.py  (dispatch, 177 lines)
                            │
        ┌──────────┬────────┼────────┬──────────┬─────────┐
        │          │        │        │          │         │
      init       harness  sessions  skills     mcp     reports
        │          │        │        │          │         │
        └──────────┴────────┼────────┴──────────┴─────────┘
                            │
              console · table · paths · configuration
```

Four modules sit at the bottom and depend on nothing else in the project:

| module | owns |
|---|---|
| `paths.py` | every path under `~/.quiver`; the single source of layout truth |
| `console.py` | colour, padding, width, truncation |
| `table.py` | column rendering |
| `configuration.py` | reading and writing `config/config.json` |

## The layout

`~/.quiver` is split by lifecycle, so the versioned half and the disposable
half never mix:

```
~/.quiver/
  AGENTS.md          canonical instructions, symlinked into 9 harnesses
  skills/            the shared skill tree, symlinked into 57 roots
  config/            registry, stars, catalogs, link records   (versioned)
  secrets/.api_keys  credential values, mode 600               (gitignored)
  mcp.json           server definitions, ${REF} placeholders    (versioned)
  cache/             sessions, rate limits, counts             (gitignored)
  backups/           pre-overwrite copies                      (gitignored)
```

`$HOME` rather than `$HOME/.config` because 24 of 28 harness config dirs live
there.

## The core idea: one file, many names

Every harness wants its own instruction filename (`CLAUDE.md`, `AGENTS.md`,
`CRUSH.md`) in its own directory. Quiver writes one file and symlinks it under
each name, so editing one changes all of them.

The same trick covers skills: one tree at `~/.quiver/skills`, symlinked into
every harness's `skills/` directory.

Link state is a small vocabulary shared by `swe init`, `swe list` and
`swe find`:

| state | meaning |
|---|---|
| `linked` | points at the shared copy |
| `relink` | a symlink aimed somewhere else |
| `create` | nothing there yet |
| `absorb` | a real directory whose contents are all duplicates or empty |
| `keep` | a real directory holding files that exist nowhere else |
| `conflict` | a real file where the link should go |
| `skipped` | harness not installed |

`keep` is the safety valve. It is never overwritten on a plain run, because
absorbing it would hide the only copy of something behind the shared tree.

## Standing

Two decisions about a harness live outside the registry, because the
registry describes what a harness *is* and these record what you decided
about it:

| file | holds |
|---|---|
| `config/stars.json` | favourites, in pin order |
| `config/archived.json` | name, reason, and date archived |

Archiving is deliberately not `swe remove`. Removing forgets the harness,
which loses the fact that you evaluated it, so a month later it reads as
untried and gets reinstalled. `swe list --scope` decides which standing
you see: `active` (default), `archived`, or `all`.

## Scope

A file is **global** when it sits directly in a harness root (`~/.<tool>/` or
`~/.config/<tool>/`) or is symlinked to the shared copy. One level deeper is
**vendored** (plugin caches, editor extensions). Everything else is **local**.

This rule drives `--scope=[all,global,local]` across `swe find`.

## Discovery

Two scans, deliberately different:

- **`init/layout.discover_skill_roots`** globs `~/.*/skills` and
  `~/.config/*/skills`. This is the authority on where harnesses keep skills.
- **`skills/discovery.skill_roots`** builds on it and adds the three kinds it
  cannot reach: plugin caches (a level deeper), non-standard names
  (`~/.cursor/skills-cursor`), and user-registered catalogs.

Roots are deduped by `realpath`, so 57 symlinked roots collapse to the 8 real
directories behind them.

## Sessions

Every harness stores history differently, so parsing is split into engines:

| engine | format | harnesses |
|---|---|---|
| `jsonl_engine` | newline-delimited JSON | claude, codex, most others |
| `json_engine` | single JSON documents | cursor, several forks |
| `sqlite_engine` | SQLite databases | editor-embedded tools |

`aggregator.py` fans out across them and merges. `failures.py` records parser
crashes so a broken parser reports an error rather than reading as "no
history" — added after Cursor silently showed 0 sessions for months.

## Caching

| cache | TTL | why |
|---|---|---|
| sessions | 60s | `swe session` should show the run you just finished |
| rate limits | 300s | network round trip |
| session counts | 24h | walks every transcript; moves by a handful a day |

`swe list` runs in ~70ms because usage is opt-in. It was 870ms when usage was
fetched unconditionally.

## MCP

`~/.quiver/mcp.json` is the hub. Credentials are stored as `${NAME}`
references; values live in `~/.quiver/secrets/.api_keys` (mode 600,
gitignored). Resolution happens when a harness config is written, not by
exporting to the environment, because Claude and Cursor launch from the Dock
and never read a shell profile.

Servers are namespaced by function: `rf` reference, `pd` productivity,
`dv` development, `so` social, `sr` search. `swe find mcps` shows what the
hub holds against what each harness actually has.

One server may appear as both a local and a remote entry, since those are
two ways to reach it. Two locals, or two remotes, under different names
are the same thing filed twice, and are reported as duplicates.

`swe find mcps` reads two sources deliberately. The registered config
paths tell you how far a sync got; a walk of the harness directories
tells you about configs nobody registered, which is where an unmanaged
server hides. The walk covers JSON and TOML, files sitting directly in
`$HOME` (`~/.claude.json` belongs to no harness directory), and follows
the same vendored rule as the rest of `find`.

## Layering rules

1. `console`, `table`, `paths` and `configuration` import nothing else from
   the project.
2. Command modules own presentation. Logic modules mostly do not print;
   `setup/wizard.py` is the deliberate exception (it is an interactive
   flow), and `harness/rate_limits.py` prints diagnostics from inside
   fetch logic, which is a leak rather than a decision.
3. `cli.py` is dispatch only.

One cycle exists, `harness <-> sessions`, and both directions are
function-local imports rather than module-level, so it does not break loading.
