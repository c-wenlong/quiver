"""Web app pages as plugins: one module here, one script in ``static/pages/``.

A page named ``skills`` is two files and touches nothing shared:

- ``swe/webapp/pages/skills.py`` defines ``get(ctx, args) -> dict``
  for ``GET /v1/app/skills`` and, when it writes, ``post(ctx, action,
  body) -> dict`` for ``POST /v1/app/skills/<action>``. ``ctx`` is a
  ``PageContext``; ``args`` maps each query parameter to its first value.
  Raise ``PageError`` for a refusal the page should show.
- ``swe/webapp/static/pages/skills.js`` registers the renderer with
  ``registerPage("skills", render)``, and may add
  ``static/pages/skills.css``. The hub serves every page script as one
  ``/pages.js`` and every stylesheet as ``/pages.css``, in name
  order, so no list needs editing.

The sidebar id is the page name, so a page goes live in the sidebar the
moment its script registers. See docs/web-app.md, "Adding a page".
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HERE = Path(__file__).resolve().parent
STATIC = _HERE.parent / "static" / "pages"


# One lock for the pages that rewrite the hub's shared config: MCP
# servers (``mcp.json`` and the harness configs synced from it), skill
# links (``skill_links.json`` and the links themselves) and Setup, which
# does both in one apply. Each held its own lock, so a Setup apply could
# rewrite mcp.json under an MCP page write, or relink a skills root the
# Skills page was linking.
CONFIG_LOCK = threading.Lock()


class PageError(Exception):
    """A refusal the page shows: an HTTP status, a code and a message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class PageContext:
    """What a page may use. ``state`` is the hub's ``HubState`` (catalog,
    machine, the shared ledger in ``state.webapp``); ``home`` is the home
    the hub runs for (``None`` means the real one)."""

    home: Path | None
    state: Any
    device_id: str | None = None

    @property
    def machine(self) -> dict:
        return self.state.machine


def home_path(ctx) -> Path:
    """The home a page reads under: ``ctx.home``, or the process's own
    when there is no context or it names none."""
    home = getattr(ctx, "home", None)
    return Path(home) if home is not None else Path.home()


def tilde(path: Path | str, home: Path | str | None = None) -> str:
    """``path`` with ``home`` (the process's own when None) written as
    ``~``, as the pages show hub paths: ``~`` for the home itself,
    ``~/…`` inside it, anything else as is."""
    text = str(path)
    seps = {"/", os.sep}  # the native one too, as Windows writes paths
    root = str(home if home is not None else Path.home()).rstrip("".join(seps))
    if not root:
        return text
    if text == root:
        return "~"
    if any(text.startswith(root + sep) for sep in seps):
        return "~" + text[len(root):]
    return text


def load(name: str) -> ModuleType | None:
    """The page module for ``name``, or ``None`` when there is none. Only
    a plain lower-case name whose module file exists here is imported."""
    if not _NAME.match(name) or name.startswith("_"):
        return None
    if not (_HERE / f"{name}.py").is_file():
        return None
    return importlib.import_module(f"swe.webapp.pages.{name}")


def _js_string(text: str) -> str:
    """``text`` as a single-quoted JavaScript string. Double quotes stay
    as they are, so the bundle still reads (and greps) like the source."""
    escaped = (text.replace("\\", "\\\\").replace("'", "\\'")
               .replace("\n", "\\n").replace("\r", "\\r")
               .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return f"'{escaped}'"


# The last bundle built per suffix: ``suffix -> (stamp, body, etag)``,
# the stamp each file's name, mtime and size, so a request that changes
# nothing costs a stat per file instead of reading and escaping them all.
_BUNDLES: dict[str, tuple[tuple, bytes, str]] = {}
_BUNDLES_LOCK = threading.Lock()


def bundled(suffix: str) -> tuple[bytes, str]:
    """``(bundle(suffix), etag)``, rebuilt only when a file changed. The
    ETag lets the browser revalidate with a 304 instead of a download."""
    try:
        stamp = tuple((p.name, st.st_mtime_ns, st.st_size)
                      for p in sorted(STATIC.glob(f"*{suffix}"))
                      for st in (p.stat(),))
    except OSError:
        stamp = None  # a file moved mid-listing: build without caching
    # The build runs under the lock too: two requests after a change
    # wait for one build instead of both reading every page script.
    with _BUNDLES_LOCK:
        hit = _BUNDLES.get(suffix)
        if stamp is not None and hit is not None and hit[0] == stamp:
            return hit[1], hit[2]
        body = bundle(suffix)
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        if stamp is not None:
            _BUNDLES[suffix] = (stamp, body, etag)
        return body, etag


def etag_matches(header: str | None, etag: str) -> bool:
    """``If-None-Match`` against ``etag`` by weak comparison, as a GET
    takes it: a list, ``W/`` tags and ``*`` all count."""
    if not header:
        return False
    tags = [t.strip() for t in header.split(",")]
    return "*" in tags or any(t.removeprefix("W/") == etag for t in tags)


def bundle(suffix: str) -> bytes:
    """Every ``static/pages/*<suffix>`` file, in name order, as one body.

    Each script is compiled and run on its own (``new Function``), so two
    pages may both define ``render`` or the same ``const``, and one page
    that fails to parse or throws while loading is logged and skipped
    instead of stopping every page after it. A page reaches the app only
    through globals (``registerPage``, ``api``...; helpers it shares with
    another page go on ``globalThis``). ``sourceURL`` keeps the page's own
    file name in the browser's errors and debugger."""
    parts = []
    for path in sorted(STATIC.glob(f"*{suffix}")):
        body = path.read_bytes()
        if suffix == ".js":
            source = body.decode("utf-8") + f"\n//# sourceURL=/pages/{path.name}\n"
            body = (
                f"try {{ new Function({_js_string(source)})(); }}\n"
                f"catch (err) {{ console.error({_js_string('web app page ' + path.name + ' failed to load:')}, err); }}"
            ).encode("utf-8")
        parts.append(f"/* {path.name} */\n".encode() + body)
    return b"\n".join(parts)


def web_url(value) -> str | None:
    """``value`` when it is an http(s) address, else None: pages put it
    in an href, and it comes from someone else's text (a provider entry,
    a status feed)."""
    if isinstance(value, str) and value.lower().startswith(("https://", "http://")):
        return value
    return None
