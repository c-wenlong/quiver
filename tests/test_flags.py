"""Tests for the shared ``--name=value`` argv normaliser."""

import unittest

from quiver.flags import expand_value_flags


class ExpandValueFlagsTest(unittest.TestCase):
    def test_pairs_and_passthrough(self):
        args = expand_value_flags(
            ["amd", "--scope=all", "--full", "-r", "--harness=active"],
            {"--scope", "--harness"},
        )
        self.assertEqual(
            args, ["amd", "--scope", "all", "--full", "-r", "--harness", "active"]
        )

    def test_value_may_contain_equals(self):
        args = expand_value_flags(["--session-arg=--server=local"], {"--session-arg"})
        self.assertEqual(args, ["--session-arg", "--server=local"])

    def test_comma_list_is_one_value(self):
        args = expand_value_flags(["--tags=a,b,c"], {"--tags"})
        self.assertEqual(args, ["--tags", "a,b,c"])

    def test_empty_value_is_kept(self):
        args = expand_value_flags(["--scope="], {"--scope"})
        self.assertEqual(args, ["--scope", ""])

    def test_bare_flag_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            expand_value_flags(["--agent", "claude"], {"--agent"})
        self.assertIn("--agent=<value>", str(ctx.exception))

    def test_similar_prefix_does_not_match(self):
        args = expand_value_flags(["--agents=x"], {"--agent"})
        self.assertEqual(args, ["--agents=x"])


if __name__ == "__main__":
    unittest.main()
