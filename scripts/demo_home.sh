#!/usr/bin/env bash
#
# demo_home.sh: build a safe, repeatable stage for recording the
# "manage and sync all text primitives" demo (issue #524).
#
#   scripts/demo_home.sh [--root=DIR] [--hub=URL] [--home=PATH]
#
#     --root=DIR   where the stage goes (default: a new `mktemp -d`). DIR may
#                  exist, but none of fork.git, machine-a, machine-b in it may.
#     --hub=URL    the hub to copy (default: the public demo hub,
#                  https://github.com/c-wenlong/swe-demo-hub). A git URL,
#                  a file:// URL or a local path.
#     --home=PATH  use PATH as machine A instead of DIR/machine-a, for the
#                  dedicated macOS demo account (the Mac app ignores $HOME).
#                  PATH must be an existing folder whose ~/.swe holds no hub.
#
# What it builds:
#   DIR/fork.git    a bare clone of the hub. It stands in for the viewer's own
#                   fork, so `swe sync` pushes land here and never on GitHub.
#   DIR/machine-a   a HOME with empty .claude/ and .codex/ and a .gitconfig
#   DIR/machine-b   holding a neutral identity (Demo User <demo@example.com>),
#                   so `swe sync` can commit under a bare environment.
#
# Nothing is copied from your real HOME: no credentials, no settings. With
# --home, existing .claude/ and .codex/ folders and an existing .gitconfig
# are left exactly as they are; only missing ones are created.
#
# It does not run `swe setup`: the demo shows that step live.
#
# Progress goes to stderr. stdout carries only `export` lines, so either
# paste them or run:  eval "$(scripts/demo_home.sh)"
#
#   export DEMO_ROOT=...                       the stage folder
#   export DEMO_FORK=file://.../fork.git       the hub URL to give swe setup
#   export DEMO_A=...                          machine A's HOME
#   export DEMO_B=...                          machine B's HOME
#
# Then, for example:
#   env -i PATH="$PATH" HOME="$DEMO_A" swe setup --hub="$DEMO_FORK"

set -euo pipefail

DEFAULT_HUB="https://github.com/c-wenlong/swe-demo-hub"
DEMO_NAME="Demo User"
DEMO_EMAIL="demo@example.com"

say() { printf '%s\n' "$*" >&2; }
die() { printf 'demo_home: %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '3,/^$/s/^# \{0,1\}//p' "$0" >&2
}

root=""
hub="$DEFAULT_HUB"
home_a=""
for arg in "$@"; do
    case "$arg" in
        --root=*) root="${arg#--root=}"; [ -n "$root" ] || die "--root needs a folder: --root=DIR" ;;
        --hub=*) hub="${arg#--hub=}"; [ -n "$hub" ] || die "--hub needs a git URL: --hub=URL" ;;
        --home=*) home_a="${arg#--home=}"; [ -n "$home_a" ] || die "--home needs a folder: --home=PATH" ;;
        --root | --hub | --home) die "$arg takes its value after =, as in $arg=..." ;;
        -h | --help) usage; exit 0 ;;
        *) die "unknown argument: $arg (see --help)" ;;
    esac
done

# --- Checks. Nothing below writes anything until the hub answers. ---

command -v git >/dev/null 2>&1 || die "git is not on PATH; install git first."
command -v swe >/dev/null 2>&1 || die "swe is not on PATH; install swe first (see README.md)."

# Refuse a path that exists but is not a folder (mkdir -p would fail later).
need_dir_or_absent() {
    if { [ -e "$1" ] || [ -L "$1" ]; } && [ ! -d "$1" ]; then
        die "$1 exists and is not a folder; refusing to touch it."
    fi
}

if [ -n "$home_a" ]; then
    [ -d "$home_a" ] || die "--home=$home_a is not an existing folder."
    home_a="$(cd "$home_a" && pwd -P)"
    # Mirrors check_target in src/swe/init/hub.py: swe setup --hub clones
    # into an absent ~/.swe or a folder with no hub in it yet.
    swe_dir="$home_a/.swe"
    if [ -L "$swe_dir" ]; then
        die "$swe_dir is a symlink; swe setup will not clone through it. Nothing changed."
    elif [ -e "$swe_dir" ] && [ ! -d "$swe_dir" ]; then
        die "$swe_dir is a file, not a folder. Nothing changed."
    elif [ -e "$swe_dir/AGENTS.md" ] || [ -L "$swe_dir/AGENTS.md" ] || [ -e "$swe_dir/.git" ]; then
        die "$swe_dir already has a hub, so this is not a fresh HOME. Use another --home, or move ~/.swe aside first. Nothing changed."
    fi
    need_dir_or_absent "$home_a/.claude"
    need_dir_or_absent "$home_a/.codex"
fi

if [ -n "$root" ]; then
    need_dir_or_absent "$root"
    for name in fork.git machine-a machine-b; do
        [ "$name" = machine-a ] && [ -n "$home_a" ] && continue
        if [ -e "$root/$name" ] || [ -L "$root/$name" ]; then
            die "$root/$name already exists; remove it or pick another --root. Nothing changed."
        fi
    done
fi

say "Checking the hub at $hub ..."
if ! refs="$(GIT_TERMINAL_PROMPT=0 git ls-remote "$hub" 2>&1)"; then
    die "can't reach the hub at $hub: $(printf '%s' "$refs" | tail -n 1)"
fi
[ -n "$refs" ] || die "the hub at $hub has no commits yet."

# --- Build. On a failure from here on, remove what this run created. ---

created_root=""
created=""   # names under $root, removed on failure when $root was not new
cleanup() {
    status=$?
    if [ "$status" -ne 0 ]; then
        if [ -n "$created_root" ]; then
            rm -rf "$created_root"
        else
            for name in $created; do rm -rf "${root:?}/$name"; done
        fi
        say "demo_home: failed; removed what this run created."
    fi
    exit "$status"
}
trap cleanup EXIT

if [ -z "$root" ]; then
    root="$(mktemp -d "${TMPDIR:-/tmp}/swe-demo.XXXXXX")"
    created_root="$root"
elif [ ! -d "$root" ]; then
    mkdir -p "$root"
    created_root="$root"
fi
root="$(cd "$root" && pwd -P)"
[ -z "$created_root" ] || created_root="$root"

say "Copying the hub into $root/fork.git (your stand-in fork) ..."
created="fork.git"
GIT_TERMINAL_PROMPT=0 git clone -q --bare "$hub" "$root/fork.git" >&2 \
    || die "git clone --bare $hub failed."
# The stand-in fork keeps no link back to where it came from.
git -C "$root/fork.git" remote remove origin >/dev/null 2>&1 || true

# Give a HOME empty harness folders and the demo identity. An existing
# .gitconfig (only possible with --home) is kept as it is.
seed_machine() {
    mkdir -p "$1/.claude" "$1/.codex"
    if [ -e "$1/.gitconfig" ] || [ -L "$1/.gitconfig" ]; then
        if ! git config --file "$1/.gitconfig" user.email >/dev/null 2>&1; then
            say "  ! $1/.gitconfig has no user.email; swe sync will fail to commit until it has one."
        fi
        say "  kept the existing $1/.gitconfig"
    else
        git config --file "$1/.gitconfig" user.name "$DEMO_NAME"
        git config --file "$1/.gitconfig" user.email "$DEMO_EMAIL"
    fi
}

machine_b="$root/machine-b"
created="$created machine-b"
seed_machine "$machine_b"

if [ -n "$home_a" ]; then
    machine_a="$home_a"
else
    machine_a="$root/machine-a"
    created="$created machine-a"
fi
seed_machine "$machine_a"

trap - EXIT

say ""
say "Demo stage ready (swe setup has not run yet):"
say "  fork       $root/fork.git"
say "  machine A  $machine_a"
say "  machine B  $machine_b"
say "Next:  env -i PATH=\"\$PATH\" HOME=\"\$DEMO_A\" swe setup --hub=\"\$DEMO_FORK\""
say ""

printf 'export DEMO_ROOT=%q\n' "$root"
printf 'export DEMO_FORK=%q\n' "file://$root/fork.git"
printf 'export DEMO_A=%q\n' "$machine_a"
printf 'export DEMO_B=%q\n' "$machine_b"
