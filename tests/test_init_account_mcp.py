"""accounts.sync.mcp: the hub's MCP servers merged into extra account homes.

Every test builds a throwaway home with fake Claude and Codex account homes
under ~/.swe/accounts/<vendor>/<id>/home, a hub mcp.json, and an
account .claude.json carrying a fake login.
"""

import json
import os
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from swe.init import account_mcp, accounts
from swe.init import commands as init_commands

from tests._tempdir import temp_dir

WORK = {"id": "claude-work", "vendor": "claude", "name": "Work", "isDefault": False}
CODEX = {"id": "codex-alt", "vendor": "codex", "name": "Alt", "isDefault": False}

# Claude Code's own layout: two-space JSON, login and projects beside the
# servers, non-ASCII text that a re-dump with ensure_ascii would escape.
CLAUDE_JSON = """{
  "numStartups": 12,
  "oauthAccount": {
    "accountUuid": "0000-1111",
    "emailAddress": "work@example.com",
    "displayName": "Zoë"
  },
  "projects": {
    "/Users/me/repo": {
      "allowedTools": [],
      "mcpServers": {"inner": {"command": "keep-me"}}
    }
  },
  "mcpServers": {
    "mine": {
      "type": "stdio",
      "command": "my-own",
      "args": []
    }
  },
  "hasCompletedOnboarding": true
}
"""

CODEX_TOML = """# my codex config
model = "gpt-5"   # trailing comment

[mcp_servers.mine]
command = 'my-own'
args = ["a"]

[projects."/Users/me/repo"]
trust_level = "trusted"
"""


def _home(case) -> Path:
    home = temp_dir(case).resolve()
    (home / ".swe" / "config").mkdir(parents=True)
    return home


def _profiles(home: Path, *profiles: dict) -> None:
    root = home / ".swe" / "accounts"
    root.mkdir(parents=True, exist_ok=True)
    (root / "profiles.json").write_text(json.dumps({"schema": 1, "profiles": list(profiles)}))
    for p in profiles:
        (root / p["vendor"] / p["id"] / "home").mkdir(parents=True, exist_ok=True)


def _acct(home: Path, vendor: str = "claude", pid: str = "claude-work") -> Path:
    return home / ".swe" / "accounts" / vendor / pid / "home"


def _flags(home: Path, **flags) -> None:
    (home / ".swe" / "config" / "config.json").write_text(
        json.dumps({"accounts": {"sync": flags}}))


def _hub(home: Path, servers: dict, **extra) -> None:
    (home / ".swe" / "mcp.json").write_text(json.dumps({"mcpServers": servers, **extra}))


def _sync(home: Path, check_only: bool = False):
    rows, notes = accounts.sync_accounts(home, [], {}, check_only=check_only)
    return {(r.primitive, r.status.label, r.name): r.result for r in rows}, notes


HUB = {
    "github": {"command": "npx", "args": ["-y", "gh-mcp"], "env": {"TOKEN": "${GH_TOKEN}"}},
    "docs": {"url": "https://docs.example.com/mcp"},
}


def _setup(case, *, claude_json: str | None = CLAUDE_JSON, codex: str | None = CODEX_TOML):
    home = _home(case)
    _profiles(home, WORK, CODEX)
    _hub(home, HUB)
    secrets = home / ".swe" / "secrets" / "mcp"
    secrets.mkdir(parents=True)
    (secrets / ".api_keys").write_text("export GH_TOKEN=tok-123\n")
    if claude_json is not None:
        (_acct(home) / ".claude.json").write_text(claude_json, encoding="utf-8")
        os.chmod(_acct(home) / ".claude.json", 0o600)
    if codex is not None:
        (_acct(home, "codex", "codex-alt") / "config.toml").write_text(codex)
    return home


def _claude(home: Path) -> dict:
    return json.loads((_acct(home) / ".claude.json").read_text(encoding="utf-8"))


def _codex(home: Path) -> dict:
    return tomllib.loads((_acct(home, "codex", "codex-alt") / "config.toml").read_text())


def _without_servers(text: str) -> str:
    """The .claude.json text with the top-level mcpServers value blanked."""
    from swe.mcp.cli import _jsonc_key_span

    start, end = _jsonc_key_span(text, "mcpServers")
    return text[:start] + text[end:]


class OffTest(unittest.TestCase):
    def test_off_by_default_touches_nothing(self):
        home = _setup(self)
        before = (_acct(home) / ".claude.json").read_bytes()
        self.assertEqual(_sync(home), ({}, []))
        self.assertEqual((_acct(home) / ".claude.json").read_bytes(), before)
        self.assertFalse(account_mcp.record_file(home).exists())


class OnTest(unittest.TestCase):
    def test_claude_gets_only_mcp_servers_and_login_bytes_stay(self):
        home = _setup(self)
        _flags(home, mcp=True)
        before = (_acct(home) / ".claude.json").read_text(encoding="utf-8")
        rows, notes = _sync(home)
        self.assertEqual(notes, [])
        self.assertEqual(rows[("mcp", "claude/claude-work", "github")], "linked")
        self.assertEqual(rows[("mcp", "claude/claude-work", "docs")], "linked")
        after = (_acct(home) / ".claude.json").read_text(encoding="utf-8")
        # Every byte outside the top-level mcpServers value is the same.
        self.assertEqual(_without_servers(after), _without_servers(before))
        data = _claude(home)
        self.assertEqual(data["mcpServers"]["github"],
                         {"command": "npx", "args": ["-y", "gh-mcp"], "env": {"TOKEN": "tok-123"}})
        self.assertEqual(data["mcpServers"]["docs"],
                         {"url": "https://docs.example.com/mcp", "type": "http"})
        self.assertEqual(data["mcpServers"]["mine"]["command"], "my-own")
        self.assertEqual(data["projects"]["/Users/me/repo"]["mcpServers"],
                         {"inner": {"command": "keep-me"}})
        self.assertEqual(os.stat(_acct(home) / ".claude.json").st_mode & 0o777, 0o600)
        # A backup of the old file, in ~/.swe/backups.
        backups = list((home / ".swe" / "backups").glob("*claude-work_home_.claude.json.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), before)
        # The record names what swe wrote, without the secret in it.
        record = account_mcp.load_record(home)
        self.assertEqual({(e["account"], e["name"]) for e in record},
                         {("claude/claude-work", "github"), ("claude/claude-work", "docs"),
                          ("codex/codex-alt", "github"), ("codex/codex-alt", "docs")})
        self.assertNotIn("tok-123", account_mcp.record_file(home).read_text())
        # Settled: a second run writes nothing.
        mtime = os.stat(_acct(home) / ".claude.json").st_mtime_ns
        self.assertEqual(set(_sync(home)[0].values()), {"linked"})
        self.assertEqual(os.stat(_acct(home) / ".claude.json").st_mtime_ns, mtime)

    def test_a_claude_json_without_servers_gets_the_member_added(self):
        login = '{\n  "oauthAccount": {\n    "emailAddress": "w@example.com"\n  },\n  "n": 1\n}\n'
        home = _setup(self, claude_json=login)
        _flags(home, mcp=True)
        _sync(home)
        after = (_acct(home) / ".claude.json").read_text()
        self.assertTrue(after.startswith(login[:login.rindex("\n}")]))
        data = _claude(home)
        self.assertEqual(data["oauthAccount"], {"emailAddress": "w@example.com"})
        self.assertEqual(set(data["mcpServers"]), {"github", "docs"})
        _flags(home, mcp=False)
        _sync(home)
        self.assertEqual(_claude(home), {"oauthAccount": {"emailAddress": "w@example.com"},
                                         "n": 1, "mcpServers": {}})

    def test_servers_as_the_last_member_round_trip_to_the_same_bytes(self):
        login = ('{\n  "oauthAccount": {"emailAddress": "w@example.com"},\n'
                 '  "mcpServers": {\n    "own": {\n      "command": "x"\n    }\n  }\n}\n')
        home = _setup(self, claude_json=login)
        _flags(home, mcp=True)
        _sync(home)
        self.assertIn("github", _claude(home)["mcpServers"])
        _flags(home, mcp=False)
        _sync(home)
        self.assertEqual((_acct(home) / ".claude.json").read_text(), login)

    def test_codex_gets_mcp_tables_and_the_rest_keeps_its_bytes(self):
        home = _setup(self)
        _flags(home, mcp=True)
        _sync(home)
        text = (_acct(home, "codex", "codex-alt") / "config.toml").read_text()
        self.assertTrue(text.startswith(CODEX_TOML))
        doc = _codex(home)
        self.assertEqual(doc["model"], "gpt-5")
        self.assertEqual(doc["projects"], {"/Users/me/repo": {"trust_level": "trusted"}})
        self.assertEqual(doc["mcp_servers"]["mine"], {"command": "my-own", "args": ["a"]})
        self.assertEqual(doc["mcp_servers"]["github"]["env"], {"TOKEN": "tok-123"})
        self.assertEqual(doc["mcp_servers"]["docs"], {"url": "https://docs.example.com/mcp"})

    def test_codex_without_a_config_gets_one(self):
        home = _setup(self, codex=None)
        _flags(home, mcp=True)
        _sync(home)
        self.assertEqual(set(_codex(home)["mcp_servers"]), {"github", "docs"})
        path = _acct(home, "codex", "codex-alt") / "config.toml"
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_claude_without_a_claude_json_is_left_to_sign_in(self):
        home = _setup(self, claude_json=None)
        _flags(home, mcp=True)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", None)], "skipped")
        self.assertFalse((_acct(home) / ".claude.json").exists())

    def test_check_only_writes_nothing(self):
        home = _setup(self)
        _flags(home, mcp=True)
        before = (_acct(home) / ".claude.json").read_bytes()
        rows, _ = _sync(home, check_only=True)
        self.assertEqual(rows[("mcp", "claude/claude-work", "github")], "would-create")
        self.assertEqual((_acct(home) / ".claude.json").read_bytes(), before)
        self.assertFalse(account_mcp.record_file(home).exists())
        self.assertFalse(account_mcp.key_file(home).exists())

    def test_a_missing_secret_skips_that_server(self):
        home = _setup(self)
        (home / ".swe" / "secrets" / "mcp" / ".api_keys").write_text("")
        _flags(home, mcp=True)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", "github")], "skipped")
        self.assertNotIn("github", _claude(home)["mcpServers"])
        self.assertIn("docs", _claude(home)["mcpServers"])

    def test_hub_changes_update_ours_and_drop_what_left_the_hub(self):
        home = _setup(self)
        _flags(home, mcp=True)
        _sync(home)
        _hub(home, {"docs": {"url": "https://docs.example.com/v2"}})
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", "docs")], "linked")
        self.assertEqual(rows[("mcp", "claude/claude-work", "github")], "unlinked")
        servers = _claude(home)["mcpServers"]
        self.assertEqual(set(servers), {"mine", "docs"})
        self.assertEqual(servers["docs"]["url"], "https://docs.example.com/v2")
        self.assertEqual(set(_codex(home)["mcp_servers"]), {"mine", "docs"})

    def test_shared_servers_go_out_as_the_gateway_url(self):
        home = _setup(self)
        _hub(home, {"github": {**HUB["github"], "shared": True}},
             gateway={"enabled": True, "port": 9000})
        _flags(home, mcp=True)
        _sync(home)
        self.assertEqual(_claude(home)["mcpServers"]["github"],
                         {"url": "http://127.0.0.1:9000/mcp/github", "type": "http"})


class ConflictTest(unittest.TestCase):
    def test_the_accounts_own_server_of_the_same_name_is_left_alone(self):
        home = _setup(self)
        _hub(home, {"mine": {"command": "hub-version"}})
        _flags(home, mcp=True)
        before = (_acct(home) / ".claude.json").read_bytes()
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", "mine")], "conflict")
        self.assertEqual(rows[("mcp", "codex/codex-alt", "mine")], "conflict")
        self.assertEqual((_acct(home) / ".claude.json").read_bytes(), before)
        self.assertEqual(_codex(home)["mcp_servers"]["mine"]["command"], "my-own")
        self.assertEqual(account_mcp.load_record(home), [])

    def test_a_server_edited_in_the_account_stops_being_ours(self):
        home = _setup(self)
        _flags(home, mcp=True)
        _sync(home)
        data = _claude(home)
        data["mcpServers"]["docs"]["headers"] = {"X": "1"}
        (_acct(home) / ".claude.json").write_text(json.dumps(data, indent=2))
        _flags(home, mcp=False)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", "github")], "unlinked")
        self.assertNotIn(("mcp", "claude/claude-work", "docs"), rows)
        self.assertEqual(set(_claude(home)["mcpServers"]), {"mine", "docs"})
        names = {(e["account"], e["name"]) for e in account_mcp.load_record(home)}
        self.assertEqual(names, set())

    def test_the_conflict_row_shows_in_json(self):
        home = _setup(self)
        _hub(home, {"mine": {"command": "hub-version"}})
        _flags(home, mcp=True)
        doc = init_commands.check_document(home)
        row = next(r for r in doc["accounts"] if r["account"] == "claude/claude-work")
        self.assertEqual((row["primitive"], row["name"], row["state"]), ("mcp", "mine", "conflict"))
        self.assertTrue(doc["account_sync"]["mcp"])


class SwitchOffTest(unittest.TestCase):
    def test_off_removes_only_what_swe_added(self):
        home = _setup(self)
        _flags(home, mcp=True)
        _sync(home)
        _flags(home, mcp=False)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", "github")], "unlinked")
        self.assertEqual(rows[("mcp", "codex/codex-alt", "docs")], "unlinked")
        after = (_acct(home) / ".claude.json").read_text(encoding="utf-8")
        self.assertEqual(json.loads(after), json.loads(CLAUDE_JSON))
        self.assertEqual(_without_servers(after), _without_servers(CLAUDE_JSON))
        self.assertEqual((_acct(home, "codex", "codex-alt") / "config.toml").read_text(),
                         CODEX_TOML)
        self.assertEqual(account_mcp.load_record(home), [])
        # Nothing left to do.
        self.assertEqual(_sync(home), ({}, []))

    def test_a_lost_key_leaves_servers_in_place(self):
        home = _setup(self)
        _flags(home, mcp=True)
        _sync(home)
        account_mcp.key_file(home).unlink()
        _flags(home, mcp=False)
        _sync(home)
        self.assertIn("github", _claude(home)["mcpServers"])


class ReviewFixesTest(unittest.TestCase):
    def test_a_linkignored_account_is_never_written(self):
        home = _setup(self)
        _flags(home, mcp=True)
        before = (_acct(home) / ".claude.json").read_bytes()
        rows, _ = accounts.sync_accounts(home, [".swe/accounts/claude"], {}, check_only=False)
        got = {(r.status.label, r.name): r.result for r in rows}
        self.assertEqual(got[("claude/claude-work", None)], "ignored")
        self.assertEqual((_acct(home) / ".claude.json").read_bytes(), before)
        self.assertIn("github", _codex(home)["mcp_servers"])

    def test_backups_are_private_even_from_a_readable_file(self):
        home = _setup(self)
        os.chmod(_acct(home) / ".claude.json", 0o644)
        _flags(home, mcp=True)
        _sync(home)
        backups = list((home / ".swe" / "backups").iterdir())
        self.assertTrue(backups)
        for b in backups:
            self.assertEqual(os.stat(b).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(_acct(home) / ".claude.json").st_mode & 0o777, 0o644)

    def test_a_damaged_secrets_file_skips_with_a_note(self):
        home = _setup(self)
        (home / ".swe" / "secrets" / "mcp" / ".api_keys").write_bytes(b"\xff\xfe bad")
        _flags(home, mcp=True)
        before = (_acct(home) / ".claude.json").read_bytes()
        _, notes = _sync(home)
        self.assertTrue(notes and "MCP account sync skipped" in notes[0])
        self.assertEqual((_acct(home) / ".claude.json").read_bytes(), before)


class RefuseTest(unittest.TestCase):
    def test_invalid_json_is_reported_and_never_written(self):
        home = _setup(self, claude_json='{"oauthAccount": {"x": 1}, oops')
        _flags(home, mcp=True)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", None)], "blocked")
        self.assertEqual((_acct(home) / ".claude.json").read_text(),
                         '{"oauthAccount": {"x": 1}, oops')
        # The Codex account still synced.
        self.assertIn("github", _codex(home)["mcp_servers"])

    def test_invalid_toml_is_reported_and_never_written(self):
        home = _setup(self, codex="model = \n[[[")
        _flags(home, mcp=True)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "codex/codex-alt", None)], "blocked")
        self.assertEqual((_acct(home, "codex", "codex-alt") / "config.toml").read_text(),
                         "model = \n[[[")

    def test_mcp_servers_of_the_wrong_type_is_refused(self):
        home = _setup(self, claude_json='{"mcpServers": [], "oauthAccount": {}}')
        _flags(home, mcp=True)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", None)], "blocked")

    def test_an_unreadable_hub_skips_the_pass_and_removes_nothing(self):
        home = _setup(self)
        _flags(home, mcp=True)
        _sync(home)
        (home / ".swe" / "mcp.json").write_text("{broken")
        before = (_acct(home) / ".claude.json").read_bytes()
        rows, notes = _sync(home)
        self.assertTrue(notes and "MCP account sync skipped" in notes[0])
        self.assertEqual((_acct(home) / ".claude.json").read_bytes(), before)

    def test_a_damaged_record_skips_the_pass(self):
        home = _setup(self)
        _flags(home, mcp=True)
        account_mcp.record_file(home).parent.mkdir(parents=True, exist_ok=True)
        account_mcp.record_file(home).write_text('{"servers": [{"name": 1}]}')
        before = (_acct(home) / ".claude.json").read_bytes()
        _, notes = _sync(home)
        self.assertTrue(notes)
        self.assertEqual((_acct(home) / ".claude.json").read_bytes(), before)

    def test_a_claude_json_linked_out_of_the_home_is_left_alone(self):
        home = _setup(self, claude_json=None)
        outside = home / ".claude.json"
        outside.write_text(CLAUDE_JSON)
        (_acct(home) / ".claude.json").symlink_to(outside)
        _flags(home, mcp=True)
        rows, _ = _sync(home)
        self.assertEqual(rows[("mcp", "claude/claude-work", None)], "conflict")
        self.assertEqual(outside.read_text(), CLAUDE_JSON)

    def test_a_file_rewritten_mid_sync_is_read_again(self):
        home = _setup(self)
        _flags(home, mcp=True)
        path = _acct(home) / ".claude.json"
        real_read = Path.read_bytes
        reads = {"n": 0}

        def read_bytes(p):
            out = real_read(p)
            if p == path:
                reads["n"] += 1
                if reads["n"] == 2:  # the check right before the rename
                    data = json.loads(out)
                    data["numStartups"] = 13
                    p.write_text(json.dumps(data, indent=2))
                    return real_read(p)
            return out

        with mock.patch.object(Path, "read_bytes", read_bytes):
            _sync(home)
        data = _claude(home)
        self.assertEqual(data["numStartups"], 13)
        self.assertIn("github", data["mcpServers"])


if __name__ == "__main__":
    unittest.main()
