## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.

## 2024-08-11 - Utilizing DirEntry cached attributes with os.scandir
**Learning:** When optimizing flat file matching by switching to `os.scandir()` (instead of `os.listdir()` or `glob.glob()`), calling helper functions that perform full stat calls via paths (like `get_mtime(entry.path)`) defeats the purpose of the optimization. In Python 3.5+, `glob.glob` actually uses `os.scandir` internally, so a direct replacement without using `DirEntry`'s cached attributes yields no stat reduction.
**Action:** Extract cached attributes like `entry.stat().st_mtime` directly instead of passing `entry.path` to helpers that perform redundant stats. Preserve the helper's business/fallback logic by wrapping the `entry.stat()` call in a `try...except Exception` block.
