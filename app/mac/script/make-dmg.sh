#!/usr/bin/env bash
# Package a built SWE.app into SWE-<version>.dmg: a laid-out install
# window with the app on the left, an /Applications link on the right and
# a drag arrow between them (dmg-settings.py, make-dmg-background.swift).
# LICENSE and THIRD_PARTY_NOTICES.md travel inside the bundle (build-app.sh).
#
#   app/mac/script/make-dmg.sh [path/to/SWE.app] [output.dmg]
#
# Defaults: the app is "$SWE_BUILD_DIR/SWE.app" (else
# app/mac/build/SWE.app), and the DMG lands next to it, named after the
# bundle's CFBundleShortVersionString ("SWE-1.0.0.dmg", or plain
# "SWE.dmg" when the bundle carries no real version).
#
# The app inside must already be signed: build-app.sh does that, with
# the hardened runtime and a secure timestamp on prod. This script adds
# what the bundle cannot do for itself:
#
#   1. Notarizes and staples the .app first when it does not already
#      carry a ticket (stapler validate), so a build signed without
#      SWE_NOTARY_PROFILE still ships stapled.
#   2. Signs the DMG itself with SWE_SIGN_IDENTITY, or the first
#      "Developer ID Application" identity in the keychain. Ad-hoc or
#      unsigned apps get an unsigned DMG.
#   3. Submits the DMG to notarization and staples the ticket onto it,
#      so Gatekeeper passes the image, and the stapled app inside, even
#      on a Mac that is offline at first launch.
#
# Notarization takes one of two credential forms, the same contract
# build-app.sh and release.yml use (see BOOTSTRAP.md):
#   SWE_NOTARY_PROFILE                        a `notarytool
#                                              store-credentials` keychain
#                                              profile (the local flow)
#   NOTARY_KEY + NOTARY_KEY_ID + NOTARY_ISSUER the App Store Connect API
#                                              key; NOTARY_KEY is the
#                                              base64 text of the .p8, as
#                                              the CI secret stores it
# A partial NOTARY_* set is an error: silently skipping notarization is
# how a signed-but-unshippable DMG gets made.
#
# With no credentials at all the DMG is still produced; a local ad-hoc
# build is exactly that. Unless the app is Developer-ID-signed: an
# unnotarized copy of that is a broken release rather than a dev
# artifact, and the script stops instead of writing it.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
app="${1:-${SWE_BUILD_DIR:-${QUIVER_BUILD_DIR:-$here/build}}/SWE.app}"

if [ ! -d "$app" ] || [ ! -f "$app/Contents/Info.plist" ]; then
  echo "make-dmg: $app is not a built .app; run script/build-app.sh first" >&2
  exit 1
fi
app="$(cd "$app" && pwd -P)"

version="$(defaults read "$app/Contents/Info" CFBundleShortVersionString 2>/dev/null || true)"
case "$version" in
  ""|0.0.0|*[!0-9.]*) name="SWE.dmg" ;;
  *)                  name="SWE-$version.dmg" ;;
esac
out="${2:-$(dirname "$app")/$name}"
mkdir -p "$(dirname "$out")"

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

# Notarization credentials, in either form. have_notary tracks whether a
# submission should happen at all; notary_args carries how.
have_notary=false
notary_args=()
if [ -n "${SWE_NOTARY_PROFILE:-${QUIVER_NOTARY_PROFILE:-}}" ]; then
  have_notary=true
  notary_args=(--keychain-profile "${SWE_NOTARY_PROFILE:-$QUIVER_NOTARY_PROFILE}")
elif [ -n "${NOTARY_KEY:-}${NOTARY_KEY_ID:-}${NOTARY_ISSUER:-}" ]; then
  if [ -z "${NOTARY_KEY:-}" ] || [ -z "${NOTARY_KEY_ID:-}" ] || [ -z "${NOTARY_ISSUER:-}" ]; then
    echo "make-dmg: NOTARY_KEY, NOTARY_KEY_ID and NOTARY_ISSUER must all be" >&2
    echo "set together; one or more is missing." >&2
    exit 1
  fi
  have_notary=true
  keyfile="$scratch/AuthKey_$NOTARY_KEY_ID.p8"
  printf '%s' "$NOTARY_KEY" | base64 --decode > "$keyfile"
  notary_args=(--key "$keyfile" --key-id "$NOTARY_KEY_ID" --issuer "$NOTARY_ISSUER")
fi
# ${arr[@]+...} guards the empty array: bash 3.2 reads a bare "${arr[@]}"
# under set -u as unbound.
notarize() { xcrun notarytool submit "$1" ${notary_args[@]+"${notary_args[@]}"} --wait; }

# What is the app signed with? An unsigned or ad-hoc bundle still makes a
# local DMG; a Developer ID one must not ship unnotarized.
sig="$(codesign -dvv "$app" 2>&1 || true)"
if printf '%s\n' "$sig" | grep -q '^Authority=Developer ID Application: '; then
  app_signed=devid
elif printf '%s\n' "$sig" | grep -q '^Signature=adhoc'; then
  app_signed=adhoc
else
  app_signed=other
fi

if [ "$have_notary" = false ]; then
  if [ "$app_signed" = "devid" ]; then
    echo "make-dmg: $app is Developer-ID-signed but no notarization" >&2
    echo "credentials were given; Gatekeeper rejects the result on every" >&2
    echo "other Mac. Set SWE_NOTARY_PROFILE or NOTARY_KEY, NOTARY_KEY_ID" >&2
    echo "and NOTARY_ISSUER (see BOOTSTRAP.md)." >&2
    exit 1
  fi
  echo "make-dmg: no notarization credentials; the DMG cannot be" >&2
  echo "notarized or stapled (app signature: $app_signed). Fine for local" >&2
  echo "use, not for release." >&2
else
  if [ "$app_signed" != "devid" ]; then
    echo "make-dmg: warning: app is $app_signed-signed; Apple normally" >&2
    echo "rejects notarization for anything but Developer ID" >&2
  fi
  # Staple the bundle first so the app inside the DMG passes Gatekeeper
  # offline; skipped when a previous run already stapled it.
  if xcrun stapler validate "$app" >/dev/null 2>&1; then
    echo "make-dmg: app already carries a stapled ticket" >&2
  else
    echo "make-dmg: notarizing $app" >&2
    ditto -c -k --keepParent "$app" "$scratch/SWE.zip"
    notarize "$scratch/SWE.zip"
    xcrun stapler staple "$app"
  fi
fi

# The install window. dmgbuild writes the window layout (.DS_Store) and
# the background straight into the image, with no Finder or AppleScript,
# so it behaves the same on a headless CI runner. It and its two
# dependencies are pure Python, pinned by hash into a throwaway venv.
cat > "$scratch/dmg-requirements.txt" <<'REQS'
dmgbuild==1.6.7 --hash=sha256:37ee5771c377beb3203d9164aae8046ffed8531c06edf9227f5788b3c599b1bf
ds_store==1.3.3 --hash=sha256:b92a371efbf1b4ccce2a04d1ed13fceacc4736c81ba09cf5aefb74c088160a35
mac_alias==2.2.3 --hash=sha256:7362b521d2132ef92f606a37abfed5fcd849ceb2f28b6f9743e014b02af92f0d
REQS
python3 -m venv "$scratch/venv"
"$scratch/venv/bin/pip" install --quiet --disable-pip-version-check \
  --only-binary :all: --require-hashes -r "$scratch/dmg-requirements.txt"

# The background at 1x and 2x, joined into one TIFF so Retina screens get
# the sharp copy.
swift "$here/script/make-dmg-background.swift" 1 "$scratch/background.png"
swift "$here/script/make-dmg-background.swift" 2 "$scratch/background@2x.png"
tiffutil -cathidpicheck "$scratch/background.png" "$scratch/background@2x.png" \
  -out "$scratch/background.tiff" 2>/dev/null

"$scratch/venv/bin/dmgbuild" -s "$here/script/dmg-settings.py" \
  -D app="$app" -D background="$scratch/background.tiff" \
  SWE "$out" >/dev/null
hdiutil verify "$out" >/dev/null

# Sign the image when a real identity is around; "-" or none leaves it
# unsigned, which is what the ad-hoc path wants anyway.
identity="${SWE_SIGN_IDENTITY:-${QUIVER_SIGN_IDENTITY:-}}"
if [ -z "$identity" ]; then
  identity="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/^ *[0-9][0-9]*) \([0-9A-F]\{40\}\) "Developer ID Application: .*/\1/p' \
    | head -n 1)"
fi
if [ -n "$identity" ] && [ "$identity" != "-" ]; then
  codesign --force --sign "$identity" --timestamp "$out"
  echo "make-dmg: signed $out" >&2
fi

if [ "$have_notary" = true ]; then
  echo "make-dmg: notarizing $out" >&2
  notarize "$out"
  xcrun stapler staple "$out"
  xcrun stapler validate "$out" >/dev/null
  echo "make-dmg: notarized and stapled" >&2
fi

echo "$out"
