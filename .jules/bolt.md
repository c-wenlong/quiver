## 2024-08-26 - Bolt Initialization\n**Learning:** Read instructions and boundaries\n**Action:** Apply constraints

## 2026-08-26 - Optimizing path normalization in tight loops
**Learning:** Filtering thousands of sessions with realpath and expanduser causes massive redundant lstat/stat syscalls, making the application unnecessarily slow.
**Action:** Use `@functools.lru_cache` for path normalizations when filtering large collections of file-system-bound objects.
