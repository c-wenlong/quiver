## 2026-07-21 - Weak Hash Used for Non-Security Purposes (B324)
**Vulnerability:** Weak MD5 hash was being used for creating dictionary keys in `src/quiver/sessions/parsers.py`.
**Learning:** Python's hashlib can flag MD5 usage as a security issue even for non-cryptographic purposes (e.g. cache keys or hashing for lookup) on FIPS-compliant systems, which causes security linters like Bandit to flag it as B324.
**Prevention:** In Python >= 3.10, always pass the keyword argument `usedforsecurity=False` when instantiating `hashlib.md5()` (or other weak hashes like SHA1) if it's strictly used for non-cryptographic purposes to explicitly denote the intent.
