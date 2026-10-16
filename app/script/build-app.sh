#!/usr/bin/env bash
# Build Quiver.app from the SwiftPM package.
#
#   app/script/build-app.sh [debug|release]   # default: release
#
# Writes app/build/Quiver.app with the widget extension embedded at
# Contents/PlugIns/QuiverWidgets.appex. Open it with
# `open app/build/Quiver.app`.
#
# Output: QUIVER_BUILD_DIR overrides where the bundle lands
# (default "$here/build"); the app ends up at
# "$QUIVER_BUILD_DIR/Quiver.app". The rebuild button in Settings uses
# this so a build never overwrites the running copy.
#
# Channel: QUIVER_CHANNEL=dev|prod stamps QuiverChannel in the app's
# Info.plist (default dev). Dev builds also stamp the checkout's commit,
# path and dirty state, which Settings > Updates compares against HEAD.
# QUIVER_SOURCE_PATH stamps another checkout as the path: install-app.sh
# builds in a worktree of its own but points Updates at the user's.
#
# Signing: macOS remembers privacy grants (Desktop and Documents access,
# Accessibility) against the app's signing identity. An ad-hoc signature
# changes with every build, so each rebuild would ask again. The script
# therefore signs with a stable identity when it can find one:
#   QUIVER_SIGN_IDENTITY="Apple Development: …"  # or a certificate hash
# else the first "Apple Development" identity in the keychain, else ad-hoc
# (CI, or a Mac without a certificate). QUIVER_SIGN_IDENTITY=- forces ad-hoc.
set -euo pipefail

config="${1:-release}"
channel="${QUIVER_CHANNEL:-dev}"
case "$channel" in
  dev|prod) ;;
  *) echo "build-app: QUIVER_CHANNEL must be dev or prod" >&2; exit 1 ;;
esac
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"
build_dir="${QUIVER_BUILD_DIR:-$here/build}"
mkdir -p "$build_dir"

# Info.plist values come from git and the environment, so a checkout in a
# path containing &, < or > would otherwise write broken XML.
xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

swift build -c "$config" --product Quiver
swift build -c "$config" --product QuiverWidgets
bindir="$(swift build -c "$config" --show-bin-path)"
bin="$bindir/Quiver"

bundle="$build_dir/Quiver.app"
rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
cp "$bin" "$bundle/Contents/MacOS/Quiver"
# QuiverCore's resources (the pricing snapshot, the harness logos). Inside
# Contents/Resources, since codesign refuses anything at the bundle root;
# CoreResources looks here. The widget extension gets its own copy below.
cp -R "$bindir/Quiver_QuiverCore.bundle" "$bundle/Contents/Resources/"

# Stops the build when a copy of QuiverCore's bundle lacks a resource or a
# harness logo: a packaged app or extension never falls back to the build
# folder, so whatever is missing here is missing for the user.
check_core_resources() {
  local core="$1/Contents/Resources/Quiver_QuiverCore.bundle" resource
  for resource in pricing.json pricing-supplement.json harness-icons.tsv \
                  "$here"/Sources/QuiverCore/Resources/harness-*.svg; do
    resource="$(basename "$resource")"
    if [ ! -f "$core/$resource" ]; then
      echo "build-app: $resource missing from $core" >&2
      exit 1
    fi
  done
}
check_core_resources "$bundle"

# App icons: light is the bundle's icon; Settings can switch the Dock icon
# to dark at runtime. Both come from square art in Icons/.
for style in light dark; do
  art="$here/Icons/AppIcon-$style.png"
  if [ ! -f "$art" ]; then
    echo "build-app: $art missing" >&2
    exit 1
  fi
  swift "$here/script/make-icon.swift" "$art" "$bundle/Contents/Resources/AppIcon-$style.icns"
done

# What Settings shows: the full describe string ("v0.2.9-296-g571fbd8").
describe="$(git -C "$here" describe --tags --always 2>/dev/null || echo 0.0.0)"
# Bundle versions must be plain dotted numbers. LaunchServices cannot parse a
# describe string, and WidgetKit then refuses every widget render with
# bundleStubNotSupported ("Bundle version did not match").
version="$( (git -C "$here" describe --tags --abbrev=0 2>/dev/null || true) | sed 's/^v//')"
case "$version" in ""|*[!0-9.]*) version="0.0.0" ;; esac
build="$(git -C "$here" rev-list --count HEAD 2>/dev/null || echo 1)"
# Update stamping: dev builds record where they were built from, so
# Settings > Updates can tell the user the checkout has moved on. Prod
# builds stamp the channel only; GitHub releases carry no source path.
commit="$(git -C "$here" rev-parse HEAD 2>/dev/null || echo "")"
if [ "$channel" = "dev" ]; then
  source_path="${QUIVER_SOURCE_PATH:-$(git -C "$here" rev-parse --show-toplevel 2>/dev/null || echo "")}"
else
  source_path=""
fi
if [ -n "$(git -C "$here" status --porcelain 2>/dev/null || echo "")" ]; then
  dirty=true
else
  dirty=false
fi
describe_xml="$(xml_escape "$describe")"
channel_xml="$(xml_escape "$channel")"
commit_xml="$(xml_escape "$commit")"
source_path_xml="$(xml_escape "$source_path")"
cat > "$bundle/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Quiver</string>
  <key>CFBundleDisplayName</key><string>Quiver</string>
  <key>CFBundleIdentifier</key><string>com.cwenlong.quiver</string>
  <key>CFBundleExecutable</key><string>Quiver</string>
  <key>CFBundleIconFile</key><string>AppIcon-light</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${version}</string>
  <key>CFBundleVersion</key><string>${build}</string>
  <key>QuiverVersion</key><string>${describe_xml}</string>
  <key>QuiverChannel</key><string>${channel_xml}</string>
  <key>QuiverCommit</key><string>${commit_xml}</string>
  <key>QuiverSourcePath</key><string>${source_path_xml}</string>
  <key>QuiverDirty</key><${dirty}/>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSDesktopFolderUsageDescription</key><string>Quiver runs swe and reads session folders, some of which live in your Desktop folder.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Quiver runs swe and reads session folders, some of which live in your Documents folder.</string>
</dict>
</plist>
PLIST

# The widget extension: SwiftPM's executable in an .appex, no Xcode project
# needed. Its Mach-O entry point is _NSExtensionMain (set in Package.swift's
# linkerSettings), which hands control to the @main WidgetBundle the way an
# Xcode-built extension would.
appex="$bundle/Contents/PlugIns/QuiverWidgets.appex"
mkdir -p "$appex/Contents/MacOS" "$appex/Contents/Resources"
cp "$bindir/QuiverWidgets" "$appex/Contents/MacOS/QuiverWidgets"
# An extension's Bundle.main is the .appex, so CoreResources looks in the
# appex's own Contents/Resources for the harness logos; without this copy
# the widgets would have none. The sandbox always lets an extension read
# its own bundle, so the entitlements need nothing for it.
cp -R "$bindir/Quiver_QuiverCore.bundle" "$appex/Contents/Resources/"
check_core_resources "$appex"
cat > "$appex/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>QuiverWidgets</string>
  <key>CFBundleDisplayName</key><string>Quiver</string>
  <key>CFBundleIdentifier</key><string>com.cwenlong.quiver.widgets</string>
  <key>CFBundleExecutable</key><string>QuiverWidgets</string>
  <key>CFBundlePackageType</key><string>XPC!</string>
  <key>CFBundleShortVersionString</key><string>${version}</string>
  <key>CFBundleVersion</key><string>${build}</string>
  <key>QuiverVersion</key><string>${describe_xml}</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSExtension</key>
  <dict>
    <key>NSExtensionPointIdentifier</key><string>com.apple.widgetkit-extension</string>
  </dict>
</dict>
</plist>
PLIST

identity="${QUIVER_SIGN_IDENTITY:-}"
if [ -z "$identity" ]; then
  # Optional: a failed lookup falls back to ad-hoc rather than stopping the
  # build. A failure signing with an identity that was found, or one asked
  # for in QUIVER_SIGN_IDENTITY, still stops it.
  if identities="$(security find-identity -v -p codesigning 2>&1)"; then
    identity="$(printf '%s\n' "$identities" \
      | sed -n 's/^ *[0-9][0-9]*) \([0-9A-F]\{40\}\) "Apple Development: .*/\1/p' | head -n 1)"
  else
    echo "build-app: couldn't list signing identities: $identities" >&2
  fi
fi
if [ -z "$identity" ] || [ "$identity" = "-" ]; then
  echo "build-app: ad-hoc signing; privacy grants won't survive a rebuild" >&2
  identity="-"
fi
# Inside out: the extension with its sandbox, then the app around it.
codesign --force --sign "$identity" --entitlements "$here/script/QuiverWidgets.entitlements" "$appex" >/dev/null
codesign --force --sign "$identity" "$bundle" >/dev/null
echo "$bundle"
