"""Tests for the migrations of ``cmd_skills_scopes`` and
``cmd_skills`` (skills/commands.py) to ``quiver.table.Table``.

Both handlers used hand-rolled ``f"{...:<{w}}"`` string interpolation
with the magic ``+9`` width offsets the cmd_list migration had to
absorb (the pre-Table era compensated for ANSI-overhead by padding
both the cell and the prefix in the same format expression). These
tests pin the new structural invariants:

1. cmd_skills_scopes — 4-column SCOPE | KIND | SKILLS | PATH with
   per-row KIND colours (yellow symlink / dim alias / green directory)
   and SKILLS colour (green n>0 / dim zero). PATH uses
   ``kind="preformatted"`` so the dim-coloured ``  → tgt`` arrow is
   preserved (the ``text`` kind strips ANSI before measuring and
   would silently drop the colour escapes).

2. cmd_skills — 3-column NAME | SCOPE | VISIBLE_VIA Table; PATH and
   DESCRIPTION (when ``--desc``) are plain ``print()`` lines below
   each rendered row, with PATH indented 28 + 2 + 14 + 2 = 46 spaces
   so it visually aligns under the VISIBLE_VIA header column.
   NAME bold, SCOPE cyan, VISIBLE_VIA cyan when reachable via
   multiple scopes (comma-joined) and dim when a single scope.
"""

import copy
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quiver.console import c, strip_ansi, visible_len
from quiver.skills.commands import cmd_skills, cmd_skills_scopes
from quiver.skills.layout import SkillRootEntry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _skill_roots_fixture():
    """Stable ``SkillRootEntry`` fixtures covering symlink/alias/directory
    cases + zero/non-zero skill counts.
    """
    home = Path.home()
    return [
        SkillRootEntry(
            label="shared",
            path=home / ".agents/skills",
            exists=True, kind="directory", skill_count=4,
        ),
        SkillRootEntry(
            label="cursor",
            path=home / ".cursor/skills",
            exists=True, kind="symlink",
            link_target=home / ".agents/skills",
            resolved=home / ".agents/skills",
            canonical_label="shared",
        ),
        SkillRootEntry(
            label="claude",
            path=home / ".claude/skills",
            exists=True, kind="directory", skill_count=0,
        ),
        SkillRootEntry(
            label="legacy-alias",
            path=home / ".legacy-old",
            exists=True, kind="directory",
            resolved=home / ".agents/skills",
            canonical_label="shared", skill_count=4,
        ),
    ]


def _skills_fixture():
    """Stable skill dicts covering multi-/single-visible_via and --desc."""
    home = Path.home()
    return [
        {
            "name": "Migrate Skills Table",
            "scope": "shared",
            "path": str(home / ".agents/skills" / "migrate"),
            "description": "Detailed migration plan for f-string tables.",
            "visible_via": ["shared", "cursor", "claude"],
        },
        {
            "name": "Inspect Redux",
            "scope": "shared",
            "path": str(home / ".agents/skills" / "inspect"),
            "description": "How to inspect state.",
            "visible_via": ["shared"],
        },
        {
            "name": "Brew Install",
            "scope": "project",
            "path": str(home / "Code/project/.cursor/skills/brew"),
            "description": "",
            "visible_via": ["project"],
        },
    ]


# ---------------------------------------------------------------------------
# cmd_skills_scopes tests
# ---------------------------------------------------------------------------


def _run_cmd_skills_scopes():
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_skills_scopes([])
    return buf.getvalue()


class CmdSkillsScopesMigrationTest(unittest.TestCase):
    """Structural invariants for cmd_skills_scopes post-migration."""

    def setUp(self):
        patches = [
            patch(
                "quiver.skills.commands.discover_skills",
                return_value=_skills_fixture(),
            ),
            patch(
                "quiver.skills.commands.enumerate_skill_roots",
                return_value=copy.deepcopy(_skill_roots_fixture()),
            ),
            patch("quiver.skills.commands.Path.home", return_value=Path.home()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_header_has_scope_kind_skills_path_labels(self):
        output = _run_cmd_skills_scopes()
        plain = strip_ansi(output)
        for label in ("SCOPE", "KIND", "SKILLS", "PATH"):
            self.assertIn(label, plain, f"header label {label!r} missing")

    def test_separator_visible_length_matches_header(self):
        output = _run_cmd_skills_scopes()
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in ("SCOPE", "KIND", "SKILLS", "PATH"))
        )
        sep_dashes = strip_ansi(lines[hdr_idx + 1]).count("\u2500")
        self.assertEqual(sep_dashes, visible_len(lines[hdr_idx]))

    def test_body_rows_visible_len_does_not_exceed_header(self):
        # The PATH column uses kind="preformatted" with fit="content"
        # so the column grows to the longest observed path + dim
        # arrow. Body rows with shorter paths are not auto-padded
        # because preformatted cells trust their own width. The
        # regression guard is: no body row may exceed the header
        # width (which would indicate the cell spilled into the next
        # column's gap).
        output = _run_cmd_skills_scopes()
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in ("SCOPE", "KIND", "SKILLS", "PATH"))
        )
        expected_max = visible_len(lines[hdr_idx])
        for offset, line in enumerate(lines[hdr_idx + 2:], start=hdr_idx + 2):
            plain = strip_ansi(line)
            if not plain.strip():
                continue
            if "roots" in plain and "unique" in plain:
                break
            self.assertLessEqual(
                visible_len(line), expected_max,
                f"line {offset} exceeded header width: {plain!r}",
            )

    def test_scope_column_renders_cyan(self):
        output = _run_cmd_skills_scopes()
        # cpad("cyan", label, 16) emits \033[36m.
        self.assertIn("\033[36m", output)

    def test_kind_column_yellow_for_symlink(self):
        output = _run_cmd_skills_scopes()
        self.assertIn("\033[33msymlink", output)

    def test_kind_column_dim_for_alias(self):
        # legacy-alias entry has matching resolved path + non-canonical
        # label, which triggers the alias branch.
        output = _run_cmd_skills_scopes()
        self.assertIn("alias", strip_ansi(output))

    def test_kind_column_green_for_directory(self):
        output = _run_cmd_skills_scopes()
        self.assertIn("\033[32mdirectory", output)

    def test_skills_count_green_above_zero(self):
        # shared directory has skill_count=4.
        output = _run_cmd_skills_scopes()
        self.assertIn("\033[32m       4", output)

    def test_skills_count_dim_at_zero(self):
        # claude directory has skill_count=0 → dim "0".
        output = _run_cmd_skills_scopes()
        self.assertIn("       0", strip_ansi(output))

    def test_path_arrow_for_symlink_preserves_dim_colour(self):
        # Regression guard: previous migration used kind="text" for
        # PATH, which strips ANSI escapes and silently dropped the
        # dim colour on the ` → tgt` arrow. We now use
        # kind="preformatted" so the dim ANSI for the arrow is
        # preserved end-to-end.
        output = _run_cmd_skills_scopes()
        # The arrow's dim colour is applied via c("dim", ...), which
        # uses \033[2m.
        self.assertIn("\033[2m  \u2192", output)

    def test_path_no_arrow_for_plain_directory(self):
        # shared directory kind: green "directory" + no arrow.
        output = _run_cmd_skills_scopes()
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in ("SCOPE", "KIND", "SKILLS", "PATH"))
        )
        plain_lines = [
            strip_ansi(line) for line in lines[hdr_idx + 2:]
            if strip_ansi(line).strip()
        ]
        shared_dir_row = next(
            line for line in plain_lines if "shared" in line and "\u2500" not in line
        )
        self.assertNotIn("\u2192", shared_dir_row)

    def test_footer_prints_total_roots_and_skills_count(self):
        output = _run_cmd_skills_scopes()
        plain = strip_ansi(output)
        # Fixture: 4 entries all exists=True → 4 roots. 3 skills total.
        self.assertIn("4 roots", plain)
        self.assertIn("3 unique skills", plain)


# ---------------------------------------------------------------------------
# cmd_skills tests
# ---------------------------------------------------------------------------


# ``cmd_skills([])`` is the help path (prints overview). The listing
# tests pass a non-empty args list (typically ``["ls"]`` which is a
# pass-through that runs the listing without subcommand routing).
def _run_cmd_skills(args):
    """Invoke cmd_skills with an explicit args list (must be non-empty).

    An empty list (``[]``) routes to ``print_skills_overview()`` which
    is the help-text path - the tests below must NOT slide into help.
    """
    if not args:
        raise ValueError(
            "_run_cmd_skills requires a non-empty args list - "
            "cmd_skills([]) is the help-text path."
        )
    buf = io.StringIO()
    with redirect_stdout(buf):
        cmd_skills(list(args))
    return buf.getvalue()


def _setup_skills_patches(testcase, skills=None):
    patches = [
        patch(
            "quiver.skills.commands.discover_skills",
            return_value=copy.deepcopy(skills if skills is not None else _skills_fixture()),
        ),
    ]
    for p in patches:
        p.start()
        testcase.addCleanup(p.stop)


# Indent used when printing PATH as a plain ``print()`` below the
# rendered table row. Computed as 28 (NAME) + 2 (gap) + 14 (SCOPE) + 2
# (gap) so the path visually starts under the VISIBLE_VIA column.
PATH_INDENT = 28 + 2 + 14 + 2

# VISIBLE_VIA column width declared in cmd_skills (40 chars) - hardcoded
# so every cpad'd cell arrives at exactly that visible width.
VISIBLE_VIA_WIDTH = 40


class CmdSkillsMigrationTest(unittest.TestCase):
    """Structural invariants for cmd_skills post-migration.

    The migration produces a single 3-column Table (NAME | SCOPE |
    VISIBLE_VIA) and emits PATH (and DESCRIPTION when ``--desc``) as
    plain ``print()`` lines below each rendered row, indented to
    ``PATH_INDENT`` so they visually align beneath the VISIBLE_VIA
    header column.
    """

    def setUp(self):
        _setup_skills_patches(self)

    def test_header_has_name_scope_visible_via_only_default(self):
        output = _run_cmd_skills(["ls"])
        plain = strip_ansi(output)
        for label in ("NAME", "SCOPE", "VISIBLE VIA"):
            self.assertIn(label, plain, f"default-mode label {label!r} missing")
        # PATH and DESCRIPTION must NOT be columns of the default-mode
        # table - they are emitted as plain print() lines below each
        # rendered row.
        self.assertNotIn("PATH", plain)
        self.assertNotIn("DESCRIPTION", plain)

    def test_separator_visible_length_matches_header(self):
        output = _run_cmd_skills(["ls"])
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in ("NAME", "SCOPE", "VISIBLE VIA"))
        )
        sep_dashes = strip_ansi(lines[hdr_idx + 1]).count("\u2500")
        self.assertEqual(sep_dashes, visible_len(lines[hdr_idx]))

    def test_body_rows_aligned_to_header_width(self):
        # Each body row's printable cells are pre-padded via cpad to
        # the column schema widths (28 / 14 / pre-measured max-via).
        # Filter for lines matching the header visible width: those
        # are exclusively body rows (path sub-lines and blanks cannot
        # accidentally match the header width).
        output = _run_cmd_skills(["ls"])
        lines = output.split("\n")
        hdr_idx = next(
            i for i, raw in enumerate(lines)
            if all(lbl in strip_ansi(raw) for lbl in ("NAME", "SCOPE", "VISIBLE VIA"))
        )
        expected_width = visible_len(lines[hdr_idx])

        # Walk all lines after the separator; collect lines whose
        # visible width matches the header visible width; assert we
        # got one per fixture skill and that each arrives at exactly
        # the header width.
        body_rows = [
            ln for ln in lines[hdr_idx + 2:]
            if visible_len(ln) == expected_width
        ]
        self.assertEqual(
            len(body_rows), 3,
            f"expected 3 body rows matching header width {expected_width}, "
            f"got {len(body_rows)}",
        )
        for row in body_rows:
            self.assertEqual(
                expected_width, visible_len(row),
                f"body row drifted: {strip_ansi(row)!r}",
            )

    def test_visible_via_cyan_for_multi_scope(self):
        output = _run_cmd_skills(["ls"])
        # "Migrate Skills Table" has visible_via = ["shared", "cursor",
        # "claude"] (comma-joined 22 chars) → cyan wrapper.
        # cpad to VISIBLE_VIA_WIDTH=40 → cell ends with 18 padding
        # spaces inside the cyan wrapper.
        self.assertIn("\033[36mshared, cursor, claude", output)

    def test_visible_via_dim_for_single_scope(self):
        output = _run_cmd_skills(["ls"])
        # "Inspect Redux" → visible_via ["shared"] single → dim
        # "shared" padded to 40 chars.
        plain = strip_ansi(output)
        self.assertIn("Inspect Redux", plain)
        self.assertIn("\033[2mshared", output)

    def test_name_column_bold(self):
        output = _run_cmd_skills(["ls"])
        # NAME is cpad("bold", name, 28) → bold escape prefix.
        self.assertIn("\033[1mMigrate Skills Table", output)

    def test_scope_column_cyan(self):
        output = _run_cmd_skills(["ls"])
        # SCOPE is cpad("cyan", scope, 14) → cyan escape prefix.
        self.assertIn("\033[36mshared", output)

    def test_path_printed_below_each_row_indented(self):
        # Regression guard: PATH is no longer a Table column. It must
        # appear as a plain ``print()`` line below each rendered
        # body row, indented PATH_INDENT spaces so it sits under the
        # VISIBLE_VIA header column.
        output = _run_cmd_skills(["ls"])
        plain = strip_ansi(output)
        expected_paths = [
            s["path"].replace(str(Path.home()), "~")
            for s in sorted(_skills_fixture(),
                            key=lambda s: (s["scope"], s["name"].lower()))
        ]
        for path in expected_paths:
            self.assertIn(
                " " * PATH_INDENT + path, plain,
                f"expected path {path!r} at indent {PATH_INDENT} below a row",
            )

    def test_path_line_uses_dim_colour(self):
        output = _run_cmd_skills(["ls"])
        # The PATH sub-line is rendered via c("dim", ...). \033[2m
        # dim escape must be present (at least once for the path
        # sub-lines).
        self.assertIn("\033[2m" + " " * PATH_INDENT, output)

    def test_blank_line_between_skills(self):
        # Each skill emits a 3-line block: rendered row, dim-indented
        # path line, blank line. There must be exactly ``len(skills)``
        # dim-indented path lines (one per skill) in the raw output,
        # and each one must be followed by a blank line. The raw
        # output is used (not ``strip_ansi``) because the indentation
        # prefix on the path line carries a dim ANSI escape sequence
        # that strip_ansi would drop.
        output = _run_cmd_skills(["ls"])
        lines = output.split("\n")
        path_line_indices = [
            i for i, ln in enumerate(lines)
            if ln.startswith("\033[2m" + " " * PATH_INDENT)
        ]
        self.assertEqual(
            len(path_line_indices), 3,
            f"expected 3 dim-indented path lines, got "
            f"{len(path_line_indices)}",
        )
        for idx in path_line_indices:
            self.assertEqual(
                lines[idx + 1], "",
                f"dim-indented path line at {idx} not followed by a "
                f"blank line: got {lines[idx + 1]!r}",
            )

    def test_path_uses_tilde_substitution(self):
        # All fixture paths live under $HOME → rendered with ~ prefix.
        output = _run_cmd_skills(["ls"])
        plain = strip_ansi(output)
        self.assertIn("~/" + ".agents/skills", plain)

    def test_filter_drops_non_matching_skills(self):
        output = _run_cmd_skills(["migrate"])
        plain = strip_ansi(output)
        self.assertIn("Migrate Skills Table", plain)
        self.assertNotIn("Inspect Redux", plain)
        self.assertNotIn("Brew Install", plain)

    def test_no_skills_match_filter_prints_notice(self):
        output = _run_cmd_skills(["nonexistent-skill-name-zzz"])
        plain = strip_ansi(output)
        self.assertIn("No skills found", plain)

    def test_alphabetical_sort_within_scope(self):
        # Two skills under "shared" — "Inspect Redux" and "Migrate
        # Skills Table". Alphabetical: "I..." before "M...".
        output = _run_cmd_skills(["ls"])
        plain = strip_ansi(output)
        idx_inspect = plain.find("Inspect Redux")
        idx_migrate = plain.find("Migrate Skills Table")
        self.assertGreater(idx_inspect, 0)
        self.assertGreater(idx_migrate, 0)
        self.assertLess(idx_inspect, idx_migrate)

    def test_long_name_truncated_to_28(self):
        # Name longer than 28 chars: truncate.
        long_skill = {
            "name": "a-name-far-longer-than-twenty-eight-chars-in-total",
            "scope": "shared",
            "path": "/tmp/foo",
            "description": "",
            "visible_via": ["shared"],
        }
        patch(
            "quiver.skills.commands.discover_skills",
            return_value=copy.deepcopy([long_skill]),
        ).start()
        self.addCleanup(patch.stopall)
        output = _run_cmd_skills(["ls"])
        plain = strip_ansi(output)
        for line in plain.split("\n"):
            if "a-name-far-longer" in line:
                first_chunk = line[:28].rstrip()
                self.assertTrue(
                    first_chunk.endswith("a-name-far") or first_chunk.endswith("..."),
                    f"NAME cell at width 28 not truncated correctly: {first_chunk!r}",
                )
                return
        self.fail("long skill row not found in output")

    def test_desc_flag_emits_description_below_path(self):
        # With --desc, description (when non-empty) appears as a
        # plain print() line below PATH, indented PATH_INDENT spaces
        # in dim colour. Description is truncated to 80 chars.
        output = _run_cmd_skills(["--desc"])
        plain = strip_ansi(output)
        # Truncate the description to 80 chars max.
        truncated_desc = "Detailed migration plan for f-string tables."[:80]
        self.assertIn(
            " " * PATH_INDENT + truncated_desc, plain,
            f"expected description sub-line indented {PATH_INDENT} spaces",
        )
        # Description line has dim ANSI applied.
        self.assertIn("\033[2m" + " " * PATH_INDENT + truncated_desc, output)


if __name__ == "__main__":
    unittest.main()
