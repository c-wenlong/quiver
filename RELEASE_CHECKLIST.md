# The SWE release process

This document is the complete walkthrough of how a SWE release ships. It
is written to double as a narration script: each numbered section is one
scene, with the why, the exact commands, what success looks like, and what
can go wrong. The pipeline itself lives in
[.github/workflows/release.yml](.github/workflows/release.yml); this file
covers everything around it.

## Scene 0 — What a release ships

A SWE release delivers three products to one GitHub Release page:

1. **The Python package** — an sdist and a wheel for the `swe` CLI, built
   from `pyproject.toml`. This is what `pip install quiver-*.whl` users get.
2. **The macOS app** — `SWE.app`, built on a macOS 26 runner, signed
   with a Developer ID certificate, notarized by Apple, and packaged into
   a `SWE-<version>.dmg`.
3. **The Sparkle appcast** — an `appcast.xml` signed with an EdDSA private
   key, so every installed copy of the app can discover and cryptographically
   verify the update.

The one thing the pipeline deliberately does **not** do is create the
GitHub Release itself. A human publishes the release notes; the workflow
only attaches its built artifacts to a release that already exists. That
split is intentional: release notes are a human decision, builds are a
machine's.

## Scene 1 — Pre-flight, before anything is tagged

Every item here must be true before a tag is pushed. Skipping one does not
just risk a failed run; it risks shipping a build whose name does not match
its release.

- [ ] **The version is bumped in two places.** `project.version` in
  `pyproject.toml` and `__version__` in `src/swe/__init__.py` must both
  say the release version. The workflow hard-fails if the pushed tag does
  not equal `pyproject.toml`'s version, and `swe version` reads
  `__init__.py`, so a mismatch ships a CLI that lies about its own version.
- [ ] **`CHANGELOG.md` has an entry for the release.** The heading format
  is `## [X.Y.Z] - YYYY-MM-DD`, with a matching link definition at the
  bottom pointing at `releases/tag/vX.Y.Z`.
- [ ] **CI is green on `main`.** All shards, Coverage, and the Greptile
  review must have finished clean on the commit the tag will point at.
- [ ] **The installed app contains the release's merges.** Install with
  `app/mac/script/install-app.sh`, then read the bundle's embedded commit:

  ```bash
  defaults read ~/Applications/SWE.app/Contents/Info SWECommit
  ```

  Every merge commit that must ship must be an ancestor of that hash:
  `git merge-base --is-ancestor <merge> <SWECommit>`.
- [ ] **Third-party notices are current.** If dependencies or bundled
  assets changed since the last release, `app/mac/THIRD_PARTY_NOTICES.md` must
  reflect them.
- [ ] **Release notes state the support floor.** The app requires
  **macOS 26 or later, Apple Silicon only**. This goes in the release
  notes every time, because the DMG runs nowhere else.

### The first SWE release (Quiver.app → SWE.app)

The rename changes the bundle identifier (`com.cwenlong.quiver` →
`com.cwenlong.swe`), so Sparkle cannot carry an installed Quiver.app
across: its updater refuses an update whose bundle ID differs from the
host's. Extra steps for that one release:

- [ ] **The release notes tell Quiver.app users to install SWE.app
  manually.** `app/mac/script/install-app.sh` does the whole move: it
  quits and removes Quiver.app, migrates the hub/agent/gateway
  LaunchAgents onto the new labels repointed at SWE.app, and SWE's first
  launch carries the `~/.quiver` data root and old preferences over.
  Dragging SWE.app in from the DMG does only that first-launch part —
  a remote hub, satellite or MCP gateway installed under Quiver.app
  still points into the old bundle, so those users reinstall the service
  (`swe remote ... --install` / `swe mcp gateway install`, then delete
  Quiver.app) or run the script. A Quiver.app that checks the feed will
  see the new appcast entry and fail validation — that is expected, not
  a bug.
- [ ] **`Casks/quiver.rb` in `c-wenlong/homebrew-quiver` is updated for
  the new artifact before tagging.** Its `url`, `name` and `app` lines
  must say `SWE` (`bump_tap` only rewrites version and sha256, so the
  name change is a manual tap commit). The cask token stays `quiver`
  until the tap migration ships `swe.rb`.
- [ ] **The stable DMG link changes name.** `releases/latest/download/
  SWE.dmg` replaces `…/Quiver.dmg` anywhere it is documented.

## Scene 2 — The seven secrets

Signing and notarization run entirely on GitHub-hosted runners, so all
credentials live as repository secrets — never in the tree, never in a
command line, never in a log. They are set once under Settings → Secrets
and variables → Actions and survive every release.

| Secret | What it is | Where it comes from |
|---|---|---|
| `MACOS_CERTIFICATE` | Base64 of the `.p12` export of the "Developer ID Application" certificate **including its private key** | Keychain Access → select cert + key → File → Export Items; then `base64 -i cert.p12` |
| `MACOS_CERTIFICATE_PASSWORD` | The password chosen at `.p12` export | Chosen by the exporter |
| `KEYCHAIN_PASSWORD` | Password for the throwaway keychain CI creates per run | Any random string; it lives only inside a run |
| `NOTARY_KEY` | Base64 of an App Store Connect API key's `.p8` file | `base64 -i AuthKey_<ID>.p8` |
| `NOTARY_KEY_ID` | The key's ID, e.g. `P3VHYCSWKN` | App Store Connect → Integrations → App Store Connect API |
| `NOTARY_ISSUER` | The issuer UUID | Same page as the key ID |
| `SPARKLE_PRIVATE_KEY` | The EdDSA private key matching the `SUPublicEDKey` baked into the app | Sparkle's `generate_keys`; stored locally at `~/.swe/secrets/` |

Three failure-mode notes that change how this scene ends:

- A **missing `MACOS_CERTIFICATE` does not fail the build.** The job
  deliberately degrades: it prints `::notice::MACOS_CERTIFICATE is not
  set`, produces an ad-hoc-signed bundle, and skips notarization and the
  release upload. A green run can therefore still mean "nothing actually
  shipped" — always confirm the DMG landed on the release.
- A **missing `SPARKLE_PRIVATE_KEY` does hard-fail** the appcast step,
  because Sparkle refuses to serve unsigned enclosures to existing
  installs.
- The `.p12`, its password, and the Sparkle private key should also live
  in a password manager. The private key especially: losing it means no
  future appcast signature can ever verify against shipped installs.

## Scene 3 — Cutting the release

The tag must be `v` followed by the exact version in `pyproject.toml`.

```bash
git tag -a v1.1.0 -m "v1.1.0"
git push origin v1.1.0
```

The push fires the `Release` workflow, which runs five jobs. The package,
app, and Nix builds start in parallel; the macOS wheel check waits for the
package build, and the attachment job waits for all four:

- **`Build sdist and wheel`** (Ubuntu) — checks the tag looks like `v*`,
  checks it matches `pyproject.toml` exactly, runs `python3 -m build`,
  `twine check --strict`s both distributions, installs the wheel into a
  clean venv from `/tmp` (so the checkout's `src/` cannot shadow a
  missing packaged module), and exercises `swe --help`, `swe list`, and
  the `session`, `config`, and `report` help under a throwaway `HOME`.
- **`Build, sign and notarize the macOS app`** (macOS 26) — runs the full
  `swift test` suite first so a test failure fails fast, then imports
  `MACOS_CERTIFICATE` into a throwaway keychain, builds the prod bundle
  via `app/mac/script/build-app.sh release`, reports the signature in the
  log, smoke-tests the signed app end-to-end (the embedded `swe` binary,
  then the GUI process, which must stay alive 20 seconds or exit
  cleanly), then
  hands `app/mac/script/make-dmg.sh` the notarization credentials: it staples
  the `.app`, stages, signs, submits to Apple, and staples the DMG.
  Finally it downloads the Sparkle 2.10.0 tools archive, verifies its
  pinned SHA-256 before executing anything, writes `appcast.xml` with
  `generate_appcast`, its signing key `SPARKLE_PRIVATE_KEY` piped to
  `--ed-key-file -` on stdin (never argv, never a file), validates the XML, and uploads a `macos-app` artifact.
- **`Nix flake build and check`** (Ubuntu) — proves the flake still
  builds on the tag; its checkPhase runs the same unittest suite.
- **`Verify the wheel on macOS`** — installs the built wheel into a clean
  venv on a Mac and runs the Darwin-only `path_health` unittest that
  needs real macOS paths.
- **`Attach artifacts to the GitHub release`** — waits on all four, then
  uploads `dist/*`, the DMG, and `appcast.xml` to the release **if one
  exists**. If none does, it leaves a notice and the artifacts stay on
  the workflow run.

Because the workflow never creates the release, the human half of this
scene is:

1. **Publish the GitHub Release for the tag.** Releases → Draft a new
   release → pick the tag → write the notes (including the macOS 26+,
   Apple Silicon floor) → Publish. `gh release create v1.1.0 --notes
   "..."` does the same thing.
2. **Re-run the workflow to attach the built artifacts.** Actions →
   Release → Run workflow → enter the existing tag. Manual dispatch
   takes the tag as a required input — it is a re-run path, not a way to
   mint a tag, and both entry points collapse to the same `RELEASE_TAG`.

## Scene 4 — Verifying before announcing

A green run is necessary but not sufficient; check the artifacts
themselves.

- [ ] **The release page holds the DMG and `appcast.xml`** under the tag,
  alongside the sdist and wheel.
- [ ] **The appcast is live and signed.** The feed URL is tag-independent:

  ```bash
  curl -sL https://github.com/c-wenlong/quiver/releases/latest/download/appcast.xml
  ```

  must return the new `<item>` carrying a `sparkle:edSignature`.
- [ ] **The DMG is genuinely notarized.** Download, mount, and run:

  ```bash
  spctl -a -vv SWE.app
  codesign -dvvv SWE.app
  ```

  Expected: `accepted source=Notarized Developer ID`, and the expected
  Developer ID plus team ID on the signature.
- [ ] **Sparkle picks the update up.** An installed older build should
  offer the new version through its normal check.
- [ ] **The wheel installs and runs** in a throwaway `HOME`:
  `pip install` the released `.whl`, then `swe --help` and
  `swe init --check`.

## Scene 5 — After the release

- [ ] Reinstall the app as usual. `app/mac/script/install-app.sh` builds
  `origin/main`, not the tag, so it tracks the branch. Verify
  `SWECommit` again.
- [ ] Close or re-milestone the issues this release shipped.

## Hard rules — the things that must never happen

- **Never put a signing secret or the Sparkle private key in the tree, on
  a command line, or in CI logs.** The key reaches `generate_appcast` on
  stdin only.
- **Never publish a build that skipped notarization.** Gatekeeper will
  refuse it on every user's machine, and the app will not open.
- **Never move or delete a published tag.** Sparkle asset URLs are
  tag-based, so breaking them breaks updates for every installed copy.
  Re-release under a new tag instead.
- **Forks inherit the update feed.** A production build embeds
  `SUFeedURL` and the bundled EdDSA public key, so a fork's app would
  "update" to upstream releases. A fork must swap both for its own, or
  disable Sparkle.

## Recovery — when something goes wrong

| Symptom | What happened | Recovery |
|---|---|---|
| `Tag v… does not match pyproject.toml version` | Tag/version mismatch | Fix the version in both files, re-tag under a new tag |
| `::notice::MACOS_CERTIFICATE is not set` | Signing secret missing | Set the secret and re-run; the ad-hoc run skips the DMG and appcast, but can still attach the sdist and wheel to an existing release |
| `No published GitHub release for …` | Release not yet published | Publish the release, then re-run the workflow with the tag |
| Notarization rejected | Credential or Apple-side issue | Check the `make-dmg.sh` log; the `NOTARY_*` trio must match one App Store Connect account |
| Appcast step fails with `The release needs SPARKLE_PRIVATE_KEY`; no new assets are attached | `SPARKLE_PRIVATE_KEY` is missing | Set the secret to the private key matching the app's `SUPublicEDKey`, then re-run the workflow |
