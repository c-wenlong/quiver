# Doctor

A port of the Mac app's Settings › Doctor (`DoctorSection.swift`,
`DoctorModel` in `SWECore/Swe/Doctor.swift`). Page id `doctor`, at
`#/doctor`. It is not in the sidebar, as in the app; the Settings page
links to it, and its header links back to Settings.

Files: `swe/webapp/pages/doctor.py`, `static/pages/doctor.js`,
`static/pages/doctor.css`, `tests/test_webapp_doctor.py`.

## What the page shows

Title "Doctor", subtitle "Checks on PATH, Node and drift between swe
and your harnesses.", then one card:

| State | Card |
|---|---|
| Loading | "Running checks…" |
| Failed | the error, with Try again |
| No findings | "Everything checks out.", with Check again |
| Findings | "N findings" with a refresh button, one row per finding, folded past 8 ("Show N more" / "Show less") |

A row is the app's `FindingRow`: a dot coloured by severity (error red,
warning amber, info grey), the harness's logo when the finding is about one
harness and a logo ships for it, the message, the check id in mono, the
`source` file when the check names one, and, when the finding has a fix,
the fix command in a code block with a copy button. A finding with
`remedies` gets a remedies box instead: one row per way out, each with
its affordance: Run for `action`, Fix with agent for `agent` (a picker:
the agent's prompt, harness chips, Launch), a copy button for `command`,
nothing but the steps for `steps`. A finding with an `action` gets Run there. Run opens a confirm
panel under the block that says what will happen on which machine
("This runs swe init on home-server: it links the shared AGENTS.md
…"); its "Run swe init" button sends the request. While a fix runs its row
says "Running…" and every other Run is off, since the app runs one repair
at a time. The report then repaints from the hub's answer. A failure shows
under its row; when the fix made the finding go away but still reported a
failure, it shows in an error card above the list instead. A setup or init
that left a real file or folder alone lists those paths in a card above
the list with "Replace and back up…", which asks first and then runs
`swe init --force`.

## Payload

`GET /v1/app/doctor` is `swe doctor --json` built in process
(`harness.commands.doctor_report`, the code `cmd_doctor` runs), plus the
machine name, `home` (the home the findings are about, the launch's
`cwd`) and `launchable` (`repositories.harnesses`: the harnesses
`POST /v1/sessions` can start on the hub, for the agent picker's chips):

```json
{"ok": true, "machine": "home-server", "home": "/Users/kai",
 "launchable": [{"name": "claude", "display_name": "Claude",
                 "launch_model": true, "launch_effort": false,
                 "launch_yolo": true, "last_model": "",
                 "recent_models": []}],
 "findings": [{"check": "setup.unlinked", "severity": "warning",
               "message": "Claude doesn't read the shared AGENTS.md yet",
               "fix": "swe init", "harness": "claude", "action": "init",
               "remedies": [{"kind": "action", "action": "init",
                             "title": "Link it with swe init"}]}]}
```

A finding may carry `source` (the file behind it, shown under the check
id) and `remedies` instead of a bare `fix`: the ways out, best first,
each `{kind, title, detail?}`. `kind` is `action` (Run runs it like the
finding's own action), `agent` (Fix with agent opens the picker: the
remedy's `prompt` previewed, a harness chip row from `launchable`, and
Launch, which posts `{"harness", "cwd": home, "prompt"}` to
`/v1/sessions` (`yolo` too when the picked harness takes it), then
follows `/v1/launches/<id>` like the new-session sheet), `command` (a
`command` to copy) or `steps` (manual work, `steps` listed). `fix`
stays the one-line summary; findings without `remedies` (the `usage.*`
rows) keep the old fix strip.

`POST /v1/app/doctor/run` takes `{"check", "message"}`, the finding's
identity (the app's `Finding.id` is the two joined), plus an optional
`action` naming which of the finding's `kind: "action"` remedies to run;
with none the finding's own `action` runs, as before. The hub reads the
report again, finds that finding, and runs the fix in process:

| `action` | Runs | Same as |
|---|---|---|
| `setup` | `setup.commands.apply_setup` (no adopt, stars left alone) | `swe setup --apply --json` |
| `init` | `init.commands.run_init(home, quiet=True, ask=False)` | `swe init` with no terminal |
| `discover` | `discover_harnesses()` then `apply_findings(min_confidence="high")` | `swe harness discover --apply --json` |
| `install` | `harness.commands.install_harness(name, via)`, output captured | `swe install <name> --via=<via>` |
| `undeclare` | `hooks.settings.drop_script_declarations(path, targets, ...)` on each of the remedy's `settings` files | deleting the hook entries invoking a `targets` token (`script` is the display name) |
| `marketplace-drop` | `harness.doctor.drop_marketplace_entries(home, ...)` on the remedy's `settings_entries` (out of `~/.claude/settings.json`'s `extraKnownMarketplaces`) and `cli_entries` (out of `~/.claude/plugins/known_marketplaces.json`) | `claude plugin marketplace remove <name>` plus the settings edit, claude only |

The two delete actions write a harness's own files, which swe otherwise
only reads. Each file is copied to `~/.swe/backups` first, and a file
`declaration_managed_elsewhere` calls managed (a symlink, as a Nix store
path, or unwritable) is refused: the edit belongs in whatever generates
it. The emit side only puts editable files in a remedy, so a managed
target never reaches the Run button; an entry present in both files
lands in both lists and is dropped in one action. Both runners hold the
Plugins page's write lock, and each file is re-stat'd after the read so
a file changed under the write (a plugin toggle, a sync) is refused
instead of overwritten. Under a Nix-managed settings file
`marketplace-drop` still drops the `cli_entries`, since
`known_marketplaces.json` is not generated; an `undeclare` on one is
never offered.

`swe doctor` itself gives an off-PATH harness `action: "install"`, `via`
and the fix `swe install <name> --via=<via>` when swe knows an installer
whose tool is on the hub's PATH (`harness.doctor.install_method`), so the
Mac app's Run does the same install; with none, the fix stays `swe install
<name>`, which only lists installers, and there is no action. The
method is the one that put the stray copy where it is (read off its path,
as `swe install --json` reads its `uninstall`: an nvm copy came from npm), else the first usable
one in swe's order. Reinstalling through the PATH's own manager puts the
command on PATH. A failed install's error ends with the last lines of its
output.

Nothing in the body says what to run. The reply is `{"ran", "error",
"blocked", "report"}`: the action, what went wrong (`null` when nothing
did), the `~/`-relative paths a real file or folder still holds after a
setup or init (`[]` after discover) and the report read again, since even
a failed run may have fixed something. A setup whose linking pass stopped
reports `links.error`. A real file left in the way is not an error; it is
in `blocked`.

`POST /v1/app/doctor/replace` takes no body and runs
`run_init(home, force=True, quiet=True, ask=False)`, `swe init --force`:
each blocked path is copied to `~/.swe/backups`, then replaced with a
link. Its reply has the same shape, with `ran: "replace"`; `error` names
any path it still left alone (one resolving outside the home or into
`~/.swe`, which `--force` never touches).

| Status | Code | When |
|---|---|---|
| 400 | `bad_finding` | `check` or `message` missing, not text, or over 4000 characters |
| 404 | `unknown_finding` | that finding is not in the report now |
| 404 | `unknown_remedy` | the body's `action` names no action remedy on the finding now |
| 404 | `unknown_action` | any action but `run` and `replace` |
| 409 | `busy` | another fix is running (one at a time, not queued; the AGENTS.md page's Link and Replace count) |
| 422 | `no_action` | the finding has no fix swe can run itself |
| 403 | `bad_origin` | the hub's Origin check |
| 503 | `page_unavailable` | the report can't be built (a corrupt harness.json) |

Fixes also take the Harnesses page's registry lock (`harness.REGISTRY_LOCK`),
since all of them can rewrite harness.json.

## Differences from the Mac app

- Run asks first, in a panel under the row that names the command and the
  machine. The app's Run runs at once.
- "Run in Terminal" for a `swe …` fix with no action is left out: the
  browser can't open a terminal on the hub. The copy button stays.
- An off-PATH harness's Run is the same install in both. The app runs
  `swe install <name> --via=<via>` as a subprocess and shows what it
  printed (on the row after a failure, with Run in Terminal beside it for
  an installer that needs a password; in an "Installed" card after a
  success). This page shows the last lines of a failure's output in the
  row's error.
- `init` never opens the new-skills-folder picker, even if the hub was
  started from a terminal: new folders wait, as in the app's run.
- Replace and back up (`swe init --force`) after a run that hit a real
  file. The app has it on the AGENTS.md page only.
- A setup step that failed outright (`applied.errors`) is shown as well as
  a stopped linking pass; the app shows only the latter.
- An error whose finding went away is shown above the list; the app keys
  it to the row, so it is not seen.
- Fix with agent launches a coding agent through `/v1/sessions` with the
  remedy's prepared prompt and `cwd` at the hub's home, `yolo` when the
  harness takes it; the session shows up under Sessions like any other
  launch. The Mac app has no counterpart yet.
- No ⌘R; the refresh button and Try again / Check again do the same.
