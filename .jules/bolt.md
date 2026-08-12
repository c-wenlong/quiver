## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.

## $(date +%Y-%m-%d) - Avoiding redundant stats when using os.scandir
**Learning:** `os.scandir` yields `DirEntry` objects which inherently cache `stat` metadata (or minimize syscalls). Extracting paths from these objects (`entry.path`) to pass to custom helpers like `get_mtime(path)` defeats the purpose, causing redundant `stat` syscalls.
**Action:** When using `os.scandir`, directly access the cached metadata via `entry.stat()` (e.g. `entry.stat().st_mtime`) within a `try-except` block (falling back to a default on `Exception` to avoid crashing on permission errors or race conditions) instead of falling back to string path-based stat helpers.
