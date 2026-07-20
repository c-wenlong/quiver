## 2024-07-20 - Use os.scandir instead of os.listdir
**Learning:** `os.listdir` combined with multiple `os.path.join` calls and filtering is slow for large directories. Using `os.scandir` allows fetching file attributes (like `is_file()` or `name`) more efficiently without multiple system calls per file.
**Action:** Used `os.scandir` in `jsonl_engine.py`'s `_list_jsonl` function to optimize scanning jsonl files in large sessions directories.
