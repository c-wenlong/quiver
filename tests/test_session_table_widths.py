"""``swe session`` sizes IDX, LAST ACTIVE and AGENT to the run, not a worst case.

Those three columns used to be pinned at 4 / 14 / 14. A listing of Codex and
Claude rows then spent 14 columns on an AGENT field whose longest value was 11
and 3 more on a LAST ACTIVE field no stamp fills, and a 3-digit index overflowed
the 4-wide IDX cell and shifted every column after it.
"""

import unittest
from unittest.mock import patch

from quiver.console import strip_ansi, visible_len
from quiver.sessions.commands import _build_session_table
from quiver.sessions.models import Session

WIDE = 200


def _session(agent="Codex CLI", age_ms=0, title="a title", now_ms=1_700_000_000_000):
    return Session(
        timestamp=now_ms - age_ms,
        agent=agent,
        path="/Users/kaichen/project",
        title=title,
        session_id="sid",
        tool_name="codex",
    )


def _render(sessions, reserve=0, cap=WIDE, now_ms=1_700_000_000_000, statuses=None):
    with patch("quiver.sessions.commands.time.time", return_value=now_ms / 1000.0), \
         patch("quiver.sessions.commands.terminal_width", return_value=cap), \
         patch("quiver.table.terminal_width", return_value=cap):
        return _build_session_table(sessions, reserve=reserve, statuses=statuses).render()


LABELS = ("[#]", "LAST ACTIVE", "AGENT", "ST", "DIRECTORY", "TITLE/SUMMARY")


def _widths(header):
    """Each column's rendered width, read off the header line.

    Every header cell is ``label`` padded out to its column, and the cells are
    joined by a two-space gap, so the distance between two labels' start
    offsets is the first one's width plus that gap.
    """
    plain = strip_ansi(header)
    starts, pos = [], 0
    for label in LABELS:
        pos = plain.index(label, pos)
        starts.append(pos)
        pos += len(label)
    out = [b - a - 2 for a, b in zip(starts, starts[1:])]
    out.append(len(plain) - starts[-1])
    return dict(zip(LABELS, out))


class SessionTableWidthTest(unittest.TestCase):
    def test_agent_column_fits_the_longest_agent_present(self):
        lines = _render([_session("Codex CLI"), _session("Claude Code")])
        # "Claude Code" is 11; the old fixed 14 left 3 dead columns.
        self.assertEqual(11, _widths(lines[0])["AGENT"])

    def test_agent_column_never_shrinks_below_its_header(self):
        lines = _render([_session("Pi")])
        self.assertEqual(len("AGENT"), _widths(lines[0])["AGENT"])

    def test_agent_column_grows_for_a_long_agent_name(self):
        lines = _render([_session("Codex CLI"), _session("GitHub Copilot")])
        self.assertEqual(len("GitHub Copilot"), _widths(lines[0])["AGENT"])

    def test_time_column_fits_its_header_when_no_stamp_is_longer(self):
        # "Just now" (8) is the longest stamp any of these produce, so the
        # header is what sets the width.
        lines = _render([_session(age_ms=0), _session(age_ms=5 * 60_000)])
        self.assertEqual(len("LAST ACTIVE"), _widths(lines[0])["LAST ACTIVE"])

    def test_idx_column_fits_its_header_for_a_short_listing(self):
        lines = _render([_session() for _ in range(9)])
        self.assertEqual(len("[#]"), _widths(lines[0])["[#]"])

    def test_tightening_narrows_the_row(self):
        # The old layout charged a fixed 4 + 14 + 14 for IDX, LAST ACTIVE and
        # AGENT; one Codex row needs 3 + 11 + 9, so the whole row is 9
        # columns shorter with the free-text columns unchanged. The ST
        # glyph column was added since, costing its 2-char header + 2 gap.
        lines = _render([_session("Codex CLI")])
        old = 4 + 2 + 14 + 2 + 14 + 2 + 45 + 2 + 50
        self.assertEqual(old - 9 + 2 + len("ST"), visible_len(lines[0]))

    def test_explicit_status_renders_its_glyph_in_colour(self):
        lines = _render([_session()], statuses=["followup"])
        header = strip_ansi(lines[0])
        self.assertIn("ST", header)
        # followup is a yellow "?"; the cell is cpad'd to the column width.
        self.assertIn("\033[33m? ", lines[2])
        # The column is a single glyph padded to the 2-char header.
        self.assertEqual(len("ST"), _widths(header)["ST"])

    def test_status_column_floors_at_its_header(self):
        lines = _render([_session()], statuses=[""])
        self.assertEqual(len("ST"), _widths(lines[0])["ST"])
        self.assertIn(" - ", strip_ansi(lines[2]))

    def test_every_row_matches_the_header_width(self):
        lines = _render([_session("GitHub Copilot", age_ms=3 * 86_400_000),
                         _session("Pi", age_ms=0),
                         _session("Claude Code", age_ms=90 * 60_000)])
        width = visible_len(lines[0])
        for i, line in enumerate(lines):
            self.assertEqual(width, visible_len(line), f"line {i} drifted")

    def test_three_digit_indices_keep_the_grid(self):
        # The regression this replaces: "[100]" is 5 visible columns and the
        # IDX cell was padded to a hardcoded 4, so every column after it
        # shifted right by one from row 100 on.
        lines = _render([_session() for _ in range(120)])
        width = visible_len(lines[0])
        for i, line in enumerate(lines):
            self.assertEqual(width, visible_len(line), f"line {i} drifted")
        self.assertEqual(len("[120]"), _widths(lines[0])["[#]"])

    def test_reserve_leaves_room_for_the_picker_pointer(self):
        lines = _render([_session()], reserve=2, cap=100)
        for line in lines:
            self.assertLessEqual(visible_len(line) + 2, 100)

    def test_narrow_terminal_still_fits(self):
        lines = _render([_session("GitHub Copilot")], cap=90)
        for line in lines:
            self.assertLessEqual(visible_len(line), 90)


if __name__ == "__main__":
    unittest.main()
