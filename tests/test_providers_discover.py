"""Tests for the discover layer that joins provider metadata + .api_keys/."""

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from quiver.providers.commands import cmd_add, cmd_info, cmd_list, cmd_remove
from quiver.providers.defaults import DEFAULT_PROVIDERS
from quiver.providers.discover import discover_provider_keys


def _registry_patches(config_dir: Path, registry_file: Path):
    return (
        patch("quiver.providers.registry.CONFIG_DIR", config_dir),
        patch("quiver.providers.registry.PROVIDERS_REGISTRY_FILE", registry_file),
    )


class DiscoverProviderKeysTest(unittest.TestCase):
    def test_returns_one_row_per_provider(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.mkdir()
            rows = discover_provider_keys(dict(DEFAULT_PROVIDERS), keys_dir)
            self.assertEqual(len(rows), len(DEFAULT_PROVIDERS))

    def test_present_key_is_masked(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.mkdir()
            (keys_dir / "openai").write_text(
                "sk-proj-AbCdEfGh1234567890" * 2
            )

            rows = discover_provider_keys(
                {"openai": DEFAULT_PROVIDERS["openai"]}, keys_dir
            )
            openai_row = next(r for r in rows if r["name"] == "openai")
            self.assertNotEqual(openai_row["masked"], "-")
            self.assertIn("***", openai_row["masked"])
            self.assertTrue(openai_row["raw_key"].startswith("sk-proj-"))
            self.assertTrue(openai_row["raw_key"].endswith("7890"))
            self.assertEqual(openai_row["key_file"], str(keys_dir / "openai"))

    def test_missing_key_shows_dash(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.mkdir()
            rows = discover_provider_keys(
                {"anthropic": DEFAULT_PROVIDERS["anthropic"]}, keys_dir
            )
            row = rows[0]
            self.assertEqual(row["masked"], "-")
            self.assertIsNone(row["raw_key"])

    def test_provider_without_filename_yields_none_key_file(self):
        providers = {"myprov": {"name": "myprov", "env_vars": ["MY_KEY"]}}
        with TemporaryDirectory() as tmp:
            keys_dir = Path(tmp) / ".api_keys"
            keys_dir.mkdir()
            rows = discover_provider_keys(providers, keys_dir)
            self.assertIsNone(rows[0]["key_file"])
            self.assertEqual(rows[0]["masked"], "-")


class CommandsSmokeTest(unittest.TestCase):
    def _setup(self, tmp: Path):
        config_dir = tmp / ".config" / "swe"
        registry_file = config_dir / "providers.json"
        keys_dir = tmp / ".api_keys"
        keys_dir.mkdir()
        return config_dir, registry_file, keys_dir

    def test_cmd_list_does_not_raise(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                cmd_list([])
                cmd_list(["openai"])  # filter
                cmd_list(["-d", "openai"])  # with desc

    def test_cmd_list_with_one_key_present(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            (keys_dir / "openai").write_text("sk-proj-MyTestKeyValue12345")
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                cmd_list([])

    def test_cmd_info_for_known_provider(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            (keys_dir / "openai").write_text("sk-proj-AbCdEfGh12345678")
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                self.assertEqual(cmd_info(["openai"]), 0)
                self.assertEqual(cmd_info(["missing"]), 1)
                self.assertEqual(cmd_info([]), 1)

    def test_cmd_add_registers_new_provider(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                cmd_add(
                    [
                        "myprov",
                        "My Provider blurb",
                        "--env",
                        "MY_API_KEY",
                        "--url",
                        "https://example.com",
                        "--file",
                        "myprov",
                    ]
                )
                # Verify the file is now in the (mocked) providers.json
                from quiver.providers.registry import load_registry

                providers = load_registry()
                self.assertIn("myprov", providers)
                self.assertEqual(providers["myprov"]["url"], "https://example.com")
                self.assertEqual(providers["myprov"]["env_vars"], ["MY_API_KEY"])
                # `name` and `aliases` are derived from env_vars[0] at load
                # time (API_KEY is the source of truth) — the description
                # blurb only lands in `description`.
                self.assertEqual(providers["myprov"]["aliases"], ["my"])
                self.assertEqual(providers["myprov"]["key_filename"], "myprov")
                self.assertEqual(providers["myprov"]["name"], "My")
                self.assertEqual(
                    providers["myprov"]["description"], "My Provider blurb"
                )

    def test_cmd_add_then_remove_roundtrip(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                cmd_add(["myprov", "--env", "MY_KEY"])
                cmd_remove(["myprov"])
                cmd_remove(["myprov"])  # second remove: should report not found

    def test_cmd_remove_unknown_returns_1(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                self.assertEqual(cmd_remove(["does-not-exist"]), 1)

    def test_cmd_remove_does_not_delete_key_file(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            key_file = keys_dir / "openai"
            key_file.write_text("sk-proj-DontDeleteMe1234567890")
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                cmd_remove(["openai"])
                self.assertTrue(key_file.exists())
                self.assertEqual(key_file.read_text(), "sk-proj-DontDeleteMe1234567890")

    def test_cmd_list_with_api_keys_dir_override(self):
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            other_dir = tmp_path / "other_keys"
            other_dir.mkdir()
            (other_dir / "openai").write_text("sk-proj-OtherDirTest1234567")
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                cmd_list([f"--api-keys-dir={other_dir}"])

    def test_cmd_list_masks_keys_in_stdout_and_never_leaks_raw(self):
        """Regression test: ensure raw key never appears in list output."""
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            raw_key = "sk-proj-SuperSecretRawValue1234567890abcdefghijklmnop"
            (keys_dir / "openai").write_text(raw_key)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_list([]), 0)
                output = buf.getvalue()
            # Masked token is rendered.
            self.assertIn("sk-proj-", output)
            self.assertIn("***", output)
            # Raw key (or any meaningful chunk of it) MUST NOT appear.
            self.assertNotIn("SuperSecretRawValue", output)
            self.assertNotIn(raw_key, output)
            self.assertNotIn("sk-proj-SuperSecretRawValue", output)
            # Missing keys render as the dash placeholder.
            self.assertRegex(output, r"\b-\b")

    def test_cmd_info_masks_key_in_stdout(self):
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            raw_key = "sk-proj-InfoOutputSecret1234567890abcdefghijklmnop"
            (keys_dir / "openai").write_text(raw_key)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_info(["openai"]), 0)
                output = buf.getvalue()
            self.assertIn("***", output)
            self.assertNotIn(raw_key, output)
            self.assertNotIn("InfoOutputSecret", output)

    def test_cmd_list_renders_headers_and_separator(self):
        """Regression: cmd_list must produce a readable header row + separator."""
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_list([]), 0)
                output = buf.getvalue()
            for label in ("PROVIDER", "ALIASES", "API KEY", "URL"):
                self.assertIn(label, output)
            # Separator line is contiguous box-drawing chars under the headers.
            self.assertIn("─" * 10, output)

    def test_cmd_list_em_dash_for_providers_without_aliases(self):
        """Regression: empty alias column must not be 16 chars of whitespace.

        Several built-in defaults (mistral, cohere, deepseek, groq) ship
        with no aliases — without an em dash they'd render a column of
        empty space that looks like missing data."""
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    cmd_list([])
                output = buf.getvalue()
            self.assertIn("—", output)

    def test_cmd_list_keys_dir_helper_renders_tilde_under_home(self):
        """Regression: cmd_list footer reads `~/...` via the display helper.

        Patches the helper directly so the test never touches the global
        Path.home() class state.
        """
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "providers.json"
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands._display_keys_dir",
                return_value="~/.api_keys",
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_list([]), 0)
                output = buf.getvalue()
        self.assertIn("~/.api_keys", output)
        self.assertNotIn(str(Path.home()), output)

    def test_display_keys_dir_helper_under_and_outside_home(self):
        """Direct unit test for the helper."""
        from quiver.providers.commands import _display_keys_dir

        home = Path.home()
        # Inside $HOME → tilde.
        self.assertEqual(_display_keys_dir(home / ".api_keys"), "~/.api_keys")
        # Nested under $HOME (one level deeper).
        self.assertEqual(
            _display_keys_dir(home / "projects" / "keys"),
            "~/projects/keys",
        )
        # Outside $HOME → full path preserved.
        self.assertEqual(
            _display_keys_dir(Path("/tmp/somewhere/else")),
            "/tmp/somewhere/else",
        )

    def test_discover_provider_keys_reads_shell_export_file(self):
        """When keys_dir is a regular file, parse it as shell exports."""
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            keys_dir = tmp_path / ".api_keys"
            keys_dir.write_text(
                "# AI API Keys\n"
                "\n"
                "# OpenAI\n"
                "export OPENAI_API_KEY=sk-proj-DiscoverKey1234\n"
                "\n"
                "# Anthropic\n"
                "export ANTHROPIC_API_KEY=sk-ant-DiscoverKey1234\n"
                "\n"
                "# Mistral (no key set)\n"
            )
            providers = {
                "openai": DEFAULT_PROVIDERS["openai"],
                "anthropic": DEFAULT_PROVIDERS["anthropic"],
                "mistral": DEFAULT_PROVIDERS["mistral"],
            }
            rows = discover_provider_keys(providers, keys_dir)
            by_name = {r["name"]: r for r in rows}

            self.assertNotEqual(by_name["openai"]["masked"], "-")
            self.assertIn("***", by_name["openai"]["masked"])
            self.assertEqual(
                by_name["openai"]["raw_key"], "sk-proj-DiscoverKey1234"
            )
            self.assertEqual(by_name["openai"]["key_file"], str(keys_dir))
            self.assertEqual(by_name["openai"]["matched_env"], "OPENAI_API_KEY")

            self.assertNotEqual(by_name["anthropic"]["masked"], "-")
            self.assertEqual(
                by_name["anthropic"]["raw_key"], "sk-ant-DiscoverKey1234"
            )
            self.assertEqual(by_name["anthropic"]["matched_env"], "ANTHROPIC_API_KEY")

            # Mistral has env var declared but no matching export in the file.
            self.assertEqual(by_name["mistral"]["masked"], "-")
            self.assertIsNone(by_name["mistral"]["raw_key"])
            self.assertIsNone(by_name["mistral"]["matched_env"])

    def test_cmd_list_env_column_shows_matched_env_in_shell_export_mode(self):
        """Regression: cmd_list `ENV VAR` column surfaces the matched env var."""
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = self._setup(tmp_path)
            shell_keys = tmp_path / ".api_keys"
            shell_keys.write_text(
                "export OPENAI_API_KEY=sk-proj-listEnvColTest12345678\n"
                "export ANTHROPIC_API_KEY=sk-ant-listEnvColTest12345\n"
            )
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=shell_keys,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_list([]), 0)
                output = buf.getvalue()
            # ENV column header present.
            self.assertIn("ENV VAR", output)
            # Each matched provider's env-var name shows up in the body.
            self.assertIn("OPENAI_API_KEY", output)
            self.assertIn("ANTHROPIC_API_KEY", output)

    def test_cmd_info_surfaces_matched_env_in_shell_export_mode(self):
        """Regression: cmd_info `Env vars` line shows matched env_var prominently."""
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = self._setup(tmp_path)
            shell_keys = tmp_path / ".api_keys"
            shell_keys.write_text(
                "# Kimi (also recognised via MOONSHOT_API_KEY)\n"
                "export MOONSHOT_API_KEY=sk-infoEnvTest1234567890\n"
            )
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=shell_keys,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    self.assertEqual(cmd_info(["kimi"]), 0)
                output = buf.getvalue()
            # Matched env var (MOONSHOT) is shown prominently. The legacy
            # KIMI_API_KEY should be listable alongside as a dim alias.
            self.assertIn("MOONSHOT_API_KEY", output)
            self.assertIn("KIMI_API_KEY", output)
            # Key was masked, not leaked.
            self.assertNotIn("infoEnvTest", output)

    def test_cmd_list_prints_tip_when_zero_keys(self):
        """Regression: 0/N with keys shows actionable tip."""
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, keys_dir = self._setup(tmp_path)
            # keys_dir exists but is empty -> n_with == 0
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=keys_dir,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    cmd_list([])
                output = buf.getvalue()
            self.assertIn("Tip:", output)
            # Number of providers reflects the current DEFAULT_PROVIDERS catalog.
            self.assertIn(f"0/{len(DEFAULT_PROVIDERS)}", output)
            self.assertIn("swe providers info", output)

    def test_cmd_list_advisory_when_keys_dir_missing(self):
        """Regression: no keys dir produces a clear notice."""
        from contextlib import redirect_stdout

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir, registry_file, _ = self._setup(tmp_path)
            missing_dir = tmp_path / "no_such_dir"
            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch(
                "quiver.providers.commands.default_keys_dir",
                return_value=missing_dir,
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    cmd_list([])
                output = buf.getvalue()
            self.assertIn("No keys directory", output)
            # Advisory must come AFTER the table footer (i.e. printing
            # preserves the order: title → header → rows → footer → advisory).
            idx_notice = output.find("No keys directory")
            idx_table = output.find("─" * 10)
            self.assertGreater(idx_table, 0)
            self.assertGreater(idx_notice, idx_table)


if __name__ == "__main__":
    unittest.main()
