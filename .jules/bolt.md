## 2024-05-18 - Avoid redundant realpath syscalls during path filtering
**Learning:** `os.path.realpath` is expensive because it triggers many `lstat` syscalls to traverse symlinks. When performing path containment checks (like `_path_is_within` using `os.path.commonpath`) on thousands of file objects sharing common directories, the redundancy dominates execution time.
**Action:** Use an `lru_cache` (or local dictionary cache) to memoize `os.path.realpath` results in query-time filtering mechanisms where the same base and target paths are queried repeatedly.
