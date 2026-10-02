"""Strict argv handling: errors exit non-zero, unknown tokens are rejected.

Two bug classes this pins:

- handlers that printed a red error and fell off the end returned None,
  which ``main()`` reads as success — ``swe info badname`` exited 0.
- flags and extra positionals were silently swallowed — ``swe list
  --verbse`` filtered on the tag "verbse" and printed an empty table.
"""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quiver.cli import cmd_autocomplete
from quiver.find.commands import cmd_find
from quiver.harness.commands import (
    cmd_add,
    cmd_aliases,
    cmd_check,
    cmd_doctor,
    cmd_harness_edit,
    cmd_info,
    cmd_list,
    cmd_list_edit,
    cmd_list_legend,
    cmd_remove,
    cmd_star,
    cmd_tags,
    cmd_use,
)
from quiver.help_text import cmd_help
from quiver.providers.commands import (
    cmd_info as provider_info,
    cmd_list as provider_list,
    cmd_remove as provider_remove,
)
from quiver.sessions.commands import cmd_models
from quiver.skills.commands import cmd_skills


def _run(fn, args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = fn(list(args))
    return code, buf.getvalue()


def _provider_patches(tmp_path: Path):
    """Keep provider commands away from the real registry and keys dir."""
    config_dir = tmp_path / ".config" / "swe"
    registry_file = config_dir / "providers.json"
    keys_dir = tmp_path / ".api_keys"
    keys_dir.mkdir()
    return (
        patch("quiver.providers.registry.CONFIG_DIR", config_dir),
        patch("quiver.providers.registry.PROVIDERS_REGISTRY_FILE", registry_file),
        patch(
            "quiver.providers.commands.default_keys_dir",
            return_value=keys_dir,
        ),
    )


class ErrorExitCodesTest(unittest.TestCase):
    """A red error message must come with a non-zero exit code."""

    def test_info_usage(self):
        code, _ = _run(cmd_info, [])
        self.assertEqual(code, 1)

    def test_info_not_found(self):
        with patch("quiver.harness.commands.load_registry", return_value={}):
            code, _ = _run(cmd_info, ["nope"])
        self.assertEqual(code, 1)

    def test_remove_usage(self):
        code, _ = _run(cmd_remove, [])
        self.assertEqual(code, 1)

    def test_remove_not_found(self):
        with patch("quiver.harness.commands.load_registry", return_value={}):
            code, _ = _run(cmd_remove, ["nope"])
        self.assertEqual(code, 1)

    def test_use_usage(self):
        with patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.archive.load_archive", return_value={}
        ):
            code, _ = _run(cmd_use, [])
        self.assertEqual(code, 1)

    def test_use_not_found(self):
        with patch("quiver.harness.commands.load_registry", return_value={}):
            code, _ = _run(cmd_use, ["nope"])
        self.assertEqual(code, 1)

    def test_use_command_not_in_path(self):
        tools = {"demo": {"command": "definitely-not-installed-xyz"}}
        with patch("quiver.harness.commands.load_registry", return_value=tools), patch(
            "quiver.harness.commands.resolve", return_value="demo"
        ), patch(
            "quiver.harness.commands.is_installed", return_value=False
        ):
            code, _ = _run(cmd_use, ["demo"])
        self.assertEqual(code, 1)

    def test_star_not_found(self):
        with patch("quiver.harness.commands.load_registry", return_value={}), patch(
            "quiver.harness.commands.is_starred", return_value=False
        ):
            code, _ = _run(cmd_star, ["nope"])
        self.assertEqual(code, 1)

    def test_add_usage(self):
        code, _ = _run(cmd_add, ["only-a-name"])
        self.assertEqual(code, 1)

    def test_help_unknown_command(self):
        code, _ = _run(cmd_help, ["bogus"])
        self.assertEqual(code, 1)

    def test_models_unknown_flag(self):
        code, out = _run(cmd_models, ["--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown argument: --bogus", out)


class UnknownArgRejectionTest(unittest.TestCase):
    """A leftover dash-token is an error, not a filter or a no-op."""

    def test_list_unknown_flag(self):
        code, out = _run(cmd_list, ["--verbse"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown flag: --verbse", out)

    def test_list_extra_positional(self):
        code, out = _run(cmd_list, ["tag", "junk"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: junk", out)

    def test_skills_unknown_flag(self):
        code, out = _run(cmd_skills, ["--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown flag: --bogus", out)

    def test_find_trailing_junk(self):
        code, out = _run(cmd_find, ["skills", "junk"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: junk", out)

    def test_providers_list_unknown_flag(self):
        with TemporaryDirectory() as tmp:
            patches = _provider_patches(Path(tmp))
            with patches[0], patches[1], patches[2]:
                code, out = _run(provider_list, ["--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown flag: --bogus", out)

    def test_providers_list_extra_positional(self):
        with TemporaryDirectory() as tmp:
            patches = _provider_patches(Path(tmp))
            with patches[0], patches[1], patches[2]:
                code, out = _run(provider_list, ["a", "b"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: b", out)

    def test_providers_info_extra_positional(self):
        with TemporaryDirectory() as tmp:
            patches = _provider_patches(Path(tmp))
            with patches[0], patches[1], patches[2]:
                code, out = _run(provider_info, ["a", "b"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: b", out)

    def test_providers_remove_extra_positional(self):
        with TemporaryDirectory() as tmp:
            patches = _provider_patches(Path(tmp))
            with patches[0], patches[1], patches[2]:
                code, out = _run(provider_remove, ["a", "b"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: b", out)

    def test_add_unknown_flag(self):
        code, out = _run(cmd_add, ["demo", "demo-cmd", "--bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown flag: --bogus", out)

    def test_star_extra_positional(self):
        code, out = _run(cmd_star, ["a", "b"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: b", out)

    def test_star_clear_extra_positional_does_not_clear(self):
        with patch("quiver.harness.commands.load_registry", return_value={}), \
                patch("quiver.harness.stars.save_stars") as save_stars:
            code, out = _run(cmd_star, ["clear", "junk"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: junk", out)
        save_stars.assert_not_called()

    def test_list_edit_reset_extra_positional_does_not_reset(self):
        with patch("quiver.harness.commands.save_columns") as save_columns, \
                patch("quiver.harness.commands.save_window") as save_window:
            code, out = _run(cmd_list_edit, ["--reset", "junk"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: junk", out)
        save_columns.assert_not_called()
        save_window.assert_not_called()

    def test_autocomplete_extra_positional(self):
        code, out = _run(cmd_autocomplete, ["zsh", "junk"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: junk", out)


class NoArgCommandsRejectArgsTest(unittest.TestCase):
    """Commands that take no arguments reject any they get."""

    def test_check(self):
        code, out = _run(cmd_check, ["x"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: x", out)

    def test_tags(self):
        code, out = _run(cmd_tags, ["x"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: x", out)

    def test_aliases(self):
        code, out = _run(cmd_aliases, ["x"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: x", out)

    def test_doctor(self):
        code, out = _run(cmd_doctor, ["x"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: x", out)

    def test_list_edit(self):
        code, out = _run(cmd_list_edit, ["x"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: x", out)

    def test_list_legend(self):
        code, out = _run(cmd_list_legend, ["x"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: x", out)

    def test_harness_edit(self):
        code, out = _run(cmd_harness_edit, ["x"])
        self.assertEqual(code, 1)
        self.assertIn("Unexpected args: x", out)


if __name__ == "__main__":
    unittest.main()
