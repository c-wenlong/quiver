#!/usr/bin/env bash
#
# reaper.sh — reap stale agent-work artifacts in this repo (issue #747).
#
# Parallel agent sessions accumulate git worktrees under .claude/worktrees/,
# local branches whose remote PRs merged long ago, and multi-hundred-MB
# build dirs (app/mac/.build alone was ~1 GB at the audit). This script removes
# the stale ones and logs every decision. Safe by default: nothing is
# deleted unless --apply (or --yes) is passed, so the same invocation can be
# dry-run by hand and applied from cron/launchd.
#
#   scripts/reaper.sh [--dry-run | --apply | --yes] [--days=N] [REPO_PATH]
#
#     --dry-run   print what would be removed (default)
#     --apply     actually remove (--yes is an alias)
#     --days=N    staleness threshold for build dirs, in days (default: 14)
#     REPO_PATH   any path inside the repo (default: the script's own
#                 directory); resolved to the primary checkout even when it
#                 points at a linked worktree
#
# What it removes:
#   a. Worktrees under .claude/worktrees/ whose branch is merged into
#      origin/main, or whose GitHub PRs are all merged/closed (via
#      `gh pr list` when gh is available). Never a worktree with uncommitted
#      changes, a locked worktree, an open PR, unpushed commits on a
#      closed-PR branch, or the checkout this script runs from. The worktree's
#      local branch is deleted too when it is merged.
#   b. Local branches whose upstream is gone — configured as origin/<name>
#      with <name> no longer on the remote (checked live via ls-remote, so
#      a dry run mutates nothing) — and which are merged into origin/main,
#      or whose tip is exactly a merged PR's head, which is the squash-merge
#      case: the commits were pushed to the PR and stay fetchable through
#      GitHub's pull refs.
#   c. app/mac/.build, app/.build (the old layout), build/ and .build/
#      dirs with no file modified in the last N days — in the main
#      checkout and in surviving worktrees (build dirs inside removed
#      worktrees go away with them). Only git-ignored dirs
#      are pruned; a fresh checkout needs no pruning, hence the age check.
#
# A dry run never writes: no deletions and no `git fetch` either — remote
# state is read through `git ls-remote` instead. Only --apply fetches.
#
# Runs unattended, e.g. from cron:
#   15 4 * * *  /path/to/repo/scripts/reaper.sh --yes >> ~/.swe/logs/reaper.log 2>&1

set -euo pipefail

DAYS=14
APPLY=0
REPO_ARG=""

usage() { sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//'; }

die() { printf 'reaper: ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '%s [reaper] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
warn() { printf '%s [reaper] WARN: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

while [[ $# -gt 0 ]]; do
	case "$1" in
		--dry-run) APPLY=0 ;;
		--apply | --yes) APPLY=1 ;;
		--days=*) DAYS="${1#*=}" ;;
		--days) die "write it as --days=N" ;;
		-h | --help) usage; exit 0 ;;
		-*) die "unknown flag: $1" ;;
		*)
			[[ -z "$REPO_ARG" ]] || die "unexpected extra argument: $1"
			REPO_ARG="$1"
			;;
	esac
	shift
done

[[ "$DAYS" =~ ^[0-9]+$ ]] || die "--days must be a non-negative integer, got: $DAYS"

# --- resolve the primary checkout -------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ORIG_CWD="$(pwd -P)"
CANDIDATE="${REPO_ARG:-$SCRIPT_DIR}"
[[ -d "$CANDIDATE" ]] || die "no such directory: $CANDIDATE"
git -C "$CANDIDATE" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repository: $CANDIDATE"

# --git-common-dir points at the real .git dir even from a linked worktree,
# so this lands on the primary checkout no matter where the script lives.
cd "$CANDIDATE"
GIT_COMMON="$(cd "$(git rev-parse --git-common-dir)" && pwd -P)"
MAIN_ROOT="$(dirname "$GIT_COMMON")"
cd "$MAIN_ROOT"

MODE="dry-run"; [[ $APPLY -eq 1 ]] && MODE="apply"
log "repo: $MAIN_ROOT (mode: $MODE, days: $DAYS)"

# A mkdir lock: atomic on every filesystem, no flock dependency (macOS has
# none). The winner writes its pid inside the dir, so a stale lock — dead
# pid, or an ancient one from before pid files — can be broken safely: the
# owner is re-checked right before removal, a lost recreate race is fatal
# for the loser, and the trap only ever releases a lock this run owns.
LOCK="$GIT_COMMON/reaper.lock"
lock_pid() { cat "$LOCK/pid" 2>/dev/null || true; }
try_lock() { mkdir "$LOCK" 2>/dev/null && printf '%s\n' "$$" >"$LOCK/pid"; }
if ! try_lock; then
	owner="$(lock_pid)"
	if [[ -n "$owner" ]] && kill -0 "$owner" 2>/dev/null; then
		die "another reaper is already running (pid $owner, lock: $LOCK)"
	fi
	stale=0
	[[ -n "$owner" ]] && stale=1 # pid file exists but the process is dead
	[[ -n "$(find "$LOCK" -mmin +360 -print -quit 2>/dev/null)" ]] && stale=1
	if [[ $stale -eq 0 ]]; then
		die "another reaper holds $LOCK and it is still fresh"
	fi
	owner_now="$(lock_pid)"
	live_now=0
	[[ -n "$owner_now" ]] && kill -0 "$owner_now" 2>/dev/null && live_now=1
	if [[ "$owner_now" != "$owner" || $live_now -eq 1 ]]; then
		die "stale lock became live while checking — another reaper won it"
	fi
	warn "breaking stale lock: $LOCK (pid ${owner:-none} is dead or lock is older than 6h)"
	rm -f "$LOCK/pid" 2>/dev/null || true
	rmdir "$LOCK" 2>/dev/null || true
	try_lock || die "another reaper won the stale-lock race"
fi
trap 'if [[ "$(lock_pid)" == "$$" ]]; then rm -f "$LOCK/pid" 2>/dev/null; rmdir "$LOCK" 2>/dev/null; fi' EXIT

# --- reference points --------------------------------------------------------

MAIN_REF=""
for ref in origin/main origin/master main master; do
	if git rev-parse --verify --quiet "$ref" >/dev/null; then MAIN_REF="$ref"; break; fi
done
[[ -n "$MAIN_REF" ]] || warn "no origin/main or local main ref; merged-ness checks will all fail safe (nothing removed)"

REMOTE_HEADS="" HAVE_REMOTE=0
if git remote | grep -qx origin; then
	# --apply may write; a dry run stays strictly read-only: no fetch, gone
	# upstreams detected from a live ls-remote instead of pruned refs.
	if [[ $APPLY -eq 1 ]]; then
		git fetch --prune origin >/dev/null 2>&1 || warn "git fetch --prune origin failed; deciding on possibly stale refs (still safe: misses only become skips)"
	else
		log "dry-run: skipping 'git fetch --prune'; using a read-only ls-remote for remote state"
	fi
	REMOTE_HEADS="$(GIT_TERMINAL_PROMPT=0 git ls-remote --heads origin 2>/dev/null | awk '{sub("refs/heads/", "", $2); print $2}')"
	if [[ -z "$REMOTE_HEADS" ]]; then
		warn "could not query origin (offline or auth); gone-upstream checks disabled, branches kept"
	else
		HAVE_REMOTE=1
	fi
else
	log "no origin remote; gone-upstream and PR checks limited"
fi

HAVE_GH=0
command -v gh >/dev/null 2>&1 && HAVE_GH=1
[[ $HAVE_GH -eq 1 ]] || log "gh not found; PR checks skipped (merged-branch check still applies)"

# $1 = branch -> "OPEN|CLOSED|MERGED <n> <headRefOid>" lines, or rc!=0 when gh failed.
pr_states() {
	[[ $HAVE_GH -eq 1 ]] || return 1
	local out
	out="$(gh pr list --state all --head "$1" --json number,state,headRefOid --jq '.[] | "\(.state) \(.number) \(.headRefOid)"' 2>/dev/null)" || return 1
	[[ -n "$out" ]] && printf '%s\n' "$out"
	return 0
}

# $1 = ref -> rc 0 when merged into MAIN_REF.
is_merged() {
	[[ -n "$MAIN_REF" ]] || return 1
	git merge-base --is-ancestor "$1" "$MAIN_REF" 2>/dev/null
}

# $1 = ref -> count of commits reachable from no remote-tracking ref.
unpushed_count() {
	git rev-list --count "$1" --not --remotes 2>/dev/null || echo "9999"
}

WT_REMOVED=0 WT_SKIPPED=0 BR_DELETED=0 BR_SKIPPED=0 BUILDS_PRUNED=0 BYTES=0
# branch<TAB>path lines for every live worktree — a packed list, not an
# assoc array, because /bin/bash on macOS is still 3.2 and cron PATHs
# resolve `env bash` to it.
CHECKED_OUT=""
checked_out_path() { awk -F'\t' -v b="$1" '$1 == b { print $2; exit }' <<<"$CHECKED_OUT"; }

kb() { du -sk "$1" 2>/dev/null | awk '{print $1}'; }

MAIN_HEAD_BRANCH="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"

# --- pass 1: worktrees under .claude/worktrees/ ------------------------------

log "--- worktrees under .claude/worktrees/ ---"

# `git worktree list --porcelain` emits records separated by blank lines:
#   worktree <abs path> / HEAD <sha> / branch refs/heads/<name>|detached /
#   bare|locked|prunable ...
while IFS=$'\t' read -r wt wt_head wt_branch wt_detached wt_locked; do
	[[ -n "$wt" ]] || continue
	[[ "$wt_head" == "-" ]] && wt_head=""
	[[ "$wt_branch" == "-" ]] && wt_branch=""
	wt_real="$(cd "$wt" 2>/dev/null && pwd -P || true)"
	if [[ -z "$wt_real" ]]; then
		log "skip $wt — directory missing (stale registration; 'git worktree prune' below will drop it)"
		WT_SKIPPED=$((WT_SKIPPED + 1))
		continue
	fi
	[[ -n "$wt_branch" ]] && CHECKED_OUT+="$wt_branch"$'\t'"$wt_real"$'\n'
	[[ "$wt_real" == "$MAIN_ROOT"/.claude/worktrees/* ]] || continue

	rel="${wt_real#"$MAIN_ROOT"/}"
	[[ "$wt_real" == "$MAIN_ROOT" ]] && { warn "skip $rel — main checkout"; WT_SKIPPED=$((WT_SKIPPED + 1)); continue; }
	if [[ "$ORIG_CWD" == "$wt_real" || "$ORIG_CWD" == "$wt_real/"* ]]; then
		warn "skip $rel — this script is running from inside it"
		WT_SKIPPED=$((WT_SKIPPED + 1)); continue
	fi
	[[ "$wt_locked" == 1 ]] && { warn "skip $rel — locked (git worktree lock)"; WT_SKIPPED=$((WT_SKIPPED + 1)); continue; }
	if ! status_out="$(git -C "$wt_real" status --porcelain 2>/dev/null)"; then
		warn "skip $rel — 'git status' failed"
		WT_SKIPPED=$((WT_SKIPPED + 1)); continue
	fi
	if [[ -n "$status_out" ]]; then
		warn "skip $rel — uncommitted changes present, never reaping those"
		WT_SKIPPED=$((WT_SKIPPED + 1)); continue
	fi

	# A worktree is reapable when its history is provably safe: merged into
	# MAIN_REF, or every PR for the branch is merged/closed with all commits
	# still reachable from some remote. Anything else is a logged skip.
	eligible="" reason=""
	if [[ "$wt_detached" == 1 ]]; then
		if is_merged "$wt_head"; then
			eligible=1; reason="detached HEAD is merged into $MAIN_REF"
		else
			reason="detached HEAD is not merged into $MAIN_REF"
		fi
	else
		pr_out="" pr_rc=0
		pr_out="$(pr_states "$wt_branch")" || pr_rc=$?
		if [[ $pr_rc -eq 0 && -n "$pr_out" ]] && grep -qx 'OPEN .*' <<<"$pr_out"; then
			reason="branch $wt_branch has an open PR (#$(awk '$1=="OPEN"{print $2; exit}' <<<"$pr_out"))"
		elif is_merged "$wt_branch"; then
			eligible=1; reason="branch $wt_branch is merged into $MAIN_REF"
		elif [[ $pr_rc -ne 0 ]]; then
			reason="branch $wt_branch is not merged into $MAIN_REF and PR state is unknown (gh failed)"
		elif [[ -z "$pr_out" ]]; then
			reason="branch $wt_branch is not merged into $MAIN_REF and has no PR"
		elif grep -qx 'MERGED .*' <<<"$pr_out"; then
			# A MERGED PR's commits live on main under a different sha when the
			# repo squash-merges, so ancestor checks miss it. The worktree goes;
			# the branch below is kept unless it is a true ancestor, so any
			# commits past the PR head survive on the local branch ref.
			eligible=1
			reason="PR #$(awk '$1=="MERGED"{print $2; exit}' <<<"$pr_out") for $wt_branch is merged (work kept on the local branch)"
		elif [[ "$(unpushed_count "$wt_branch")" == 0 ]]; then
			eligible=1
			reason="all PRs for $wt_branch are closed ($(awk '{printf "#%s ", $2}' <<<"$pr_out")) and every commit is on a remote"
		else
			reason="all PRs for $wt_branch are closed unmerged and the branch has commits not on any remote"
		fi
	fi

	if [[ -z "$eligible" ]]; then
		log "keep $rel — $reason"
		WT_SKIPPED=$((WT_SKIPPED + 1)); continue
	fi

	size="$(kb "$wt_real")"; size="${size:-0}"
	if [[ $APPLY -eq 0 ]]; then
		log "would remove worktree $rel (~$((size / 1024))MB) — $reason"
	else
		if git worktree remove "$wt_real" >/dev/null 2>&1; then
			log "removed worktree $rel (~$((size / 1024))MB) — $reason"
		else
			warn "could not remove $rel — 'git worktree remove' refused despite a clean status"
			WT_SKIPPED=$((WT_SKIPPED + 1)); continue
		fi
	fi
	WT_REMOVED=$((WT_REMOVED + 1)); BYTES=$((BYTES + size))

	# Delete the local branch too, but only when it is merged — the branch is
	# the last ref pointing at closed-but-unmerged work. The default and the
	# checked-out branch are never deleted: a worktree can legitimately sit
	# on main, but main must survive its removal.
	if [[ "$wt_detached" != 1 && -n "$wt_branch" ]]; then
		if [[ "$wt_branch" == "$MAIN_HEAD_BRANCH" || "$wt_branch" == main || "$wt_branch" == master ]]; then
			log "keep branch $wt_branch — the checked-out or default branch is never deleted"
		elif is_merged "$wt_branch"; then
			if [[ $APPLY -eq 0 ]]; then
				log "would delete branch $wt_branch — merged into $MAIN_REF"
				BR_DELETED=$((BR_DELETED + 1))
			elif git branch -D "$wt_branch" >/dev/null 2>&1; then
				log "deleted branch $wt_branch — merged into $MAIN_REF"
				BR_DELETED=$((BR_DELETED + 1))
			else
				warn "could not delete branch $wt_branch"
			fi
		else
			log "keep branch $wt_branch — not merged into $MAIN_REF"
		fi
	fi
done < <(git worktree list --porcelain | awk '
	/^worktree / { if (p != "") emit()
		p = substr($0, 10); h = "-"; b = "-"; d = 0; l = 0; next }
	/^HEAD /    { h = $2; next }
	/^branch /  { b = $2; sub("refs/heads/", "", b); next }
	/^detached/ { d = 1; next }
	/^locked/   { l = 1; next }
	END         { if (p != "") emit() }
	function emit() { print p "\t" h "\t" b "\t" d "\t" l }')
	# "-" marks empty fields: a bare "" would collapse under IFS=tab read.

# --- pass 2: local branches with gone upstreams -------------------------------

log "--- local branches whose upstream is gone ---"

# "Gone" is computed live: the branch's configured upstream is origin/<name>
# and <name> no longer exists on the remote. That is what `git branch -vv`'s
# "[gone]" marker would say after a fetch --prune, except ls-remote answers
# without mutating anything — so a dry run stays read-only.
while IFS=$'\t' read -r br ups; do
	[[ -n "$br" && "$ups" == origin/* ]] || continue
	remote_name="${ups#origin/}"
	grep -qxF "$remote_name" <<<"$REMOTE_HEADS" && continue   # upstream still exists

	if [[ "$br" == "$MAIN_HEAD_BRANCH" || "$br" == main || "$br" == master ]]; then
		log "keep $br — the checked-out or default branch is never deleted"
		BR_SKIPPED=$((BR_SKIPPED + 1)); continue
	fi
	checked_out="$(checked_out_path "$br")"
	if [[ -n "$checked_out" ]]; then
		log "keep $br — checked out in worktree $checked_out"
		BR_SKIPPED=$((BR_SKIPPED + 1)); continue
	fi
	if ! is_merged "$br"; then
		# Not an ancestor of main — but a squash-merged PR leaves a branch
		# unmerged while its content sits on main. Such a branch is still
		# safe to drop when its tip is exactly the merged PR's head: every
		# commit on it was pushed to the PR and stays fetchable through
		# GitHub's pull refs, and the squashed content is on main already.
		pr_out="" pr_rc=0
		pr_out="$(pr_states "$br")" || pr_rc=$?
		if [[ $pr_rc -eq 0 && -n "$pr_out" ]] && grep -qx 'OPEN .*' <<<"$pr_out"; then
			log "keep $br — has an open PR (#$(awk '$1=="OPEN"{print $2; exit}' <<<"$pr_out"))"
			BR_SKIPPED=$((BR_SKIPPED + 1)); continue
		fi
		tip="$(git rev-parse "$br" 2>/dev/null || true)"
		merged_pr="$(awk -v t="$tip" '$1=="MERGED" && $3==t {print $2; exit}' <<<"$pr_out")"
		if [[ -z "$merged_pr" ]]; then
			if grep -qx 'MERGED .*' <<<"$pr_out"; then
				log "keep $br — PR merged but branch tip has commits beyond the PR head"
			else
				log "keep $br — not merged into $MAIN_REF"
			fi
			BR_SKIPPED=$((BR_SKIPPED + 1)); continue
		fi
		squash_reason="branch tip is merged PR #${merged_pr}'s head; content is on main"
	else
		pr_out="" pr_rc=0
		pr_out="$(pr_states "$br")" || pr_rc=$?
		if [[ $pr_rc -eq 0 && -n "$pr_out" ]] && grep -qx 'OPEN .*' <<<"$pr_out"; then
			log "keep $br — has an open PR (#$(awk '$1=="OPEN"{print $2; exit}' <<<"$pr_out"))"
			BR_SKIPPED=$((BR_SKIPPED + 1)); continue
		fi
		squash_reason="upstream gone and merged into $MAIN_REF"
	fi
	if [[ $APPLY -eq 0 ]]; then
		log "would delete branch $br — $squash_reason"
	elif git branch -D "$br" >/dev/null 2>&1; then
		log "deleted branch $br — $squash_reason"
	else
		warn "could not delete branch $br"
		BR_SKIPPED=$((BR_SKIPPED + 1)); continue
	fi
	BR_DELETED=$((BR_DELETED + 1))
done < <(if [[ $HAVE_REMOTE -eq 1 ]]; then git for-each-ref --format='%(refname:short)%09%(upstream:short)' refs/heads; fi)

[[ $BR_DELETED -eq 0 && $BR_SKIPPED -eq 0 ]] && log "none found"

# --- pass 3: stale build dirs -------------------------------------------------

log "--- build dirs older than ${DAYS}d ---"

# Candidates: the main checkout plus every surviving .claude/worktrees/*
# worktree. Removed worktrees took their build dirs with them; a dir outside
# those roots (e.g. the swe install-cache worktree) is not ours to touch.
roots=("$MAIN_ROOT")
while IFS=$'\t' read -r wt _ _ _ _; do
	[[ -n "$wt" ]] || continue
	wt_real="$(cd "$wt" 2>/dev/null && pwd -P || true)"
	[[ -n "$wt_real" && "$wt_real" == "$MAIN_ROOT"/.claude/worktrees/* ]] && roots+=("$wt_real")
done < <(git worktree list --porcelain | awk '/^worktree /{print substr($0,10)}')

for root in "${roots[@]}"; do
	for rel in "app/mac/.build" "app/.build" "build" ".build"; do
		d="$root/$rel"
		[[ -d "$d" ]] || continue
		label="${root#"$MAIN_ROOT"}"; label="${label#/}"; label="${label:+$label/}$rel"
		# Never delete something git actually tracks: only ignored build
		# output is regenerable. A committed dir named 'build' is not.
		if ! git -C "$root" check-ignore -q "$rel" 2>/dev/null; then
			warn "skip $label — not git-ignored, refusing to prune"
			continue
		fi
		# Stale = nothing inside (the dir itself included) touched in DAYS
		# days; an active build writes continuously and stays fresh.
		if [[ -n "$(find "$d" -mtime "-$DAYS" -print -quit 2>/dev/null)" ]]; then
			log "keep $label — modified within ${DAYS}d"
			continue
		fi
		size="$(kb "$d")"; size="${size:-0}"
		if [[ $APPLY -eq 0 ]]; then
			log "would prune $label (~$((size / 1024))MB) — no file modified in ${DAYS}d"
		elif rm -rf "$d"; then
			log "pruned $label (~$((size / 1024))MB) — no file modified in ${DAYS}d"
		else
			warn "could not prune $label"
			continue
		fi
		BUILDS_PRUNED=$((BUILDS_PRUNED + 1)); BYTES=$((BYTES + size))
	done
done

# --- finish -------------------------------------------------------------------

# Drop registrations for worktree dirs that vanished out from under git.
if [[ $APPLY -eq 0 ]]; then
	git worktree prune -n -v 2>/dev/null | sed 's/^/    would prune: /' || true
else
	git worktree prune -v 2>/dev/null | sed 's/^/    pruned: /' || true
fi

verb="would remove"; [[ $APPLY -eq 1 ]] && verb="removed"
log "summary: $verb $WT_REMOVED worktree(s), skipped $WT_SKIPPED; \
$verb $BR_DELETED branch(es), skipped $BR_SKIPPED; \
$verb $BUILDS_PRUNED build dir(s); ~$((BYTES / 1024))MB ${verb/would remove/reclaimable}"
[[ $APPLY -eq 1 ]] || log "dry-run: re-run with --apply to make these changes"
