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
  config/            registry (harness.json), catalogs, link records (versioned)
  secrets/.api_keys  credential values, mode 600               (gitignored)
  mcp.json           server definitions, ${REF} placeholders    (versioned)
  cache/             sessions, rate limits, counts             (gitignored)
  backups/           pre-overwrite copies                      (gitignored)
```

`$HOME` rather than `$HOME/.config` because 24 of 28 harness config dirs live
there.

## Data plane vs control plane

Two repos, two lifecycles. This repo is the control plane: code, in git,
public. `~/.quiver` is the data plane: state, in its own private git repo,
versioned separately from the CLI that reads it. Cloning this repo and
building it gets you a `swe` with no harnesses, no stars, nothing archived —
that state lives on the machine, or in whichever private repo the operator
points `~/.quiver` at.

The boundary is a rule, not just a directory split: code in this repo never
hardcodes what `harness.json` can say. `skills/layout.py` and
`find/plugins.py` keep small fallback tables for machines with no registry
data (see Capabilities-first, below), but those tables are read-only
defaults, never a second source of truth standing beside the registry.
Anything this repo needs to know about a specific harness — is it starred,
does it support plugins, where do its skills live — is a question for
`harness.json`, not a constant in source.

`.gitignore` enforces the boundary mechanically: `harness.json`, `mcp.json`,
`tools.json`, `providers.json`, `stars.json`, and the caches are all listed,
so a machine's actual state can never land in this repo by accident.
`~/.quiver` carries its own `.gitignore` (`cache/`, `backups/`, `secrets/`)
and its own git history, independent of this one.

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

## Registry: harness.json

One file says everything the CLI knows about a harness:
`~/.quiver/config/harness.json`, keyed by name. `registry.py` is the only
module that touches it — `load_registry()`, `save_registry()`,
`state_of()`, `is_active()`, `starred_names()`, `archived_names()`,
`active_names()`, and the read-only `load_registry_if_present()` that
`swe find` uses, so a look-only command can never seed the file on a
machine that has never run a write. (`load_registry()`'s default seeding
deep-copies `DEFAULT_TOOLS` before saving now, not shallow-copies it — a
shallow copy handed out the same nested per-harness dicts the module-level
default holds, so starring or archiving a harness on a fresh machine used
to mutate `DEFAULT_TOOLS` itself for the rest of the process.)

Each entry carries the harness's own fields (command, aliases, tags, ...)
plus:

| field | meaning |
|---|---|
| `state` | `"active"` \| `"starred"` \| `"archived"`; absent means active |
| `pin` | starred only — 1 is top, higher sorts lower |
| `archived` | archived only — `{reason, archived_at, usage}` |
| `capabilities` | optional — see Capabilities-first, below |

Three states, matching `multiselect.py`'s `STATES` tuple exactly — the
interactive editor cycles a row through the same three words the registry
stores, so there is no translation layer between what you pick on screen
and what lands on disk.

This used to be three files: `tools.json` said what a harness is,
`stars.json` said which ones you favourite, `archived.json` said which
ones you shelved. They always moved together in practice — starring meant
resolving an alias against the registry and then writing a second file —
and anything that wanted the full picture had to load all three and join
them by name. `stars.py` and `archive.py` are now compatibility shims:
same function signatures, same return shapes, but every read and write
goes through `registry.py` underneath, so `swe hs star`, `swe hs archive`,
and a handful of other old call sites kept working unmodified.

A fresh machine, or one upgrading from before this consolidation, gets a
lazy migration the first time `load_registry()` runs: `tools.json` plus
whichever of `stars.json` / `archived.json` exist get merged into
`harness.json`, and the legacy files are moved — never deleted — into
`~/.quiver/.backup/registry-migration-<date>/`. Once `harness.json` exists
on disk, it is authoritative; the migration never runs again, even if the
legacy files are still sitting there.

Archiving is deliberately not `swe remove`. Removing forgets the harness,
which loses the fact that you evaluated it, so a month later it reads as
untried and gets reinstalled. `swe list --scope` decides which state you
see: `active` (default), `archived`, or `all`.

## Capabilities-first

Two hardcoded tables predate the registry knowing about capabilities:
`skills/layout.py`'s `HARNESS_ROOTS` (which harness's skills root lives
where) and `find/plugins.py`'s `PLUGIN_FALLBACK` (which harnesses support
plugins, and where their install record lives). Both are now fallbacks,
not sources of truth. A harness entry's own
`capabilities.skills.{supported,root}` or `capabilities.plugins.{...}`
wins whenever the registry has one; the table only fires for a harness the
registry has never heard of, or for a machine with no registry data at
all.

This is why a registry entry knowing a root the table lacks is healthy —
capabilities extending the table is the design working — and why a table
entry the registry contradicts is also healthy: the fallback losing to a
capability that overrides it is the design working too. What is *not*
healthy: the same path filed under two different names (the join between
table and registry breaks), a `supported: true` capability with no `root`
to join on, or a table entry naming a harness the registry has never
heard of. `swe doctor`'s code-vs-data check (see Drift, below) enforces
exactly that distinction.

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

## Find: the harness activity filter

`swe find` and every one of its subviews (`amd`, `skills`, `plugins`,
`mcps`) take `--harness=active|all`, default `active`. `active` hides rows
belonging to an archived harness; starred still counts as active, since
starring is a pin, not a shelf. `all` shows archived harnesses too.

A hidden row is never silent. Every view that filters ends with a footer —
`N archived harnesses hidden; --harness=all to show` — so `--harness=active`
narrows what you see without ever costing you the knowledge that something
was narrowed. A row that cannot be resolved to a harness in `harness.json`
at all — the shared quiver copy, an unregistered config — is never
filtered either way: unknown is exactly the thing `swe find` exists to
keep visible, regardless of `--harness`.

Which harnesses get walked, and where each one's files live, comes from
`harness.json`'s own `capabilities` first, falling back to the hardcoded
tables above only for a harness the registry has never heard of — the same
capabilities-first rule the rest of the CLI follows.

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

## Drift

`swe doctor` used to check one thing: Node/PATH mismatches hiding a
globally installed harness. `harness/drift.py` adds a second, unrelated
kind of check — places where two parts of quiver that are supposed to
agree have quietly drifted apart. Four checks, all read-only and cheap
enough to run on every invocation:

| check | compares |
|---|---|
| help vs dispatch | `help_text.py`'s HELP topics against `cli.py`'s COMMANDS |
| registry schema | every `harness.json` entry's shape (state, pin, archived, capabilities) |
| code-table vs data | `HARNESS_ROOTS` / `PLUGIN_FALLBACK` against the registry's capabilities |
| dangling symlinks | top-level symlinks in the repo root and `~/.quiver` pointing nowhere |

Every check exists as a pure `check_*` function (plain data in, findings
out, no I/O) for unit testing, and `run_drift_checks()` wires the real
data in for `cmd_doctor`.

The principle behind all four, made explicit once capabilities-first
landed: a warning must be actionable. Before that change, a registry entry
the fallback table didn't know about, or a table entry the registry
happened to override, both read as drift — which meant `swe doctor` warned
about the system working exactly as designed. Now those are asymmetries
the design expects, not findings; what still warns is a broken join (a
name mismatch, a `supported` capability missing its `root`, a table entry
for a harness the registry has never heard of) — something an operator can
actually go fix.

## Command tree

Two layers, on purpose. The top level is shortcuts for the harness verbs
run most often; everything else lives under the domain command that owns
it.

```
swe <verb>                          top level: shortcuts only
  list · info · add · edit · remove · use · check · install
  discover                          alias for `swe harness discover`

swe <domain> <verb>                 domains: everything else
  harness    star · archive · discover · list · edit
  mcp        discover · sync · diff · doctor · export · import
  find       amd · skills · plugins · mcps
  providers  list · info · add · remove
  report     daily · weekly · followups · warnings
  skills     tree · link · unlink · move · discover · catalog
  session    use
  config     get · set · unset · edit · check · setup
```

`swe harness star` and `swe harness archive` live under the harness domain
deliberately, not at the top level: they change what you *decided* about a
harness rather than what it *is*, and grouping them with `list` / `info` /
`edit` keeps every verb that touches a harness's registry row in one
place instead of scattering favourite/shelve logic across the top-level
namespace. `swe doctor`'s help-vs-dispatch check enforces the shortcut
half of this policy mechanically — every top-level `COMMANDS` entry needs
a HELP topic (or a whitelisted alias), and every topic needs a matching
command. The dead top-level `star` help topic this consolidation removed
was the leftover of an earlier layout where that boundary did not exist.

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
