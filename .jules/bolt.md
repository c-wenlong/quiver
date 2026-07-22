## 2026-07-22 - File System Traversal Performance Issue
**Learning:** os.listdir() combined with os.path.join and os.path.isdir/os.path.isfile generates many redundant stat syscalls, slowing down the parsing of sessions from deeply nested directories.
**Action:** Switch from os.listdir() to os.scandir() which yields DirEntry objects containing cached metadata. Use entry.is_dir() and entry.is_file() instead of os.path.isdir and os.path.isfile.
