"""Declarative, pluggable table renderer for the quiver CLI.

Replaces hand-rolled ``f"{...:<{w}}"`` string interpolation across
``swe list``, ``swe models``, ``swe session``, and ``swe providers``
with a single component that:

- normalises each column's width to ``max(preferred_width, max_cell_width)``
  (bounded by ``max_width`` when ``fit="bounded"``),
- truncates cells that overflow the column with ``…``,
- left-aligns text by default while ANSI-aware padding preserves colors,
- dispatches to a per-kind render function (extensible via
  ``@register_kind``), and
- emits a dim header row + ``─`` separator that matches the table's
  total visible width.

The component is intentionally additive: nothing in the existing
commands imports it yet. Migration is opt-in, one ``cmd_*`` handler at
a time (see ``tests/test_table.py`` for the contract examples).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal

from quiver.console import c, cpad, strip_ansi, truncate, visible_len


FitMode = Literal["fixed", "content", "bounded"]

# A kind is a single callable:
#     render(value, width, **attrs) -> str
# returning an already-width-padded cell. The truncate step used for
# width measurement is derived automatically (stringify + visible_len)
# — keep kinds simple unless a real callable needs a non-string measure.
KindRenderFn = Callable[[Any, int, dict], str]
KindTruncateFn = Callable[[Any, int, dict], str]

_KINDS: Dict[str, tuple[KindRenderFn, KindTruncateFn]] = {}


def register_kind(
    name: str,
) -> Callable[[Callable], Callable]:
    """Decorator to register a custom column kind.

        @register_kind("text")
        def render_text(value, width, attrs): ...

    Signature is ``(value: Any, width: int, attrs: dict) -> str``.
    """

    def _decorator(fn: Callable) -> Callable:
        if name in _KINDS:
            raise ValueError(f"Kind {name!r} already registered")
        _KINDS[name] = _normalise_kind(fn)
        return fn

    return _decorator


def _normalise_kind(fn: Callable) -> tuple[KindRenderFn, KindTruncateFn]:
    """Build the (render, truncate) pair from a single user-supplied fn."""

    def render(value: Any, width: int, attrs: dict) -> str:
        return fn(value, width, attrs)

    def trunc(value: Any, width_limit: int, attrs: dict) -> str:
        if value is None:
            return ""
        return str(value)

    return render, trunc


def _kind(name: str) -> tuple[KindRenderFn, KindTruncateFn]:
    if name not in _KINDS:
        raise KeyError(
            f"Unknown column kind {name!r}. "
            f"Registered: {sorted(_KINDS)}. "
            f"Use @register_kind to add a custom kind."
        )
    return _KINDS[name]


# ---------------------------------------------------------------------------
# Built-in kinds
# ---------------------------------------------------------------------------


def _register_default_kinds() -> None:
    """The six built-in kinds documented in the design plan."""

    @register_kind("text")
    def _text(value, width, attrs):
        raw = "" if value is None else str(value)
        # ANSI-painted inputs are stripped to plain before truncation so
        # ``console.truncate`` (which slices by bytes) cannot chop a
        # ``\033[…m`` escape mid-sequence — that would leak the colour
        # into the gap that follows this cell. Callers who want ANSI
        # in a text-style column should use ``kind="preformatted"`` or
        # ``trust_cell_width=True``; ``text`` is plain-only by design.
        plain = strip_ansi(raw)
        truncated = truncate(plain, width)
        return truncated + " " * max(width - visible_len(truncated), 0)

    @register_kind("number")
    def _number(value, width, attrs):
        if value is None:
            plain = attrs.get("empty", "—")
        else:
            plain = f"{int(value):,}" if attrs.get("thousands") else str(int(value))
        return plain.rjust(width)

    @register_kind("count_threshold")
    def _count_threshold(value, width, attrs):
        threshold = attrs.get("threshold", 0)
        if value is None:
            plain = attrs.get("empty", "—")
            return plain.rjust(width)
        n = int(value)
        plain = str(n)
        if n >= threshold:
            return c("green", plain.rjust(width))
        return plain.rjust(width)

    @register_kind("list")
    def _list(value, width, attrs):
        # Use cpad for color coherence with ``console.cpad`` — pad AND
        # text get the same color so neighbouring columns never see a
        # dim gap that visually disconnects from the cell.
        empty = attrs.get("empty", "—")
        color = attrs.get("color", "cyan")
        if not value:
            return cpad(color, empty, width)
        joined = ", ".join(str(v) for v in value)
        return cpad(color, truncate(joined, width), width)

    @register_kind("timestamp")
    def _timestamp(value, width, attrs):
        # Pass the raw ``value`` through the column's ``formatter`` attr
        # (a Callable[[Any], str]) so callers don't have to wrap every
        # row in a ``(seconds, lambda)`` tuple.
        formatter: Callable[[Any], str] | None = attrs.get("formatter")
        empty = attrs.get("empty", "—")
        right = attrs.get("right", False)
        if value is None or formatter is None:
            return empty.rjust(width) if right else empty.ljust(width)
        label = formatter(value)
        return label.rjust(width) if right else label.ljust(width)

    @register_kind("preformatted")
    def _preformatted(value, width, attrs):
        # The cell hands us a pre-padded / pre-colored ANSI string that
        # already carries its own width. Pass it through.
        return str(value)


_register_default_kinds()


# ---------------------------------------------------------------------------
# Column / Row dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Column:
    name: str
    header: str
    width: int
    kind: str = "text"
    fit: FitMode = "bounded"
    max_width: int | None = None
    trust_cell_width: bool = False
    # Extra kwargs forwarded to the kind renderer (color, threshold,
    # empty marker, formatter, ...). Mutable post-construction.
    attrs: dict = field(default_factory=dict)


@dataclass
class Row:
    cells: Dict[str, Any]
    accent: str | None = None


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------


class Table:
    """Builder for a fixed-width, ANSI-aware table.

    Usage::

        t = Table(separator_char="─", header_style="dim")
        t.add_column("name", "NAME", width=14)
        t.add_column("rate", "RATE", width=14, kind="preformatted",
                     trust_cell_width=True)
        for tool, info in tools.items():
            t.add_row({"name": tool, "rate": format_rate(info)},
                      accent="neon")
        for line in t.render():
            print(line)
    """

    def __init__(self, separator_char: str = "─", header_style: str = "dim"):
        self.separator_char = separator_char
        self.header_style = header_style
        self._columns: list[Column] = []
        self._rows: list[Row] = []
        self._column_gap = 2  # visible spaces between columns

    # ------------------------------------------------------------------ API

    def add_column(
        self,
        name: str,
        header: str,
        width: int,
        max_width: int | None = None,
        kind: str = "text",
        fit: FitMode = "bounded",
        trust_cell_width: bool = False,
        **attrs,
    ) -> "Table":
        self._columns.append(
            Column(
                name=name,
                header=header,
                width=width,
                kind=kind,
                fit=fit,
                max_width=max_width,
                trust_cell_width=trust_cell_width,
                attrs=dict(attrs),
            )
        )
        return self

    def add_row(self, data: Dict[str, Any], accent: str | None = None) -> "Table":
        column_names = {c.name for c in self._columns}
        unknown = set(data) - column_names
        if unknown:
            # Drop quietly — see design §8. Future: warn via logging.
            data = {k: v for k, v in data.items() if k in column_names}
        self._rows.append(Row(cells=data, accent=accent))
        return self

    def render(self) -> list[str]:
        if not self._columns:
            return []
        widths = self._compute_widths()
        out: list[str] = []
        out.append(self._render_header(widths))
        out.append(self._render_separator(widths))
        for row in self._rows:
            out.append(self._render_row(row, widths))
        return out

    # -------------------------------------------------------------- helpers

    def _compute_widths(self) -> Dict[str, int]:
        widths: Dict[str, int] = {}
        for col in self._columns:
            _, trunc_fn = _kind(col.kind)
            # Width samples: header + every cell's plain-text form.
            # We sample via the same substitution as render so the width
            # math sees the *displayed* text, not a missing-key placeholder.
            samples = [
                trunc_fn(_cell_value(col, r), col.width, col.attrs)
                for r in self._rows
            ]
            samples.append(col.header)
            observed = max((visible_len(s) for s in samples), default=0)
            if col.fit == "fixed":
                widths[col.name] = col.width
            elif col.fit == "content":
                widths[col.name] = max(col.width, observed)
            else:  # "bounded"
                upper = col.max_width if col.max_width is not None else observed
                widths[col.name] = max(col.width, min(observed, upper))
        return widths

    def _render_header(self, widths: Dict[str, int]) -> str:
        parts: list[str] = []
        for i, col in enumerate(self._columns):
            label = truncate(col.header, widths[col.name])
            parts.append(
                c(self.header_style, label + " " * max(widths[col.name] - visible_len(label), 0))
            )
            if i < len(self._columns) - 1:
                parts.append(" " * self._column_gap)
        return "".join(parts)

    def _render_separator(self, widths: Dict[str, int]) -> str:
        # Width math must match the header — the gap is plain spaces.
        total = sum(widths[c.name] for c in self._columns) + max(
            0, len(self._columns) - 1
        ) * self._column_gap
        return c(self.header_style, self.separator_char * total)

    def _render_row(self, row: Row, widths: Dict[str, int]) -> str:
        renderable: list[str] = []
        for i, col in enumerate(self._columns):
            value = _cell_value(col, row)
            # ``trust_cell_width`` (or the preformatted kind) means the
            # cell has *already* been padded inside its own renderer
            # (e.g. ``RateLimitInfo.format_column`` ships a self-coloured,
            # width-aligned string). Skipping the column-width pad keeps
            # bounds intact and avoids double-wrapping existing ANSI.
            if col.trust_cell_width or col.kind == "preformatted":
                cell_text = str(value)
            else:
                render_fn, _ = _kind(col.kind)
                cell_text = render_fn(value, widths[col.name], col.attrs)
            if (
                row.accent
                and not col.trust_cell_width
                and col.kind != "preformatted"
            ):
                cell_text = c(row.accent, cell_text)
            renderable.append(cell_text)
            if i < len(self._columns) - 1:
                renderable.append(" " * self._column_gap)
        return "".join(renderable)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cell_value(col: Column, row: Row) -> Any:
    """Return the cell value, substituting the column's empty marker if missing.

    Both width measurement AND rendering go through here so they agree
    on what they see. Falling through with ``row.cells[col.name]``
    would corrupt width math into ``str(KeyError('a'))``.
    """
    if col.name in row.cells:
        return row.cells[col.name]
    return col.attrs.get("empty", "—")


def registered_kinds() -> list[str]:
    """Return a sorted list of currently registered kind names."""
    return sorted(_KINDS)
