#!/usr/bin/env bash
# Build Quiver.app from the SwiftPM package.
#
#   app/script/build-app.sh [debug|release]   # default: release
#
# Writes app/build/Quiver.app with the widget extension embedded at
# Contents/PlugIns/QuiverWidgets.appex, ad-hoc signed unless
# QUIVER_SIGN_IDENTITY names a codesigning identity. Open it with
# `open app/build/Quiver.app`.
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
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>${version}</string>
  <key>CFBundleVersion</key><string>${version}</string>
  <key>LSMinimumSystemVersion</key><string>26.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
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

# Inside out: the extension with its sandbox, then the app around it.
identity="${QUIVER_SIGN_IDENTITY:--}"
codesign --force --sign "$identity" --entitlements "$here/script/QuiverWidgets.entitlements" "$appex" >/dev/null
codesign --force --sign "$identity" "$bundle" >/dev/null
echo "$bundle"
