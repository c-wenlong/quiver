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

from quiver.console import c, strip_ansi
from quiver.skills.commands import cmd_skills
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


NAME_W, SCOPE_W = 30, 16


class CmdSkillsListTest(unittest.TestCase):
    """One line per skill: NAME | SCOPE | PATH.

    The previous layout spent three lines per skill and carried a
    VISIBLE VIA column that repeated SCOPE in almost every row. Now that
    root discovery reaches every root on disk the listing is several
    hundred rows, so one line each is the difference between a readable
    table and a scrollback dump.
    """

    def setUp(self):
        _setup_skills_patches(self)

    def test_header_is_name_scope_path(self):
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        for label in ("NAME", "SCOPE", "PATH"):
            self.assertIn(label, plain)

    def test_visible_via_column_is_gone(self):
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        self.assertNotIn("VISIBLE VIA", plain)

    def test_one_line_per_skill(self):
        skills = _skills_fixture()
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        body = [
            ln for ln in plain.splitlines()
            if ln.strip() and not ln.startswith("─")
            and "NAME" not in ln and "skills across" not in ln
            and "Agent Skills" not in ln
        ]
        self.assertEqual(len(body), len(skills))

    def test_separator_matches_header_width(self):
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        lines = plain.splitlines()
        head = next(i for i, ln in enumerate(lines) if "NAME" in ln)
        # The header is space-padded to the full table width, so compare
        # the padded length, not the rstripped one.
        self.assertEqual(len(lines[head + 1]), len(lines[head]))

    def test_every_row_shows_its_path(self):
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        for skill in _skills_fixture():
            tail = skill["path"].rsplit("/", 2)[-2]
            self.assertIn(tail[:12], plain, f"no path shown for {skill['name']}")

    def test_a_long_path_elides_in_the_middle_keeping_both_ends(self):
        long = "/Users/x/" + "deep/" * 40 + "thing/SKILL.md"
        _setup_skills_patches(self, skills=[
            {"name": "n", "scope": "s", "path": long, "description": "",
             "visible_via": ["s"]}])
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        row = next(ln for ln in plain.splitlines() if "…" in ln)
        self.assertIn("SKILL.md", row, "the tail identifies which file")
        self.assertTrue(row.strip().startswith("n"), "the head survives too")

    def test_desc_flag_adds_a_line_under_the_row(self):
        plain = strip_ansi(_run_cmd_skills(["ls", "-d"]))
        self.assertIn(_skills_fixture()[0]["description"][:20], plain)

    def test_no_desc_line_without_the_flag(self):
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        self.assertNotIn(_skills_fixture()[0]["description"][:20], plain)

    def test_long_name_is_truncated_to_the_column(self):
        _setup_skills_patches(self, skills=[
            {"name": "x" * 80, "scope": "s", "path": "/p/SKILL.md",
             "description": "", "visible_via": ["s"]}])
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        row = next(ln for ln in plain.splitlines() if "x" in ln)
        self.assertLessEqual(len(row.split()[0]), NAME_W)

    def test_footer_points_at_find(self):
        plain = strip_ansi(_run_cmd_skills(["ls"]))
        self.assertIn("swe find skills", plain)


class SupersededCommandsTest(unittest.TestCase):
    """`swe skills tree` and `swe skills scope list` forward to find.

    They drew the same map from a hardcoded list of candidate paths, so
    they saw 18 roots where the filesystem scan sees 60, and reported 0
    skills for every symlinked root because they counted the link rather
    than its target.
    """

    def test_tree_forwards_to_find(self):
        with patch("quiver.find.commands.cmd_find_skills", return_value=0) as m:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_skills(["tree"])
            m.assert_called_once()
            self.assertTrue(m.call_args.kwargs.get("root_flag"))
        self.assertIn("swe find skills", strip_ansi(buf.getvalue()))

    def test_scope_list_forwards_to_find(self):
        with patch("quiver.find.commands.cmd_find_skills", return_value=0) as m:
            buf = io.StringIO()
            with redirect_stdout(buf):
                cmd_skills(["scope", "list"])
            m.assert_called_once()

    def test_scope_all_is_passed_through(self):
        with patch("quiver.find.commands.cmd_find_skills", return_value=0) as m:
            with redirect_stdout(io.StringIO()):
                cmd_skills(["tree", "--scope=all"])
            self.assertEqual(m.call_args.kwargs.get("scope"), "all")
