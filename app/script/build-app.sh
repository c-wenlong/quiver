#!/usr/bin/env bash
# Build Quiver.app from the SwiftPM package.
#
#   app/script/build-app.sh [debug|release]   # default: release
#
# Writes app/build/Quiver.app with the widget extension embedded at
# Contents/PlugIns/QuiverWidgets.appex. Open it with
# `open app/build/Quiver.app`.
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
here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"

swift build -c "$config" --product Quiver
swift build -c "$config" --product QuiverWidgets
bindir="$(swift build -c "$config" --show-bin-path)"
bin="$bindir/Quiver"

bundle="$here/build/Quiver.app"
rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"
cp "$bin" "$bundle/Contents/MacOS/Quiver"
# QuiverCore's resources (the pricing snapshot). Inside Contents/Resources,
# since codesign refuses anything at the bundle root; CoreResources looks here.
cp -R "$bindir/Quiver_QuiverCore.bundle" "$bundle/Contents/Resources/"
for resource in pricing.json pricing-supplement.json; do
  if [ -z "$(find "$bundle/Contents/Resources/Quiver_QuiverCore.bundle" -name "$resource" -print -quit)" ]; then
    echo "build-app: $resource missing from the app bundle" >&2
    exit 1
  fi
done

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

version="$(git -C "$here" describe --tags --always 2>/dev/null || echo 0.0.0)"
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
  <key>CFBundleVersion</key><string>${version}</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>NSDesktopFolderUsageDescription</key><string>Quiver runs swe and reads session folders, some of which live in your Desktop folder.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>Quiver runs swe and reads session folders, some of which live in your Documents folder.</string>
</dict>
</plist>
PLIST

# The widget extension: SwiftPM's executable in an .appex. Its @main
# WidgetBundle is the whole entry point, so no Xcode project is needed.
appex="$bundle/Contents/PlugIns/QuiverWidgets.appex"
mkdir -p "$appex/Contents/MacOS"
cp "$bindir/QuiverWidgets" "$appex/Contents/MacOS/QuiverWidgets"
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
  <key>CFBundleVersion</key><string>${version}</string>
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
