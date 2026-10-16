#!/usr/bin/env bash
# Build origin/main and install it as ~/Applications/Quiver.app.
#
#   app/script/install-app.sh [debug|release]   # default: release
#
# Several agent sessions work on this repo at once, each on its own
# branch, so the installed app is never built from the shared checkout:
# whatever branch it happens to be on is not what shipped. This builds
# origin/main in a worktree of its own under ~/.quiver/cache/install,
# kept between runs so each build is incremental, then swaps the bundle
# in and relaunches it. Sessions check their own changes with a separate
# `open -n app/build/Quiver.app` and never write to the installed copy.
#
# A failed fetch or build stops before anything is touched: the old app
# keeps running. One install runs at a time.
#
# Overrides:
#   QUIVER_INSTALL_REF   what to build (default origin/main)
#   QUIVER_INSTALL_DIR   where Quiver.app goes (default ~/Applications)
#   QUIVER_INSTALL_CACHE the worktree, lock and build output (default
#                        ~/.quiver/cache/install)
set -euo pipefail

config="${1:-release}"
case "$config" in
  debug|release) ;;
  *) echo "install-app: config must be debug or release" >&2; exit 1 ;;
esac
repo="$(cd "$(dirname "$0")/../.." && pwd)"
ref="${QUIVER_INSTALL_REF:-origin/main}"
dest="${QUIVER_INSTALL_DIR:-$HOME/Applications}/Quiver.app"
cache="${QUIVER_INSTALL_CACHE:-$HOME/.quiver/cache/install}"
src="$cache/src"
out="$cache/out"
lock="$cache/lock"

mkdir -p "$cache" "$(dirname "$dest")"
if ! mkdir "$lock" 2>/dev/null; then
  echo "install-app: another install is running (remove $lock if it is not)" >&2
  exit 1
fi
staged="$dest.installing"
trap 'rm -rf "$lock" "$staged"' EXIT

git -C "$repo" fetch --quiet origin
# The worktree is ours alone, so it is moved to the ref by force: nothing
# in it is anyone's work.
git -C "$repo" worktree prune
if [ -e "$src/.git" ]; then
  git -C "$src" checkout --quiet --detach --force "$ref"
  git -C "$src" clean -fdq
else
  rm -rf "$src"
  git -C "$repo" worktree add --quiet --detach "$src" "$ref"
fi
commit="$(git -C "$src" rev-parse --short HEAD)"
echo "install-app: building $ref ($commit), $config"

# Stamp the user's checkout as the source, as a build from it would be:
# Settings > Updates compares against it and rebuilds from it.
rm -rf "$out"
QUIVER_BUILD_DIR="$out" QUIVER_SOURCE_PATH="$repo" \
  "$src/app/script/build-app.sh" "$config" >/dev/null

# Stage next to the destination first, so the swap below is two renames
# on one volume and the running app is down only for the restart.
rm -rf "$staged"
ditto "$out/Quiver.app" "$staged"

binary="$dest/Contents/MacOS/Quiver"
running="$(pgrep -f "^$binary" || true)"
if [ -n "$running" ]; then
  # Only the installed copy: dev builds share the bundle id, so quitting
  # by app name could close another session's build instead.
  kill -TERM $running
  for _ in $(seq 1 50); do
    pgrep -f "^$binary" >/dev/null || break
    sleep 0.2
  done
  if pgrep -f "^$binary" >/dev/null; then
    echo "install-app: Quiver did not quit; nothing was replaced" >&2
    exit 1
  fi
fi

if [ -e "$dest" ]; then
  rm -rf "$dest.old"
  mv "$dest" "$dest.old"
fi
if ! mv "$staged" "$dest"; then
  [ -e "$dest.old" ] && mv "$dest.old" "$dest"
  echo "install-app: could not move the new app into place; the old one is back" >&2
  exit 1
fi
rm -rf "$dest.old"
open "$dest"
echo "install-app: installed $commit at $dest"
