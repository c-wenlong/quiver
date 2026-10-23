# Skills

The Mac app's Skills page (`SkillsPage.swift`, `SkillDetailView` in
`PrimitiveDetail.swift`, `SkillsModel` in `QuiverCore/Primitives/Skills.swift`)
as a web page, read only. Module `quiver/webapp/pages/skills.py`, script
`static/pages/skills.js`, styles `static/pages/skills.css`.

## What it shows

- **List** (`#/skills`): every skill on the hub machine outside a plugin,
  from `swe skills --json` built in process (`skills/inventory.py::installed_skills`).
  A skill moves to the Plugins page when its source is `plugin` and exactly
  one catalog plugin owns the deepest folder holding it
  (`plugins/catalog.py::plugin_dirs` and `owners`, the rule the app's
  `SkillPlacement` follows), but only once the web app has a Plugins page
  to show them on; until then every skill is listed here. If the catalog
  can't be read, every skill stays and a caption says why. Harness and Source selects (only values the list
  has), "Filter skills" search over name, description and root, the
  "N of M skills" count, the "N skills ship inside plugins" link to
  `#/plugins`, the app's order (Shared, Harness, Catalog, Project, Plugin,
  Built in, then inventory order) and its fold at 15 rows.
- **Harness links** in the header: "8 of 10 linked" with an amber dot when a
  folder is blocked. It opens the chips per harness skills folder (Linked,
  Not linked, Blocked) and, per chip, the folder path and the app's
  `SkillRoot.explanation` sentence.
- **Detail** (`#/skills?skill=<folder>`): the lock note, the header card
  (name, source badge, path, description, the app's per-source note and the
  link chips: every folder for a shared skill, the loading harnesses
  otherwise), the SKILL.md frontmatter fields (simple `key: value` lines,
  as `MarkdownFrontmatter` reads them, folded to 3), the SKILL.md text, and
  the Contents card (`PrimitiveTree`'s sections, folded to 3). A file opens
  read only in a sheet.

## Payload

`GET /v1/app/skills`:

```json
{"machine": "home-server", "home": "/home/dev",
 "shared_dir": "/home/dev/.quiver/skills",
 "harnesses": [{"name": "codex", "label": "codex", "path": "…/.codex/skills",
                "state": "keep", "detail": "6 of 6 skills exist nowhere else", "archived": false}],
 "skills": [{"name": "tdd", "description": "…", "path": "…", "root": "shared",
             "source": "shared", "harnesses": ["claude", "codex"], "plugin": null}],
 "plugin_skill_count": 84, "catalog_error": null}
```

`skills` holds every skill; `plugin` is the ref of the plugin a skill
moves to, else null, and `plugin_skill_count` counts those. The page
hides them while `PAGES` has a `plugins` page.

`GET /v1/app/skills?skill=<folder>` adds `skill`, `editable` (the app's
`PrimitiveOwnership.isEditable`, which only picks the lock note's words),
`skill_file`, `skill_md` (text, or null when it can't preview),
`frontmatter` (`[{key, value}]` or null) and `tree`
(`[{kind, title, entries: [{name, path, folder, preview?, children?}]}]`,
paths relative to the folder), plus `machine`, `home`, `shared_dir` and
`harnesses` so the header renders on a deep link.

`GET /v1/app/skills?skill=<folder>&file=<relative path>` returns
`{name, path, outside, text}`.

Only a folder the inventory lists is accepted (404 otherwise). A file must
stay inside that folder once symlinks resolve (`outside: true` and no text
otherwise; an absolute name is a 400). Text is read only under 1 MB with no
NUL byte in the first 8 KB, as `PrimitiveTree.isTextFile` decides.

## Differences from the Mac app

Writes are left out; they stay in `swe skills` and the Mac app:

- Link to shared skills, Unlink, and Link all in the Harness links popover
  (the popover says to use `swe skills link`).
- New skill (`swe skills new`); the empty state names the command instead.
- Editing the frontmatter form and the files in Contents (Raw/Rich editor,
  Save, Revert, New part, Move to Trash).
- Show in Finder.

Also:

- The skills come from the hub machine, so the subtitle names it rather
  than "this Mac".
- Project skills (`<cwd>/.cursor/skills`) are read with `/` as the working
  folder, as the app's `swe` runs, so none is listed.
- A back link replaces the app's ⌘[ and sidebar reselect; the detail has
  its own URL.
- SKILL.md is shown in its own card on the detail page; in the app it opens
  from Contents.
- Paths are not middle-truncated by measured width; a long one is cut to
  90 characters with both ends kept.
