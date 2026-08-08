## 2024-07-18 - Caching os.path.realpath in aggregation loops
**Learning:** In the `quiver` CLI, gathering sessions from all tools heavily duplicates the `path` variable (e.g. all sessions for a single project share the same path). Applying `os.path.realpath` to each session object directly results in an O(N) filesystem hit which is an architectural bottleneck when filtering thousands of sessions by `cwd`.
**Action:** When iterating over a large number of items where paths are highly redundant, introduce a local dictionary cache to memoize path normalizations (like `os.path.realpath`) and hoist invariant string manipulations out of the loop.

---

## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.

## 2024-07-25 - Avoid flat glob.glob for performance
**Learning:** `glob.glob("*.extension")` inside nested loops creates significant I/O overhead by fetching all filenames, sorting them, and keeping them in memory, which is inefficient when we only need to read the `mtime` and content of matching files.
**Action:** When performing flat file searches, especially inside nested loops where a directory only contains flat files, use `os.scandir` combined with `entry.name.endswith(".extension")` and `entry.is_file()` instead of `glob.glob`. This approach is an iterator and avoids loading the entire directory contents into memory, significantly reducing the overhead. Ensure `os.scandir` is always safely wrapped in a `try...except Exception:` block to catch `FileNotFoundError` or `PermissionError`.
