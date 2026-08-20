"""Edge cases in the shared string helpers, and the skill-name matcher.

None of these were reachable from today's callers, but all three helpers
are used to build fixed-width table cells, so a return value wider than
the column silently breaks alignment for every cell after it.
"""

import unittest
from pathlib import Path
from unittest import mock

from quiver.console import c, elide, truncate


class UnknownColourTest(unittest.TestCase):
    def test_an_unknown_name_returns_plain_text(self):
        self.assertEqual(c("chartreuse", "hello"), "hello")

    def test_a_known_name_still_colours(self):
        self.assertIn("hello", c("green", "hello"))
        self.assertNotEqual(c("green", "hello"), "hello")

    def test_a_typo_does_not_take_down_the_command(self):
        # This used to raise KeyError from inside a print.
        try:
            c("dimm", "x")
        except KeyError:
            self.fail("unknown colour still raises")


class TruncateWidthTest(unittest.TestCase):
    def test_never_returns_more_than_the_width(self):
        for n in range(-2, 12):
            self.assertLessEqual(len(truncate("abcdefgh", n)), max(n, 0), f"n={n}")

    def test_zero_and_negative_give_empty(self):
        self.assertEqual(truncate("abc", 0), "")
        self.assertEqual(truncate("abc", -5), "")

    def test_short_text_is_untouched(self):
        self.assertEqual(truncate("abc", 10), "abc")

    def test_normal_widths_still_use_an_ellipsis(self):
        self.assertEqual(truncate("abcdefgh", 6), "abc...")


class ElideWidthTest(unittest.TestCase):
    def test_never_returns_more_than_the_width(self):
        for n in range(-2, 20):
            self.assertLessEqual(len(elide("abcdefghijklm", n)), max(n, 0), f"n={n}")

    def test_zero_and_negative_give_empty(self):
        self.assertEqual(elide("abc", 0), "")
        self.assertEqual(elide("abc", -1), "")

    def test_keeps_both_ends(self):
        out = elide("/Users/me/deep/path/to/SKILL.md", 20)
        self.assertEqual(len(out), 20)
        self.assertTrue(out.startswith("/Users"))
        self.assertTrue(out.endswith(".md"))

    def test_exactly_at_width_is_untouched(self):
        self.assertEqual(elide("abcde", 5), "abcde")

    def test_it_lives_in_console_not_behind_a_private_name(self):
        """Two packages render paths this way; neither should reach into
        the other for a private helper."""
        import quiver.find.commands as find_cmds
        import quiver.skills.commands as skills_cmds

        self.assertNotIn("_elide", find_cmds.__dict__)
        for mod in (find_cmds, skills_cmds):
            src = Path(mod.__file__).read_text()
            self.assertNotIn("from quiver.find.commands import _elide", src)


class SkillNameMatchTest(unittest.TestCase):
    """An exact name must win over a longer name that contains it."""

    SKILLS = [
        {"name": "five-whys", "scope": "shared", "path": "/s/five-whys/SKILL.md"},
        {"name": "five-whys-extended", "scope": "shared",
         "path": "/s/five-whys-extended/SKILL.md"},
        {"name": "mind-mapping", "scope": "shared", "path": "/s/mind-mapping/SKILL.md"},
    ]

    def _match(self, query):
        from quiver.skills import link_ops

        with mock.patch.object(link_ops, "discover_skills", return_value=self.SKILLS):
            return [
                s["name"]
                for s in link_ops._find_skill_matches(
                    query, "shared", Path("/h"), Path("/c")
                )
            ]

    def test_an_exact_name_is_not_ambiguous(self):
        self.assertEqual(self._match("five-whys"), ["five-whys"])

    def test_the_longer_exact_name_also_resolves(self):
        self.assertEqual(self._match("five-whys-extended"), ["five-whys-extended"])

    def test_an_ambiguous_substring_still_reports_both(self):
        self.assertEqual(len(self._match("whys")), 2)

    def test_a_unique_substring_still_works(self):
        self.assertEqual(self._match("mapping"), ["mind-mapping"])

    def test_no_match_is_empty(self):
        self.assertEqual(self._match("nothing-like-this"), [])

    def test_an_exact_name_resolves_to_one_directory(self):
        from quiver.skills import link_ops

        with mock.patch.object(link_ops, "discover_skills", return_value=self.SKILLS):
            found = link_ops.find_skill_directory(
                "five-whys", "shared", home=Path("/h"), cwd=Path("/c")
            )
        self.assertEqual(found.name, "five-whys")


if __name__ == "__main__":
    unittest.main()
