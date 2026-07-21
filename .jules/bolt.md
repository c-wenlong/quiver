## 2024-07-21 - Optimize File Traversal with os.scandir
**Learning:** In heavily I/O-bound operations like directory traversal for parsing sessions, using `os.listdir()` followed by `os.path.isdir()` creates an N+1 problem of `stat` syscalls. The `os.scandir()` method significantly reduces filesystem overhead by returning DirEntry objects with cached metadata from the initial directory scan.
**Action:** Prefer `os.scandir()` when iterating over directory contents that need file-type filtering or metadata access.
