## 2024-05-18 - SSRF/LFI via urllib.request.urlopen in Health Checks
**Vulnerability:** `urllib.request.urlopen()` in `quiver/mcp/cli.py` (`check_server_health`) was used to perform HEAD requests to arbitrary URLs from user configuration (`mcp.json`). `urllib` natively supports `file://` schemes, which allowed reading local files (like `/etc/passwd`) without an actual network request.
**Learning:** Python's `urllib` has a surprising behavior where it evaluates `file://` URLs even when used for supposedly HTTP/HTTPS only operations. The method `method="HEAD"` is ignored by the `file://` handler which executes the operation and reads the file.
**Prevention:** Always explicitly validate that user-provided URLs start with `http://` or `https://` before passing them to URL fetching libraries like `urllib`.
## 2026-07-22 - Fix weak MD5 hash usage
**Vulnerability:** Use of weak MD5 hash without specifying it is not used for security purposes (`usedforsecurity=False`), leading to potential FIPS non-compliance and security linter failures.
**Learning:** `hashlib.md5` was used for non-cryptographic purposes (caching directory paths in Kimi sessions) but lacked the `usedforsecurity=False` flag required in Python >= 3.9 for FIPS environments.
**Prevention:** Always add the `usedforsecurity=False` keyword argument when using `hashlib.md5` (or similar algorithms) for non-cryptographic purposes (e.g., cache keys, hashing object identities) to comply with FIPS and pass security linters like Bandit.
## 2026-08-09 - Ensure urlopen Mocks Provide geturl and url Attributes
**Vulnerability:** Not a direct vulnerability, but a critical learning on testing security fixes.
**Learning:** When adding explicit `req.full_url` validation checks to methods that wrap `urllib.request.urlopen`, unit tests that previously relied on a generic `MagicMock` for the response may suddenly fail or return `None` (failing silently). This happens because `urllib` internals or the new validation logic might rely on the response having the `geturl()` method or `url` property available.
**Prevention:** When mocking `urllib.request.urlopen` responses, always ensure the mock includes `.geturl.return_value = "<URL>"` and `.url = "<URL>"` alongside `.read()`.
