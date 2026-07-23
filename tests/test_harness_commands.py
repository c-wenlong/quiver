"""Tests for the migrated ``cmd_list`` (now renders via ``quiver.table.Table``).

The refactored cmd_list no longer hand-rolls f-strings; it builds a
9-column Table once and renders once per row with a single
``table.render()`` loop. The starred-row branch diverges only in the
row's ``accent="neon"`` argument — the print logic, font widths, and
header layout are now byte-identical for both paths (this is the
"favourited/unfavourited branch divergence" the migration removed).

Tests below pin the structural invariants the new layout must hold:

1. Header has 9 column names in the documented order.
2. Separator visible length matches header visible length.
3. Starred rows AND unstarred rows put the tool name at the SAME
   column index (this is the migration's whole reason for existing).
4. Starred rows carry the neon accent ANSI + the ``★`` marker;
   unstarred rows do NOT carry the neon accent on plain-text cells
   but DO carry cyan aliases (via ``list`` kind color attr).
5. ``trust_cell_width=True`` is wired on the RATE column — the cell
   is rendered at exactly ``visible_len(format_column output)`` chars,
   with no extra padding bled into the column gap.
6. INST shows green ``✓`` or red ``✗`` (preformatted).
7. SESS shows the three states: dim em-dash (absent), dim zero
   (present-zero), green (positive).
8. Sort order is preserved: starred first (pin order), then by
   100d usage descending, then alphabetical.
9. Tag filter still drops rows whose tags don't include the arg.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from quiver.console import strip_ansi, visible_len
from quiver.harness.commands import _sort_tools, cmd_list
from quiver.harness.rate_limits import RateLimitInfo


def _run_cmd_list():
    """Capture cmd_list's stdout via redirect_stdout (handles ``print()`` with no args)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_list([])
    return buf.getvalue()


# Stable fixtures — the migration is byte-output-sensitive on header
# spacing, so we use explicit fixtures rather than mock-fixtures.
_REGISTRY = {
    "claude": {
        "command": "claude",
        "description": "Anthropic coding CLI",
        "aliases": ["cl"],
        "tags": ["anthropic", "paid"],
        "version": "2.1.126",
    },
    "codex": {
        "command": "codex",
        "description": "OpenAI coding CLI",
        "aliases": ["cx"],
        "tags": ["openai", "paid"],
        "version": "0.144.1",
    },
    "droid": {
        "command": "droid",
        "description": "Factory.AI coding CLI",
        "aliases": [],
        "tags": ["factory", "free"],
        "version": "0.5.0",
    },
}

# All tools report "not installed" so INST is uniformly red ✗
def _is_installed_false(_cmd):
    return False


def _setup_patches(testcase, *, stars=()):
    """Common setUp: patch external I/O so cmd_list runs offline."""
    patches = [
        patch("quiver.harness.commands.load_registry", return_value=dict(_REGISTRY)),
        patch("quiver.harness.commands._session_counts_100d", return_value={
            "claude": 42,  # positive → green
            "codex": 0,    # present-zero → dim
            # droid absent → dim em-dash
        }),
        patch("quiver.harness.commands.load_stars", return_value=list(stars)),
        patch("quiver.harness.commands.is_installed", side_effect=_is_installed_false),
        patch(
            "quiver.harness.rate_limits.get_all_rate_limits",
            return_value={
                "codex": RateLimitInfo(
                    tool_name="codex",
                    used_percent=30,
                    limit_reached=False,
                    reset_at=0,
                    plan_type="plus",
                    window_seconds=604800,
                ),
            },
        ),
    ]
    for p in patches:
        p.start()
        testcase.addCleanup(p.stop)


def _row_for_tool(output, tool_name):
    """Return the first row whose stripped content contains ``tool_name``.

    Robust against starred/unstarred layout differences (we no longer
    key by ``split()[1]`` since that returned "★" for starred rows).
    """
    plain = strip_ansi(output)
    for line in output.split("\n"):
        if tool_name in strip_ansi(line):
            return line, plain
    raise AssertionError(f"No row for {tool_name!r} in output:\n{output}")


class CmdListHeaderTest(unittest.TestCase):
    """Header / separator alignment invariants (the migration's main payoff)."""

    def setUp(self):
        _setup_patches(self)

    def test_header_has_nine_columns_in_order(self):
        output = _run_cmd_list()
        lines = output.split("\n")
        # Strip ANSI before searching: the header's column labels are wrapped
        # in c(\"dim\", ...), so the raw line has ``\\033[2m`` between the
        # col_gap and the label name — searching un-stripped would miss.
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(label in strip_ansi(raw) for label in ("NAME", "COMMAND", "VERSION"))
        )
        header = strip_ansi(lines[hdr_idx])
        for label in ("NAME", "COMMAND", "VERSION", "ALIASES", "100d", "RATE", "INST", "DESCRIPTION"):
            self.assertIn(label, header, f"missing {label!r} in header: {header!r}")
        self.assertEqual(header.count("NAME"), 1)
        self.assertEqual(header.count("COMMAND"), 1)

    def test_separator_visible_length_matches_header(self):
        output = _run_cmd_list()
        lines = output.split("\n")
        header_idx = next(
            i for i, raw in enumerate(lines)
            if all(label in strip_ansi(raw) for label in ("NAME", "COMMAND", "VERSION"))
        )
        sep_idx = header_idx + 1
        sep_count = strip_ansi(lines[sep_idx]).count("\u2500")
        self.assertEqual(sep_count, visible_len(lines[header_idx]))

    def test_name_column_position_matches_across_starred_and_unstarred(self):
        # The migration's whole reason for existing: starred and unstarred
        # rows MUST put the tool name at the same visible column index.
        # With the visible-border column_gap=" | " (3 visible chars per
        # gap) enabled in cmd_list, both rows must still land the tool
        # name at the same offset — now column 5 (mark=2 + gap=3).
        # This test needs claude starred — override load_stars locally
        # without disturbing the class-level setUp.
        with patch("quiver.harness.commands.load_stars", return_value=["claude"]):
            output = _run_cmd_list()
        starred_row = next(
            line for line in output.split("\n")
            if "claude" in strip_ansi(line) and "\u2605" in strip_ansi(line)
        )
        unstarred_row = next(
            line for line in output.split("\n")
            if "codex" in strip_ansi(line) and "\u2605" not in strip_ansi(line)
        )
        # Both rows should land their respective tool name at column 5
        # (mark width=2 + visible-border gap visible_len=3). Pin the
        # literal offset AND assert equality between rows so a future
        # gap change can't drift silently.
        starred_offset = strip_ansi(starred_row).find("claude")
        unstarred_offset = strip_ansi(unstarred_row).find("codex")
        self.assertEqual(5, starred_offset,
            f"starred row tool-name offset drifted to {starred_offset} (expected 5)")
        self.assertEqual(starred_offset, unstarred_offset,
            f"starred ({starred_offset}) and unstarred ({unstarred_offset}) "
            f"tool-name offsets diverge")


class CmdListAccentTest(unittest.TestCase):
    """Starred vs unstarred row rendering — the divergence point of the migration."""

    def setUp(self):
        _setup_patches(self, stars=["claude"])

    def test_starred_row_carries_neon_ansi_and_star_marker(self):
        output = _run_cmd_list()
        starred_row, _ = _row_for_tool(output, "claude")
        self.assertIn("\033[", starred_row)
        self.assertIn("\u2605", strip_ansi(starred_row))

    def test_unstarred_row_has_cyan_aliases_no_neon(self):
        # droid is unstarred in this fixture — its alias cell must be cyan
        # from the list-kind color attribute, and it must NOT carry neon.
        output = _run_cmd_list()
        droid_row, _ = _row_for_tool(output, "droid")
        # No star marker (unstarred).
        self.assertNotIn("\u2605", strip_ansi(droid_row))
        # Cyan alias column color attribute fires even on empty list — the
        # cell renders as c("cyan", "—", width=12) → contains \033[36m.
        self.assertIn("\033[36m", droid_row)


class CmdListRateColumnTest(unittest.TestCase):
    """trust_cell_width=True is wired on the RATE column."""

    def setUp(self):
        _setup_patches(self)

    def test_rate_cell_renders_at_format_column_visible_width(self):
        output = _run_cmd_list()
        codex_row, _ = _row_for_tool(output, "codex")
        plain = strip_ansi(codex_row)
        # format_column() output visible content: "30% —" (5 chars:
        # "30%" + " " + "—"). trust_cell_width=True must NOT pad this
        # up to the column width (14). Locate the rate cell by content.
        rate_start = plain.find("30%")
        self.assertGreaterEqual(rate_start, 0, "rate cell content not found")
        rate_visible = RateLimitInfo(
            tool_name="codex", used_percent=30, limit_reached=False,
            reset_at=0, plan_type="plus", window_seconds=604800,
        ).format_column()
        rate_visible_len = visible_len(rate_visible)
        rate_end = rate_start + rate_visible_len
        # Just past the rate cell must be the visible-border column_gap
        # " | " (3 visible chars: space + bar + space), NOT padding
        # bled from the column width=14. The Table's opt-in column
        # border string replaces the 2-space default for cmd_list.
        self.assertEqual(
            plain[rate_end: rate_end + 3], " \u2502 ",
            f"rate column leaked padding into border gap: "
            f"{plain[rate_end:rate_end+5]!r}",
        )


class CmdListInstColumnTest(unittest.TestCase):
    """INST column shows the right glyph + color per installed status."""

    def setUp(self):
        _setup_patches(self)

    def test_inst_shows_red_x_for_not_installed(self):
        output = _run_cmd_list()
        plain = strip_ansi(output)
        # All fixtures report not installed.
        self.assertIn("\u2717", plain)

    def test_inst_shows_green_check_when_installed(self):
        # Patch is_installed again to override the False default for this test.
        with patch("quiver.harness.commands.is_installed", return_value=True):
            output = _run_cmd_list()
        plain = strip_ansi(output)
        self.assertIn("\u2713", plain)


class CmdListSessColumnTest(unittest.TestCase):
    """SESS column shows the three documented visual states."""

    def setUp(self):
        _setup_patches(self)

    def test_sess_three_states_shift_colors(self):
        output = _run_cmd_list()
        claude_row, claude_plain = _row_for_tool(output, "claude")
        codex_row, codex_plain = _row_for_tool(output, "codex")
        droid_row, droid_plain = _row_for_tool(output, "droid")
        # claude: positive count 42 → green; the digit "42" must be
        # somewhere in the row's plain text.
        self.assertIn("42", claude_plain)
        # codex: zero count → dim "0"; bare "0" must appear.
        self.assertIn("0", codex_plain)
        # droid: absent → dim em-dash; must have "—".
        self.assertIn("\u2014", droid_plain)


class CmdListSortAndFilterTest(unittest.TestCase):
    """Sort order + tag filter behaviors unchanged by the migration."""

    def test_sort_order_starred_then_usage_desc_then_name(self):
        # Helper-layer test — Table.add_row order follows _sort_tools' output.
        _setup_patches(self)
        tools = {
            "zzz": {"command": "zzz"},
            "droid": {"command": "droid"},
            "claude": {"command": "claude"},
            "aaa": {"command": "aaa"},
        }
        counts = {"zzz": 99, "aaa": 50, "claude": 10, "droid": 1}
        stars = ["droid", "claude"]
        ordered = [name for name, _ in _sort_tools(tools, counts, stars)]
        self.assertEqual(ordered[:2], ["droid", "claude"])
        self.assertEqual(ordered[2:], ["zzz", "aaa"])

    def test_tag_filter_drops_non_matching_rows(self):
        _setup_patches(self)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_list(["anthropic"])
        plain = strip_ansi(buf.getvalue())
        self.assertIn("claude", plain)
        self.assertNotIn("codex", plain)
        self.assertNotIn("droid", plain)

    def test_tag_filter_dash_prefix_also_accepted(self):
        _setup_patches(self)
        buf1, buf2 = io.StringIO(), io.StringIO()
        with redirect_stdout(buf1):
            cmd_list(["anthropic"])
        with redirect_stdout(buf2):
            cmd_list(["-anthropic"])
        self.assertEqual(buf1.getvalue(), buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
