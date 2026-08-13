## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.

## 2024-05-19 - glob.glob vs os.scandir for cached stats
**Learning:** While `glob.glob` uses `os.scandir` internally in Python 3.5+, it drops the cached metadata (like `st_mtime`) and only returns the paths. Thus, using `glob.glob` followed by `get_mtime(path)` results in redundant stat syscalls.
**Action:** When filtering flat files and needing metadata (like modified times), avoid `glob.glob` + stat calls. Instead, use `os.scandir` directly, check the extension (e.g. `entry.name.endswith('.json')`), and extract the cached stat via `entry.stat().st_mtime`.
