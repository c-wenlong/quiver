# BOOTSTRAP.md — session handoff

Where the Quiver app work stands and what a new session needs. Authoritative task list is [app/ROADMAP.md](app/ROADMAP.md); this file is the state, not the spec.

## Repo map

| What | Where |
|---|---|
| App source | `app/` (SwiftPM; `QuiverApp` shell + `QuiverCore` library) |
| Python CLI (`swe`) | `src/quiver/` |
| Tests | `app/Tests/`, `tests/` |
| Roadmap | `app/ROADMAP.md` |
| App build | `app/script/build-app.sh [debug\|release]`; `QUIVER_CHANNEL=prod` for release builds |
| Install | `app/script/install-app.sh` → `~/Applications/Quiver.app` (builds `origin/main` only — never install a branch) |
| Bundled Python | `app/script/bundle-swe.sh` → `Contents/Resources/swe/` |
| Release workflow | `.github/workflows/release.yml` |

## PR loop (follow every time)

1. `git worktree add -b <branch> /tmp/quiver-<name> origin/main` — one PR per concern.
2. Tests: `python3 -m unittest discover -s tests -p 'test_*.py'` (throwaway `HOME`), `swift test` in `app/`.
3. Push, `gh pr create`, wait for CI + Greptile, fix or rebut every thread, resolve threads, merge.
4. **Reinstall after every merge**: `app/script/install-app.sh`, then verify `git merge-base --is-ancestor <merge> "$(defaults read ~/Applications/Quiver.app/Contents/Info QuiverCommit)"`.
5. Tick the roadmap line when a milestone item merges.
6. Never `git config`, never force-push to main, never auto-merge.

## M8 (DMG milestone) status

Phases 0–3: **done**. Phase 4: code-complete, credential-gated.

| Phase 4 item | State | PR |
|---|---|---|
| Missing `swe` → `.notInstalled`; `~/.api_keys` file-vs-dir | Merged | #408 |
| Prod channel hides picker/Rebuild | Merged | #409 |
| Keychain writes via Security framework | Merged | #410 |
| Version stamps in shallow CI (fetch tags) | Merged | #411 |
| `NSAppleEventsUsageDescription` | Merged | #412 |
| Repo public for release check | Done | — |
| Bundled libcrypto for Droid limits | Merged | #413 |
| Developer ID + hardened runtime + timestamp; notarize+staple via `QUIVER_NOTARY_PROFILE` | Merged; **first real submission pending** | #414 |
| macOS release job (build → sign → notarize → DMG → appcast → attach) | Merged; unsigned until secrets land | #415, #417 |
| Sparkle in-app updates + appcast feed | Merged | #416, #417 |
| Apple Silicon only (decided; no universal) | Done — say so on the download page | — |

## Blocked on you

Two things stand between "code complete" and "shipping":

1. **Issuer ID** — UUID at App Store Connect › Users and Access › Integrations › App Store Connect API. Once known:
   ```sh
   xcrun notarytool store-credentials quiver-notary \
     --key ~/Downloads/AuthKey_2JD7N46XV7.p8 \
     --key-id 2JD7N46XV7 --issuer <UUID>
   QUIVER_NOTARY_PROFILE=quiver-notary QUIVER_CHANNEL=prod \
     app/script/build-app.sh release   # local signed+notarized build
   ```

2. **GitHub secrets** (repo Settings › Secrets and variables › Actions):

   | Secret | Value |
   |---|---|
   | `MACOS_CERTIFICATE` | base64 of `.p12` export of *Developer ID Application: Wenlong Chen (MAK855QGSV)* — Keychain Access › My Certificates › export **with** private key |
   | `MACOS_CERTIFICATE_PASSWORD` | the `.p12` export password |
   | `KEYCHAIN_PASSWORD` | any string (throwaway CI keychain) |
   | `NOTARY_KEY` | `base64 -i ~/Downloads/AuthKey_2JD7N46XV7.p8` |
   | `NOTARY_KEY_ID` | `2JD7N46XV7` |
   | `NOTARY_ISSUER` | the UUID above |
   | `SPARKLE_PRIVATE_KEY` | contents of `~/.quiver/secrets/sparkle-ed25519.txt` (0600, already exported) |

   After these land, pushing a `v*` tag produces a signed, notarized DMG plus a signed `appcast.xml` that prod builds' Check for Updates reads.

## Then cut a release

- Order matters: push `v0.x.y` **then** publish the GitHub release. If the workflow finishes before the release exists, it skips the attach step and exits green — re-run it (`Actions › Release › Run workflow`, same tag) so the wheel + DMG + `appcast.xml` land on the release.
- Release notes should say **macOS 26+, Apple Silicon only**.
- First-run smoke test on a fresh Mac profile: Gatekeeper pass, Check for Updates, bundled `swe` with no system Python.

## Other open roadmap work (M8 phase 5 leftovers)

- Ledger scan progress + partial results; Desktop/Documents prompt explanation; don't poll Cursor/Devin/Codex for users without those harnesses; fan-helper edge cases; menu-bar incident filtering; Claude IP check (low). See the unchecked lines at the bottom of `app/ROADMAP.md`.

## Gotchas learned this milestone

- Bundled pbs Python: `CDLL(None)` segfaults and system `libcrypto` aborts under the signed interpreter — that is why `bundle-swe.sh` ships its own `libcrypto.3.dylib` + `OPENSSL-LICENSE.txt`, and `QUIVER_BUNDLED_PYTHON=1` makes `rate_limits.py` load only that file.
- SwiftPM binary deps land in `$(swift build --show-bin-path)` — `build-app.sh` copies `Sparkle.framework` from there; a missing copy fails the build because the app would not launch.
- Sparkle: `updaterDelegate` is **weak** (retain it yourself); `generate_appcast --ed-key-file -` streams the private key via stdin; `generate_keys -x <file>` exports the keychain-held key for CI.
- `notarytool` keychain profiles (`store-credentials`) and CI secrets are different stores — both are wired: `QUIVER_NOTARY_PROFILE` local, `NOTARY_*` in the workflow.
- Bash 3.2: expand optional arrays as `${arr[@]+"${arr[@]}"}` under `set -u`.
- Greptile reviews every PR; expect P1/P2 comments each round — fix or rebut, then resolve the thread before merging.
