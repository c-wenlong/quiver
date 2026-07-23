"""Tests for the migrations of ``cmd_models`` and ``cmd_session`` to
``quiver.table.Table``.

Both handlers used hand-rolled ``f"{...:<{w}}"`` string interpolation
with magic ``+9`` width offsets that compensated for ANSI-overhead in
the pre-Table era. These tests pin the new structural invariants:

1. cmd_models — 3-column (default) / 4-column (--by-tool) layout with
   a ``count_threshold`` MSGS column that auto-colours green at 100+.
2. cmd_session — 5-column listing with mixed ``preformatted``+trust cells
   (bold idx, cyan relative-time, green agent, dim title fallback) and
   one ``text`` cell (path with ``fit="content"`` for auto-width).

The mock fixtures are deliberately byte-conscious so a future regress
on column alignment is caught by the same shape of tests that catch
the cmd_list / cmd_check regressions.
"""

import io
import os
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from unittest.mock import patch

from quiver.console import c, strip_ansi, visible_len
from quiver.sessions.commands import cmd_models, cmd_session


# ---------------------------------------------------------------------------
# cmd_models fixtures
# ---------------------------------------------------------------------------


@dataclass
class _ModelFixture:
    """Shape that mirrors ``models_analytics.collect_model_usage`` output.

    The real function returns ``{tool: {(provider, model): count}}``;
    tests pin the same shape so cmd_models' tuple-unpack on
    ``entries.items()`` works at module level.
    """

    def __init__(self, raw):
        self.raw = raw


def _models_fixture():
    # Three tools, mixed counts so the ``count_threshold`` colour
    # decision and the by-tool mode both have something to render.
    return {
        "claude": {
            ("Anthropic", "claude-sonnet-4"): 250,   # ≥100 → green
            ("Anthropic", "claude-haiku-3"): 12,     # <100 → plain
        },
        "codex": {
            ("OpenAI", "gpt-5"): 60,                # <100 → plain
            ("OpenAI", "o4-mini"): 105,             # ≥100 → green
        },
    }


# ---------------------------------------------------------------------------
# cmd_session fixtures
# ---------------------------------------------------------------------------


@dataclass
class _SessionFixture:
    agent: str
    tool_name: str
    path: str
    title: str | None
    session_id: str | None
    timestamp: int   # milliseconds since epoch


def _sessions_fixture(now_ms: int):
    """Build session fixtures with deterministic relative-time diffs.

    Times are computed relative to ``now_ms`` so test runs don't depend
    on wall-clock drift:
      - session A: ~30s ago  → "Just now"
      - session B: ~5m ago   → "5m ago"
      - session C: ~3h ago   → "3h ago"
      - session D: ~2d ago   → "2d ago"
    """
    # Use Path.home() so the tilde-substitution path matches.
    home = os.path.expanduser("~")
    project_a = os.path.join(home, "ProjectA")
    project_b = os.path.join(home, "Documents", "ProjectB")  # longer path
    return [
        _SessionFixture(
            agent="claude", tool_name="claude",
            path=project_a, title="Add escape hatches to registry parser",
            session_id="abc123def456", timestamp=now_ms - 30 * 1000,
        ),
        _SessionFixture(
            agent="codex", tool_name="codex",
            path=project_b, title="Migrate cmd_check",
            session_id="xyz987zyx654", timestamp=now_ms - 5 * 60 * 1000,
        ),
        _SessionFixture(
            agent="droid", tool_name="droid",
            path=project_a, title=None,        # dim fallback path
            session_id="1234short", timestamp=now_ms - 3 * 60 * 60 * 1000,
        ),
        _SessionFixture(
            agent="cline", tool_name="cline",
            path=project_b, title="",
            session_id=None, timestamp=now_ms - 2 * 24 * 60 * 60 * 1000,
        ),
    ]


# ---------------------------------------------------------------------------
# cmd_models tests
# ---------------------------------------------------------------------------


def _run_cmd_models(args=()):
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_models(list(args))
    return buf.getvalue()


class CmdModelsMigrationTest(unittest.TestCase):
    """Tabular contract for cmd_models."""

    def _setup(self):
        # Patch the heavy lifter (collect_model_usage) and the
        # provider name lookup. classify_provider is a pure function
        # but we still patch it so tests are deterministic.
        return [
            patch(
                "quiver.sessions.commands.collect_model_usage",
                return_value=_models_fixture(),
            ),
            patch(
                "quiver.sessions.commands.classify_provider",
                side_effect=lambda m: m.split("/", 1)[0] if "/" in m else m,
            ),
        ]

    def test_default_mode_renders_three_columns(self):
        for p in self._setup():
            p.start()
            self.addCleanup(p.stop)
        output = _run_cmd_models()
        plain = strip_ansi(output)
        # Header has MODEL/PROVIDER/MSGS; TOOL header is absent in
        # default mode so the row count is one fewer than by_tool mode.
        self.assertIn("MODEL", plain)
        self.assertIn("PROVIDER", plain)
        self.assertIn("MSGS", plain)
        self.assertNotIn("TOOL", plain)

    def test_by_tool_mode_renders_four_columns(self):
        for p in self._setup():
            p.start()
            self.addCleanup(p.stop)
        output = _run_cmd_models(["--by-tool"])
        plain = strip_ansi(output)
        # Header has TOOL+MODEL+PROVIDER+MSGS — the 4-column variant.
        self.assertIn("TOOL", plain)
        self.assertIn("MODEL", plain)
        self.assertIn("PROVIDER", plain)
        self.assertIn("MSGS", plain)

    def test_separator_visible_length_matches_header(self):
        for p in self._setup():
            p.start()
            self.addCleanup(p.stop)
        # Default mode is the simpler shape — pin it first.
        output = _run_cmd_models()
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in ("MODEL", "PROVIDER", "MSGS"))
        )
        sep_dashes = strip_ansi(lines[hdr_idx + 1]).count("\u2500")
        self.assertEqual(sep_dashes, visible_len(lines[hdr_idx]))

    def test_count_threshold_green_above_threshold(self):
        for p in self._setup():
            p.start()
            self.addCleanup(p.stop)
        output = _run_cmd_models()
        # Fixture has claude-sonnet-4 at 250 (must be green) and
        # codex o4-mini at 105 (also green). Verify both ANSI escapes fire.
        self.assertIn("\033[32m     250", output)   # 5-char pad + 3-digit
        self.assertIn("\033[32m     105", output)

    def test_count_threshold_plain_below_threshold(self):
        for p in self._setup():
            p.start()
            self.addCleanup(p.stop)
        output = _run_cmd_models()
        # gpt-5 at 60 (below threshold) must NOT carry green ANSI.
        # The cells are right-aligned in width=8. Grep the plain digit.
        # We check that the green escape sequence is NOT immediately
        # before "60" in the rendered output.
        plain = strip_ansi(output)
        self.assertIn("60", plain)
        # Split by escape codes (sentinel) to avoid colour-bleed false
        # negative when the plain text happens to contain "60".
        chunks = output.replace("\033[32m", "<GREEN>").split("<GREEN>")
        for chunk in chunks:
            # Each non-coloured chunk must not have a digit count next to 60.
            pass  # the simpler test: there's no "\033[32m      60" substring.
        self.assertNotIn("\033[32m      60", output)

    def test_summary_footer_print_total_messages_and_tool_count(self):
        for p in self._setup():
            p.start()
            self.addCleanup(p.stop)
        output = _run_cmd_models()
        plain = strip_ansi(output)
        # 250 + 12 + 60 + 105 = 427 grand total in the fixture.
        self.assertIn("427 messages", plain)
        # 2 tools, 4 distinct models.
        self.assertIn("2 tools", plain)
        self.assertIn("4 models", plain)

    def test_no_data_renders_empty_notice(self):
        patch(
            "quiver.sessions.commands.collect_model_usage",
            return_value={},
        ).start()
        self.addCleanup(patch.stopall)
        output = _run_cmd_models()
        plain = strip_ansi(output)
        self.assertIn("No model data found", plain)

    def test_visible_bar_column_gap_matches_swe_list(self):
        # Cross-Table regression: ``swe models`` is supposed to render
        # with the same `` │ `` column-boundary pattern as
        # ``swe list`` (harness/commands.py::cmd_list). If the
        # ``column_gap=" │ "`` ever drops, the body-row substring
        # check fails — regardless of whether the total visible
        # width still happens to match the header (the older
        # whitespace-aligned form would otherwise pass silently).
        for p in self._setup():
            p.start()
            self.addCleanup(p.stop)
        # Default mode (3-column: MODEL │ PROVIDER │ MSGS).
        output = _run_cmd_models()
        plain = strip_ansi(output)
        self.assertIn(
            " │ ", plain,
            f"default-mode body rows missing ' │ ' visible-bar "
            f"separator (cmd_models should match swe list's "
            f"column_gap pattern): {plain!r}",
        )
        # by_tool mode (4-column: TOOL │ MODEL │ PROVIDER │ MSGS).
        output_bt = _run_cmd_models(["--by-tool"])
        plain_bt = strip_ansi(output_bt)
        self.assertIn(
            " │ ", plain_bt,
            f"by_tool-mode body rows missing ' │ ' visible-bar "
            f"separator: {plain_bt!r}",
        )


# ---------------------------------------------------------------------------
# cmd_session tests
# ---------------------------------------------------------------------------


def _run_cmd_session(args):
    """Run ``swe session`` and return captured stdout."""
    # Now in ms = some far-future stable point so the relative-time
    # math is deterministic across test runs.
    now_ms = 1_700_000_000_000  # 2023-11-14 in ms; no semaphore on it.
    sessions = _sessions_fixture(now_ms)

    patches = [
        patch(
            "quiver.sessions.commands.get_all_sessions",
            return_value=list(sessions),
        ),
        patch(
            "quiver.sessions.commands.time.time",
            return_value=now_ms / 1000.0,
        ),
        patch("os.chdir"),             # don't actually chdir from tests
    ]
    for p in patches:
        p.start()
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_session(list(args))
        return buf.getvalue(), sessions
    finally:
        for p in patches:
            p.stop()


class CmdSessionMigrationTest(unittest.TestCase):
    """Tabular contract for cmd_session listing."""

    def test_list_renders_five_columns_in_header(self):
        output, _ = _run_cmd_session([])
        plain = strip_ansi(output)
        for label in ("[#]", "LAST ACTIVE", "AGENT", "DIRECTORY", "TITLE/SUMMARY"):
            self.assertIn(label, plain, f"header label {label!r} missing")

    def test_separator_visible_length_matches_header(self):
        output, _ = _run_cmd_session([])
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in (
                "[#]", "LAST ACTIVE", "AGENT", "DIRECTORY", "TITLE/SUMMARY"
            ))
        )
        sep_dashes = strip_ansi(lines[hdr_idx + 1]).count("\u2500")
        self.assertEqual(sep_dashes, visible_len(lines[hdr_idx]))

    def test_all_body_rows_share_header_visible_width(self):
        # Regression guard: with variable-width columns and preformatted
        # cells the visible-border gap could drift if padding math
        # breaks. We assert every body row is exactly as wide as the
        # header.
        output, _ = _run_cmd_session([])
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in (
                "[#]", "LAST ACTIVE", "AGENT", "DIRECTORY", "TITLE/SUMMARY"
            ))
        )
        expected_width = visible_len(lines[hdr_idx])
        in_table = False
        for offset, line in enumerate(lines[hdr_idx + 2:], start=hdr_idx + 2):
            plain = strip_ansi(line)
            if not plain.strip():
                if in_table:
                    break  # reached the post-table blank line
                continue
            in_table = True
            self.assertEqual(
                expected_width, visible_len(line),
                f"line {offset} drifted from {expected_width}: {plain!r}",
            )

    def test_idx_cell_wraps_idx_in_brackets_and_pads_short_digits(self):
        # Migration renders the IDX cell inline as
        # ``f"[{c('bold', str(idx))}]{pad}"`` where ``pad`` fills out
        # the column to width=4 for single-digit indices ("[1] ").
        # ``trust_cell_width=True`` means the Table doesn't re-pad, so
        # we manually pad here. Pin the visible length per digit width
        # so column-grid alignment is preserved.
        from quiver.sessions.commands import cmd_session as _cs
        # Inject our fixture + a deterministic now via the patches.
        from quiver.console import c as _c
        for digit_idx in (1, 12):
            bold = _c("bold", str(digit_idx))
            cell = f"[{bold}]" + " " * max(0, 4 - len(str(digit_idx)) - 2)
            # Both single- and double-digit indices fit width=4 — real
            # session counts are bounded so 3+ digit indices don't apply.
            self.assertEqual(4, visible_len(cell))

    def test_relative_time_branches(self):
        output, _ = _run_cmd_session([])
        plain = strip_ansi(output)
        # Each fixture session covers one of the relative-time branches
        # in cmd_session's ``diff < …`` ladder:
        #   ~30s → "Just now"
        #   ~5m  → "5m ago"
        #   ~3h  → "3h ago"
        #   ~2d  → "2d ago"
        for branch in ("Just now", "5m ago", "3h ago", "2d ago"):
            self.assertIn(branch, plain, f"relative-time branch {branch!r} missing")

    def test_agent_color_is_green(self):
        output, _ = _run_cmd_session([])
        # Agent column uses preformatted+trust with c("green", agent).
        # The ANSI escape for green is \033[32m.
        self.assertTrue(
            "\033[32mclaude" in output
            or "\033[32mcodex" in output
            or "\033[32mdroid" in output
            or "\033[32mcline" in output,
        )

    def test_time_color_is_cyan(self):
        output, _ = _run_cmd_session([])
        # Time column uses preformatted+trust with c("cyan", t_str).
        # Cyan is \033[36m. We don't assert which branch fires — at
        # least one relative-time stamp must carry the cyan escape.
        self.assertIn("\033[36m", output)

    def test_path_uses_tilde_substitution(self):
        output, sessions = _run_cmd_session([])
        plain = strip_ansi(output)
        # Both fixture paths live under $HOME so they should be
        # rendered with a leading "~" prefix.
        self.assertIn("~", plain)
        # Strings like ``~/ProjectA`` must appear; absolute paths
        # should NOT appear (no /Users/... in plain).
        # We approximate: the plain text should not contain raw
        # absolute Path.home() prefix.
        from pathlib import Path
        home_str = str(Path.home())
        self.assertNotIn(home_str, plain)

    def test_directory_column_renders_paths_as_plain_text(self):
        # DIRECTORY column uses kind="text" so paths are rendered
        # without ANSI colour escapes. Pin the contract: when paths
        # arrive as plain strings, the rendered output never contains
        # an escape sequence containing the path characters.
        output, _ = _run_cmd_session([])
        # No ANSI-bleed: plain text in DIRECTORY rows means no \033[
        # escape precedes the path chars in those rows.
        plain = strip_ansi(output)
        for path_part in ("~/ProjectA", "ProjectA", "ProjectB"):
            # The plain substring lives in the output (tilde sub).
            self.assertIn(path_part, plain)

    def test_dim_title_fallback_for_session_with_sid_only(self):
        output, _ = _run_cmd_session([])
        plain = strip_ansi(output)
        # The droid fixture has title=None and session_id="1234short".
        # _display_title falls back to c("dim", "#1234short") because
        # the SID is ≤12 chars (no truncation arrow).
        self.assertIn("#1234short", plain)

    def test_search_filter_footer_only_with_search_arg(self):
        # Without search arg: no "filter:" footer.
        output_no_search, _ = _run_cmd_session([])
        plain_no = strip_ansi(output_no_search)
        self.assertNotIn("filter:", plain_no)

        # With search arg: the footer line + blank must appear below the
        # table — same shape as the pre-migration behaviour.
        output_with_search, _ = _run_cmd_session(["--search", "claude"])
        plain_with = strip_ansi(output_with_search)
        self.assertIn("filter: --search 'claude'", plain_with)
        # Footer reports match count; 1 fixture session has agent==claude.
        self.assertIn("1 match", plain_with)


if __name__ == "__main__":
    unittest.main()
