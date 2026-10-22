# Setting up quiver on boxes.dev

Runbook for provisioning this repository as a boxes.dev **Template box**: a
cloud development machine that mirrors the local checkout so coding agents
can work on quiver from anywhere. Written from the actual setup run on
2026-10-22; another agent following these steps should reach the same
result without re-deriving the investigation.

Terms: the `dvb` CLI drives everything. `dvb agent-init` is the setup
workflow. Its output is one YAML **manifest** (the plan) plus a **Template
box** (the running machine). New **devboxes** are cloned from the Template
box's latest snapshot.

The working manifest lives outside the repo at
`~/.devbox/accounts/<account>/projects/<project>/agent-init/manifest.yaml`.
`dvb agent-init start` prints the exact path; `dvb agent-init status` always
reprints it and names the next action.

## 0. Prerequisites

```bash
dvb agent-init status      # names the next action; fails if the cwd is not a git repo
dvb agent-init accounts    # opens boxes.dev connect page; waits for the user to click Done
```

- The user must already be signed in to boxes.dev (`dvb agent-init --login-code <code>`
  if not).
- At least one coding agent (Codex and/or Claude Code) must be connected on
  the accounts page. `accounts` blocks until the user clicks **Done** — run
  it in the background and continue investigating.
- If the GitHub App cannot see the repository, run
  `dvb agent-init github connect` early so the user can grant access in the
  browser while you investigate. `check` verifies it again later.

## 1. Pick the project folder and start

quiver is a single repository — its siblings under `orchestrators/` and the
rest of `ai-engineering/` are independent checkouts or reference material,
not dependencies, so there is no multi-repo case.

```bash
cd /Users/kaichen/Desktop/Personal/Internal/Projects/ai-engineering/orchestrators/quiver
dvb agent-init start --project "$(pwd)"
```

`start` seeds the manifest with detected repos, agent config, untracked-file
defaults, the live instance-type catalog, and the team's default. Every
later `dvb agent-init` command must run from the project root — running one
from another directory fails with "The selected project folder does not
contain a supported Git repo."

## 2. Investigate

Each manifest section marked `TODO(agent)` has a guide:
`dvb agent-init guide <topic>` where topic is one of `flow`, `manifest`,
`env-files`, `external`, `artifacts`, `databases`, `app`, `untracked`,
`git-ssh`, `git-signing`, `agent-config`, `recovery`. Product behavior
questions go to `dvb agent-docs` (index) then `dvb agent-docs <topic>`.

Investigation is read-only. What we found for quiver:

| Question | Evidence | Answer |
|---|---|---|
| Env/secret files? | `.gitignore` lists `.env`, `.env.*`; `find . -name '.env*'` | One gitignored `.env`: macOS release creds (NOTARY_*, MACOS_CERTIFICATE, SPARKLE_*) for `app/script/build-app.sh release`. User chose **not** to copy — macOS signing can't run on a Linux box. |
| Untracked files? | `dvb agent-init detect untracked` | None. |
| Databases? | No ORM/compose/`DATABASE_URL`; CLI is stdlib-only | None. |
| App servers? | CLI tool, `swe` console entry point in `pyproject.toml` | No entrypoints or services; install + test commands go in `setupNotes`. |
| External deps? | `pyproject.toml` `requires-python = ">=3.11"`; `zensical.toml` + `scripts/docs_site.py` | Python >=3.11 required; zensical optional (docs site only). |
| Git signing? | `git config [--global] commit.gpgsign`, `user.signingkey` | Not configured → `mode: skip`. |
| GitHub deps? | `dependencies = []`, extras are PyPI only | None. |
| Branch / local work | `git branch --show-current`, `git log origin/main..HEAD`, `git ls-remote --heads origin` | On `remote/ios-xcodegen` @ `6777087`, already pushed to origin; one dirty tracked file `remote/ios/Assets.xcassets/AppIcon.appiconset/icon-1024.png`. Start on `main`; stash the dirty file on the box. |
| Agent config | Seeded by `start`; `dvb agent-init detect agent-config` reprints | Kept seeded defaults: Claude+Codex settings, skills, memory, remote MCP servers. Command-based MCP servers and Claude hooks stay excluded (local paths don't exist on the box). |
| Agent threads | `dvb agent-init detect agent-threads --project <path> --json` | 74 Claude threads for this project; user chose to import all. |

Notes for investigators:

- `~/.claude/CLAUDE.md`, `~/.claude/skills`, `~/.codex/AGENTS.md` etc. are
  symlinks into `~/.quiver` and `~/dotfiles`. `check` warns about them;
  contents are copied as real files — expected and fine.
- `app/` (SwiftUI) and `remote/ios` (Xcode) cannot build on Linux. Nothing
  in the manifest references them; `setupNotes` tells the remote agent to
  skip them.

## 3. Fill the manifest

Decisions recorded (all confirmed with the user before `check`):

```yaml
project:
  instanceType: "4vcpu8gb"   # user picked team default; options were 4vcpu8gb / 4vcpu16gb / 8vcpu16gb / 8vcpu24gb / 8vcpu32gb
agents:
  runtime: [claude, codex]
  setup: null                # setup session was Devin, not a claude/codex backend
files: { env: [], other: [] }
external:
  dependencies:
    - { name: Python, version: ">=3.11", evidence: "pyproject.toml requires-python" }
    - { name: zensical, version: null, evidence: "zensical.toml + scripts/docs_site.py" }
  configs: []
artifacts: []
databases: []
app: { entrypoints: [], services: [] }
localChanges: { copyDirty: true }    # saves the dirty icon PNG in a named stash on the box
untracked: { copy: true, mode: defaults }
githubAccess: managed                # GitHub App HTTPS; gitSsh rows stay mode: skip
gitSigning: { mode: skip }
agentConfig: { include: [...] }      # seeded 18 items kept
agentThreads:
  import: selection
  include: ["<74 thread ids>"]       # ids from detect agent-threads --json
```

Devbox lifecycle — every new devbox resets to `main` and reinstalls:

```yaml
devboxLifecycle:
  startupTimeoutSeconds: 120   # hard max is 120; check rejects anything higher
  startupScript: |
    git fetch origin
    git checkout -f -B main origin/main
    git clean -fd
    python3 -m venv .venv
    .venv/bin/pip install -e ".[test]"
  teardownTimeoutSeconds: 30
  teardownScript: ""
```

`.venv` is gitignored, so `git clean -fd` does not remove it. The editable
install survives branch resets; reinstalling keeps entry points fresh.

Template maintenance — fast-forward `main` and refresh the install when the
dependency manifest or the Nix pin changes:

```yaml
templateMaintenance:
  script: |
    branch=main
    if [ "$(git branch --show-current)" != "$branch" ]; then
      echo "The Template box is not on $branch." >&2; exit 1
    fi
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
      echo "The Template box has uncommitted changes." >&2; exit 1
    fi
    git fetch origin "$branch"
    git merge --ff-only "origin/$branch"
    python3 -m venv .venv
    .venv/bin/pip install -e ".[test]"
  timeoutSeconds: 300            # max 480
  watches:
    - repository: "."
      enabled: true
      paths: [pyproject.toml, flake.lock]
```

Watch lockfiles/toolchain files only — every matching push wakes the box.
Setup leaves the schedule off and saves fresh baselines; the remote agent
tests the script twice under the maintenance environment.

`setupNotes` carries the branch choice, the stash instruction, the `.env`
exclusion, install/verify commands, and the gotchas. The full text is in
the manifest; the load-bearing lines are:

- Always run tests and `swe` against a throwaway `HOME="$(mktemp -d)"` —
  the CLI reads/writes `~/.quiver`.
- Verify with `HOME="$(mktemp -d)" .venv/bin/python -m unittest discover -s tests -p 'test_*.py'`
  and `HOME="$(mktemp -d)" .venv/bin/swe list`.
- Python floor is 3.11; no PEP 701 nested f-strings.
- The remote agent must not modify tracked files; record failures instead.

## 4. Validate, approve, launch

```bash
dvb agent-init check   # validates manifest, re-verifies GitHub App access, prints launch summary
dvb agent-init run     # package -> encrypt -> upload -> provision -> remote setup (several minutes)
```

- Show the user the launch summary and get explicit approval before `run`.
- `run` streams progress lines; the bundle upload for this repo took ~6
  minutes (~90 s per 10% after a slow start).
- When `[handoff]` prints, the setup thread is live on the Template box —
  the user can watch it in the boxes.dev Mac app (`dvb agent-init
  open-desktop`) or mobile app.
- On completion `run` opens the desktop app on the setup thread. Done.

## 5. After setup

- Drive the box's agents from a terminal: `dvb agents quiver`
  (`--workspace` keeps the thread list beside the agent; needs tmux >=3.3).
- Imported OAuth-based MCP servers may need re-authentication — the setup
  thread lists them; the user completes it in the desktop app.
- Threads can be imported later too: `dvb threads import`.
- Resume after any failure: every stage is resumable
  (`dvb agent-init guide recovery`).

## Gotchas hit during this run

- `dvb agent-init` commands must run from the project root; cwd elsewhere
  fails the project check.
- `devboxLifecycle.startupTimeoutSeconds` max is 120 (not 300);
  `templateMaintenance.timeoutSeconds` max is 480.
- Leftover `# TODO(agent)` comment markers anywhere in the manifest block
  `check` — replace each with a real value or an explicit `[]`/`null`.
- `dvb agent-init github connect` opened once from the wrong cwd and failed
  with the same "not a supported Git repo" error; rerun from the repo root.
- Secret values never appear in the manifest, chat, or command output —
  reference file paths and variable names only.
- The dirty-worktree stash and imported branch refs ride in the encrypted
  bundle; preserving local work never requires a `git push`.
