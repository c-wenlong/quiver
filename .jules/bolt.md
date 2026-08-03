## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.
## 2024-08-03 - Flat File Matching Performance Issue
**Learning:** `glob.glob` followed by iterating over the returned list generates an array of path strings, missing out on caching from filesystem stats.
**Action:** When searching for flat file patterns (like `*.jsonl`), replace `glob.glob` with `os.scandir` combined with `str.endswith()` to reduce syscall overhead and memory usage footprint, as `os.scandir` directly exposes cached filesystem properties (like `.is_file()`).
