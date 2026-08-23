## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.

---

## 2026-07-31 - Over-inlining helper functions during refactor
**Learning:** Replacing an existing helper function (like `get_mtime`) directly with native inline calls (for example, `entry.stat().st_mtime * 1000`) during an optimization (like switching to `os.scandir`) is a risky assumption. It drops fallback logic, exception handling, and potentially domain-specific behavior hidden inside the helper.
**Action:** When performing file traversal optimizations like `os.scandir`, prefer passing `entry.path` to the existing, proven helper functions unless you have explicitly verified that inlining provides a measurable bottleneck fix and preserves all edge-case functionality exactly.

## 2024-07-28 - Caching os.path.realpath in SessionQuery aggregation loops
**Learning:** Similar to the aggregator, `SessionQuery.apply` filtering sessions by `cwd` runs `os.path.realpath` and `os.path.commonpath` for every session (via `_path_is_within`). In large codebases or with thousands of sessions, many sessions share identical paths, making repeated path normalization an O(N) architectural bottleneck.
**Action:** When repeatedly checking `_path_is_within` in a loop (like `SessionQuery.apply`), introduce a local dictionary cache (e.g., `path_cache`) to memoize the boolean result of `_path_is_within` by session path to avoid redundant filesystem hits while safely preserving the helper function's domain logic.
