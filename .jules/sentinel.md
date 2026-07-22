## 2026-07-22 - Fix weak MD5 hash usage
**Vulnerability:** Use of weak MD5 hash without specifying it is not used for security purposes (`usedforsecurity=False`), leading to potential FIPS non-compliance and security linter failures.
**Learning:** `hashlib.md5` was used for non-cryptographic purposes (caching directory paths in Kimi sessions) but lacked the `usedforsecurity=False` flag required in Python >= 3.9 for FIPS environments.
**Prevention:** Always add the `usedforsecurity=False` keyword argument when using `hashlib.md5` (or similar algorithms) for non-cryptographic purposes (e.g., cache keys, hashing object identities) to comply with FIPS and pass security linters like Bandit.
