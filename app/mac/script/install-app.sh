#!/usr/bin/env bash
# Build origin/main and install it as ~/Applications/SWE.app.
#
#   app/mac/script/install-app.sh [debug|release]   # default: release
#
# Several agent sessions work on this repo at once, each on its own
# branch, so the installed app is never built from the shared checkout:
# whatever branch it happens to be on is not what shipped. This builds
# origin/main in a worktree of its own under ~/.swe/cache/install,
# kept between runs so each build is incremental, then swaps the bundle
# in and relaunches it. Sessions check their own changes with a separate
# `open -n app/mac/build/SWE.app` and never write to the installed copy.
#
# A failed fetch or build stops before anything is touched: the old app
# keeps running. One install runs at a time, and one stopped mid-swap
# puts the old app back.
#
# Overrides:
#   SWE_INSTALL_REF      what to build (default origin/main)
#   SWE_INSTALL_DIR      where SWE.app goes (default ~/Applications)
#   SWE_INSTALL_CACHE the worktree, lock and build output (default
#                        ~/.swe/cache/install)
set -euo pipefail

config="${1:-release}"
case "$config" in
  debug|release) ;;
  *) echo "install-app: config must be debug or release" >&2; exit 1 ;;
esac
# Run under a power assertion, so a build driven over SSH isn't killed
# when the Mac idles to sleep after the session ends. Once only: the
# variable stops the re-exec from looping.
if [ "$(uname)" = Darwin ] && [ -z "${SWE_INSTALL_CAFFEINATED:-${QUIVER_INSTALL_CAFFEINATED:-}}" ] &&
   command -v caffeinate >/dev/null 2>&1; then
  export SWE_INSTALL_CAFFEINATED=1
  exec caffeinate -i "$BASH" "$0" "$@"
fi
repo="$(cd "$(dirname "$0")/../../.." && pwd)"
ref="${SWE_INSTALL_REF:-${QUIVER_INSTALL_REF:-origin/main}}"
dest="${SWE_INSTALL_DIR:-${QUIVER_INSTALL_DIR:-$HOME/Applications}}/SWE.app"
cache="${SWE_INSTALL_CACHE:-${QUIVER_INSTALL_CACHE:-$HOME/.swe/cache/install}}"
src="$cache/src"
out="$cache/out"
staged="$dest.installing"
binary="$dest/Contents/MacOS/SWE"

# Two locks: the cache's guards its worktree and build output, the
# destination's guards the swap, so installs that share either one wait
# for each other whatever else they were given.
cache_lock="$cache/lock"
dest_lock="$dest.lock"
held_cache=0
held_dest=0

# The cache lives under ~/.swe, and creating it first would block the
# ~/.quiver -> ~/.swe move swe runs on first sight: anything at ~/.swe
# wins. Do the move here before mkdir -p touches the new root, mirroring
# swe.paths._resolve_data_dir (a ~/.quiver that is itself a link stays).
if [ ! -e "$HOME/.swe" ] && [ ! -L "$HOME/.swe" ] \
    && [ -d "$HOME/.quiver" ] && [ ! -L "$HOME/.quiver" ]; then
  mv "$HOME/.quiver" "$HOME/.swe" 2>/dev/null \
    && ln -s .swe "$HOME/.quiver" 2>/dev/null
fi
cleanup() {
  if [ "$held_dest" = 1 ]; then
    # Stopped between the two renames: put the old app back.
    if [ ! -e "$dest" ] && [ -e "$dest.old" ]; then mv "$dest.old" "$dest"; fi
    rm -rf "$staged"
    rmdir "$dest_lock" 2>/dev/null || true
  fi
  if [ "$held_cache" = 1 ]; then rmdir "$cache_lock" 2>/dev/null || true; fi
}
trap cleanup EXIT
take_lock() {
  if ! mkdir "$1" 2>/dev/null; then
    echo "install-app: another install is running (remove $1 if it is not)" >&2
    exit 1
  fi
}
mkdir -p "$cache" "$(dirname "$dest")"
take_lock "$cache_lock"; held_cache=1
take_lock "$dest_lock"; held_dest=1

git -C "$repo" fetch --quiet origin
if ! sha="$(git -C "$repo" rev-parse --verify --quiet "$ref^{commit}")"; then
  echo "install-app: $ref is not a commit" >&2
  exit 1
fi
# The worktree is ours alone, so it is moved to the commit by force:
# nothing in it is anyone's work. A worktree another clone made is
# replaced, since it would not see this clone's fetch.
git -C "$repo" worktree prune
common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)"
if [ -e "$src/.git" ] &&
   [ "$(git -C "$src" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" = "$common" ]; then
  git -C "$src" checkout --quiet --detach --force "$sha"
  git -C "$src" clean -fdq
else
  # Prune again once the path is gone: a registration of ours for it
  # survived the first prune while another clone's worktree sat there.
  rm -rf "$src"
  git -C "$repo" worktree prune
  git -C "$repo" worktree add --quiet --detach "$src" "$sha"
fi
commit="$(git -C "$src" rev-parse --short HEAD)"
echo "install-app: building $ref ($commit), $config"

# Stamp the user's checkout as the source, as a build from it would be:
# Settings > Updates compares against it and rebuilds from it.
# A SWE_INSTALL_REF from before the app moved to app/mac has its build
# script at app/script.
builder="$src/app/mac/script/build-app.sh"
[ -f "$builder" ] || builder="$src/app/script/build-app.sh"
rm -rf "$out"
SWE_BUILD_DIR="$out" SWE_SOURCE_PATH="$repo" \
  "$builder" "$config" >/dev/null

# Stage next to the destination first, so the swap below is two renames
# on one volume and the running app is down only for the restart.
rm -rf "$staged"
ditto "$out/SWE.app" "$staged"

# Processes running the installed binary, matched as a literal path.
# Only the installed copy: dev builds share the bundle id, so quitting by
# app name could close another session's build instead.
installed_pids() {
  ps -axo pid=,comm= | B="$binary" awk '{
    pid = $1; sub(/^[ \t]*[0-9]+[ \t]+/, "")
    if ($0 == ENVIRON["B"]) print pid
  }'
}
running="$(installed_pids)"
if [ -n "$running" ]; then
  # The app takes SIGTERM as Quit, so unsaved AGENTS.md or hook edits
  # put up its "Quit anyway?" alert rather than being dropped.
  kill -TERM $running
  for _ in $(seq 1 50); do
    [ -z "$(installed_pids)" ] && break
    sleep 0.2
  done
  if [ -n "$(installed_pids)" ]; then
    echo "install-app: SWE did not quit (unsaved edits waiting on its Quit alert?); nothing was replaced" >&2
    exit 1
  fi
fi

if [ -e "$dest" ]; then
  rm -rf "$dest.old"
  mv "$dest" "$dest.old"
fi
mv "$staged" "$dest"
# A remote hub or satellite LaunchAgent (swe remote serve|agent --install)
# runs this bundle's swe. Labels were com.cwenlong.quiver.remote-* while the
# app was Quiver.app: an old plist is moved onto the new label, repointing
# it at this bundle when it ran an app-bundle swe.
for role in hub agent; do
  new_label="com.cwenlong.swe.remote-$role"
  old_plist="$HOME/Library/LaunchAgents/com.cwenlong.quiver.remote-$role.plist"
  plist="$HOME/Library/LaunchAgents/$new_label.plist"
  if [ -f "$old_plist" ]; then
    launchctl bootout "gui/$(id -u)/com.cwenlong.quiver.remote-$role" >/dev/null 2>&1 || true
    if [ -f "$plist" ]; then
      rm -f "$old_plist"
    else
      cp "$old_plist" "$plist"
      /usr/libexec/PlistBuddy -c "Set :Label $new_label" "$plist" 2>/dev/null || true
      i=0
      while arg="$(/usr/libexec/PlistBuddy -c "Print :ProgramArguments:$i" "$plist" 2>/dev/null)"; do
        case "$arg" in
          *.app/Contents/*)
            /usr/libexec/PlistBuddy -c "Set :ProgramArguments:$i $dest${arg#*.app}" "$plist" 2>/dev/null || true ;;
        esac
        i=$((i + 1))
      done
      rm -f "$old_plist"
      launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1 \
        && echo "install-app: moved the remote $role agent to $new_label" || true
    fi
  fi
  [ -f "$plist" ] || continue
  program="$(/usr/libexec/PlistBuddy -c 'Print :ProgramArguments:0' "$plist" 2>/dev/null || true)"
  case "$program" in
    "$dest"/*)
      launchctl kickstart -k "gui/$(id -u)/$new_label" >/dev/null 2>&1 \
        && echo "install-app: restarted the remote $role on the new bundle" || true
      ;;
  esac
done
rm -rf "$dest.old"
# The Quiver.app this replaces: quit it and drop the bundle so the Dock
# and LaunchServices see one SWE.app. A copy that will not quit stays; the
# next install retries.
install_dir="${SWE_INSTALL_DIR:-${QUIVER_INSTALL_DIR:-$HOME/Applications}}"
old_app="$install_dir/Quiver.app"
pids_of() {
  ps -axo pid=,comm= | B="$1" awk '{
    pid = $1; sub(/^[ \t]*[0-9]+[ \t]+/, "")
    if ($0 == ENVIRON["B"]) print pid
  }'
}
if [ -e "$old_app" ] && [ "$old_app" != "$dest" ]; then
  old_binary="$old_app/Contents/MacOS/Quiver"
  old_pids="$(pids_of "$old_binary")"
  if [ -n "$old_pids" ]; then
    kill -TERM $old_pids
    for _ in $(seq 1 50); do
      [ -z "$(pids_of "$old_binary")" ] && break
      sleep 0.2
    done
  fi
  if [ -z "$(pids_of "$old_binary")" ]; then
    rm -rf "$old_app" && echo "install-app: removed $old_app"
  else
    echo "install-app: $old_app is still running; remove it after it quits" >&2
  fi
fi
open "$dest"
echo "install-app: installed $commit at $dest"
