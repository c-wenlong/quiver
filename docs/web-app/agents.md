# AGENTS.md

The Mac app's AGENTS.md page (`AgentsMdPage.swift`, `AgentsModel`,
`InitCheck`) as a web page. Module
`quiver/webapp/pages/agents.py`, script `static/pages/agents.js`, styles
`static/pages/agents.css` (it also uses a few pieces of `skills.css` and
`skills.js`).

## What it shows

- **Global**: the Harnesses strip from `swe init --check --json`, built in
  process by `init/commands.py::check_document` (the same function the CLI
  now calls). One dot per harness that takes the shared instruction file:
  green linked, red when a real file is in the way (`keep`, `conflict`),
  amber when a plain `swe init` would fix it, grey otherwise, with the
  state in words when it isn't linked. "Link with swe init" ends the strip
  when a plain `swe init` would link something or the shared file is
  missing. When a real file or folder is in the way, a note says so with
  "Replace and back up…", which opens a confirm listing every path
  `swe init --force` would replace (instructions, skills and hooks) before
  it runs. Then the shared `~/.swe/AGENTS.md` as text with "Linked into
  N harnesses".
- **Project**: a folder on the hub, typed or picked from the hub's recent
  session folders, and its AGENTS.md and CLAUDE.md as tabs. The app's tab
  titles ("CLAUDE.md → AGENTS.md" for a link to the other file, "(new)" for
  a missing one). The browser remembers the folder.

## Payload

`GET /v1/app/agents` is the `swe init --check --json` document
(`agents_file`, `agents_file_exists`, `harnesses: [{name, instructions:
{path, state} | null, skills}]`) plus `machine`, `home`, `text` (the shared
file, or null when missing or not previewable), `blocked` (the
`~/`-relative paths a real file or folder holds, from
`doctor.blocked_paths`) and `suggestions` (up to 12 recent session folders
on the hub machine, under its home).

`POST /v1/app/agents/link` runs `swe init` and `POST
/v1/app/agents/replace` runs `swe init --force`, in process through
`doctor.run_init_now` (`run_init(home, quiet=True, ask=False)`), so new
skills folders wait and nothing asks. They share the Doctor page's one-fix
lock (409 `busy`) and the registry lock. The reply is the Global document
read again plus `ran` and `error` (`null` unless something failed, or a
forced run still left a path alone because it resolves outside the home
or into `~/.swe`). Any other action is a 404.

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

Also:

- "Replace and back up…" asks first, inline, and lists the paths it will
  replace; the app's asks in a dialog without the list.
- The folder picker (`NSOpenPanel`) becomes a path field with suggestions,
  limited to folders under the hub's home.
- The file shows as plain text, the app's Raw view; there is no Rich view.
