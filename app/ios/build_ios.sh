#!/bin/bash
# Rebuild SWERemote and install it on a paired iPhone.
#
#   app/ios/build_ios.sh            # first paired iOS device
#   app/ios/build_ios.sh <udid>     # a specific device
#
# The phone needs Developer Mode on and, on a free team, a one-time trust
# of the profile under Settings > General > VPN & Device Management.
set -euo pipefail
cd "$(dirname "$0")"

command -v jq >/dev/null || {
  echo "build_ios: jq is required (brew install jq)" >&2
  exit 1
}

app=build/Build/Products/Debug-iphoneos/SWERemote.app
json=$(mktemp)
trap 'rm -f "$json"' EXIT

# devicectl and xcodebuild speak different id spaces for the same phone:
# .identifier is the CoreDevice UUID (devicectl), .hardwareProperties.udid
# is the hardware UDID (xcodebuild destinations).
if [ $# -ge 1 ]; then
  udid=$1
  device=$1
else
  xcrun devicectl list devices --json-output "$json" >/dev/null
  udid=$(jq -r '[.result.devices[]
    | select(.hardwareProperties.platform == "iOS"
             and .connectionProperties.pairingState == "paired")][0]
    .hardwareProperties.udid // empty' "$json")
  device=$(jq -r '[.result.devices[]
    | select(.hardwareProperties.udid == "'"$udid"'")][0]
    .identifier // empty' "$json")
  if [ -z "$udid" ] || [ -z "$device" ]; then
    echo "build_ios: no paired iPhone found — plug it in and trust this Mac" >&2
    exit 1
  fi
fi
echo "==> device $udid"

echo "==> xcodegen generate"
xcodegen generate

echo "==> xcodebuild"
xcodebuild -project SWERemote.xcodeproj -scheme SWERemote \
  -destination "platform=iOS,id=$udid" \
  -allowProvisioningUpdates -derivedDataPath build -quiet build

echo "==> install"
xcrun devicectl device install app --device "$device" "$app"

echo "==> done — open SWE Remote on the phone"
