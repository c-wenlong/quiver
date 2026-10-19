"""`swe mcp secret set`: writing ${NAME} values into the secrets file,
plus the sync refusal while a referenced name has no value.

Every value here is synthetic; nothing is a real credential.
"""

import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest

MCP_MODULE = "quiver.mcp"
PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _mcp_env(home: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(PROJECT_SRC) if not existing else f"{PROJECT_SRC}{os.pathsep}{existing}"
    )
    return env


class SetSecretTest(unittest.TestCase):
    """secrets.set_secret: quoting, in-place replace, modes and the seal."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self) -> pathlib.Path:
        return self.home / ".quiver" / "secrets" / "mcp" / ".api_keys"

    def test_creates_the_file_private_in_a_private_dir(self):
        from quiver.mcp import secrets

        path, created = secrets.set_secret("EXA_KEY", "v-1", home=self.home)
        self.assertEqual(path, self._file())
        self.assertTrue(created)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        self.assertEqual(path.read_text(), "export EXA_KEY='v-1'\n")
        self.assertEqual(secrets.load_secrets(self.home)["EXA_KEY"], "v-1")

    def test_seals_the_secrets_dir(self):
        from quiver import paths
        from quiver.mcp import secrets

        secrets.set_secret("A", "1", home=self.home)
        seal = self.home / ".quiver" / "secrets" / ".gitignore"
        self.assertEqual(seal.read_text(), paths.SECRETS_GITIGNORE_BODY)

    def test_replaces_every_duplicate_so_a_stale_later_line_cannot_win(self):
        from quiver.mcp import secrets

        path = self._file()
        path.parent.mkdir(parents=True)
        path.write_text("export A='one'\nexport A='two'\n")
        secrets.set_secret("A", "fresh", home=self.home)
        self.assertEqual(secrets.load_secrets(self.home)["A"], "fresh")

    def test_writes_through_a_symlinked_store(self):
        from quiver.mcp import secrets

        real = self.home / "elsewhere" / ".api_keys"
        real.parent.mkdir()
        real.write_text("export B='keep'\n")
        link = self._file()
        link.parent.mkdir(parents=True)
        link.symlink_to(real)
        secrets.set_secret("A", "v", home=self.home)
        self.assertTrue(link.is_symlink())
        self.assertEqual(real.read_text(), "export B='keep'\nexport A='v'\n")

    def test_replaces_in_place_and_keeps_other_lines(self):
        from quiver.mcp import secrets

        path = self._file()
        path.parent.mkdir(parents=True)
        path.write_text(
            "# a note\n"
            "export A='old'\n"
            "\n"
            "export B='keep-me'\n"
        )
        _, created = secrets.set_secret("A", "new-value", home=self.home)
        self.assertFalse(created)
        self.assertEqual(
            path.read_text(),
            "# a note\n"
            "export A='new-value'\n"
            "\n"
            "export B='keep-me'\n",
        )
        self.assertEqual(secrets.load_secrets(self.home)["B"], "keep-me")

    def test_appends_after_a_missing_trailing_newline(self):
        from quiver.mcp import secrets

        path = self._file()
        path.parent.mkdir(parents=True)
        path.write_text("export A='x'")
        secrets.set_secret("B", "y", home=self.home)
        self.assertEqual(path.read_text(), "export A='x'\nexport B='y'\n")

    def test_round_trip_for_each_quoting_shape(self):
        from quiver.mcp import secrets

        shapes = [
            ("PLAIN", "ntn_plain123"),
            ("SPACES", "a value with spaces"),
            ("SQUOTE", "it's quoted"),
            ("DQUOTE", 'has "double" quotes'),
            ("DOLLAR", "costs $5 or `lots`"),
        ]
        for name, value in shapes:
            with self.subTest(name=name):
                secrets.set_secret(name, value, home=self.home)
                self.assertEqual(secrets.load_secrets(self.home)[name], value)

    def test_refusals(self):
        from quiver.mcp import secrets

        for name in ("1BAD", "A-B", "a b", ""):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    secrets.set_secret(name, "v", home=self.home)
        with self.assertRaises(ValueError):
            secrets.set_secret("A", "", home=self.home)
        with self.assertRaises(ValueError):
            secrets.set_secret("A", "one\ntwo", home=self.home)
        # ' plus a shell-active double-quote char: no plain line stores it.
        with self.assertRaises(ValueError):
            secrets.set_secret("A", "it's $HOME", home=self.home)
        self.assertFalse(self._file().exists())

    def test_legacy_file_location_is_written(self):
        from quiver.mcp import secrets

        legacy = self.home / ".quiver" / "secrets" / ".api_keys"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("export OLD='o'\n")
        path, _ = secrets.set_secret("NEW_KEY", "n", home=self.home)
        self.assertEqual(path, legacy)
        self.assertEqual(secrets.load_secrets(self.home)["NEW_KEY"], "n")


class SecretCommandTest(unittest.TestCase):
    """The CLI surface: stdin only, no echo, --json shape."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home),
            input=stdin,
            capture_output=True,
            text=True,
        )

    def test_set_from_stdin_never_echoes_the_value(self):
        result = self.run_mcp("secret", "set", "MY_TOKEN", stdin="s3cr3t-value\n")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Saved MY_TOKEN", result.stdout)
        self.assertNotIn("s3cr3t-value", result.stdout + result.stderr)
        store = self.home / ".quiver" / "secrets" / "mcp" / ".api_keys"
        self.assertEqual(store.read_text(), "export MY_TOKEN='s3cr3t-value'\n")

    def test_json_envelope(self):
        result = self.run_mcp("secret", "set", "MY_TOKEN", "--json", stdin="v\n")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        doc = json.loads(result.stdout)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["data"]["name"], "MY_TOKEN")
        self.assertTrue(doc["data"]["created"])
        self.assertTrue(doc["data"]["path"].endswith(".api_keys"))
        self.assertNotIn("v\n", result.stdout)

    def test_empty_value_refused(self):
        result = self.run_mcp("secret", "set", "MY_TOKEN", stdin="")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(
            (self.home / ".quiver" / "secrets" / "mcp" / ".api_keys").exists())

    def test_newline_value_refused(self):
        result = self.run_mcp("secret", "set", "MY_TOKEN", stdin="a\nb\n")
        self.assertEqual(result.returncode, 1)

    def test_bad_name_refused(self):
        result = self.run_mcp("secret", "set", "1Nope", stdin="v\n")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(
            (self.home / ".quiver" / "secrets" / "mcp" / ".api_keys").exists())

    def test_a_flag_value_is_refused(self):
        result = self.run_mcp("secret", "set", "MY_TOKEN", "the-value", stdin="")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("the-value", result.stdout + result.stderr)


class SecretsMissingInventoryTest(unittest.TestCase):
    """server_inventory reports the hub's unsatisfied ${NAME} references."""

    def test_missing_names_come_from_the_hub_definition(self):
        from unittest import mock

        from quiver.mcp import cli
        from quiver.mcp.inventory import server_inventory

        hub = {
            "alpha": {"command": "a", "env": {"A_KEY": "${HAVE}", "B_KEY": "${MISSING}"}},
        }
        tool_data = {
            "claude": {
                "alpha": {"command": "a", "env": {"A_KEY": "x", "B_KEY": "y"}},
                "beta": {"command": "b"},
            }
        }
        with mock.patch.object(cli, "get_hub_servers", return_value=hub), \
             mock.patch("quiver.mcp.inventory.load_secrets",
                        return_value={"HAVE": "v"}):
            rows = [row for row, _ in server_inventory(tool_data)]
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["alpha"]["secrets_missing"], ["MISSING"])
        self.assertEqual(by_name["beta"]["secrets_missing"], [])


class SyncSecretRefusalTest(unittest.TestCase):
    """Hub -> harness refuses while a selected server's ${NAME} is unsatisfied."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)

        swe_cfg = self.home / ".quiver" / "config"
        swe_cfg.mkdir(parents=True, exist_ok=True)
        (swe_cfg / "tools.json").write_text(
            json.dumps({"claude": {"aliases": ["cc"]}}) + "\n"
        )
        (self.home / ".quiver" / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fixture": {
                            "command": "fixture-mcp",
                            "env": {"FIXTURE_TOKEN": "${FIXTURE_TOKEN}"},
                        }
                    }
                }
            )
            + "\n"
        )
        (self.home / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "fixture": {
                            "command": "fixture-mcp",
                            "env": {"FIXTURE_TOKEN": "old-value"},
                        }
                    },
                    "other": "state",
                }
            )
            + "\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_mcp(self, *args, stdin=""):
        return subprocess.run(
            [sys.executable, "-m", MCP_MODULE, *args],
            env=_mcp_env(self.home),
            input=stdin,
            capture_output=True,
            text=True,
        )

    def claude(self):
        return json.loads((self.home / ".claude.json").read_text())

    def test_sync_refuses_until_the_value_is_set(self):
        before = (self.home / ".claude.json").read_text()
        for extra in ([], ["--dry-run"], ["--json"]):
            result = self.run_mcp("sync", "quiver", "claude",
                                  "--no-interactive", *extra)
            self.assertEqual(result.returncode, 1, msg=extra)
            text = result.stdout + result.stderr
            self.assertIn("no value for FIXTURE_TOKEN", text)
            self.assertIn("used by fixture", text)
            self.assertIn("swe mcp secret set FIXTURE_TOKEN", text)
            self.assertEqual((self.home / ".claude.json").read_text(), before)
            self.assertNotIn("FIXTURE-VALUE", text)

        result = self.run_mcp("secret", "set", "FIXTURE_TOKEN",
                              stdin="FIXTURE-VALUE\n")
        self.assertEqual(result.returncode, 0, msg=result.stderr)

        result = self.run_mcp("sync", "quiver", "claude",
                              "--no-interactive", "--force")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        written = self.claude()
        self.assertEqual(
            written["mcpServers"]["fixture"]["env"]["FIXTURE_TOKEN"],
            "FIXTURE-VALUE")
        self.assertEqual(written["other"], "state")

    def test_list_reports_missing_and_then_clear(self):
        def missing_names():
            result = self.run_mcp("list", "--json")
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            doc = json.loads(result.stdout)
            return {s["name"]: s["secrets_missing"] for s in doc["data"]["servers"]}

        self.assertEqual(missing_names()["fixture"], ["FIXTURE_TOKEN"])
        result = self.run_mcp("secret", "set", "FIXTURE_TOKEN", stdin="x\n")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(missing_names()["fixture"], [])


if __name__ == "__main__":
    unittest.main()
