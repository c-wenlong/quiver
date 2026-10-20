"""Credential indirection for the MCP hub.

``~/.quiver/mcp.json`` records which servers exist and is worth versioning.
Credentials are not, so the hub stores ``${NAME}`` references and the values
live in ``~/.quiver/secrets/mcp/.api_keys``, mode 600 and gitignored. A machine
`swe init` has not migrated yet still reads ``~/.quiver/secrets/.api_keys``.

Resolution happens when a harness config is written, not by exporting to the
environment: Claude and Cursor launch from the Dock and never
read a shell profile, so an unresolved reference would reach the server as a
literal token string.

Only names present in the store are substituted. ``$HOME`` and
``$(gh auth token)`` appear in real configs and must survive untouched.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from quiver import paths

SECRETS_DIRNAME = paths.SECRETS_SUBDIR
SECRETS_BASENAME = paths.SECRETS_BASENAME

# export NAME=value, with optional quotes. Comments and blanks are skipped.
_EXPORT_RE = re.compile(r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$""")
_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def secrets_dir_for(home: Path | None = None) -> Path:
    return paths.mcp_secrets_file_for(home).parent


def secrets_file_for(home: Path | None = None) -> Path:
    return paths.mcp_secrets_path_for(home)


def load_secrets(home: Path | None = None) -> dict[str, str]:
    """Parse the store into {NAME: value}. Missing file reads as empty."""
    path = secrets_file_for(home)
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}

    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _EXPORT_RE.match(line)
        if not m:
            continue
        name, raw = m.group(1), _strip_inline_comment(m.group(2).strip())
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        out[name] = raw
    return out


def _strip_inline_comment(raw: str) -> str:
    """Drop a trailing unquoted `` # comment`` from an export value.

    ``export KEY=value # note`` used to load ``value # note`` — the broken
    string then resolved ``${KEY}`` into every harness config a sync wrote.
    A ``#`` starts a comment only after whitespace and outside quotes.
    """
    quote = ""
    for i, ch in enumerate(raw):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and i > 0 and raw[i - 1] in " \t":
            return raw[:i].rstrip()
    return raw


def resolve(value, secrets: dict[str, str] | None = None, home: Path | None = None):
    """Replace ``${NAME}`` with its value, recursing through dicts and lists.

    An unknown name is left as-is rather than blanked: a literal ``${FOO}``
    in a config is visible and debuggable, whereas an empty string silently
    authenticates as nobody.
    """
    if secrets is None:
        secrets = load_secrets(home)
    if isinstance(value, dict):
        return {k: resolve(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, secrets) for v in value]
    if isinstance(value, str):
        return _REF_RE.sub(
            lambda m: secrets.get(m.group(1), m.group(0)), value
        )
    return value


def unresolved_names(value, secrets: dict[str, str] | None = None,
                     home: Path | None = None) -> list[str]:
    """Reference names that the store cannot satisfy, for warning on."""
    if secrets is None:
        secrets = load_secrets(home)
    found: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for name in _REF_RE.findall(v):
                if name not in secrets:
                    found.add(name)

    walk(value)
    return sorted(found)


def redact(value, secrets: dict[str, str] | None = None, home: Path | None = None):
    """Inverse of resolve: swap known secret values back to ``${NAME}``.

    ``swe mcp discover`` reads harness configs, which hold real values. Without
    this every discover would write plaintext back into the hub and quietly
    undo the indirection.
    """
    if secrets is None:
        secrets = load_secrets(home)
    # Longest first, so a value that contains another is not half-replaced.
    pairs = sorted(secrets.items(), key=lambda kv: -len(kv[1]))

    def swap(text: str) -> str:
        """Replace whole-field values, and long embedded credentials.

        Blind substring matching is unsafe for short values:
        TELEGRAM_API_ID is 8 digits, and a blind replace would rewrite any
        config text that happened to contain that number. But resolve()
        substitutes ``${NAME}`` anywhere in a string, so a
        ``?key=TOKEN`` URL gets a literal credential written into the
        harness — and without embedded matching it is written back into
        the hub in plaintext, undoing the indirection. Values long enough
        to be unambiguous (16+ chars) are replaced wherever they appear;
        shorter ones keep the whole-field anchor.
        """
        for name, secret in pairs:
            if not secret:
                continue
            if text == secret:
                return f"${{{name}}}"
            for scheme in ("Bearer ", "Token ", "Basic "):
                if text == scheme + secret:
                    return f"{scheme}${{{name}}}"
            if len(secret) >= 16 and secret in text:
                text = text.replace(secret, f"${{{name}}}")
        return text

    def walk(v):
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, str):
            return swap(v)
        return v

    return walk(value)


# ── Display scrubbing ───────────────────────────────────────────────
#
# redact() only knows values that are in the store. A harness config can
# also hold a credential nobody ever put there, in a URL or an argument, and
# a machine-readable listing must not carry it. scrub_url / scrub_args hide
# anything credential-shaped, keeping ${NAME} references (they name a secret
# without being one) and the structure around the hidden part.

MASK = "***"

_REF_ONLY_RE = re.compile(r"(?:\$\{[A-Za-z_][A-Za-z0-9_]*\})+")
_SCHEME_VALUE_RE = re.compile(r"(?:Bearer|Basic|Token)\s+(.+)", re.IGNORECASE)
_SECRET_FLAG_RE = re.compile(
    r"key|token|secret|passw|bearer|credential|cookie|signature"
    r"|authori[sz]ation",
    re.IGNORECASE,
)
_HEADER_FLAGS = frozenset({"--header", "--headers", "-H"})
_OPAQUE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_\-=.%~+]{24,}")
_ENV_ASSIGN_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", re.DOTALL)


def _hide(value: str) -> str:
    """``***``, unless the value is only ``${NAME}`` references."""
    if _REF_ONLY_RE.fullmatch(value):
        return value
    scheme = _SCHEME_VALUE_RE.fullmatch(value)
    if scheme and _REF_ONLY_RE.fullmatch(scheme.group(1)):
        return value
    return MASK


def _hide_header(value: str) -> str:
    """Keep a header's name, hide what follows the colon."""
    name, colon, rest = value.partition(":")
    if not colon:
        return _hide(value)
    return f"{name}: {_hide(rest.strip())}"


def _looks_opaque(segment: str) -> bool:
    """A long run of token characters with letters and digits in it.

    Webhook-style MCP URLs carry their credential as a path segment
    (``/s/<token>/mcp``). Real path words ("mcp", "sse", "v1") are short
    or have no digits, so only credential-shaped segments are hidden.
    """
    return (
        _OPAQUE_SEGMENT_RE.fullmatch(segment) is not None
        and any(ch.isdigit() for ch in segment)
        and any(ch.isalpha() for ch in segment)
    )


def scrub_url(url: str, secrets: dict[str, str] | None = None) -> str:
    """A URL safe to print: no password, query value, fragment or token path."""
    from urllib.parse import urlsplit, urlunsplit

    url = redact(url, secrets)
    try:
        parts = urlsplit(url)
    except ValueError:
        return MASK
    netloc = parts.netloc
    userinfo, at, hostport = netloc.rpartition("@")
    if at:
        user, colon, password = userinfo.partition(":")
        userinfo = f"{user}:{_hide(password)}" if colon else _hide(userinfo)
        netloc = f"{userinfo}@{hostport}"
    path = "/".join(
        _hide(seg) if _looks_opaque(seg) else seg
        for seg in parts.path.split("/")
    )
    query = "&".join(
        f"{key}{eq}{_hide(value) if value else value}"
        for key, eq, value in (
            pair.partition("=") for pair in parts.query.split("&") if pair)
    )
    fragment = _hide(parts.fragment) if parts.fragment else ""
    return urlunsplit((parts.scheme, netloc, path, query, fragment))


def scrub_args(args, secrets: dict[str, str] | None = None) -> list[str]:
    """Command-line arguments safe to print.

    Hides the value of a credential-named flag (``--token X``,
    ``--api-key=X``) or env assignment (``API_KEY=X``), the value of a
    header flag (``--header "Name: X"``), and the credential parts of any
    URL argument. Everything else stays.
    """
    out: list[str] = []
    pending = None  # "secret" or "header": the next bare arg is its value
    for arg in redact([str(a) for a in args], secrets):
        if pending and not arg.startswith("-"):
            out.append(_hide_header(arg) if pending == "header" else _hide(arg))
            pending = None
            continue
        pending = None
        if arg.startswith("-"):
            flag, eq, value = arg.partition("=")
            if flag in _HEADER_FLAGS:
                if eq:
                    arg = f"{flag}={_hide_header(value)}"
                else:
                    pending = "header"
            elif _SECRET_FLAG_RE.search(flag):
                if eq:
                    arg = f"{flag}={_hide(value)}"
                else:
                    pending = "secret"
            elif eq and "://" in value:
                # `--url=https://host/mcp?token=...`: an ordinary flag can
                # still carry a credential-bearing URL.
                arg = f"{flag}={scrub_url(value, secrets)}"
        elif "://" in arg:
            arg = scrub_url(arg, secrets)
        elif (assign := _ENV_ASSIGN_RE.fullmatch(arg)) and _SECRET_FLAG_RE.search(
                assign.group(1)):
            # `env API_KEY=... npx server`, a common wrapper in configs.
            arg = f"{assign.group(1)}={_hide(assign.group(2))}"
        out.append(arg)
    return out


def _secret_hint(raw: str) -> str:
    """A field name normalised to a valid store name: ``X-Api-Key`` → ``X_API_KEY``."""
    hint = re.sub(r"[^A-Za-z0-9_]", "_", raw).upper()
    if not hint or hint[0].isdigit():
        hint = "MCP_" + hint if hint else "MCP_SECRET"
    return hint


def _store_literal(hint: str, value: str, secrets: dict[str, str],
                   home: Path | None = None) -> str:
    """Move one plaintext credential into the store; return its ``${NAME}``."""
    for n, v in secrets.items():
        if v == value:
            return f"${{{n}}}"
    base = _secret_hint(hint)
    name = base
    i = 2
    while name in secrets:
        name = f"{base}_{i}"
        i += 1
    set_secret(name, value, home)
    secrets[name] = value
    return f"${{{name}}}"


def shield_credentials(server: dict, home: Path | None = None) -> tuple[dict, list[str]]:
    """Move credential-shaped literals in a canonical server dict into the
    secrets store; return (shielded copy, notes).

    ``swe mcp discover --apply`` copies harness values into the versioned
    hub file. ``redact()`` only substitutes values already in the store, so
    a literal ``env.API_KEY = "sk-live"`` — the common case — landed in
    ``mcp.json`` in plaintext, one ``git add`` from a commit. Fields named
    like credentials that are not already ``${NAME}`` references are moved
    into the store and replaced by the reference. Shielding is
    output-equivalent: ``resolve()`` reproduces the same value in every
    harness config a sync writes.
    """
    secrets = load_secrets(home)
    out = dict(server)
    notes: list[str] = []

    def stash(hint: str, value: str) -> str:
        ref = _store_literal(hint, value, secrets, home)
        notes.append(
            f"{hint}: plaintext credential moved to the secrets store ({ref})")
        return ref

    env = out.get("env")
    if isinstance(env, dict):
        shielded = dict(env)
        for key, value in env.items():
            if (isinstance(value, str) and value
                    and _SECRET_FLAG_RE.search(str(key))
                    and not _REF_ONLY_RE.fullmatch(value)):
                shielded[key] = stash(str(key), value)
        out["env"] = shielded

    headers = out.get("headers")
    if isinstance(headers, dict):
        shielded = dict(headers)
        for key, value in headers.items():
            if not isinstance(value, str) or not value or _REF_ONLY_RE.fullmatch(value):
                continue
            scheme = _SCHEME_VALUE_RE.fullmatch(value)
            if scheme:
                token = scheme.group(1)
                if _REF_ONLY_RE.fullmatch(token):
                    continue
                shielded[key] = f"{value[: -len(token)]}{stash(str(key), token)}"
            elif _SECRET_FLAG_RE.search(str(key)):
                shielded[key] = stash(str(key), value)
        out["headers"] = shielded

    url = out.get("url")
    if isinstance(url, str) and url:
        from urllib.parse import urlsplit, urlunsplit

        try:
            parts = urlsplit(url)
        except ValueError:
            parts = None
        if parts and parts.query:
            pairs = []
            touched = False
            for pair in parts.query.split("&"):
                if not pair:
                    continue
                key, eq, value = pair.partition("=")
                if (eq and value and _SECRET_FLAG_RE.search(key)
                        and not _REF_ONLY_RE.fullmatch(value)):
                    value = stash(key, value)
                    touched = True
                pairs.append(key + eq + value)
            if touched:
                out["url"] = urlunsplit(
                    (parts.scheme, parts.netloc, parts.path,
                     "&".join(pairs), parts.fragment))
    return out, notes


def store_env(home: Path | None = None) -> dict[str, str]:
    """The store merged over os.environ, for callers that want a full env."""
    return {**os.environ, **load_secrets(home)}


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def render_secret_line(name: str, value: str) -> str:
    """The ``export NAME=…`` line ``load_secrets`` reads back as ``value``.

    ``load_secrets`` strips matching outer quotes but never unescapes, so
    single quotes serve any value without ``'`` and double quotes any
    without ``"`` ``\\`` ``$`` or a backtick. A value holding both raises.
    """
    if "'" not in value:
        return f"export {name}='{value}'"
    if not any(ch in value for ch in "\"\\$`"):
        return f'export {name}="{value}"'
    raise ValueError(
        "the value has both quote kinds, and the secrets file can't escape them")


def set_secret(name: str, value: str, home: Path | None = None) -> tuple[Path, bool]:
    """Write ``name=value`` into the MCP secrets file; return (path, created).

    An existing ``NAME`` line is replaced in place and every other line is
    kept byte-for-byte. The file is mode 600 in a mode-700 ``secrets/``,
    sealed with its own ``.gitignore`` the way ``swe init`` leaves it, and
    swapped in atomically so a reader never sees a half-written store.

    Raises :class:`ValueError` on a bad name, an empty or multi-line value,
    or a value no line here could store (see :func:`render_secret_line`).
    """
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"{name} isn't a valid variable name: letters, digits and "
            "underscores, not starting with a digit")
    if not value:
        raise ValueError("empty value; pipe one in, e.g. `printf %s \"$V\" | swe mcp secret set NAME`")
    if "\n" in value or "\r" in value:
        raise ValueError("a value can't span more than one line")
    line = render_secret_line(name, value)

    shown = secrets_file_for(home)
    # Quiver's own folder is private; a store symlinked elsewhere keeps
    # whatever access its real folder has.
    shown.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not shown.parent.is_symlink():
        os.chmod(shown.parent, 0o700)
    # The real file, so the swap below never replaces a symlink with a copy.
    path = Path(os.path.realpath(shown))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    try:
        import fcntl
    except ImportError:
        # Windows has no fcntl; the save still runs, just unlocked.
        fcntl = None

    # One writer at a time, Terminal and app alike: the lock file sits
    # beside the store and is never replaced, so the swap can't bypass it.
    lock_fd = os.open(path.parent / f".{path.name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        created = not path.exists()
        try:
            # newline="" keeps CRLF and every other byte as it was.
            with open(path, encoding="utf-8", newline="") as handle:
                text = handle.read()
        except FileNotFoundError:
            text = ""
        lines = text.splitlines(keepends=True)
        # Every line for the name, not just the first: load_secrets keeps the
        # last one, so a stale duplicate further down would win.
        replaced = False
        for i, existing in enumerate(lines):
            body = existing.rstrip("\r\n")
            match = _EXPORT_RE.match(body)
            if match and match.group(1) == name:
                lines[i] = line + (existing[len(body):] or "\n")
                replaced = True
        if not replaced:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(line + "\n")

        fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write("".join(lines))
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    finally:
        os.close(lock_fd)

    # The store sits under secrets/, which must stay out of any `git add .`.
    from quiver.init.migrate import seal_secrets_dir
    seal_secrets_dir(home)

    if load_secrets(home).get(name) != value:
        raise ValueError(f"the value could not be written in a form {shown} reads back")
    return shown, created
