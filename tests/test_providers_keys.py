"""Unit tests for quiver.providers.keys — masking/discovery/read helpers."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from quiver.providers.keys import (
    default_keys_dir,
    find_key_file,
    mask_key,
    read_key,
    read_shell_export_keys,
)


class MaskKeyTest(unittest.TestCase):
    def test_none_returns_dash(self):
        self.assertEqual(mask_key(None), "-")

    def test_empty_returns_dash(self):
        self.assertEqual(mask_key(""), "-")
        self.assertEqual(mask_key("   "), "-")

    def test_short_key_uses_three_chars_fallback(self):
        self.assertEqual(mask_key("abc"), "abc*** (3)")
        self.assertEqual(mask_key("supershort"), "sup*** (10)")
        self.assertEqual(mask_key("abcdefghijkl"), "abc*** (12)")  # boundary for short

    def test_long_key_uses_eight_plus_four(self):
        key = "abcdefghijklmnopqrstuvwxyz123456"  # 32 chars
        out = mask_key(key)
        self.assertEqual(out, "abcdefgh***3456 (32)")
        # 16 chars is the smallest key that still uses the long format.
        self.assertEqual(mask_key("abcdefghijklmnop"), "abcdefgh***mnop (16)")
        # 13 chars (just above the short threshold) still gets the long format.
        self.assertEqual(mask_key("abcdefghijklm"), "abcdefgh***jklm (13)")

    def test_strips_whitespace_before_masking(self):
        self.assertEqual(mask_key("  abcdefghijklmnop  "), "abcdefgh***mnop (16)")


class FindKeyFileTest(unittest.TestCase):
    def test_returns_basename_under_keys_dir(self):
        info = {"key_filename": "openai"}
        path = find_key_file(info, Path("/tmp/keys"))
        self.assertEqual(path, Path("/tmp/keys/openai"))

    def test_missing_filename_returns_none(self):
        self.assertIsNone(find_key_file({}, Path("/tmp/keys")))
        self.assertIsNone(find_key_file({"key_filename": ""}, Path("/tmp/keys")))


class ReadKeyTest(unittest.TestCase):
    def test_reads_existing_file(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "openai"
            f.write_text("sk-proj-test123\n")
            self.assertEqual(read_key(f), "sk-proj-test123")

    def test_reads_existing_file_with_trailing_whitespace(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "openai"
            f.write_text("sk-proj-test123   \n\n\n")
            self.assertEqual(read_key(f), "sk-proj-test123")

    def test_missing_file_returns_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(read_key(Path(tmp) / "missing"))

    def test_none_returns_none(self):
        self.assertIsNone(read_key(None))

    def test_empty_file_returns_none(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "openai"
            f.write_text("\n")
            self.assertIsNone(read_key(f))


class DefaultKeysDirTest(unittest.TestCase):
    def test_default_keys_dir_points_to_dot_api_keys(self):
        self.assertEqual(default_keys_dir(Path("/tmp/home")), Path("/tmp/home/.api_keys"))

    def test_default_keys_dir_defaults_to_home(self):
        self.assertEqual(default_keys_dir(), Path.home() / ".api_keys")


class ReadShellExportKeysTest(unittest.TestCase):
    def test_extracts_export_with_value(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "openai"
            f.write_text("export OPENAI_API_KEY=sk-proj-AbCdEfGh1234\n")
            self.assertEqual(
                read_shell_export_keys(f, ["OPENAI_API_KEY"]),
                ("sk-proj-AbCdEfGh1234", "OPENAI_API_KEY"),
            )

    def test_extracts_unquoted_value(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".api_keys"
            f.write_text("OPENAI_API_KEY=sk-test-12345")
            self.assertEqual(
                read_shell_export_keys(f, ["OPENAI_API_KEY"]),
                ("sk-test-12345", "OPENAI_API_KEY"),
            )

    def test_extracts_double_quoted_value(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".api_keys"
            f.write_text('export ANTHROPIC_API_KEY="sk-ant-api03-abcdef"\n')
            self.assertEqual(
                read_shell_export_keys(f, ["ANTHROPIC_API_KEY"]),
                ("sk-ant-api03-abcdef", "ANTHROPIC_API_KEY"),
            )

    def test_extracts_single_quoted_value(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".api_keys"
            f.write_text("export X='abc123'\n")
            self.assertEqual(
                read_shell_export_keys(f, ["X"]),
                ("abc123", "X"),
            )

    def test_returns_first_matching_env_var(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".api_keys"
            f.write_text(
                "export FOO_API_KEY=foo\n"
                "export BAR_API_KEY=bar\n"
                "export BAZ_API_KEY=baz\n"
            )
            self.assertEqual(
                read_shell_export_keys(f, ["NOPE", "BAR_API_KEY", "BAZ_API_KEY"]),
                ("bar", "BAR_API_KEY"),
            )

    def test_swallows_comments_and_blank_lines(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".api_keys"
            f.write_text(
                "# top comment\n"
                "\n"
                "# OpenAI\n"
                "export OPENAI_API_KEY=sk-proj-OK12345\n"
                "\n"
                "# Anthropic\n"
                "export ANTHROPIC_API_KEY=sk-ant-OK12345\n"
            )
            self.assertEqual(
                read_shell_export_keys(f, ["ANTHROPIC_API_KEY"]),
                ("sk-ant-OK12345", "ANTHROPIC_API_KEY"),
            )

    def test_returns_none_when_no_env_var_matches(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".api_keys"
            f.write_text("export SOMETHING_ELSE=value\n")
            self.assertIsNone(read_shell_export_keys(f, ["OPENAI_API_KEY"]))

    def test_returns_none_when_path_does_not_exist(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(
                read_shell_export_keys(Path(tmp) / "missing", ["OPENAI_API_KEY"])
            )

    def test_returns_none_when_path_is_a_directory(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp) / ".api_keys"
            d.mkdir()
            self.assertIsNone(read_shell_export_keys(d, ["OPENAI_API_KEY"]))

    def test_handles_realistic_shell_export_block(self):
        """Mirror the actual ~/.api_keys layout the user has."""
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / ".api_keys"
            f.write_text(
                "# ==============================================================================\n"
                "# AI API Keys\n"
                "# ==============================================================================\n"
                "\n"
                "# OpenAI\n"
                "export OPENAI_API_KEY=sk-proj-realFake1\n"
                "\n"
                "# Anthropic\n"
                "export ANTHROPIC_API_KEY=sk-ant-realFake2\n"
                "\n"
                "# GitHub\n"
                "export GITHUB_TOKEN=ghp_realFake3\n"
            )
            self.assertEqual(
                read_shell_export_keys(f, ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]),
                ("sk-proj-realFake1", "OPENAI_API_KEY"),
            )
            self.assertEqual(
                read_shell_export_keys(f, ["GITHUB_TOKEN"]),
                ("ghp_realFake3", "GITHUB_TOKEN"),
            )


if __name__ == "__main__":
    unittest.main()
