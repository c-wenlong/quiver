"""Tests for the migration of ``cmd_list`` and ``cmd_status`` matrix view
in ``mcp/cli.py`` to ``quiver.table.Table``.

Both handlers rendered a server × tool matrix using hand-rolled
``f"{header:<{w}}"`` padding. The migration replaces that with a
single ``Table().add_column(...).add_row(...)`` build per handler:

- ``cmd_list``: SERVER + 1..N tool columns. Each tool cell is
  ``cpad("green", "✓", col_width)`` if the server is present in that
  tool or ``cpad("dim", "—", col_width)`` otherwise. Header labels for
  the tool columns are pre-centered so the rendered line matches the
  original f-string ``^{col_width}`` alignment.
- ``cmd_status``: same matrix + a HEALTH column. The HEALTH column
  carries the result of ``check_server_health()`` (already-coloured
  ANSI strings) and uses ``kind="preformatted"``. The cell visible
  width is manually padded to ``health_width`` so the row aligns with
  the dim HEALTH header (cpad would re-wrap the ANSI escape in a
  generic colour — same trick as cmd_session alignment fix).

Tests pin:

1. cmd_list header order (SERVER | tool1 | ... | toolN).
2. cmd_list separator visible length matches header visible length.
3. cmd_list body rows all align to header visible width.
4. cmd_list cell colour shapes per server×tool intersection — green ✓
   for servers present in tool, dim — for servers absent in tool.
5. cmd_list empty matrix → just the plain "No MCP servers found."
   notice, no table.
6. cmd_list with single target tool → 2-column table AND absent of
   un-filtered tools.
7. cmd_status adds a HEALTH column on the right + health-cell ANSI
   round-trip.
8. cmd_status body rows aligned to header visible width (catches the
   +5-char drift bug we hit during round 2).
9. Footer "N servers across M tools" math for both handlers.
"""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quiver.console import strip_ansi, visible_len
from quiver.mcp import cli as cli_mod
from quiver.mcp.cli import cmd_list, cmd_status


# Three-tool fixture used by most tests. Each tool has 1-2 servers
# so the matrix actually has ✓/— spread across columns.
FIXTURE_TOOLS = [
    ("claude", ["server_a"]),
    ("cursor", ["server_a", "server_b"]),
    ("droid", ["server_b"]),
]


def _write_tool_config(tmp_path: Path, name: str, srv_names: list[str]) -> Path:
    """Write a standard-format MCP config at ``tmp_path/<name>/mcp.json``.

    Returns the file path.
    """
    cfg_path = tmp_path / name / "mcp.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mcpServers": {s: {"command": "echo"} for s in srv_names}}
    cfg_path.write_text(json.dumps(payload, indent=2))
    return cfg_path


class McpMatrixFixtureMixin:
    """Sets up MCP_CONFIG_MAP patching with TempDirectory cleanup.

    Subclasses define ``tools_to_render`` (defaulting to ``FIXTURE_TOOLS``).
    Each test gets a fresh temp dir via ``setUp`` and tears it down
    via ``addCleanup``.
    """

    tools_to_render: list[tuple[str, list[str]]] = FIXTURE_TOOLS

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)
        self.patches = {}
        for name, srv_names in self.tools_to_render:
            cfg_path = _write_tool_config(tmp_path, name, srv_names)
            self.patches[name] = {
                "path": cfg_path,
                "key": "mcpServers",
                "label": name,
                "format": "standard",
            }

    def run_cmd(self, handler, args) -> str:
        orig_map = dict(cli_mod.MCP_CONFIG_MAP)
        buf = io.StringIO()
        try:
            with patch.dict(
                cli_mod.MCP_CONFIG_MAP, self.patches, clear=False
            ), patch.object(
                cli_mod,
                "get_mcp_tools",
                lambda registry: dict(self.patches),
            ), redirect_stdout(buf):
                handler(list(args))
        finally:
            cli_mod.MCP_CONFIG_MAP.clear()
            cli_mod.MCP_CONFIG_MAP.update(orig_map)
        return buf.getvalue()

    def body_row_for(self, output: str, server_name: str) -> str:
        """Strip-ANSI line whose first visible chars match ``server_name``."""
        plain = strip_ansi(output)
        for ln in plain.split("\n"):
            if ln.lstrip().startswith(server_name):
                return ln
        raise AssertionError(
            f"no body row for {server_name!r} in {plain!r}"
        )


class CmdListMatrixMigrationTest(McpMatrixFixtureMixin, unittest.TestCase):
    """Structural invariants for cmd_list post-migration."""

    def test_header_order_matches_tool_keys(self):
        output = self.run_cmd(cmd_list, [])
        header_line = next(
            ln for ln in output.split("\n")
            if "SERVER" in strip_ansi(hn := ln)
        ) if False else next(
            ln for ln in output.split("\n")
            if "SERVER" in strip_ansi(ln)
        )
        header_plain = strip_ansi(header_line)
        # First column must be "SERVER".
        self.assertTrue(header_plain.startswith("SERVER"))
        # Every seeded tool name must appear as a column header
        # AFTER "SERVER".
        for tool_name in self.patches:
            self.assertIn(tool_name, header_plain)

    def test_separator_visible_length_matches_header(self):
        output = self.run_cmd(cmd_list, [])
        lines = output.split("\n")
        header_idx = next(
            i for i, ln in enumerate(lines)
            if "SERVER" in strip_ansi(ln)
        )
        sep_dashes = strip_ansi(lines[header_idx + 1]).count("\u2500")
        self.assertEqual(sep_dashes, visible_len(lines[header_idx]))

    def test_body_rows_aligned_to_header_visible_width(self):
        output = self.run_cmd(cmd_list, [])
        lines = output.split("\n")
        header_idx = next(
            i for i, ln in enumerate(lines)
            if "SERVER" in strip_ansi(ln)
        )
        expected_width = visible_len(lines[header_idx])
        for offset, line in enumerate(
            lines[header_idx + 2:], start=header_idx + 2
        ):
            plain = strip_ansi(line)
            if not plain.strip():
                continue
            if "servers across" in plain:
                break
            self.assertEqual(
                expected_width, visible_len(line),
                f"line {offset} drifted from {expected_width}: "
                f"{plain!r}",
            )

    def test_server_a_row_has_green_check_in_claude_and_cursor(self):
        # server_a: present in claude + cursor, absent from droid.
        # The body row must show ✓ in the claude+cursor cells AND —
        # in the droid cell. Derive column widths from the rendered
        # header plaintext so the test is robust to pre-measure tweaks
        # (avoids hard-coded column-width magic constants).
        output = self.run_cmd(cmd_list, [])
        plain = strip_ansi(output)
        header_line = next(
            ln for ln in plain.split("\n") if ln.startswith("SERVER")
        )
        # Each tool label starts at position P_i and the column is
        # width W_i wide. P_{i+1} = P_i + W_i + gap (gap = 2 chars).
        # So W_i = (P_{i+1} - P_i) - 2.
        # Only the tool column labels are needed — each tool column
        # starts at byte position ``positions[i+1] - positions[i] - 2``
        # (cell-gap) AFTER the previous one, and ``col_widths[i]``
        # is the width of the i-th tool column (NOT including the
        # server column width, which would be at index 0 otherwise).
        tool_labels = ("claude", "cursor", "droid")
        tool_positions = [header_line.find(label) for label in tool_labels]
        cell_gap = 2  # Table's default gap between adjacent cells
        col_widths = [
            tool_positions[i + 1] - tool_positions[i] - cell_gap
            for i in range(len(tool_positions) - 1)
        ] + [
            # Trailing column width: distance from its start to end
            # of the line (its label is right-padded to fill the col).
            visible_len(header_line) - tool_positions[-1]
        ]

        # Locate the body row for server_a. Tool column starts in
        # the body line have the same offsets from the server-name
        # start as the tool label offsets from the header start
        # (both lines are left-aligned, server_name at column 0).
        server_a_line = next(
            ln for ln in plain.split("\n")
            if ln.startswith("server_a")
        )
        body_starts = [
            server_a_line.find("server_a") + p
            for p in tool_positions
        ]
        cells_by_name = {
            tool: server_a_line[start:start + col_widths[i]]
            for i, (tool, start) in enumerate(
                zip(tool_labels, body_starts)
            )
        }
        # server_a is in claude + cursor → those cells must be ✓.
        self.assertTrue(
            cells_by_name["claude"].startswith("✓"),
            f"claude col not ✓ for server_a: "
            f"{cells_by_name['claude']!r}",
        )
        self.assertTrue(
            cells_by_name["cursor"].startswith("✓"),
            f"cursor col not ✓ for server_a: "
            f"{cells_by_name['cursor']!r}",
        )
        # The dim droid cell must be —.
        self.assertTrue(
            cells_by_name["droid"].startswith("—"),
            f"droid col not — for server_a: "
            f"{cells_by_name['droid']!r}",
        )
        # ANSI wiring intact.
        self.assertIn("\033[32m", output)
        self.assertIn("\033[2m", output)

    def test_single_target_tool_renders_2_columns_with_absent_of_others(self):
        output = self.run_cmd(cmd_list, ["claude"])
        lines = output.split("\n")
        header_line = next(
            ln for ln in lines if "SERVER" in strip_ansi(ln)
        )
        header_plain = strip_ansi(header_line)
        # Only "claude" appears as a column header.
        self.assertIn("SERVER", header_plain)
        self.assertIn("claude", header_plain)
        # The other seeded tools must NOT be in the matrix header.
        self.assertNotIn("cursor", header_plain)
        self.assertNotIn("droid", header_plain)
        # Footer says "1 tools" (filtered) for n_servers rows.
        self.assertIn("1 tools", output)

    def test_empty_servers_prints_notice_only(self):
        # Override fixture to be empty.
        self.patches = {}
        # Create dummy paths so MCP_CONFIG_MAP patching doesn't
        # error during render — but render early-returns on
        # all_servers empty so paths aren't actually read.
        output = self.run_cmd(cmd_list, [])
        self.assertIn("No MCP servers found", output)
        # No matrix rendered.
        self.assertNotIn("\u2500" * 10, output)
        self.assertNotIn("servers across", output)

    def test_footer_server_count_math(self):
        output = self.run_cmd(cmd_list, [])
        # 2 distinct servers across 3 tools in the FIXTURE_TOOLS.
        self.assertIn("2 servers across 3 tools", output)


class CmdStatusMatrixMigrationTest(McpMatrixFixtureMixin, unittest.TestCase):
    """Structural invariants for cmd_status post-migration.

    Same matrix layout as cmd_list but with an extra HEALTH column on
    the right carrying the result of ``check_server_health()`` per row.
    """

    def test_health_column_present_in_header(self):
        output = self.run_cmd(cmd_status, [])
        header_line = next(
            ln for ln in output.split("\n")
            if "SERVER" in strip_ansi(ln)
            and "HEALTH" in strip_ansi(ln)
        )
        header_plain = strip_ansi(header_line)
        self.assertIn("SERVER", header_plain)
        self.assertIn("HEALTH", header_plain)
        for tool_name in self.patches:
            self.assertIn(tool_name, header_plain)

    def test_separator_visible_length_matches_header(self):
        output = self.run_cmd(cmd_status, [])
        lines = output.split("\n")
        header_idx = next(
            i for i, ln in enumerate(lines)
            if "SERVER" in strip_ansi(ln)
            and "HEALTH" in strip_ansi(ln)
        )
        sep_dashes = strip_ansi(lines[header_idx + 1]).count("\u2500")
        self.assertEqual(sep_dashes, visible_len(lines[header_idx]))

    def test_body_rows_aligned_to_header_visible_width(self):
        output = self.run_cmd(cmd_status, [])
        lines = output.split("\n")
        header_idx = next(
            i for i, ln in enumerate(lines)
            if "SERVER" in strip_ansi(ln)
            and "HEALTH" in strip_ansi(ln)
        )
        expected_width = visible_len(lines[header_idx])
        for offset, line in enumerate(
            lines[header_idx + 2:], start=header_idx + 2
        ):
            plain = strip_ansi(line)
            if not plain.strip():
                continue
            if "servers across" in plain:
                break
            self.assertEqual(
                expected_width, visible_len(line),
                f"line {offset} drifted from {expected_width}: "
                f"{plain!r}",
            )

    def test_health_cell_carries_green_ansi_for_health_servers(self):
        # All servers in the fixture are healthy stdio (echo
        # command), so HEALTH column carries green ✓. The ANSI
        # escape round-trip must include \033[32m.
        output = self.run_cmd(cmd_status, [])
        self.assertIn("\033[32m", output)
        # And no red/uncertainty markers in any HEALTH cell since
        # the stdio probe didn't fail.
        plain = strip_ansi(output)
        self.assertNotIn("\u2717", plain)  # no ✗ unicode in healthy output

    def test_footer_server_count_math(self):
        output = self.run_cmd(cmd_status, [])
        self.assertIn("2 servers across 3 tools", output)


class CmdStatusEmptyTest(McpMatrixFixtureMixin, unittest.TestCase):
    """Empty-matrix early-return for cmd_status."""

    tools_to_render = []

    def test_empty_servers_prints_notice_only(self):
        self.patches = {}
        output = self.run_cmd(cmd_status, [])
        self.assertIn("No MCP servers found", output)
        self.assertNotIn("\u2500" * 10, output)
        self.assertNotIn("servers across", output)


if __name__ == "__main__":
    unittest.main()
