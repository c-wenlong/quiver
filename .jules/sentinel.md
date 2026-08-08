## 2024-05-18 - SSRF/LFI via urllib.request.urlopen in Health Checks
**Vulnerability:** `urllib.request.urlopen()` in `quiver/mcp/cli.py` (`check_server_health`) was used to perform HEAD requests to arbitrary URLs from user configuration (`mcp.json`). `urllib` natively supports `file://` schemes, which allowed reading local files (like `/etc/passwd`) without an actual network request.
**Learning:** Python's `urllib` has a surprising behavior where it evaluates `file://` URLs even when used for supposedly HTTP/HTTPS only operations. The method `method="HEAD"` is ignored by the `file://` handler which executes the operation and reads the file.
**Prevention:** Always explicitly validate that user-provided URLs start with `http://` or `https://` before passing them to URL fetching libraries like `urllib`.
## 2026-07-22 - Fix weak MD5 hash usage
**Vulnerability:** Use of weak MD5 hash without specifying it is not used for security purposes (`usedforsecurity=False`), leading to potential FIPS non-compliance and security linter failures.
**Learning:** `hashlib.md5` was used for non-cryptographic purposes (caching directory paths in Kimi sessions) but lacked the `usedforsecurity=False` flag required in Python >= 3.9 for FIPS environments.
**Prevention:** Always add the `usedforsecurity=False` keyword argument when using `hashlib.md5` (or similar algorithms) for non-cryptographic purposes (e.g., cache keys, hashing object identities) to comply with FIPS and pass security linters like Bandit.

## 2026-10-09 - SSRF/LFI via file:// scheme in urllib.request.urlopen
**Vulnerability:**  called without checking if the URL scheme was strictly http or https allowed reading local files if a  scheme URL was provided, leading to Server-Side Request Forgery or Local File Inclusion.
**Learning:** In python,  processes  by default. Even when performing operations intended only for HTTP (like sending headers or tracking rate limits), failing to explicitly restrict the scheme leaves the application vulnerable.
**Prevention:** When using , explicitly validate the scheme of the constructed Request object (e.g. ) before calling urlopen, and add  to ignore the static check after this validation.
## 2026-10-09 - SSRF/LFI via file:// scheme in urllib.request.urlopen
**Vulnerability:** `urllib.request.urlopen()` called without checking if the URL scheme was strictly http or https allowed reading local files if a `file://` scheme URL was provided, leading to Server-Side Request Forgery or Local File Inclusion.
**Learning:** In python, `urllib.request.urlopen` processes `file://` by default. Even when performing operations intended only for HTTP (like sending headers or tracking rate limits), failing to explicitly restrict the scheme leaves the application vulnerable.
**Prevention:** When using `urllib.request.urlopen()`, explicitly validate the scheme of the constructed Request object (e.g. `if not req.full_url.startswith(("http://", "https://"))`) before calling urlopen, and add `# nosec B310` to ignore the static check after this validation.
