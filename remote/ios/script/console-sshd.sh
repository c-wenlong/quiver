#!/bin/bash
# A throwaway sshd for the console's integration tests: its own host key,
# config and authorized_keys in a temp folder, on a high port, as you.
# Nothing under ~/.ssh or /etc/ssh is read or changed.
#
#   remote/ios/script/console-sshd.sh [port]    # default 2222, Ctrl-C stops it
#
# It prints the env to pass to xcodebuild, e.g.
#   TEST_RUNNER_QUIVER_SSH_PORT=2222 TEST_RUNNER_QUIVER_SSH_USER=you \
#   TEST_RUNNER_QUIVER_SSH_HOST_KEY='ssh-ed25519 AAAA…' \
#   TEST_RUNNER_QUIVER_SSH_KEY=<hex> xcodebuild … test
# The key is new every run and the env is the only copy, so don't paste
# it anywhere while the server runs.
set -euo pipefail
port=${1:-2222}
dir=$(mktemp -d)
trap 'rm -rf "$dir"' EXIT

ssh-keygen -q -t ed25519 -N "" -C "" -f "$dir/host_key"

# A fresh P-256 key every run. Its private half goes to the tests only
# through the printed env, so no one can recreate it to log in here.
key_hex=$(openssl rand -hex 32)
cat > "$dir/pubkey.swift" <<'SWIFT'
import CryptoKit
import Foundation
func field(_ d: Data) -> Data { var n = UInt32(d.count).bigEndian; return Data(bytes: &n, count: 4) + d }
let hex = CommandLine.arguments[1]
let raw = Data(stride(from: 0, to: hex.count, by: 2).map {
    UInt8(hex[hex.index(hex.startIndex, offsetBy: $0)..<hex.index(hex.startIndex, offsetBy: $0 + 2)], radix: 16)!
})
let key = try! P256.Signing.PrivateKey(rawRepresentation: raw)
let blob = field(Data("ecdsa-sha2-nistp256".utf8)) + field(Data("nistp256".utf8)) + field(key.publicKey.x963Representation)
print("ecdsa-sha2-nistp256 " + blob.base64EncodedString())
SWIFT
swift "$dir/pubkey.swift" "$key_hex" > "$dir/authorized_keys"
chmod 600 "$dir/authorized_keys"

cat > "$dir/sshd_config" <<EOF
Port $port
ListenAddress 127.0.0.1
HostKey $dir/host_key
AuthorizedKeysFile $dir/authorized_keys
PidFile $dir/sshd.pid
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes no
EOF

host_key=$(cut -d' ' -f1-2 "$dir/host_key.pub")
echo "TEST_RUNNER_QUIVER_SSH_PORT=$port TEST_RUNNER_QUIVER_SSH_USER=$(id -un) TEST_RUNNER_QUIVER_SSH_HOST_KEY='$host_key' TEST_RUNNER_QUIVER_SSH_KEY=$key_hex"
/usr/sbin/sshd -D -e -f "$dir/sshd_config"
