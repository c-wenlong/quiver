"""Tests for ``swe skills link|unlink|move`` command handlers."""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quiver.skills.layout_commands import (
    _parse_flags,
    cmd_skills_link,
    cmd_skills_move,
    cmd_skills_unlink,
)
from quiver.skills.link_ops import SkillLayoutError

ALL_FLAGS = {"--from", "--to", "--force", "--mkdir", "--json"}


def _run(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(list(args))
    return code, buf.getvalue()


class ParseFlagsTest(unittest.TestCase):
    def test_equals_form_splits_into_pairs(self):
        opts, rest = _parse_flags(
            ["--from=shared", "--to=codex", "--force", "name"], ALL_FLAGS)
        self.assertEqual(opts["from"], "shared")
        self.assertEqual(opts["to"], "codex")
        self.assertTrue(opts["force"])
        self.assertEqual(rest, ["name"])

    def test_bare_value_flag_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_flags(["--from", "shared"], ALL_FLAGS)
        self.assertIn("--from=<value>", str(ctx.exception))

    def test_json_flag_is_accepted(self):
        opts, _ = _parse_flags(["--json"], ALL_FLAGS)
        self.assertTrue(opts["json"])

    def test_help_token_survives_to_rest(self):
        _, rest = _parse_flags(["--help"], ALL_FLAGS)
        self.assertEqual(rest, ["--help"])

    def test_flag_outside_allowed_set_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_flags(["--mkdir"], {"--force"})
        self.assertIn("--mkdir is not valid here", str(ctx.exception))

    def test_value_flag_outside_allowed_set_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_flags(["--to=codex"], {"--force"})
        self.assertIn("--to is not valid here", str(ctx.exception))

    def test_unknown_flag_is_an_error(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_flags(["--bogus"], ALL_FLAGS)
        self.assertIn("Unknown flag: --bogus", str(ctx.exception))


class SkillsLinkTest(unittest.TestCase):
    def test_help(self):
        code, _ = _run(cmd_skills_link, ["--help"])
        self.assertEqual(code, 0)

    def test_no_args_is_usage_error(self):
        code, out = _run(cmd_skills_link, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe skills link", out)

    def test_extra_args_rejected(self):
        code, out = _run(cmd_skills_link, ["a", "b", "c"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: c", out)

    def test_bare_value_flag_rejected(self):
        code, out = _run(cmd_skills_link, ["shared", "--force", "--to"])
        self.assertEqual(code, 1)
        self.assertIn("--to=<value>", out)

    def test_layout_error_is_reported(self):
        with patch(
            "quiver.skills.layout_commands.link_skill_root",
            side_effect=SkillLayoutError("already linked"),
        ):
            code, out = _run(cmd_skills_link, ["shared"])
        self.assertEqual(code, 1)
        self.assertIn("already linked", out)

    def test_success(self):
        with patch(
            "quiver.skills.layout_commands.link_skill_root",
            return_value=("codex", Path("/x/shared"), Path("/y/codex")),
        ) as link:
            code, out = _run(cmd_skills_link, ["codex", "shared", "--force"])
        self.assertEqual(code, 0)
        link.assert_called_once_with("codex", "shared", force=True)
        self.assertIn("Linked codex", out)

    def test_mkdir_is_rejected_on_link(self):
        code, out = _run(cmd_skills_link, ["codex", "--mkdir"])
        self.assertEqual(code, 1)
        self.assertIn("--mkdir is not valid here", out)

    def test_unknown_flag_is_rejected(self):
        code, out = _run(cmd_skills_link, ["codex", "--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown flag: --bogus", out)

    def test_json_output(self):
        with patch(
            "quiver.skills.layout_commands.link_skill_root",
            return_value=("codex", Path("/x/shared"), Path("/y/codex")),
        ):
            code, out = _run(cmd_skills_link, ["codex", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["action"], "link")
        self.assertEqual(payload["harness"], "codex")


class SkillsUnlinkTest(unittest.TestCase):
    def test_help(self):
        code, _ = _run(cmd_skills_unlink, ["-h"])
        self.assertEqual(code, 0)

    def test_no_args_is_usage_error(self):
        code, out = _run(cmd_skills_unlink, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe skills unlink", out)

    def test_bare_value_flag_rejected(self):
        code, out = _run(cmd_skills_unlink, ["codex", "--from"])
        self.assertEqual(code, 1)
        self.assertIn("--from=<value>", out)

    def test_layout_error_is_reported(self):
        with patch(
            "quiver.skills.layout_commands.unlink_skill_root",
            side_effect=SkillLayoutError("not a link"),
        ):
            code, out = _run(cmd_skills_unlink, ["codex"])
        self.assertEqual(code, 1)
        self.assertIn("not a link", out)

    def test_success_with_mkdir_mentions_directory(self):
        with patch(
            "quiver.skills.layout_commands.unlink_skill_root",
            return_value=("codex", Path("/y/codex")),
        ) as unlink:
            code, out = _run(cmd_skills_unlink, ["codex", "--mkdir"])
        self.assertEqual(code, 0)
        unlink.assert_called_once_with("codex", mkdir=True)
        self.assertIn("empty directory created", out)

    def test_success_without_mkdir(self):
        with patch(
            "quiver.skills.layout_commands.unlink_skill_root",
            return_value=("codex", Path("/y/codex")),
        ):
            code, out = _run(cmd_skills_unlink, ["codex"])
        self.assertEqual(code, 0)
        self.assertNotIn("empty directory", out)

    def test_force_is_rejected_on_unlink(self):
        code, out = _run(cmd_skills_unlink, ["codex", "--force"])
        self.assertEqual(code, 1)
        self.assertIn("--force is not valid here", out)

    def test_extra_positional_is_rejected(self):
        code, out = _run(cmd_skills_unlink, ["codex", "extra"])
        self.assertEqual(code, 1)
        self.assertIn("Usage: swe skills unlink", out)

    def test_json_output(self):
        with patch(
            "quiver.skills.layout_commands.unlink_skill_root",
            return_value=("codex", Path("/y/codex")),
        ):
            code, out = _run(cmd_skills_unlink, ["codex", "--json", "--mkdir"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["action"], "unlink")
        self.assertTrue(payload["mkdir"])


class SkillsMoveTest(unittest.TestCase):
    def test_help(self):
        code, _ = _run(cmd_skills_move, ["--help"])
        self.assertEqual(code, 0)

    def test_missing_from_or_to_is_usage_error(self):
        code, out = _run(cmd_skills_move, ["my-skill", "--from=shared"])
        self.assertEqual(code, 1)
        self.assertIn("--from=<scope> --to=<scope>", out)

    def test_bare_value_flag_rejected(self):
        code, out = _run(cmd_skills_move, ["my-skill", "--from"])
        self.assertEqual(code, 1)
        self.assertIn("--from=<value>", out)

    def test_layout_error_is_reported(self):
        with patch(
            "quiver.skills.layout_commands.move_skill",
            side_effect=SkillLayoutError("no such skill"),
        ):
            code, out = _run(
                cmd_skills_move, ["my-skill", "--from=shared", "--to=codex"]
            )
        self.assertEqual(code, 1)
        self.assertIn("no such skill", out)

    def test_success(self):
        with patch(
            "quiver.skills.layout_commands.move_skill",
            return_value=(Path("/a"), Path("/b")),
        ) as move:
            code, out = _run(
                cmd_skills_move,
                ["my-skill", "--from=shared", "--to=codex", "--force"],
            )
        self.assertEqual(code, 0)
        move.assert_called_once_with("my-skill", "shared", "codex", force=True)
        self.assertIn("Moved my-skill", out)

    def test_mkdir_is_rejected_on_move(self):
        code, out = _run(
            cmd_skills_move,
            ["my-skill", "--from=shared", "--to=codex", "--mkdir"])
        self.assertEqual(code, 1)
        self.assertIn("--mkdir is not valid here", out)

    def test_json_output(self):
        with patch(
            "quiver.skills.layout_commands.move_skill",
            return_value=(Path("/a"), Path("/b")),
        ):
            code, out = _run(
                cmd_skills_move,
                ["my-skill", "--from=shared", "--to=codex", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["action"], "move")
        self.assertEqual(payload["name"], "my-skill")


if __name__ == "__main__":
    unittest.main()
