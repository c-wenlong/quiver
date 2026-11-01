#!/usr/bin/env bash
# Build SWE.app from the SwiftPM package.
#
#   app/mac/script/build-app.sh [debug|release]   # default: release
#
# Writes app/mac/build/SWE.app with the widget extension embedded at
# Contents/PlugIns/SWEWidgets.appex and swe, with a Python of its own,
# at Contents/Resources/swe (see bundle-swe.sh). Open it with
# `open app/mac/build/SWE.app`.
#
# Output: SWE_BUILD_DIR overrides where the bundle lands
# (default "$here/build"); the app ends up at
# "$SWE_BUILD_DIR/SWE.app". The rebuild button in Settings uses
# this so a build never overwrites the running copy.
#
# Channel: SWE_CHANNEL=dev|prod stamps SWEChannel in the app's
# Info.plist (default dev). Dev builds also stamp the checkout's commit,
# path and dirty state, which Settings > Updates compares against HEAD.
# SWE_SOURCE_PATH stamps another checkout as the path: install-app.sh
# builds in a worktree of its own but points Updates at the user's.
#
# Signing: macOS remembers privacy grants (Desktop and Documents access,
# Accessibility) against the app's signing identity. An ad-hoc signature
# changes with every build, so each rebuild would ask again. The script
# therefore signs with a stable identity when it can find one:
#   SWE_SIGN_IDENTITY="Developer ID Application: …"  # or a certificate hash
# else the first keychain identity for the channel: "Developer ID
# Application" under SWE_CHANNEL=prod, "Apple Development" under dev.
# A dev build with no identity falls back to ad-hoc (CI, or a Mac without
# a certificate); a prod build stops instead — a release without a
# Developer ID cannot ship. SWE_SIGN_IDENTITY=- still forces ad-hoc,
# which is how CI builds prod before the certificate exists.
#
# Prod signatures also set the hardened runtime (required before Apple
# will notarize) and a secure timestamp, so they keep verifying after
# the certificate expires. SWE_NOTARY_PROFILE=… (a `notarytool
# store-credentials` keychain profile) notarizes and staples after
# signing; script/make-dmg.sh then packages the DMG and can notarize it
# with either that profile or the NOTARY_* App Store Connect key.
#
# A prod build also refuses a tree it cannot ship from: non-arm64 hosts
# (releases are Apple Silicon only, per app/mac/ROADMAP.md), a dirty checkout
# (untracked files under src/swe would bundle in silently) and a
# checkout with no version tag (the bundle would stamp 0.0.0, which
# ReleaseCheck then treats as older than every release).
set -euo pipefail

config="${1:-release}"
channel="${SWE_CHANNEL:-${QUIVER_CHANNEL:-dev}}"
case "$channel" in
  dev|prod) ;;
  *) echo "build-app: SWE_CHANNEL must be dev or prod" >&2; exit 1 ;;
esac
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"
build_dir="${SWE_BUILD_DIR:-${QUIVER_BUILD_DIR:-$here/build}}"
mkdir -p "$build_dir"

if [ "$channel" = "prod" ]; then
  if [ "$(uname -m)" != "arm64" ]; then
    echo "build-app: prod releases are Apple Silicon only; this is $(uname -m)." >&2
    echo "Ship Intel by running the same tag's build on Intel hardware." >&2
    exit 1
  fi
  if ! git -C "$here" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "build-app: prod needs a git checkout; the version stamp and the" >&2
    echo "bundled swe both come from the tree." >&2
    exit 1
  fi
  if [ -n "$(git -C "$here" status --porcelain)" ]; then
    echo "build-app: refusing a prod build from a dirty checkout: uncommitted" >&2
    echo "edits (and untracked files under src/swe) ship silently. Commit or" >&2
    echo "stash first, or build a clean worktree like install-app.sh does." >&2
    exit 1
  fi
  if ! git -C "$here" describe --tags --abbrev=0 >/dev/null 2>&1; then
    echo "build-app: prod needs a version tag and git describe found none" >&2
    echo "(a shallow clone or tarball), so the bundle would stamp 0.0.0." >&2
    echo "Fetch tags first: git fetch --tags." >&2
    exit 1
  fi
fi

# Info.plist values come from git and the environment, so a checkout in a
# path containing &, < or > would otherwise write broken XML.
xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

swift build -c "$config" --product SWE
swift build -c "$config" --product SWEWidgets
swift build -c "$config" --product SWEFanHelper
bindir="$(swift build -c "$config" --show-bin-path)"
bin="$bindir/SWE"

bundle="$build_dir/SWE.app"
rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
cp "$bin" "$bundle/Contents/MacOS/SWE"
# The root fan helper the Compute page installs. Its own executable, since
# a copy of SWE's outside the bundle can't find Sparkle.framework.
cp "$bindir/SWEFanHelper" "$bundle/Contents/MacOS/SWEFanHelper"
# SWECore's resources (the pricing snapshot, the harness logos). Inside
# Contents/Resources, since codesign refuses anything at the bundle root;
# CoreResources looks here. The widget extension gets its own copy below.
cp -R "$bindir/SWE_SWECore.bundle" "$bundle/Contents/Resources/"

# Stops the build when a copy of SWECore's bundle lacks a resource or a
# harness logo: a packaged app or extension never falls back to the build
# folder, so whatever is missing here is missing for the user. The native
# build system (Swift 6.3 and older) puts the files at the bundle's top
# level; swiftbuild (the default from Swift 6.4) makes a macOS bundle with
# them under Contents/Resources. Foundation's Bundle reads either, so
# CoreResources needs no change, and the check looks where they are.
check_core_resources() {
  local core="$1/Contents/Resources/SWE_SWECore.bundle" resource
  if [ -d "$core/Contents/Resources" ]; then
    core="$core/Contents/Resources"
  fi
  for resource in pricing.json pricing-supplement.json harness-icons.tsv \
                  Geist.ttf Geist-Italic.ttf GeistMono.ttf GeistMono-Italic.ttf \
                  Geist-OFL.txt \
                  "$here"/Sources/SWECore/Resources/harness-*.svg; do
    resource="$(basename "$resource")"
    if [ ! -f "$core/$resource" ]; then
      echo "build-app: $resource missing from $core" >&2
      exit 1
    fi
  done
}
check_core_resources "$bundle"

# The licence and the notices third-party licences ask to travel with
# copies. They live in the bundle rather than beside it in the DMG, so they
# go wherever the app is dragged and the install window shows only the app.
for notice in "$here/../../LICENSE" "$here/THIRD_PARTY_NOTICES.md"; do
  if [ ! -f "$notice" ]; then
    echo "build-app: $notice missing" >&2
    exit 1
  fi
  cp "$notice" "$bundle/Contents/Resources/"
done

# swe and its Python, so the app works on a Mac that has neither.
"$here/script/bundle-swe.sh" "$bundle"

# The app icon, from the square art in Icons/ (assets/logo-square.png).
art="$here/Icons/AppIcon.png"
if [ ! -f "$art" ]; then
  echo "build-app: $art missing" >&2
  exit 1
fi
swift "$here/script/make-icon.swift" "$art" "$bundle/Contents/Resources/AppIcon.icns"

# What Settings shows: the full describe string ("v0.2.9-296-g571fbd8").
describe="$(git -C "$here" describe --tags --always 2>/dev/null || echo 0.0.0)"
# Bundle versions must be plain dotted numbers. LaunchServices cannot parse a
# describe string, and WidgetKit then refuses every widget render with
# bundleStubNotSupported ("Bundle version did not match").
version="$( (git -C "$here" describe --tags --abbrev=0 2>/dev/null || true) | sed 's/^v//')"
# A prerelease or build suffix (v0.3.0-rc1, v0.3.0+mac) keeps its dotted
# part: 0.0.0 would read as older than every release.
version="${version%%[-+]*}"
case "$version" in
  ""|*[!0-9.]*)
    if [ "$channel" = "prod" ]; then
      echo "build-app: the version tag gives no dotted version to stamp; prod would ship 0.0.0." >&2
      exit 1
    fi
    version="0.0.0" ;;
esac
build="$(git -C "$here" rev-list --count HEAD 2>/dev/null || echo 1)"
# Update stamping: dev builds record where they were built from, so
# Settings > Updates can tell the user the checkout has moved on. Prod
# builds stamp the channel only; GitHub releases carry no source path.
commit="$(git -C "$here" rev-parse HEAD 2>/dev/null || echo "")"
if [ "$channel" = "dev" ]; then
  source_path="${SWE_SOURCE_PATH:-${QUIVER_SOURCE_PATH:-$(git -C "$here" rev-parse --show-toplevel 2>/dev/null || echo "")}}"
else
  source_path=""
fi
if [ -n "$(git -C "$here" status --porcelain 2>/dev/null || echo "")" ]; then
  dirty=true
else
  dirty=false
fi
# Sparkle keys ship only in prod builds: a dev build has no release to
# compare against and could not verify a signature without SUPublicEDKey
# either, so it never starts the updater. The feed is the appcast attached
# to the latest GitHub release; the EdDSA public key pairs with the private
# key that signs each release's appcast (Sparkle's generate_keys/sign_update).
sparkle_plist=""
if [ "$channel" = "prod" ]; then
  sparkle_plist='  <key>SUFeedURL</key><string>https://github.com/c-wenlong/quiver/releases/latest/download/appcast.xml</string>
  <key>SUPublicEDKey</key><string>'"${SWE_SUPUBLICEDKEY:-${QUIVER_SUPUBLICEDKEY:-g22OIueAUiWwAy7NNP8kSh8Pgr5cufLqh4Ssgos1PBE=}}"'</string>
'
fi
describe_xml="$(xml_escape "$describe")"
channel_xml="$(xml_escape "$channel")"
commit_xml="$(xml_escape "$commit")"
source_path_xml="$(xml_escape "$source_path")"
# NSAllowsArbitraryLoadsInWebContent: a hub on `swe remote serve --tailnet`
# answers plain http on its tailnet address (WireGuard already encrypts
# it), and the machine switcher's web view must reach it. It covers web
# views only, and that view never leaves the hub's origin (HubNavigation).
cat > "$bundle/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SWE</string>
  <key>CFBundleDisplayName</key><string>SWE</string>
  <key>CFBundleIdentifier</key><string>com.cwenlong.swe</string>
  <key>CFBundleExecutable</key><string>SWE</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${version}</string>
  <key>CFBundleVersion</key><string>${build}</string>
  <key>SWEVersion</key><string>${describe_xml}</string>
  <key>SWEChannel</key><string>${channel_xml}</string>
  <key>SWECommit</key><string>${commit_xml}</string>
  <key>SWESourcePath</key><string>${source_path_xml}</string>
  <key>SWEDirty</key><${dirty}/>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSDesktopFolderUsageDescription</key><string>SWE runs swe and reads session folders, some of which live in your Desktop folder.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>SWE runs swe and reads session folders, some of which live in your Documents folder.</string>
  <key>NSAppleEventsUsageDescription</key><string>SWE opens your terminal to run a sign-in or resume a session.</string>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsArbitraryLoadsInWebContent</key><true/></dict>
${sparkle_plist}</dict>
</plist>
PLIST

# The widget extension: SwiftPM's executable in an .appex, no Xcode project
# needed. Its Mach-O entry point is _NSExtensionMain (set in Package.swift's
# linkerSettings), which hands control to the @main WidgetBundle the way an
# Xcode-built extension would.
appex="$bundle/Contents/PlugIns/SWEWidgets.appex"
mkdir -p "$appex/Contents/MacOS" "$appex/Contents/Resources"
cp "$bindir/SWEWidgets" "$appex/Contents/MacOS/SWEWidgets"
# An extension's Bundle.main is the .appex, so CoreResources looks in the
# appex's own Contents/Resources for the harness logos; without this copy
# the widgets would have none. The sandbox always lets an extension read
# its own bundle, so the entitlements need nothing for it.
cp -R "$bindir/SWE_SWECore.bundle" "$appex/Contents/Resources/"
check_core_resources "$appex"
cat > "$appex/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SWEWidgets</string>
  <key>CFBundleDisplayName</key><string>SWE</string>
  <key>CFBundleIdentifier</key><string>com.cwenlong.swe.widgets</string>
  <key>CFBundleExecutable</key><string>SWEWidgets</string>
  <key>CFBundlePackageType</key><string>XPC!</string>
  <key>CFBundleShortVersionString</key><string>${version}</string>
  <key>CFBundleVersion</key><string>${build}</string>
  <key>SWEVersion</key><string>${describe_xml}</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSExtension</key>
  <dict>
    <key>NSExtensionPointIdentifier</key><string>com.apple.widgetkit-extension</string>
  </dict>
</dict>
</plist>
PLIST

# Sparkle.framework: SWEApp links it and finds it through
# @executable_path/../Frameworks. Without it the app cannot launch, so a
# missing copy stops the build rather than shipping a dead app.
frameworks="$bundle/Contents/Frameworks"
mkdir -p "$frameworks"
if [ ! -d "$bindir/Sparkle.framework" ]; then
  echo "build-app: Sparkle.framework missing from $bindir" >&2
  exit 1
fi
cp -R "$bindir/Sparkle.framework" "$frameworks/"

identity="${SWE_SIGN_IDENTITY:-${QUIVER_SIGN_IDENTITY:-}}"
if [ -z "$identity" ]; then
  # Optional: a failed lookup falls back to ad-hoc for dev rather than
  # stopping the build; prod has no fallback and stops below. A failure
  # signing with an identity that was found, or one asked for in
  # SWE_SIGN_IDENTITY, still stops either channel.
  if identities="$(security find-identity -v -p codesigning 2>&1)"; then
    if [ "$channel" = "prod" ]; then
      cert_label="Developer ID Application: "
    else
      cert_label="Apple Development: "
    fi
    identity="$(printf '%s\n' "$identities" \
      | sed -n "s/^ *[0-9][0-9]*) \([0-9A-F]\{40\}\) \"$cert_label.*/\1/p" | head -n 1)"
  else
    echo "build-app: couldn't list signing identities: $identities" >&2
  fi
fi
if [ "$identity" = "-" ]; then
  echo "build-app: ad-hoc signing; privacy grants won't survive a rebuild" >&2
elif [ -z "$identity" ]; then
  if [ "$channel" = "prod" ]; then
    echo "build-app: prod needs a \"Developer ID Application\" identity and none" >&2
    echo "was found; a release cannot fall back to ad-hoc. Install the certificate," >&2
    echo "pass SWE_SIGN_IDENTITY, or set SWE_SIGN_IDENTITY=- to force an" >&2
    echo "unsigned build anyway." >&2
    exit 1
  fi
  echo "build-app: ad-hoc signing; privacy grants won't survive a rebuild" >&2
  identity="-"
fi
# Prod signs for Gatekeeper: every Mach-O gets the hardened runtime (a
# notarization prerequisite) and a secure timestamp, so the signature
# keeps verifying after the certificate expires. The same goes for any
# build headed to notarization (SWE_NOTARY_PROFILE set), whatever its
# channel. Dev builds keep plain signatures: --timestamp needs Apple's
# timestamp server, which a dev machine may be offline from, and ad-hoc
# signatures can't carry one.
sign_opts=()
if [ "$identity" != "-" ] &&
   { [ "$channel" = "prod" ] || [ -n "${SWE_NOTARY_PROFILE:-${QUIVER_NOTARY_PROFILE:-}}" ]; }; then
  sign_opts=(--options runtime --timestamp)
fi
# Inside out: the bundled Python's binaries, the extension with its sandbox,
# then the app around them. The Python sits in Resources, where codesign
# would only hash its binaries as data, so each is signed as code first.
# ${arr[@]+...}: bash 3.2 reads a bare empty "${arr[@]}" under set -u as
# unbound, so the expansion is guarded.
# The Python executable itself also carries SWE.entitlements: the hub
# it runs (a LaunchAgent, so its own responsible process) opens Terminal
# through osascript for the web app's Sessions page, which the hardened
# runtime refuses without the Apple Events entitlement. Libraries and
# extension modules get plain signatures; entitlements mean nothing there.
find "$bundle/Contents/Resources/swe" -type f | while IFS= read -r file; do
  kind="$(file -b "$file")"
  case "$kind" in
    Mach-O*executable*)
      codesign --force --sign "$identity" ${sign_opts[@]+"${sign_opts[@]}"} \
        --entitlements "$here/script/SWE.entitlements" "$file" >/dev/null ;;
    Mach-O*)
      codesign --force --sign "$identity" ${sign_opts[@]+"${sign_opts[@]}"} "$file" >/dev/null ;;
  esac
done
# Embedded frameworks sign inside-out like swe: loose Mach-O files first
# (Sparkle's Autoupdate, the XPC helpers' binaries), then the nested
# bundles around them (Downloader.xpc, Installer.xpc, Updater.app), then
# the framework itself. find -depth orders children before parents.
find "$frameworks" -type f | while IFS= read -r file; do
  if file -b "$file" | grep -q '^Mach-O'; then
    codesign --force --sign "$identity" ${sign_opts[@]+"${sign_opts[@]}"} "$file" >/dev/null
  fi
done
find "$frameworks" -depth -type d \
  \( -name '*.xpc' -o -name '*.app' -o -name '*.framework' \) \
  | while IFS= read -r inner; do
    codesign --force --sign "$identity" ${sign_opts[@]+"${sign_opts[@]}"} "$inner" >/dev/null
  done
codesign --force --sign "$identity" ${sign_opts[@]+"${sign_opts[@]}"} \
  --entitlements "$here/script/SWEWidgets.entitlements" "$appex" >/dev/null
# The bundle's signature only covers its main executable as code; the fan
# helper beside it is signed on its own, like the frameworks above.
codesign --force --sign "$identity" ${sign_opts[@]+"${sign_opts[@]}"} \
  "$bundle/Contents/MacOS/SWEFanHelper" >/dev/null
# Hardened runtime audit for the app itself (see commit 5abae68d for the
# full list): the bundled libcrypto is re-signed here with the same
# identity, which is all library validation asks. The one exception it
# needs is Apple Events: Terminal launches go through an osascript child,
# and TCC charges that event to SWE as the responsible app, so the
# hardened runtime refuses it (-1743, no prompt) unless SWE.entitlements
# grants com.apple.security.automation.apple-events.
codesign --force --sign "$identity" ${sign_opts[@]+"${sign_opts[@]}"} \
  --entitlements "$here/script/SWE.entitlements" "$bundle" >/dev/null

# Notarize and staple when a `notarytool store-credentials` keychain
# profile is named. The zip exists only for the submission; stapler
# writes the ticket onto the bundle itself so Gatekeeper passes it
# offline. make-dmg.sh packages the result into a DMG and runs the same
# submit + staple on the image (it also accepts the NOTARY_* App Store
# Connect key form, which is what release.yml passes it).
if [ -n "${SWE_NOTARY_PROFILE:-${QUIVER_NOTARY_PROFILE:-}}" ]; then
  notary_dir="$(mktemp -d)"
  # The zip exists only for the submission; drop it and its folder whether
  # notarization passes or a failed submit/staple exits the script.
  trap 'rm -rf "$notary_dir"' EXIT
  ditto -c -k --keepParent "$bundle" "$notary_dir/SWE.zip"
  xcrun notarytool submit "$notary_dir/SWE.zip" \
    --keychain-profile "${SWE_NOTARY_PROFILE:-$QUIVER_NOTARY_PROFILE}" --wait
  xcrun stapler staple "$bundle"
  rm -rf "$notary_dir"
  trap - EXIT
fi
echo "$bundle"
