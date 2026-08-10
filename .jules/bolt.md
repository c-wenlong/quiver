## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.

## 2026-07-23 - Utilizing DirEntry.stat() directly from os.scandir
**Learning:** In Python 3.5+, `glob.glob` uses `os.scandir` internally, so simply replacing it with `os.scandir` without utilizing `DirEntry`'s cached attributes yields no stat reduction. Passing `entry.path` to helper functions like `get_mtime` triggers redundant stat syscalls, defeating the purpose of the optimization.
**Action:** To actually optimize flat file matching and file iterations, use `os.scandir` and extract cached attributes directly via `entry.stat().st_mtime` (wrapped in a `try...except` block for safety) instead of passing the path to external stat helpers.
