## 2026-07-20 - [Weak MD5 in parsers]
**Vulnerability:** MD5 used without usedforsecurity=False
**Learning:** hashlib.md5 needs usedforsecurity=False to work on FIPS-enabled systems when not used for security.
**Prevention:** Add usedforsecurity=False to all non-security uses of MD5/SHA1.
