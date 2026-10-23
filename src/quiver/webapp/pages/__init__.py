"""Web app pages as plugins: one module here, one script in ``static/pages/``.

A page named ``skills`` is two files and touches nothing shared:

- ``quiver/webapp/pages/skills.py`` defines ``get(ctx, args) -> dict``
  for ``GET /v1/app/skills`` and, when it writes, ``post(ctx, action,
  body) -> dict`` for ``POST /v1/app/skills/<action>``. ``ctx`` is a
  ``PageContext``; ``args`` maps each query parameter to its first value.
  Raise ``PageError`` for a refusal the page should show.
- ``quiver/webapp/static/pages/skills.js`` registers the renderer with
  ``registerPage("skills", render)``, and may add
  ``static/pages/skills.css``. The hub serves every page script as one
  ``/app/pages.js`` and every stylesheet as ``/app/pages.css``, in name
  order, so no list needs editing.

The sidebar id is the page name, so a page goes live in the sidebar the
moment its script registers. See docs/web-app.md, "Adding a page".
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_HERE = Path(__file__).resolve().parent
STATIC = _HERE.parent / "static" / "pages"


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


def load(name: str) -> ModuleType | None:
    """The page module for ``name``, or ``None`` when there is none. Only
    a plain lower-case name whose module file exists here is imported."""
    if not _NAME.match(name) or name.startswith("_"):
        return None
    if not (_HERE / f"{name}.py").is_file():
        return None
    return importlib.import_module(f"quiver.webapp.pages.{name}")


def _js_string(text: str) -> str:
    """``text`` as a single-quoted JavaScript string. Double quotes stay
    as they are, so the bundle still reads (and greps) like the source."""
    escaped = (text.replace("\\", "\\\\").replace("'", "\\'")
               .replace("\n", "\\n").replace("\r", "\\r")
               .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
    return f"'{escaped}'"


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
            source = body.decode("utf-8") + f"\n//# sourceURL=/app/pages/{path.name}\n"
            body = (
                f"try {{ new Function({_js_string(source)})(); }}\n"
                f"catch (err) {{ console.error({_js_string('web app page ' + path.name + ' failed to load:')}, err); }}"
            ).encode("utf-8")
        parts.append(f"/* {path.name} */\n".encode() + body)
    return b"\n".join(parts)
