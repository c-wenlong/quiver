## 2026-08-16 - SSRF/LFI via Custom URL Schemes

**Vulnerability:** The application was vulnerable to Server-Side Request Forgery (SSRF) and Local File Inclusion (LFI) because `urllib.request.urlopen()` was used to fetch URLs without validating the protocol scheme. This could allow an attacker to read local files via `file://` or exploit custom internal schemes.
**Learning:** `urllib.request.urlopen()` automatically follows various schemes like `file://` by default in Python.
**Prevention:** Always validate that the constructed URL scheme is explicitly restricted to `http://` or `https://` (e.g., `req.full_url.lower().startswith(('http://', 'https://'))`) before passing it to `urlopen()`. Additionally, appending `# nosec B310` informs Bandit that the validation is intentionally handling the security risk.
