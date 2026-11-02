# Demo: every text primitive in one hub

This is the script for a five-minute recorded walkthrough. It shows SWE
keeping all five text primitives (AGENTS.md, skills, plugins, MCP servers and
hooks) in one git repo, `~/.swe`, linking them into Claude Code and Codex, and
syncing them to a second machine.

The video leads with the Mac app, with one terminal pane beside it for the
`swe` commands. Every step also has a terminal check, so the whole script can
be rehearsed in a terminal on its own (see [Rehearse in the terminal
only](#rehearse-in-the-terminal-only)).

Part of [#524](https://github.com/c-wenlong/swe/issues/524).

## What the demo uses

- **The demo hub**, [c-wenlong/swe-demo-hub](https://github.com/c-wenlong/swe-demo-hub):
  an `AGENTS.md` built from two chapters in `AGENTS_MD/`, three skills
  (`review-checklist`, `debug-systematically`, `write-pr-description`), one
  plugin marketplace (`plugins/starter`, with the `git-helpers` plugin), two
  MCP servers in `mcp.json` (`context7` and `github`), and one Claude Code hook,
  `hooks/claude/block-force-push.py`, which ships disabled.
- **`scripts/demo_home.sh`** in this repo. It makes a bare clone of the demo
  hub, `fork.git`, that stands in for the viewer's own fork, so nothing in the
  demo ever pushes to GitHub. It also makes two home folders, machine A and
  machine B, each with an empty `.claude/` and `.codex/` and a neutral
  `.gitconfig`. It does not run `swe setup`; the demo does that on camera.

## Prerequisites

- **swe from `main`**, with `swe sync` in it (check: `swe sync --help`
  prints the sync help). `swe sync` is not in a tagged release yet. Install
  with `pipx install --force git+https://github.com/c-wenlong/swe.git`.
- **git**, **Python 3.11 or newer** (`python3`), and **Node.js** (`npx`,
  which the `context7` MCP server runs on).
- **Claude Code** (`claude`) and **Codex** (`codex`) on `PATH`. Neither has
  to be logged in for the script below; see [What needs a logged-in
  harness](#what-needs-a-logged-in-harness).
- **A clone of this repo**, for `scripts/demo_home.sh`. Run the commands
  from its root.
- **For the Mac app segments: a separate, fresh macOS user account** with
  SWE.app installed in it (run `app/mac/script/install-app.sh` while logged in
  as that user; it builds `origin/main` into that account's `~/Applications`).
  The app ignores `$HOME`: it reads the account's real `~/.swe` (see the
  "app ignores `$HOME`" entry in the [app gotchas](app-architecture.md#gotchas)),
  so a temporary HOME cannot point it at the demo hub. A dedicated account
  keeps your own `~/.swe`, `~/.claude` and `~/.codex` out of the recording
  and out of harm's way. Launch the app once before recording and dismiss its
  first-run prompts.
- **Screen recording**: macOS screen recording (Shift-Command-5) or any
  recorder, set to 1920 by 1080. Put the app on the left two thirds of the
  screen and a terminal on the right third, with a large font (16 pt or more)
  and a short prompt. Turn on Do Not Disturb.

## Before you record

Logged in as the demo account, from the root of this repo:

```bash
eval "$(scripts/demo_home.sh --root=/tmp/swe-demo --home="$HOME")"
on_a() { env -i PATH="$PATH" TERM="$TERM" HOME="$DEMO_A" "$@"; }
on_b() { env -i PATH="$PATH" TERM="$TERM" HOME="$DEMO_B" "$@"; }
```

`--home="$HOME"` makes the demo account's own home machine A, so the app and
the terminal see the same `~/.swe`. The script refuses if `~/.swe` already
holds a hub. It sets four variables:

| Variable | What it is |
|---|---|
| `DEMO_ROOT` | `/tmp/swe-demo`, which holds everything below except machine A in the demo account |
| `DEMO_FORK` | `file:///tmp/swe-demo/fork.git`, the stand-in for your fork |
| `DEMO_A` | Machine A's home: the demo account's `$HOME` |
| `DEMO_B` | Machine B's home: `/tmp/swe-demo/machine-b` |

`on_a` and `on_b` run one command as if on machine A or machine B. They
clear the environment and keep only `PATH`, `TERM` and that machine's `HOME`,
so nothing reads another home by accident. In the demo account `on_a` is the
same as running the command bare; it is there so the same script works in a
rehearsal.

Machine B is a second home folder on the same Mac, standing in for a second
computer. The app does not show it; its segment is terminal only.

## Segments

| # | Segment | Starts | Length |
|---|---|---|---|
| 1 | [Set up from the hub](#1-set-up-from-the-hub-040) | 0:00 | 0:40 |
| 2 | [AGENTS.md](#2-agentsmd-025) | 0:40 | 0:25 |
| 3 | [Skills](#3-skills-020) | 1:05 | 0:20 |
| 4 | [Plugins](#4-plugins-030) | 1:25 | 0:30 |
| 5 | [MCP servers](#5-mcp-servers-030) | 1:55 | 0:30 |
| 6 | [Hooks](#6-hooks-015) | 2:25 | 0:15 |
| 7 | [Edit a skill and a chapter](#7-edit-a-skill-and-a-chapter-035) | 2:40 | 0:35 |
| 8 | [The change in Claude Code and Codex](#8-the-change-in-claude-code-and-codex-025) | 3:15 | 0:25 |
| 9 | [Enable the hook](#9-enable-the-hook-030) | 3:40 | 0:30 |
| 10 | [A second machine](#10-a-second-machine-050) | 4:10 | 0:50 |
| | Total | | 5:00 |

Each segment gives what to say, the commands, and what you should see. The
expected output below was copied from a rehearsal; paths, timestamps and the
list of other coding CLIs found will differ on your machine.

### 1. Set up from the hub (0:40)

Say: "This is a fresh Mac account. My hub is a git repo with my instructions,
skills, plugins, MCP servers and hooks. One command brings it in."

```bash
on_a swe setup --hub="$DEMO_FORK" --apply
```

`--apply` runs setup without questions and ends with the same linking pass as
`swe init`. The interactive wizard (`swe setup --hub=…` without `--apply`)
links the skills folders but not AGENTS.md or hooks, so after it you would
also run `swe init`.

You should see:

```text
  Cloning file:///tmp/swe-demo/fork.git into ~/.swe…
  Cloned.
  Plugin marketplaces
    ✓ starter registered in claude
    ✓ starter registered in codex
```

then one line per coding CLI it found on `PATH` (at least `claude` and
`codex`), a "Link … skills to the shared tree" line for each one with a
skills folder, and a summary.
The rehearsal Mac had nine coding CLIs installed:

```text
  ✓ Applied: 9 harness(es), 7 skills action(s), 17 link(s)
```

With only Claude Code and Codex installed the counts are smaller. Then:

```bash
on_a swe init --check
```

```text
  Instructions  8 linked
  Skills        9 linked
  Hooks         1 ignored

  17 linked, 0 blocked, edit ~/.swe/AGENTS.md to change them all
```

Say: "AGENTS.md and the skills folder are now linked into every harness. The
hook is ignored because the hub ships it switched off."

Now open SWE.app.

### 2. AGENTS.md (0:25)

In the app, open **AGENTS.md** under Primitives. The Global tab shows:

- a **Chapters** rail listing `01-working.md` and `02-response-style.md`;
- a **Generated AGENTS.md** strip reading **Current**, with **Rebuild** and
  **Preview**;
- a **Harnesses** strip with a dot beside each linked harness, Claude Code
  and Codex among them (a dot alone means linked).

Source: `app/mac/Sources/SWEApp/Pages/AgentsMdPage.swift` (chapters rail,
generated strip and harness strip).

Say: "One AGENTS.md, written as chapters. Claude Code reads it as CLAUDE.md,
Codex as AGENTS.md."

```bash
ls -l "$DEMO_A/.claude/CLAUDE.md" "$DEMO_A/.codex/AGENTS.md"
on_a swe instructions check
```

```text
… …/.claude/CLAUDE.md -> …/.swe/AGENTS.md
… …/.codex/AGENTS.md -> …/.swe/AGENTS.md
Current: ~/.swe/AGENTS.md
```

### 3. Skills (0:20)

Open **Skills**. The three hub skills are listed with the **Shared** badge.
The header chip reads **Harness links** with every harness linked ("N of N
linked"), and each row's last cell shows how many harnesses load it.

Source: `app/mac/Sources/SWEApp/Pages/SkillsPage.swift` (harness links chip,
source badge, seen-by cell).

```bash
on_a swe skills -d
```

```text
debug-systematically    shared   ~/.swe/skills/debug-systematically/SKILL.md
review-checklist        shared   ~/.swe/skills/review-checklist/SKILL.md
write-pr-description    shared   ~/.swe/skills/write-pr-description/SKILL.md

  3 skills across 1 roots
```

### 4. Plugins (0:30)

Open **Plugins**. On the Explore tab, the `starter` marketplace appears under
**Local** ("Your SWE hub, ~/.swe/plugins"). Expand it: `git-helpers` has a
chip for Claude Code and one for Codex, both reading **Available**, since
setup registered the marketplace in both.

Say: "Setup registered the marketplace. Installing a plugin is still a
choice per harness."

Install it from the harness chip's menu (**Install in Claude Code**, then
**Install in Codex**), or in the terminal:

```bash
on_a swe plugins install git-helpers@starter --harness=claude
on_a swe plugins install git-helpers@starter --harness=codex
on_a swe plugins catalog starter
```

```text
  ✓ claude git-helpers@starter: installed
  ✓ codex git-helpers@starter: installed

  PLUGIN        CLAUDE     CODEX      DESCRIPTION
  git-helpers   on         on         Small git helpers, starting with a commit message writer.
```

Press Refresh (Command-R) on the page: both chips now read **Enabled**.

Source: `app/mac/Sources/SWECore/Primitives/PluginExplore.swift` (a hub
marketplace goes under Local with every harness's chip) and `app/mac/Sources/SWECore/Primitives/PluginCatalog.swift`
(status chips and install menu items).

### 5. MCP servers (0:30)

Say: "mcp.json in the hub lists two servers. Secrets stay out of git: the
GitHub server only holds a `${GITHUB_TOKEN}` reference."

```bash
on_a swe mcp sync swe claude codex --only=context7
```

```text
Selected 1 server(s) from swe

  ✓ claude: 1 added

  ✓ codex: 1 added
```

`--only=context7` matters: without it, sync refuses to write anything while
`GITHUB_TOKEN` has no value (`Not syncing: no value for GITHUB_TOKEN (used by
github)`). To sync both, store a token first with
`swe mcp secret set GITHUB_TOKEN`, which keeps the value in
`~/.swe/secrets/`, never in git. Do not show a real token on camera.

Open **MCP servers** and press Refresh. The grid has a Claude Code and a Codex
column: `context7` has a checkmark in both, and `github` is marked missing in
both, with a warning triangle whose hover text reads "Needs a value for
GITHUB_TOKEN".

Source: `app/mac/Sources/SWEApp/Pages/McpServersPage.swift` (harness columns,
cells, the secrets warning) and `src/swe/mcp/inventory.py` (hub-only rows).

```bash
on_a swe mcp list
```

```text
SERVER        claude   …   codex
context7      ✓        …   ✓
```

### 6. Hooks (0:15)

Open **Hooks**. `block-force-push.py` is in the **Not running** card ("No
settings file declares these, so no harness fires them.") with a
**Disabled** badge.

Source: `app/mac/Sources/SWEApp/Pages/HooksPage.swift` (`NotRunningCard`
and the Disabled badge).

```bash
on_a swe hooks list
```

```text
  block-force-push.py  claude   harness  ignored (disabled via swe hooks disable)  -
```

Say: "The hook is in the hub but switched off. We'll turn it on in a minute."

### 7. Edit a skill and a chapter (0:35)

Say: "Now I change one skill and one chapter, in one place."

```bash
printf '%s\n' '- End with a one-line verdict: "ship it" or "fix first".' \
  >> "$DEMO_A/.swe/skills/review-checklist/SKILL.md"
printf '%s\n' '- Use plain words. Say "use", not "leverage".' \
  >> "$DEMO_A/.swe/AGENTS_MD/02-response-style.md"
on_a swe instructions check
```

```text
Stale or missing: ~/.swe/AGENTS.md. Run swe instructions build.
```

Back in the app, switch to **AGENTS.md**: the strip now reads **Stale**.
(You can make the chapter edit in the app's chapter editor instead; saving
there rebuilds on its own.)

```bash
on_a swe instructions build
```

```text
Built ~/.swe/AGENTS.md
```

The strip goes back to **Current**, and **Preview** shows the new last line.

### 8. The change in Claude Code and Codex (0:25)

Say: "Nothing was copied. Both harnesses read the same files."

```bash
tail -n 1 "$DEMO_A/.claude/CLAUDE.md" "$DEMO_A/.codex/AGENTS.md"
tail -n 1 "$DEMO_A/.claude/skills/review-checklist/SKILL.md" \
  "$DEMO_A/.codex/skills/review-checklist/SKILL.md"
```

```text
==> …/.claude/CLAUDE.md <==
- Use plain words. Say "use", not "leverage".

==> …/.codex/AGENTS.md <==
- Use plain words. Say "use", not "leverage".

==> …/.claude/skills/review-checklist/SKILL.md <==
- End with a one-line verdict: "ship it" or "fix first".

==> …/.codex/skills/review-checklist/SKILL.md <==
- End with a one-line verdict: "ship it" or "fix first".
```

Then each harness's own view of the plugin and the MCP server, which needs no
login:

```bash
on_a claude plugin list
on_a claude mcp list
on_a codex plugin list
on_a codex mcp list
```

```text
  ❯ git-helpers@starter
    Status: ✔ enabled

context7: npx -y @upstash/context7-mcp - ✔ Connected

git-helpers@starter  installed, enabled  0.1.0  …/.swe/plugins/starter/git-helpers

Name      Command  Args                      Env  Cwd  Status   Auth
context7  npx      -y @upstash/context7-mcp  -    -    enabled  Unsupported
```

`claude mcp list` starts the server to check it, so the first run downloads
`@upstash/context7-mcp` and takes a few seconds.

### 9. Enable the hook (0:30)

```bash
on_a swe hooks enable block-force-push.py
```

```text
  ✓ enabled block-force-push.py: ~/.swe/hooks/claude/block-force-push.py
```

A dim note about declarations and a missing script follows. It is written
for `disable`, not `enable`; ignore it.

That links the script into `~/.claude/hooks`, but Claude Code only runs a
hook its settings declare, and swe never edits your settings. Declare it:

```bash
on_a python3 - <<'EOF'
import json, pathlib
path = pathlib.Path.home() / ".claude" / "settings.json"
settings = json.loads(path.read_text()) if path.exists() else {}
settings.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
    {"matcher": "Bash",
     "hooks": [{"type": "command",
                "command": "python3 ~/.claude/hooks/block-force-push.py"}]})
path.write_text(json.dumps(settings, indent=2) + "\n")
EOF
on_a swe hooks check
```

```text
Hook drift

  ✓ declared, linked and present all agree
```

Press Refresh on **Hooks**: the page now has a Claude Code tab, and
`block-force-push.py` sits on the PreToolUse lane with a `Bash` matcher pill.

Show what it does by handing it the event Claude Code would send:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git push --force"}}' \
  | on_a python3 "$DEMO_A/.claude/hooks/block-force-push.py"; echo "exit $?"
```

```text
Blocked by block-force-push.py: force-push is off. Use --force-with-lease, or ask the user to run it.
exit 2
```

### 10. A second machine (0:50)

Say: "Everything so far is one commit away from my other Mac."

In the app, open **Settings › Files**. The **Hub sync** card reads "Branch
main, shared by every machine." with the fork as the remote, and under
Changes "5 uncommitted files: AGENTS.md 2, config 1, hooks 1, skills 1."
Press **Sync**, or run the same thing in the terminal:

```bash
on_a swe sync
```

```text
Hub sync  single branch main, shared by every machine
  ✓ committed 5 file(s): swe sync from <host>: 5 files (AGENTS.md 2, config 1, hooks 1, skills 1)
    M AGENTS.md
    M AGENTS_MD/02-response-style.md
    A config/harness.json
    D hooks/claude/.disabled
    M skills/review-checklist/SKILL.md
  ✓ pushed main
```

The card now reads "Nothing to commit."

Source: `app/mac/Sources/SWEApp/Pages/HubSyncSection.swift` (the rows and
the Sync button) and `app/mac/Sources/SWECore/Swe/HubSync.swift` (the
wording).

Machine B starts from the same fork:

```bash
on_b swe setup --hub="$DEMO_FORK" --apply
tail -n 1 "$DEMO_B/.claude/skills/review-checklist/SKILL.md"
on_b swe hooks list
```

```text
  Cloning file:///tmp/swe-demo/fork.git into ~/.swe…
  Cloned.
  …
  ✓ No new high-confidence harnesses to register.
  …
- End with a one-line verdict: "ship it" or "fix first".
  …
  block-force-push.py  claude   harness  linked  -
```

Machine B has the edited skill, the rebuilt AGENTS.md and the enabled hook,
and it found no new harnesses because machine A's registry came with the hub.
Now a new skill on machine A, and `swe sync` on both:

```bash
on_a swe skills new explain-a-diff --description="Explain a diff in plain words, file by file."
on_a swe sync
on_b swe sync
on_b swe skills -d
```

```text
  ✓ Created explain-a-diff: …/machine-a/.swe/skills/explain-a-diff/SKILL.md

Hub sync  single branch main, shared by every machine
  ✓ committed 1 file(s): swe sync from <host>: 1 file (skills 1)
    A skills/explain-a-diff/SKILL.md
  ✓ pushed main

Hub sync  single branch main, shared by every machine
  ✓ merged 1 commit(s) from origin/main
  ✓ pushed main
  ✓ re-linked 1 incoming file(s) into every harness

explain-a-diff          shared   ~/.swe/skills/explain-a-diff/SKILL.md
  …
  4 skills across 1 roots
```

Say: "One repo, every harness, every machine."

## What does not carry over on its own

These are real behaviours the demo works around; say them out loud or leave
them out of the cut, but do not hide them in the script.

- **MCP servers are not synced by setup.** `swe setup --hub` copies
  `mcp.json` into `~/.swe` but writes no harness config. Segment 5 runs
  `swe mcp sync`, and machine B needs the same command.
- **One missing secret stops the whole MCP sync.** Without `GITHUB_TOKEN`,
  `swe mcp sync swe claude codex` writes nothing, hence `--only=context7`.
- **Plugins are registered, not installed.** Setup adds the marketplace to
  Claude Code and Codex; installing `git-helpers` is a separate step on each
  machine.
- **A hook's declaration lives in the harness's settings, outside the hub.**
  Machine B gets the script linked but not declared, so `swe hooks check`
  there reports it will not fire until you add the same `PreToolUse` entry.
- **`config/harness.json` is part of the hub.** Setup writes it on each
  machine, so machine A has to `swe sync` before machine B runs setup, as in
  segment 10. If both machines run setup from the original hub first, machine
  B's first `swe sync` stops with a conflict on `config/harness.json` and
  changes nothing.

## What needs a logged-in harness

Everything above runs without logging in to Claude Code or Codex. To show a
harness actually using the new rule, the edited skill or the hook in a
session, both have to be logged in, which the demo account would then hold
real credentials for. If you record that:

- in `claude`, ask "Review my last commit": the `review-checklist` skill
  now asks for the verdict line at the end. Type `/hooks` to see the
  PreToolUse hook, and ask it to run `git push --force` to see the hook
  block it;
- in `codex`, ask the same review question.

The rehearsal for this script did not cover these steps; try them before a
take.

## Rehearse in the terminal only

The same script runs without the Mac app or a second account. Skip the app
steps, and build both machines under a temporary root instead of your home:

```bash
eval "$(scripts/demo_home.sh --root=/tmp/swe-demo)"
on_a() { env -i PATH="$PATH" TERM="$TERM" HOME="$DEMO_A" "$@"; }
on_b() { env -i PATH="$PATH" TERM="$TERM" HOME="$DEMO_B" "$@"; }
```

Then run every bash block of segments 1 to 10 in order. Machine A is
`/tmp/swe-demo/machine-a`, and nothing touches your own `~/.swe`, `~/.claude`
or `~/.codex`, since every command runs through `on_a` or `on_b`. The
commands themselves take well under a minute: a scripted run of every block,
including the clone from GitHub and `claude mcp list` fetching `context7`,
took 15 seconds. Reading the output as you go is what takes the time.

## Reset and clean up

For the terminal rehearsal, delete the root:

```bash
rm -rf /tmp/swe-demo
```

For the demo account, delete `/tmp/swe-demo` too, then quit SWE.app and
remove what setup and the harnesses wrote to the account's home, before the
next take:

```bash
rm -rf /tmp/swe-demo ~/.swe ~/.claude ~/.claude.json ~/.codex
```

Run that only while logged in as the demo account. Setup also links into the
home folder of every other coding CLI it found (`~/.cursor`, `~/.qwen` and so
on; `swe init --check --full` lists them before you delete `~/.swe`). The
simplest clean slate is to delete the demo account in System Settings › Users
& Groups and create it again.
