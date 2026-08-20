"""Pattern filters for `swe mcp sync --only/--except`.

Server names carry a category prefix (dv__, pd__, rf__, so__, sr__), so
selecting a whole category, or every categorised server, is the common case.
Exact names must keep working, and a pattern that matches nothing must say so
rather than looking the same as an empty result.
"""

import unittest

from quiver.mcp.cli import _filter_by_patterns

SERVERS = {
    "dv__github": {}, "dv__linear": {}, "dv__sentry": {},
    "pd__notion": {}, "pd__gdrive": {},
    "so__reddit": {},
    "computer-use": {}, "node_repl": {}, "trigger": {},
}


class FilterByPatternsTest(unittest.TestCase):
    def test_exact_name_still_matches_only_itself(self):
        sel, un = _filter_by_patterns(SERVERS, ["dv__github"])
        self.assertEqual(set(sel), {"dv__github"})
        self.assertEqual(un, [])

    def test_category_glob(self):
        sel, _ = _filter_by_patterns(SERVERS, ["dv__*"])
        self.assertEqual(set(sel), {"dv__github", "dv__linear", "dv__sentry"})

    def test_every_prefixed_server(self):
        sel, _ = _filter_by_patterns(SERVERS, ["*__*"])
        self.assertEqual(
            set(sel),
            {"dv__github", "dv__linear", "dv__sentry", "pd__notion", "pd__gdrive", "so__reddit"},
        )
        # The tool-bound ones stay out, which is the whole point.
        for name in ("computer-use", "node_repl", "trigger"):
            self.assertNotIn(name, sel)

    def test_patterns_combine_as_a_union(self):
        sel, _ = _filter_by_patterns(SERVERS, ["dv__*", "so__reddit"])
        self.assertEqual(set(sel), {"dv__github", "dv__linear", "dv__sentry", "so__reddit"})

    def test_overlapping_patterns_do_not_duplicate(self):
        sel, _ = _filter_by_patterns(SERVERS, ["dv__*", "dv__github"])
        self.assertEqual(len(sel), 3)

    def test_unmatched_patterns_are_reported(self):
        sel, un = _filter_by_patterns(SERVERS, ["dv__github", "dv__githbu", "zz__*"])
        self.assertEqual(set(sel), {"dv__github"})
        self.assertEqual(un, ["dv__githbu", "zz__*"])

    def test_empty_pattern_list_selects_nothing(self):
        sel, un = _filter_by_patterns(SERVERS, [])
        self.assertEqual(sel, {})
        self.assertEqual(un, [])

    def test_matching_is_case_sensitive(self):
        # Server names are identifiers; DV__* silently matching dv__* would be
        # a nasty surprise on a case-insensitive filesystem.
        sel, un = _filter_by_patterns(SERVERS, ["DV__*"])
        self.assertEqual(sel, {})
        self.assertEqual(un, ["DV__*"])

    def test_values_are_carried_through_not_replaced(self):
        servers = {"dv__github": {"type": "local", "command": ["gh"]}}
        sel, _ = _filter_by_patterns(servers, ["dv__*"])
        self.assertEqual(sel["dv__github"], servers["dv__github"])

    def test_question_mark_wildcard(self):
        sel, _ = _filter_by_patterns({"a1": {}, "a2": {}, "b1": {}}, ["a?"])
        self.assertEqual(set(sel), {"a1", "a2"})


if __name__ == "__main__":
    unittest.main()
