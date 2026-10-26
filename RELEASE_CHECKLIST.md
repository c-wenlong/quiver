# Release checklist

Cutting a Quiver release. The pipeline in
[.github/workflows/release.yml](.github/workflows/release.yml) does the
building, signing, notarizing and publishing; this list is what a human does
around it.

## 1. Pre-flight

- [ ] `version` in `pyproject.toml` bumped to the release version; the
      workflow rejects a tag that does not match it.
- [ ] `CHANGELOG.md` has an entry for the release.
- [ ] CI is green on `main` (all shards, Coverage, Greptile Review).
- [ ] Every PR merged since the last release is in the installed app:
      `app/script/install-app.sh`, then confirm the merge commit is an
      ancestor of
      `defaults read ~/Applications/Quiver.app/Contents/Info QuiverCommit`.
- [ ] Third-party notices current if dependencies or bundled assets changed:
      `app/THIRD_PARTY_NOTICES.md`.
- [ ] Release notes state the support floor: **macOS 26+, Apple Silicon
      only**.

## 2. Secrets (must exist in repo settings, never in the tree)

`MACOS_CERTIFICATE`, `MACOS_CERTIFICATE_PASSWORD`, `KEYCHAIN_PASSWORD`,
`NOTARY_KEY`, `NOTARY_KEY_ID`, `NOTARY_ISSUER`, `SPARKLE_PRIVATE_KEY`.

Check presence without printing values in GitHub repo settings under
Secrets and variables → Actions. If any is missing, the macOS job fails
partway: fix the secret, delete the tag, re-tag, re-run.

## 3. Cut the release

Either push a tag:

```bash
git tag -a v1.0.1 -m "v1.0.1"
git push origin v1.0.1
```

or run the workflow manually: Actions → Release → Run workflow → enter the
tag (e.g. `v1.0.1`). The tag must be `v` + dotted version.

The pipeline then runs without further input:

- builds and `twine check`s the sdist/wheel, smoke-tests the wheel in a
  clean venv on Linux and macOS,
- runs `swift test`, builds `Quiver.app` signed with the Developer ID,
  smoke-tests the bundle, notarizes it and packages the DMG,
- downloads Sparkle 2.10.0 tools, verifies the pinned SHA-256, signs the
  appcast with `SPARKLE_PRIVATE_KEY` (stdin, never argv/files), validates
  the XML, uploads the `macos-app` artifact and creates the GitHub
  Release with the DMG + appcast.

## 4. Verify before announcing

- [ ] Release page has the DMG and `appcast.xml` under the tag.
- [ ] `curl -sL https://github.com/c-wenlong/quiver/releases/latest/download/appcast.xml`
      returns the new item with a `sparkle:edSignature`.
- [ ] Download the DMG, mount it, run
      `spctl -a -vv Quiver.app` → `accepted source=Notarized Developer ID`,
      and `codesign -dvvv Quiver.app` → expected Developer ID + team.
- [ ] An installed older build picks the update up via Sparkle.
- [ ] `pip install` the released wheel in a throwaway HOME and run
      `swe --help` / `swe init --check`.

## 5. Afterwards

- [ ] Reinstall the app from the release commit (`app/script/install-app.sh`)
      and verify `QuiverCommit` again.
- [ ] Close or re-milestone issues shipped by the release.

## Hard rules

- Never put a signing secret or the Sparkle private key in the tree, on a
  command line, or in CI logs. The key goes to `sign_update` on stdin only.
- Never publish a build that skipped notarization; Gatekeeper will refuse it.
- Never move or delete a published tag: Sparkle asset URLs are tag-based, so
  breaking them breaks updates for every installed copy. Re-release under a
  new tag instead.
- Forks inherit this feed: a prod build embeds `SUFeedURL` and the bundled
  EdDSA public key, so a fork's app would "update" to upstream releases.
  A fork must swap both for its own or disable Sparkle.
