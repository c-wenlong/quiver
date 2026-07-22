"""Unit tests for the declarative table renderer in ``quiver.table``.

Locks in:
- Width computation under each fit mode (fixed/content/bounded).
- ANSI-safe column math (no colour bleed between cells).
- Per-kind rendering + truncating.
- ``@register_kind`` extensibility.
- Header + separator rendering matching the table's total width.
- Row accent wrapping.
- Missing/malformed cell values falling back to the column's empty marker.
"""

import unittest

from quiver.console import c, strip_ansi, visible_len
from quiver.table import (
    Column,
    Row,
    Table,
    register_kind,
    registered_kinds,
    _KINDS,
)


class TableFitModesTest(unittest.TestCase):
    """Width formula under each ``fit`` mode."""

    def test_fit_fixed_ignores_content_width(self):
        t = Table()
        t.add_column("a", "A", width=5, fit="fixed")
        t.add_row({"a": "a" * 20})
        out = t.render()
        body = out[-1]
        self.assertEqual(visible_len(body), 5)
        self.assertTrue(body.startswith("a"))

    def test_fit_content_stretches_above_minimum(self):
        t = Table()
        t.add_column("a", "A", width=4, fit="content")
        t.add_row({"a": "abcdefghijkl"})
        t.add_row({"a": "short"})
        out = t.render()
        body = out[-1]
        self.assertEqual(visible_len(body), 12)
        self.assertTrue(body.startswith("short"))

    def test_fit_bounded_caps_at_max_width(self):
        t = Table()
        t.add_column("a", "A", width=4, max_width=8, fit="bounded")
        t.add_row({"a": "abcdefghijkl"})
        out = t.render()
        body = out[-1]
        self.assertEqual(visible_len(body), 8)
        # ``console.truncate`` adds the ASCII ``...`` suffix (not the
        # Unicode ``…``); pin the actual contract rather than the
        # artist's intent so we don't drift on visual polish.
        self.assertTrue(body.rstrip().endswith("..."))

    def test_fit_bounded_grows_to_content_when_below_max(self):
        t = Table()
        t.add_column("a", "A", width=4, max_width=20, fit="bounded")
        t.add_row({"a": "abcdef"})
        out = t.render()
        self.assertEqual(visible_len(out[-1]), 6)


class TableAnsiSafetyTest(unittest.TestCase):
    def test_ansi_stripped_from_width_calc(self):
        t = Table()
        t.add_column("a", "A", width=10, fit="fixed")
        t.add_row({"a": c("green", "codex")})
        out = t.render()
        self.assertEqual(visible_len(out[-1]), 10)

    def test_text_strips_ansi_from_input(self):
        t = Table()
        t.add_column("a", "A", width=5, fit="fixed")
        t.add_column("b", "B", width=5, fit="fixed")
        t.add_row({"a": c("green", "abcdefghij"), "b": "ok"})
        out = t.render()
        # Visible width: 5 (a) + 2 gap + 5 (b) = 12.
        self.assertEqual(visible_len(out[-1]), 12)
        # The reset code must appear at least once (green-wrapped on "a"
        # produces green + reset; the row ends naturally). The *stronger*
        # guarantee — no green escape survives into the next column —
        # is verified by checking the gap region (chars 5..6) has no
        # ANSI colour escape codes flowing past the truncated cell.
        body = out[-1]
        # Contract: ``text`` strips ANSI on input, so the rendered body
        # contains zero escapes — colour never bleeds into the gap or
        # the next column. (A pre-fix version of this assertion counted
        # reset codes ``>= 1``, but the truncated cell could lose its
        # closing reset mid-slice; stripping eliminates the class of
        # bugs entirely.)
        self.assertNotIn("\033[", body)
        # Gap-segment is plain space. The slice bounds are derived
        # from the column widths above (``width=5``) and the Table's
        # internal ``_column_gap = 2`` so the test stays correct if
        # any width changes — a hardcoded ``body[5:7]`` would silently
        # lie on width-bump.
        col_a_width = 5
        table_column_gap = 2
        gap = body[col_a_width:col_a_width + table_column_gap]
        self.assertEqual(gap, "  ")


class TableRegisteredKindsTest(unittest.TestCase):
    def test_default_kinds_registered(self):
        expected = {"text", "number", "count_threshold", "list", "timestamp", "preformatted"}
        self.assertTrue(expected.issubset(set(registered_kinds())))


class TableCustomKindTest(unittest.TestCase):
    """``@register_kind`` must work for third-party code."""

    def setUp(self):
        self._saved = dict(_KINDS)

    def tearDown(self):
        _KINDS.clear()
        _KINDS.update(self._saved)

    def test_register_kind_callback_invoked(self):
        seen: list[tuple] = []

        @register_kind("price")
        def _price(value, width, attrs):
            seen.append((value, width, attrs))
            return f"${float(value):.2f}".rjust(width)

        t = Table()
        t.add_column("p", "P", width=8, kind="price")
        t.add_row({"p": 1.5})
        t.add_row({"p": 99.99})
        out = t.render()
        self.assertEqual(len(seen), 2)
        self.assertEqual(visible_len(out[-1]), 8)
        self.assertTrue(out[-1].endswith("99.99"))

    def test_register_kind_rejects_duplicate(self):
        # The decorator raises at *decoration* time (before the function
        # body runs) — wrap it in ``assertRaises`` to actually pin the
        # rejection. Without the wrap, the test crashes inside the
        # decorator without ever reaching its assertions.
        with self.assertRaises(ValueError):
            @register_kind("text")  # already registered at import time
            def _duplicate(value, width, attrs):
                return str(value)


class TableHeaderSeparatorTest(unittest.TestCase):
    def test_separator_matches_total_width(self):
        t = Table()
        t.add_column("a", "A", width=6, fit="fixed")
        t.add_column("b", "B", width=4, fit="fixed")
        t.add_row({"a": "abcdef", "b": "uvwx"})
        out = t.render()
        header, separator, *_ = out
        # Headers render with the configured ``header_style`` ANSI prefix,
        # so strip before asserting the literal starts-with.
        self.assertEqual(strip_ansi(header).strip().startswith("A"), True)
        self.assertEqual(visible_len(header), visible_len(separator))
        body_len = visible_len(out[-1])
        self.assertEqual(visible_len(separator), body_len)

    def test_header_truncates_when_longer_than_width(self):
        t = Table()
        t.add_column("a", "VERY_LONG_HEADER", width=6, fit="fixed")
        t.add_row({"a": "short"})
        out = t.render()
        header = out[0]
        self.assertEqual(visible_len(header), 6)
        self.assertEqual(strip_ansi(header).rstrip().endswith("..."), True)


class TableRowAccentTest(unittest.TestCase):
    def test_accent_wraps_non_preformatted_cells(self):
        t = Table()
        t.add_column("a", "A", width=6, fit="fixed")
        t.add_row({"a": "hi"}, accent="neon")
        out = t.render()
        body = out[-1]
        self.assertIn("\033[", body)
        self.assertEqual(visible_len(body), 6)

    def test_accent_does_not_double_color_preformatted_cells(self):
        t = Table()
        t.add_column("a", "A", width=8, fit="fixed", kind="preformatted")
        raw = c("yellow", "okay")
        t.add_row({"a": raw}, accent="neon")
        out = t.render()
        # No neon wrap on the preformatted cell — only its inner yellow
        # paints the visible string.
        self.assertNotIn("\033[38;5;51m", out[-1])
        self.assertIn("okay", strip_ansi(out[-1]))


class TableMissingKeysTest(unittest.TestCase):
    def test_missing_known_key_renders_empty_marker(self):
        t = Table()
        t.add_column("a", "A", width=4, fit="fixed", empty="—")
        t.add_row({})
        out = t.render()
        self.assertEqual(visible_len(out[-1]), 4)
        self.assertTrue(out[-1].lstrip().startswith("—"))

    def test_unknown_row_keys_dropped_silently(self):
        t = Table()
        t.add_column("a", "A", width=4, fit="fixed")
        t.add_row({"a": "ok", "extra": "noisy"})
        out = t.render()
        self.assertEqual(visible_len(out[-1]), 4)
        self.assertIn("ok", strip_ansi(out[-1]))
        self.assertNotIn("noisy", strip_ansi(out[-1]))


class TableNumberKindsTest(unittest.TestCase):
    def test_number_right_aligned(self):
        t = Table()
        t.add_column("n", "N", width=6, fit="fixed", kind="number")
        t.add_row({"n": 42})
        out = t.render()
        self.assertEqual(visible_len(out[-1]), 6)
        self.assertTrue(out[-1].rstrip().endswith("42"))

    def test_number_thousands_format(self):
        # ``thousands=True`` triggers the ``f"{n:,}"`` format — used by
        # ``swe providers MSGS``-style columns to render message counts.
        t = Table()
        t.add_column("n", "N", width=10, fit="fixed", kind="number",
                     thousands=True)
        t.add_row({"n": 1234567})
        out = t.render()
        body = out[-1].rstrip()
        # Comma-formatted, right-aligned in width 10.
        self.assertTrue(body.endswith("1,234,567"))
        self.assertEqual(visible_len(out[-1]), 10)

    def test_count_threshold_green_when_above(self):
        t = Table()
        t.add_column("n", "N", width=6, fit="fixed",
                     kind="count_threshold", threshold=10)
        t.add_row({"n": 1})
        t.add_row({"n": 99})
        out = t.render()
        self.assertEqual(len(out), 4)
        self.assertIn("\033[32m", out[-1])
        for line in out[2:]:
            self.assertEqual(visible_len(line), 6)

    def test_count_threshold_green_at_equal_boundary(self):
        # The threshold check is ``n >= threshold`` (inclusive). Pin the
        # boundary so a future ``>`` typo doesn't silently drift to
        # exclusive comparison.
        t = Table()
        t.add_column("n", "N", width=6, fit="fixed",
                     kind="count_threshold", threshold=10)
        t.add_row({"n": 10})  # exactly at the threshold
        out = t.render()
        body = out[-1]
        # Must be green (threshold comparison includes equality).
        self.assertIn("\033[32m", body)
        # And the rendered width is still 6.
        self.assertEqual(visible_len(body), 6)


class TableListKindTest(unittest.TestCase):
    def test_list_csv_with_color(self):
        t = Table()
        t.add_column("aliases", "ALIASES", width=14, fit="fixed",
                     kind="list", color="cyan", empty="—")
        t.add_row({"aliases": ["a", "b", "c"]})
        out = t.render()
        body = out[-1]
        self.assertEqual(visible_len(body), 14)
        self.assertIn("\033[36m", body)
        self.assertIn("a, b, c", strip_ansi(body))

    def test_list_empty_renders_em_dash(self):
        t = Table()
        t.add_column("aliases", "ALIASES", width=6, fit="fixed",
                     kind="list", empty="—")
        t.add_row({"aliases": []})
        out = t.render()
        self.assertEqual(visible_len(out[-1]), 6)
        self.assertIn("—", strip_ansi(out[-1]))


class TableTimestampKindTest(unittest.TestCase):
    def test_timestamp_left_aligned(self):
        # ``formatter`` is now a per-column kwarg (called with the raw
        # row value) so callers don't have to wrap every row in
        # ``(seconds, lambda)`` tuples.
        t = Table()
        t.add_column(
            "t", "T", width=10, fit="fixed", kind="timestamp",
            formatter=lambda secs: f"{secs // 60}m ago",
        )
        t.add_row({"t": 300})
        out = t.render()
        self.assertEqual(visible_len(out[-1]), 10)
        self.assertTrue(out[-1].startswith("5m ago"))


class TableEmptyTableTest(unittest.TestCase):
    def test_render_with_no_columns_returns_empty(self):
        t = Table()
        out = t.render()
        self.assertEqual(out, [])

    def test_render_with_no_rows_still_emits_header_separator(self):
        t = Table()
        t.add_column("a", "A", width=4, fit="fixed")
        out = t.render()
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
