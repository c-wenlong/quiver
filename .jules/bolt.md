## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.

## 2024-10-25 - Caching stat syscalls inside glob loops
**Learning:** `glob.glob` under the hood uses `os.scandir` in modern Python, but simply yielding paths through `glob` forces users to call `os.path.getmtime` again to get file dates, throwing away the cached `.stat()` metadata from `scandir`. This double-stat is especially visible in `parse_antigravity`.
**Action:** When searching for files where timestamps are needed, don't use `glob.glob` and call `getmtime()`. Instead, write a manual `os.scandir` loop to filter files (e.g. `entry.name.endswith()`) and extract `entry.stat().st_mtime * 1000` to completely skip the redundant stat syscalls.
