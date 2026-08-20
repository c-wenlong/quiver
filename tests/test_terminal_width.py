"""Output fits the window it is printed into.

One wrapped row does not just look bad, it breaks every row after it: the
columns no longer line up, so a table becomes unreadable from the first
overflow onward. Long free-text fields are therefore truncated to the
width actually available rather than to a number picked in advance.

Authored help text is deliberately exempt. Truncating a documented
example would hand the reader a command that does not run.
"""

import unittest
from unittest import mock

from quiver.console import fit_widths, strip_ansi, terminal_width
from quiver.table import Table


class FitWidthsTest(unittest.TestCase):
    def test_a_wide_window_leaves_everything_alone(self):
        self.assertEqual(fit_widths(40, {"a": 45, "b": 50}, cap=400),
                         {"a": 45, "b": 50})

    def test_it_takes_from_the_widest_column_first(self):
        out = fit_widths(0, {"short": 20, "long": 60}, gap=0, cap=70)
        self.assertEqual(out["short"], 20, "narrowed the wrong column")
        self.assertEqual(out["long"], 50)

    def test_it_stops_at_the_floor_rather_than_vanishing(self):
        out = fit_widths(0, {"a": 40}, gap=0, minimum=12, cap=1)
        self.assertEqual(out["a"], 12)

    def test_the_fixed_part_counts_against_the_budget(self):
        narrow = fit_widths(60, {"a": 40}, gap=0, cap=80)
        wide = fit_widths(0, {"a": 40}, gap=0, cap=80)
        self.assertLess(narrow["a"], wide["a"])

    def test_gaps_count_too(self):
        no_gap = fit_widths(0, {"a": 30, "b": 30}, gap=0, cap=60)
        gapped = fit_widths(0, {"a": 30, "b": 30}, gap=10, cap=60)
        self.assertLess(sum(gapped.values()), sum(no_gap.values()))

    def test_no_flexible_columns_is_not_an_error(self):
        self.assertEqual(fit_widths(100, {}, cap=10), {})


class TableClampTest(unittest.TestCase):
    def _table(self, cap, flex_width=120):
        t = Table(max_total_width=cap)
        t.add_column("fixed", "F", width=10, kind="preformatted",
                     trust_cell_width=True)
        t.add_column("flex", "FLEX", width=flex_width, kind="text")
        t.add_row({"fixed": "x" * 10, "flex": "y" * 200})
        return [strip_ansi(line) for line in t.render()]

    def test_the_row_fits_the_cap(self):
        for line in self._table(cap=60):
            self.assertLessEqual(len(line), 60, repr(line[:40]))

    def test_the_separator_matches_the_header(self):
        header, sep, *_ = self._table(cap=60)
        self.assertEqual(len(sep), len(header))

    def test_a_generous_cap_does_not_shrink_anything(self):
        header, *_ = self._table(cap=400, flex_width=120)
        self.assertGreaterEqual(len(header), 120)

    def test_columns_that_pad_their_own_cells_are_left_alone(self):
        """Narrowing one would shift every column after it, because the
        cell keeps the width the caller already gave it."""
        t = Table(max_total_width=20)
        t.add_column("a", "A", width=40, kind="preformatted",
                     trust_cell_width=True)
        t.add_row({"a": "z" * 40})
        # It overflows rather than corrupting the grid.
        self.assertGreaterEqual(len(strip_ansi(t.render()[0])), 40)

    def test_it_measures_the_terminal_when_no_cap_is_given(self):
        with mock.patch("quiver.table.terminal_width", return_value=50):
            t = Table()
            t.add_column("flex", "FLEX", width=200, kind="text")
            t.add_row({"flex": "y" * 300})
            self.assertLessEqual(len(strip_ansi(t.render()[0])), 50)


class TerminalWidthTest(unittest.TestCase):
    def test_an_implausible_width_falls_back(self):
        with mock.patch("shutil.get_terminal_size") as size:
            size.return_value = mock.Mock(columns=0)
            self.assertEqual(terminal_width(default=146), 146)

    def test_a_real_width_is_used(self):
        with mock.patch("shutil.get_terminal_size") as size:
            size.return_value = mock.Mock(columns=99)
            self.assertEqual(terminal_width(), 99)

    def test_a_failure_to_measure_does_not_raise(self):
        with mock.patch("shutil.get_terminal_size", side_effect=OSError):
            self.assertEqual(terminal_width(default=120), 120)


if __name__ == "__main__":
    unittest.main()
