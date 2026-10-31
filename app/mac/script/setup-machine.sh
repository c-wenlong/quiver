#!/usr/bin/env bash
# Set up a Mac for Quiver development, end to end.
#
#   app/mac/script/setup-machine.sh [--dry-run] [--only=STEP,...]
#
# Steps, in order (each is safe to run again and says what it did):
#   xcode     full Xcode selected, not Command Line Tools: SwiftUI's
#             macros (SwiftUIMacros) ship with Xcode alone, so a CLT-only
#             build fails minutes in. Prints the `sudo xcode-select -s`
#             fix rather than running sudo.
#   checkout  this checkout on main at origin/main. Diverged branches are
#             renamed aside with a dated name, never deleted; a dirty tree
#             or a merge or rebase in progress stops it with nothing changed.
#   install   app/mac/script/install-app.sh, unless the installed app is
#             already origin/main.
#   hub       `swe remote serve --install`, unless the hub LaunchAgent is
#             already loaded.
#   app       which Quiver is running vs installed: quits this checkout's
#             own app/mac/build dev copy and opens the installed app.
#
# --dry-run prints the plan and changes nothing (no fetch either, so the
# plan is against the origin/main fetched last). --only runs a subset.
#
# Overrides:
#   QUIVER_SETUP_REPO   the checkout to set up (default: the one holding
#                       this script)
#   QUIVER_INSTALL_DIR  where Quiver.app is installed (default ~/Applications)
set -euo pipefail

# Everything runs from main(), so bash has read the whole file before the
# checkout step moves the branch this script was read from.
main() {
  dry=0
  only="xcode,checkout,install,hub,app"
  for arg in "$@"; do
    case "$arg" in
      --dry-run|-n) dry=1 ;;
      --only=*) only="${arg#--only=}" ;;
      -h|--help) sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; return 0 ;;
      *) echo "setup-machine: unknown argument $arg" >&2; return 2 ;;
    esac
  done
  for step in $(echo "$only" | tr ',' ' '); do
    case "$step" in
      xcode|checkout|install|hub|app) ;;
      *) echo "setup-machine: unknown step $step (xcode, checkout, install, hub, app)" >&2
         return 2 ;;
    esac
  done

  repo="${QUIVER_SETUP_REPO:-$(cd "$(dirname "$0")/../../.." && pwd)}"
  dest="${QUIVER_INSTALL_DIR:-$HOME/Applications}/Quiver.app"
  if [ "$(uname)" != Darwin ] && [ "$only" != checkout ]; then
    echo "setup-machine: only the checkout step runs off macOS (--only=checkout)" >&2
    return 1
  fi
  if [ "$dry" = 1 ]; then echo "setup-machine: dry run, nothing will change"; fi

  # Called plainly, never inside an && or || list, so set -e stays on in
  # each step and its first failing command stops the run.
  if wants xcode; then step_xcode; fi
  if wants checkout; then step_checkout; fi
  if wants install; then step_install; fi
  if wants hub; then step_hub; fi
  if wants app; then step_app; fi
  echo "setup-machine: done"
}

wants() { case ",$only," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
say() { echo "  $*"; }
# Runs a command, or under --dry-run only says it would.
act() {
  if [ "$dry" = 1 ]; then say "would run: $*"; else say "run: $*"; "$@"; fi
}

step_xcode() {
  echo "[xcode] full Xcode toolchain"
  local dev xcode="" app
  dev="$(xcode-select -p 2>/dev/null || true)"
  # The first Xcode in /Applications, Xcode.app before betas.
  for app in /Applications/Xcode.app /Applications/Xcode*.app; do
    if [ -d "$app/Contents/Developer" ]; then xcode="$app"; break; fi
  done
  case "$dev" in
    *.app/Contents/Developer)
      if [ ! -e "$dev/Platforms/MacOSX.platform/Developer/usr/lib/swift/host/plugins/libSwiftUIMacros.dylib" ]; then
        say "xcode-select points at $dev, but it has no SwiftUIMacros plugin"
        say "update Xcode (Quiver needs Xcode 26 or newer), then run this again"
        return 1
      fi
      say "ok: $dev"
      return 0
      ;;
  esac
  if [ -n "$xcode" ]; then
    say "xcode-select points at ${dev:-nothing}, not Xcode, so SwiftUI's macros are missing"
    say "fix it with:"
    say "  sudo xcode-select -s $xcode/Contents/Developer"
  else
    say "Xcode is not installed (${dev:-no developer tools} is selected)."
    say "Command Line Tools cannot build Quiver: SwiftUIMacros ships with Xcode only."
    say "install Xcode from the App Store, then:"
    say "  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer"
  fi
  say "then run this script again"
  return 1
}

# A branch name nobody has yet: <name>-stale-<date>, then -2, -3, ...
stale_name() {
  local base name n=2
  base="$1-stale-$(date +%Y-%m-%d)"
  name="$base"
  while git -C "$repo" show-ref --verify --quiet "refs/heads/$name"; do
    name="$base-$n"; n=$((n + 1))
  done
  echo "$name"
}

step_checkout() {
  echo "[checkout] $repo on main at origin/main"
  if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    say "$repo is not a git checkout"; return 1
  fi
  local gitdir common op dirty here elsewhere
  gitdir="$(git -C "$repo" rev-parse --path-format=absolute --git-dir)"
  common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir)"
  if [ "$gitdir" != "$common" ]; then
    say "$repo is a linked worktree; run this from the main checkout"
    return 1
  fi
  for op in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD rebase-merge rebase-apply BISECT_LOG; do
    if [ -e "$gitdir/$op" ]; then
      say "a merge, rebase, cherry-pick, revert or bisect is in progress ($op);"
      say "finish or abort it, then run this again. Nothing was changed."
      return 1
    fi
  done
  # Untracked files count too: switching to main can overwrite one that
  # main tracks.
  dirty="$(git -C "$repo" status --porcelain --untracked-files=normal)"
  if [ -n "$dirty" ]; then
    say "the working tree has uncommitted changes, so nothing was changed:"
    echo "$dirty" | head -20 | sed 's/^/    /'
    say "commit or stash them, then run this again"
    return 1
  fi
  # main checked out in a linked worktree can't be renamed or moved here.
  here="$(git -C "$repo" rev-parse --show-toplevel)"
  elsewhere="$(git -C "$repo" worktree list --porcelain | awk -v here="$here" '
    /^worktree / { wt = substr($0, 10) }
    $0 == "branch refs/heads/main" && wt != here { print wt }')"
  if [ -n "$elsewhere" ]; then
    say "main is checked out in another worktree ($elsewhere); switch that one off main first"
    return 1
  fi

  if [ "$dry" = 1 ]; then
    say "would run: git fetch origin (the plan below uses the origin/main fetched last)"
  else
    say "run: git fetch origin"
    git -C "$repo" fetch --quiet origin
  fi
  local target branch head keep local_main tracked=0
  if ! target="$(git -C "$repo" rev-parse --verify --quiet 'refs/remotes/origin/main^{commit}')"; then
    say "origin/main does not exist in $repo"; return 1
  fi
  branch="$(git -C "$repo" symbolic-ref --quiet --short HEAD || true)"
  head="$(git -C "$repo" rev-parse --verify --quiet 'HEAD^{commit}' || true)"

  # A detached HEAD whose commits no branch holds would survive only in
  # the reflog after the switch: give it a branch.
  if [ -z "$branch" ] && [ -n "$head" ] &&
     ! git -C "$repo" merge-base --is-ancestor "$head" "$target" &&
     [ -z "$(git -C "$repo" for-each-ref --contains "$head" refs/heads)" ]; then
    keep="$(stale_name detached)"
    say "detached HEAD ${head:0:12} has commits no branch holds; keeping them as $keep"
    act git -C "$repo" branch "$keep" "$head"
  fi

  # A non-main branch with no history in common with origin/main is a
  # snapshot from before a rewrite: it can never merge, so mark it stale.
  # One that shares history is ordinary work and keeps its name.
  if [ -n "$branch" ] && [ "$branch" != main ]; then
    if git -C "$repo" merge-base "$branch" "$target" >/dev/null 2>&1; then
      say "leaving $branch as it is (it shares history with origin/main)"
    else
      keep="$(stale_name "$branch")"
      say "$branch shares no history with origin/main; renaming it to $keep"
      act git -C "$repo" branch -m "$branch" "$keep"
      branch="$keep"
    fi
  fi

  local_main="$(git -C "$repo" rev-parse --verify --quiet 'refs/heads/main^{commit}' || true)"
  if [ -z "$local_main" ]; then
    say "no local main; creating it at origin/main"
    act git -C "$repo" branch --track main origin/main
    tracked=1
  elif [ "$local_main" = "$target" ]; then
    say "main is already at origin/main (${target:0:12})"
  elif git -C "$repo" merge-base --is-ancestor "$local_main" "$target"; then
    say "main is behind origin/main; fast-forwarding"
    if [ "$branch" = main ]; then
      act git -C "$repo" merge --ff-only --quiet origin/main
    else
      act git -C "$repo" update-ref refs/heads/main "$target" "$local_main"
    fi
  else
    # Ahead or diverged: its own commits stay on the renamed branch.
    keep="$(stale_name main)"
    say "main has commits origin/main lacks; renaming it to $keep and starting main at origin/main"
    act git -C "$repo" branch -m main "$keep"
    act git -C "$repo" branch --track main origin/main
    tracked=1
    if [ "$branch" = main ]; then branch="$keep"; fi
  fi

  if [ "$branch" != main ]; then
    act git -C "$repo" switch --quiet main
  fi
  if [ "$tracked" = 0 ] &&
     [ "$(git -C "$repo" rev-parse --abbrev-ref 'main@{upstream}' 2>/dev/null || true)" != origin/main ]; then
    act git -C "$repo" branch --quiet --set-upstream-to=origin/main main
  fi
  if [ "$dry" = 0 ]; then say "ok: on main at ${target:0:12}"; fi
}

step_install() {
  echo "[install] Quiver.app at $dest"
  local want have="" plist="$dest/Contents/Info.plist"
  want="$(git -C "$repo" rev-parse --verify --quiet 'refs/remotes/origin/main^{commit}' || true)"
  # PlistBuddy reports a missing file on stdout, so check first.
  if [ -f "$plist" ]; then
    have="$(/usr/libexec/PlistBuddy -c 'Print :QuiverCommit' "$plist" 2>/dev/null || true)"
  fi
  if [ -n "$want" ] && [ "$have" = "$want" ]; then
    say "already installed at origin/main (${want:0:12})"
    return 0
  fi
  say "installed: ${have:-nothing}; origin/main: ${want:-unknown}"
  act "$repo/app/mac/script/install-app.sh"
}

step_hub() {
  echo "[hub] remote hub LaunchAgent"
  local label=com.cwenlong.quiver.remote-hub swe
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    say "already loaded ($label)"
    return 0
  fi
  # The app's bundled swe, which the LaunchAgent can read wherever the
  # checkout is; a PATH swe only when the app is missing.
  swe="$dest/Contents/Resources/swe/bin/swe"
  if [ ! -x "$swe" ] && [ "$dry" = 0 ]; then
    if ! swe="$(command -v swe)"; then
      say "no swe: install Quiver.app first (the install step)"; return 1
    fi
  fi
  act "$swe" remote serve --install
}

step_app() {
  echo "[app] running vs installed Quiver"
  local binary="$dest/Contents/MacOS/Quiver" installed_running=0 quit="" lines pid path _
  # Every running Quiver binary, as "pid path" lines.
  lines="$(ps -axo pid=,comm= | awk '{
    pid = $1; sub(/^[ \t]*[0-9]+[ \t]+/, "")
    if ($0 ~ /\/Quiver\.app\/Contents\/MacOS\/Quiver$/) print pid, $0
  }')"
  if [ -z "$lines" ]; then say "no Quiver is running"; fi
  while read -r pid path; do
    [ -n "$pid" ] || continue
    if [ "$path" = "$binary" ]; then
      say "running: the installed app (pid $pid)"
      installed_running=1
      continue
    fi
    case "$path" in
      "$repo"/app/mac/build/*|"$repo"/app/build/*)
        say "running: this checkout's dev build $path (pid $pid), not the installed app"
        if [ "$dry" = 1 ]; then
          say "would quit it (SIGTERM, so unsaved edits get the Quit alert)"
        else
          say "quitting it"; kill -TERM "$pid" || true; quit="$quit $pid"
        fi
        ;;
      *)
        # Another worktree's or session's build: not ours to quit.
        say "running: $path (pid $pid), left alone"
        ;;
    esac
  done <<EOF
$lines
EOF
  if [ "$installed_running" = 1 ]; then return 0; fi
  # Give a quit dev copy up to 10 s, so `open` doesn't race it.
  if [ -n "$quit" ]; then
    for _ in $(seq 1 50); do
      kill -0 $quit 2>/dev/null || break
      sleep 0.2
    done
  fi
  if [ ! -d "$dest" ] && [ "$dry" = 0 ]; then
    say "nothing installed at $dest"; return 1
  fi
  act open "$dest"
}

main "$@"; exit
