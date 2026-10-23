# AGENTS.md

The Mac app's AGENTS.md page (`AgentsMdPage.swift`, `AgentsModel`,
`InitCheck`) as a web page, read only. Module
`quiver/webapp/pages/agents.py`, script `static/pages/agents.js`, styles
`static/pages/agents.css` (it also uses a few pieces of `skills.css` and
`skills.js`).

## What it shows

- **Global**: the Harnesses strip from `swe init --check --json`, built in
  process by `init/commands.py::check_document` (the same function the CLI
  now calls). One dot per harness that takes the shared instruction file:
  green linked, red when a real file is in the way (`keep`, `conflict`),
  amber when a plain `swe init` would fix it, grey otherwise, with the
  state in words when it isn't linked. The app's note about real files in
  the way, then the shared `~/.quiver/AGENTS.md` as text with "Linked into
  N harnesses".
- **Project**: a folder on the hub, typed or picked from the hub's recent
  session folders, and its AGENTS.md and CLAUDE.md as tabs. The app's tab
  titles ("CLAUDE.md → AGENTS.md" for a link to the other file, "(new)" for
  a missing one). The browser remembers the folder.

## Payload

`GET /v1/app/agents` is the `swe init --check --json` document
(`agents_file`, `agents_file_exists`, `harnesses: [{name, instructions:
{path, state} | null, skills}]`) plus `machine`, `home`, `text` (the shared
file, or null when missing or not previewable) and `suggestions` (up to 12
recent session folders on the hub machine, under its home).

`GET /v1/app/agents?folder=<path>` returns `{folder, home, files: [{name,
path, exists, links_to, outside, text}]}` for AGENTS.md and CLAUDE.md.
`~/` is the hub's home. The folder must be absolute and resolve under the
hub's home (400 relative, 403 outside, 404 missing), and a file is read
only when it resolves under the home too (`outside: true` otherwise). Text
follows the Skills page's rule: under 1 MB, no NUL in the first 8 KB.

## Differences from the Mac app

Writes are left out; they stay in `swe` and the Mac app:

- Editing and saving AGENTS.md or a project file (Raw/Rich editor, Save,
  Revert, the changed-on-disk banner), and creating a missing file.
- "Link with swe init". When a plain `swe init` would link something, the
  strip says to run it on the hub instead.

Also:

- The folder picker (`NSOpenPanel`) becomes a path field with suggestions,
  limited to folders under the hub's home.
- The file shows as plain text, the app's Raw view; there is no Rich view.
