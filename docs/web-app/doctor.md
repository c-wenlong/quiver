# Doctor

A port of the Mac app's Settings › Doctor (`DoctorSection.swift`,
`DoctorModel` in `QuiverCore/Swe/Doctor.swift`). Page id `doctor`, at
`#/doctor`. It is not in the sidebar, as in the app; the Settings page
links to it, and its header links back to Settings.

Files: `quiver/webapp/pages/doctor.py`, `static/pages/doctor.js`,
`static/pages/doctor.css`, `tests/test_webapp_doctor.py`.

## What the page shows

Title "Doctor", subtitle "Checks on PATH, Node and drift between quiver
and your harnesses.", then one card:

| State | Card |
|---|---|
| Loading | "Running checks…" |
| Failed | the error, with Try again |
| No findings | "Everything checks out.", with Check again |
| Findings | "N findings" with a refresh button, one row per finding, folded past 8 ("Show N more" / "Show less") |

A row is the app's `FindingRow`: a dot coloured by severity (error red,
warning amber, info grey), the harness's logo when the finding is about one
harness and a logo ships for it, the message, the check id in mono, and,
when the finding has a fix, the fix command in a code block with a copy
button. A finding with an `action` gets Run there. Run opens a confirm
panel under the block that says what will happen on which machine
("This runs swe init on kai-home-server: it links the shared AGENTS.md
…"); its "Run swe init" button sends the request. While a fix runs its row
says "Running…" and every other Run is off, since the app runs one repair
at a time. The report then repaints from the hub's answer. A failure shows
under its row; when the fix made the finding go away but still reported a
failure (a `swe init` that left a real file alone), it shows in an error
card above the list instead.

## Payload

`GET /v1/app/doctor` is `swe doctor --json` built in process
(`harness.commands.doctor_report`, the code `cmd_doctor` runs), plus the
machine name:

```json
{"ok": true, "machine": "kai-home-server",
 "findings": [{"check": "setup.unlinked", "severity": "warning",
               "message": "Claude doesn't read the shared AGENTS.md yet",
               "fix": "swe init", "harness": "claude", "action": "init"}]}
```

`POST /v1/app/doctor/run` takes `{"check", "message"}`, the finding's
identity (the app's `Finding.id` is the two joined). The hub reads the
report again, finds that finding, and runs the fix the finding itself
names in `action`, in process:

| `action` | Runs | Same as |
|---|---|---|
| `setup` | `setup.commands.apply_setup` (no adopt, stars left alone) | `swe setup --apply --json` |
| `init` | `init.commands.run_init(home, quiet=True, ask=False)` | `swe init` with no terminal |
| `discover` | `discover_harnesses()` then `apply_findings(min_confidence="high")` | `swe harness discover --apply --json` |

Nothing in the body says what to run. The reply is `{"ran", "error",
"report"}`: the action, what went wrong (`null` when nothing did) and the
report read again, since even a failed run may have fixed something. A
setup whose linking pass stopped reports `links.error`; an init that left
paths blocked reports them with `swe init`'s own advice.

| Status | Code | When |
|---|---|---|
| 400 | `bad_finding` | `check` or `message` missing, not text, or over 4000 characters |
| 404 | `unknown_finding` | that finding is not in the report now |
| 404 | `unknown_action` | any action but `run` |
| 409 | `busy` | another fix is running (one at a time, not queued) |
| 422 | `no_action` | the finding has no fix swe can run itself |
| 403 | `bad_origin` | the hub's Origin check |
| 503 | `page_unavailable` | the report can't be built (a corrupt harness.json) |

Fixes also take the Harnesses page's registry lock (`harness.REGISTRY_LOCK`),
since all three can rewrite harness.json.

## Differences from the Mac app

- Run asks first, in a panel under the row that names the command and the
  machine. The app's Run runs at once.
- "Run in Terminal" for a `swe …` fix with no action is left out: the
  browser can't open a terminal on the hub. The copy button stays.
- `init` never opens the new-skills-folder picker, even if the hub was
  started from a terminal: new folders wait, as in the app's run.
- A setup step that failed outright (`applied.errors`) is shown as well as
  a stopped linking pass; the app shows only the latter.
- An error whose finding went away is shown above the list; the app keys
  it to the row, so it is not seen.
- No ⌘R; the refresh button and Try again / Check again do the same.
