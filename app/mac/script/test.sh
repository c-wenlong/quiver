#!/usr/bin/env bash
# Run the Swift test suite with a watchdog.
#
#   app/mac/script/test.sh [extra swift test arguments]
#
# The suite runs in well under a minute once built, but on a 3-core CI
# runner it has frozen outright: no output for hours until GitHub's job
# limit killed it, with nothing in the log saying which test held the
# process. This builds first, so the build's own time does not count, then
# gives the run QUIVER_TEST_LIMIT seconds (default 600). A run past that
# prints the stack of every thread in the test process, so the log names
# what blocked, then is killed and the script exits 124.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd)"
cd "$here"
limit="${QUIVER_TEST_LIMIT:-600}"

swift build --build-tests

swift test --skip-build "$@" &
runner=$!
waited=0
while kill -0 "$runner" 2>/dev/null; do
  if [ "$waited" -ge "$limit" ]; then
    echo "::error::swift test was still running after ${limit}s; thread stacks follow" >&2
    stacks="$(mktemp -d)"
    # swift test hands the bundle to a helper process; sample this run's
    # descendants only (another checkout may be testing on the same Mac),
    # then stop them.
    tree="$runner" frontier="$runner"
    while [ -n "$frontier" ]; do
      next=""
      for parent in $frontier; do
        next="$next $(pgrep -P "$parent" || true)"
      done
      frontier="$(echo $next)"
      tree="$tree $frontier"
    done
    for pid in $tree; do
      [ "$pid" = "$runner" ] && continue
      echo "::group::Thread stacks of pid $pid" >&2
      # A CI runner may refuse task_for_pid to its own user; it has
      # passwordless sudo, so that is the fallback. -n never prompts.
      if sample "$pid" 3 -file "$stacks/$pid.txt" >/dev/null 2>&1 ||
         sudo -n sample "$pid" 3 -file "$stacks/$pid.txt" >/dev/null 2>&1; then
        cat "$stacks/$pid.txt" >&2
      else
        echo "sample could not read pid $pid" >&2
      fi
      echo "::endgroup::" >&2
      kill -KILL "$pid" 2>/dev/null || true
    done
    rm -rf "$stacks"
    kill "$runner" 2>/dev/null || true
    wait "$runner" 2>/dev/null || true
    exit 124
  fi
  sleep 5
  waited=$((waited + 5))
done
wait "$runner"
