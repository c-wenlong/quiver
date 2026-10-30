"""slash.py: the "/" menu's command list, and its hub route."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.paths import config_dir_for
from quiver.remote import agent as agent_mod
from quiver.remote import slash, slash_builtins, supervise
from quiver.sessions.models import Session

from tests.test_remote_hub import _Server


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _names(rows):
    return [r["name"] for r in rows]


class _Home(unittest.TestCase):
    """A throwaway $HOME and project folder; caches start empty."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.proj = Path(tempfile.mkdtemp())
        (self.proj / ".git").mkdir()
        env = patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CODEX_HOME", None)
        slash.clear_cache()
        self.addCleanup(slash.clear_cache)


class TestFrontmatter(_Home):
    def test_description_and_argument_hint(self):
        md = _write(self.home / "c.md", (
            "---\n"
            "description: \"Open a PR for the branch\"\n"
            "argument-hint: [title]\n"
            "---\n"
            "Body text\n"
        ))
        meta = slash.read_meta(md)
        self.assertEqual(meta["description"], "Open a PR for the branch")
        self.assertEqual(meta["argument_hint"], "[title]")
        self.assertFalse(meta["hidden"])

    def test_folded_description_is_one_line(self):
        md = _write(self.home / "SKILL.md", (
            "---\nname: deploy\ndescription: >\n  Ship it\n  to prod\n---\n"
        ))
        meta = slash.read_meta(md)
        self.assertEqual(meta["name"], "deploy")
        self.assertEqual(meta["description"], "Ship it to prod")

    def test_missing_description_uses_first_body_line(self):
        md = _write(self.home / "c.md", "\n# Run the linters\n\nmore\n")
        self.assertEqual(slash.read_meta(md)["description"],
                         "Run the linters")

    def test_long_description_is_cut(self):
        md = _write(self.home / "c.md",
                    "---\ndescription: " + "word " * 100 + "\n---\n")
        desc = slash.read_meta(md)["description"]
        self.assertLessEqual(len(desc), slash.MAX_DESCRIPTION)
        self.assertTrue(desc.endswith("…"))

    def test_user_invocable_false_is_hidden(self):
        _write(self.home / ".claude/skills/secret/SKILL.md",
               "---\ndescription: x\nuser-invocable: false\n---\n")
        _write(self.home / ".claude/skills/open/SKILL.md",
               "---\ndescription: y\n---\n")
        self.assertEqual(
            _names(slash.custom_entries("claude", str(self.proj))),
            ["open"],
        )


class TestHostileFiles(_Home):
    """A repo controls its .claude folder; opening the menu must not hang,
    exhaust memory, or leak files from outside it."""

    def setUp(self):
        super().setUp()
        self.cmds = self.proj / ".claude" / "commands"
        _write(self.cmds / "ok.md", "---\ndescription: fine\n---\n")

    def names(self):
        return _names(slash.custom_entries("claude", str(self.proj)))

    @unittest.skipUnless(Path("/dev/zero").exists(), "needs /dev/zero")
    def test_symlink_to_dev_zero_is_skipped(self):
        (self.cmds / "hang.md").symlink_to("/dev/zero")
        skill = self.proj / ".claude" / "skills" / "hang"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").symlink_to("/dev/zero")
        self.assertEqual(self.names(), ["ok"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "needs mkfifo")
    def test_fifo_is_skipped_without_blocking(self):
        os.mkfifo(self.cmds / "pipe.md")
        self.assertEqual(self.names(), ["ok"])

    def test_symlink_out_of_the_folder_is_skipped(self):
        secret = _write(self.home / "secret.txt", "PRIVATE KEY\n")
        (self.cmds / "leak.md").symlink_to(secret)
        outside = self.home / "elsewhere"
        outside.mkdir()
        _write(outside / "far.md", "far\n")
        (self.cmds / "sub").symlink_to(outside)
        self.assertEqual(self.names(), ["ok"])

    def test_user_level_links_out_still_load_but_devices_do_not(self):
        # ~/.quiver/skills/x -> ~/.agents/skills/x is how users share
        # skills; only a project's folders are confined.
        real = self.home / ".agents/skills/shared"
        _write(real / "SKILL.md", "---\ndescription: s\n---\n")
        skills = self.home / ".claude/skills"
        skills.mkdir(parents=True)
        (skills / "shared").symlink_to(real)
        user_cmds = self.home / ".claude/commands"
        user_cmds.mkdir(parents=True)
        if Path("/dev/zero").exists():
            (user_cmds / "zero.md").symlink_to("/dev/zero")
        names = self.names()
        self.assertIn("shared", names)
        self.assertNotIn("zero", names)

    def test_link_loop_terminates(self):
        (self.cmds / "loop").symlink_to(self.cmds)
        self.assertEqual(self.names(), ["ok"])

    def test_big_file_is_read_only_to_the_cap(self):
        _write(self.cmds / "big.md",
               "---\ndescription: big one\n---\n" + "x" * (4 << 20))
        got = []
        real_read = os.read

        def read(fd, n):
            data = real_read(fd, n)
            got.append(len(data))
            return data

        with patch.object(slash.os, "read", side_effect=read):
            rows = {r["name"]: r for r in
                    slash.custom_entries("claude", str(self.proj))}
        self.assertEqual(rows["big"]["description"], "big one")
        self.assertLessEqual(sum(got), slash.MAX_READ * len(rows))


class TestClaudeSources(_Home):
    def test_sources_and_names(self):
        _write(self.home / ".claude/commands/standup.md",
               "---\ndescription: Daily standup\n---\n")
        _write(self.proj / ".claude/commands/frontend/component.md",
               "---\ndescription: New component\n---\n")
        _write(self.proj / ".claude/skills/release/SKILL.md",
               "---\ndescription: Cut a release\n---\n")
        rows = {r["name"]: r for r in
                slash.custom_entries("claude", str(self.proj))}
        self.assertEqual(rows["standup"]["source"], "user")
        self.assertEqual(rows["frontend:component"]["source"], "project")
        self.assertEqual(rows["release"]["source"], "skill")
        self.assertEqual(rows["release"]["description"], "Cut a release")

    def test_personal_beats_project_and_skill_beats_command(self):
        # code.claude.com/docs/en/skills: "personal over project", and a
        # skill wins over a .claude/commands file of the same name.
        _write(self.home / ".claude/commands/deploy.md",
               "---\ndescription: user deploy\n---\n")
        _write(self.proj / ".claude/commands/deploy.md",
               "---\ndescription: project deploy\n---\n")
        _write(self.proj / ".claude/commands/lint.md",
               "---\ndescription: command lint\n---\n")
        _write(self.proj / ".claude/skills/lint/SKILL.md",
               "---\ndescription: skill lint\n---\n")
        rows = {r["name"]: r for r in
                slash.custom_entries("claude", str(self.proj))}
        self.assertEqual(rows["deploy"]["description"], "user deploy")
        self.assertEqual(rows["lint"]["description"], "skill lint")
        self.assertEqual(_names(rows.values()).count("deploy"), 1)

    def test_project_skills_from_parents_up_to_repo_root(self):
        sub = self.proj / "apps" / "web"
        sub.mkdir(parents=True)
        _write(self.proj / ".claude/skills/root-skill/SKILL.md",
               "---\ndescription: r\n---\n")
        self.assertIn("root-skill",
                      _names(slash.custom_entries("claude", str(sub))))

    def test_registry_skills_root_wins(self):
        _write(config_dir_for(self.home) / "harness.json", json.dumps({
            "claude": {"capabilities": {"skills": {"root": "~/elsewhere"}}},
        }))
        _write(self.home / "elsewhere/moved/SKILL.md",
               "---\ndescription: m\n---\n")
        _write(self.home / ".claude/skills/ignored/SKILL.md",
               "---\ndescription: i\n---\n")
        names = _names(slash.custom_entries("claude", str(self.proj)))
        self.assertIn("moved", names)
        self.assertNotIn("ignored", names)

    def test_enabled_plugins_are_namespaced(self):
        live = self.home / "cache/eng/0.1.0"
        _write(live / "commands/ship.md", "---\ndescription: Ship\n---\n")
        _write(live / "skills/tdd/SKILL.md", "---\ndescription: TDD\n---\n")
        off = self.home / "cache/off/0.1.0"
        _write(off / "commands/nope.md", "---\ndescription: no\n---\n")
        _write(self.home / ".claude/plugins/installed_plugins.json",
               json.dumps({"version": 2, "plugins": {
                   "eng@dev": [{"scope": "user", "installPath": str(live)}],
                   "off@dev": [{"scope": "user", "installPath": str(off)}],
               }}))
        _write(self.home / ".claude/settings.json",
               json.dumps({"enabledPlugins": {"off@dev": False}}))
        rows = {r["name"]: r for r in
                slash.custom_entries("claude", str(self.proj))}
        self.assertEqual(rows["eng:ship"]["source"], "plugin")
        self.assertEqual(rows["eng:tdd"]["description"], "TDD")
        self.assertNotIn("off:nope", rows)

    def test_cache_reuses_scan_until_ttl(self):
        _write(self.home / ".claude/commands/a.md", "a\n")
        first = slash.custom_entries("claude", str(self.proj), now=1000.0)
        _write(self.home / ".claude/commands/b.md", "b\n")
        again = slash.custom_entries("claude", str(self.proj), now=1001.0)
        self.assertEqual(_names(again), _names(first))
        later = slash.custom_entries(
            "claude", str(self.proj), now=1000.0 + slash.CACHE_TTL_S + 1)
        self.assertIn("b", _names(later))


class TestOtherHarnesses(_Home):
    def test_opencode_project_overrides_user(self):
        _write(self.home / ".config/opencode/command/test.md",
               "---\ndescription: global test\n---\n")
        _write(self.proj / ".opencode/commands/test.md",
               "---\ndescription: project test\n---\n")
        _write(self.proj / ".opencode/command/db/migrate.md", "Migrate\n")
        rows = {r["name"]: r for r in
                slash.custom_entries("opencode", str(self.proj))}
        self.assertEqual(rows["test"]["description"], "project test")
        self.assertEqual(rows["test"]["source"], "project")
        self.assertIn("db/migrate", rows)

    def test_codex_skills_insert_a_mention(self):
        _write(self.home / ".codex/skills/.system/imagegen/SKILL.md",
               "---\nname: imagegen\ndescription: Images\n---\n")
        _write(self.proj / ".agents/skills/compact/SKILL.md",
               "---\ndescription: a skill named like a built-in\n---\n")
        _write(self.home / ".codex/prompts/old.md", "deprecated\n")
        custom = slash.custom_entries("codex", str(self.proj))
        rows = {r["name"]: r for r in custom}
        self.assertEqual(rows["imagegen"]["insert"], "$imagegen ")
        self.assertNotIn("prompts:old", rows)
        listed, _ok = slash.for_channel("codex", "tmux", custom)
        # $compact never shadows /compact.
        self.assertEqual(
            [r["source"] for r in listed if r["name"] == "compact"],
            ["builtin", "skill"],
        )

    def test_codex_home_moves_the_skills_root(self):
        moved = self.home / "codex-elsewhere"
        _write(moved / "skills/moved/SKILL.md", "---\ndescription: m\n---\n")
        _write(self.home / ".codex/skills/default/SKILL.md",
               "---\ndescription: d\n---\n")
        with patch.dict(os.environ, {"CODEX_HOME": str(moved)}):
            names = _names(slash.custom_entries("codex", str(self.proj)))
        self.assertIn("moved", names)
        self.assertNotIn("default", names)

    def test_registry_root_beats_codex_home(self):
        _write(config_dir_for(self.home) / "harness.json", json.dumps({
            "codex": {"capabilities": {"skills": {"root": "~/reg"}}},
        }))
        _write(self.home / "reg/chosen/SKILL.md", "---\ndescription: c\n---\n")
        moved = self.home / "codex-elsewhere"
        _write(moved / "skills/moved/SKILL.md", "---\ndescription: m\n---\n")
        with patch.dict(os.environ, {"CODEX_HOME": str(moved)}):
            names = _names(slash.custom_entries("codex", str(self.proj)))
        self.assertIn("chosen", names)
        self.assertNotIn("moved", names)

    def test_unknown_harness_is_empty(self):
        self.assertEqual(slash.custom_entries("nope", str(self.proj)), [])
        rows, ok = slash.for_channel("nope", "tmux", [])
        self.assertEqual((rows, ok), ([], False))
        self.assertFalse(slash.supported("nope"))


class TestChannelFilter(unittest.TestCase):
    custom = [{"name": "deploy", "description": "d", "source": "user"}]

    def test_tmux_has_builtins_and_custom(self):
        rows, ok = slash.for_channel("claude", "tmux", self.custom)
        names = _names(rows)
        self.assertTrue(ok)
        self.assertIn("compact", names)
        self.assertIn("deploy", names)
        model = next(r for r in rows if r["name"] == "model")
        self.assertTrue(model["argument_required"])
        # Bare pickers that no argument skips never appear.
        for picker in ("resume", "permissions", "login", "exit"):
            self.assertNotIn(picker, names)
        # Built-ins first, then custom.
        self.assertEqual(rows[-1]["source"], "user")

    def test_headless_keeps_print_mode_builtins_only(self):
        rows, ok = slash.for_channel("claude", "headless", self.custom)
        names = _names(rows)
        self.assertTrue(ok)
        self.assertIn("deploy", names)
        self.assertIn("model", names)
        self.assertNotIn("compact", names)
        self.assertNotIn("clear", names)

    def test_server_channels_drop_builtins(self):
        rows, ok = slash.for_channel("opencode", "serve", self.custom)
        self.assertEqual((rows, ok), ([], False))
        skill = [{"name": "x", "description": "", "source": "skill",
                  "insert": "$x "}]
        rows, ok = slash.for_channel("codex", "appserver", skill)
        self.assertTrue(ok)
        self.assertEqual(_names(rows), ["x"])

    def test_custom_replaces_builtin_of_same_name(self):
        mine = [{"name": "init", "description": "mine", "source": "skill"}]
        rows, _ = slash.for_channel("claude", "tmux", mine)
        inits = [r for r in rows if r["name"] == "init"]
        self.assertEqual([r["source"] for r in inits], ["skill"])

    def test_tables_are_well_formed(self):
        for harness, items in slash_builtins.BUILTINS.items():
            names = [i.name for i in items]
            self.assertEqual(len(names), len(set(names)), harness)
            for item in items:
                self.assertFalse(item.name.startswith("/"))
                self.assertTrue(item.description)
                self.assertTrue(item.channels <= {"tmux", "headless"})
                if item.argument_required:
                    self.assertTrue(item.argument_hint, item.name)


class TestRoute(_Home):
    def setUp(self):
        super().setUp()
        _write(self.proj / ".claude/commands/deploy.md",
               "---\ndescription: Deploy\nargument-hint: <env>\n---\n")
        sessions = [
            Session(timestamp=0, agent="Claude Code", path=str(self.proj),
                    title="t", session_id="s1", tool_name="claude"),
            Session(timestamp=0, agent="Mystery", path=str(self.proj),
                    title="t", session_id="m1", tool_name="mystery"),
        ]
        self.srv = _Server(sessions=sessions)
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.auth = {"Authorization": f"Bearer {cred['credential']}"}

    def get(self, key, headers=None):
        status, data = self.srv.request(
            "GET", f"/v1/sessions/{key}/commands",
            headers=self.auth if headers is None else headers)
        return status, json.loads(data)

    def test_requires_auth(self):
        status, _ = self.get("claude:s1", headers={})
        self.assertEqual(status, 401)

    def test_unknown_session_is_404(self):
        status, body = self.get("claude:nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "unknown_session")

    def test_attached_tmux_session(self):
        supervise.mark(self.srv.home, "claude:s1",
                       {"mode": "tmux", "tool_name": "claude"})
        with patch.object(supervise, "tmux_alive", return_value=True):
            status, body = self.get("claude:s1")
        self.assertEqual(status, 200)
        self.assertEqual(body["channel"], "tmux")
        self.assertTrue(body["attached"])
        self.assertTrue(body["custom_available"])
        deploy = next(r for r in body["commands"] if r["name"] == "deploy")
        self.assertEqual(deploy, {
            "name": "deploy", "description": "Deploy",
            "source": "project", "argument_hint": "<env>",
        })

    def test_observed_session_reports_auto_channel(self):
        with patch.object(supervise, "resolve_mode", return_value="headless"):
            status, body = self.get("claude:s1")
        self.assertEqual(status, 200)
        self.assertFalse(body["attached"])
        self.assertEqual(body["channel"], "headless")
        self.assertNotIn("compact", _names(body["commands"]))
        self.assertIn("deploy", _names(body["commands"]))

    def test_unknown_harness_answers_empty(self):
        status, body = self.get("mystery:m1")
        self.assertEqual(status, 200)
        self.assertEqual(body["commands"], [])
        self.assertFalse(body["supported"])


class TestSatellite(_Home):
    def setUp(self):
        super().setUp()
        self.srv = _Server()
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.auth = {"Authorization": f"Bearer {cred['credential']}"}
        self.mid = "sat-1"
        # Catalog pushes need a satellite credential bound to the
        # machine; the phone credential stays for the session routes.
        self.sat_auth = self.srv.satellite(self.mid)
        self.row = {"key": "claude:r1", "harness": "claude",
                    "cwd": str(self.proj), "ownership": "observed"}

    def push(self, extra=None):
        body = {"machine_id": self.mid, "rows": [self.row], **(extra or {})}
        status, data = self.srv.request(
            "POST", "/v1/agent/catalog", body, headers=self.sat_auth)
        self.assertEqual(status, 200)
        return json.loads(data)

    def get(self):
        status, data = self.srv.request(
            "GET", f"/v1/sessions/claude:r1@{self.mid}/commands",
            headers=self.auth)
        self.assertEqual(status, 200, data)
        return json.loads(data)

    def test_without_pushed_commands_builtins_only(self):
        self.push()
        body = self.get()
        self.assertFalse(body["custom_available"])
        self.assertIn("aren't available", body["note"])
        self.assertTrue(body["commands"])
        self.assertEqual({r["source"] for r in body["commands"]},
                         {"builtin"})

    def test_pushed_commands_merge_on_the_hub(self):
        _write(self.home / ".claude/commands/standup.md", "Standup\n")
        _write(self.proj / ".claude/commands/deploy.md", "Deploy\n")
        with patch.object(slash, "_auto_channel", return_value="tmux"):
            payload = slash.satellite_payload([self.row])
        reply = self.push({"slash": payload})
        self.assertTrue(reply["slash"])
        body = self.get()
        self.assertTrue(body["custom_available"])
        self.assertEqual(body["channel"], "tmux")
        names = _names(body["commands"])
        self.assertIn("standup", names)
        self.assertIn("deploy", names)

    def test_malformed_payload_is_not_stored(self):
        reply = self.push({"slash": {"harnesses": "nope"}})
        self.assertFalse(reply["slash"])

    def test_bound_payload_drops_folders_then_gives_up(self):
        row = {"name": "n", "description": "d" * 100, "source": "project"}
        payload = {"harnesses": {"claude": {
            "user": {"skills": [dict(row, name=f"u{i}") for i in range(3)]},
            "folders": {
                "/a": {"commands": [dict(row, name=f"a{i}")
                                    for i in range(2000)]},
                "/b": {"commands": [dict(row, name="b")]},
            },
            "auto": "tmux",
        }}, "attached": {}}
        with patch.object(slash, "MAX_PAYLOAD_BYTES", 20000):
            out = slash.bound_payload(payload)
        folders = out["harnesses"]["claude"]["folders"]
        self.assertNotIn("/a", folders)
        self.assertIn("/b", folders)
        self.assertTrue(out["truncated"])
        with patch.object(slash, "MAX_PAYLOAD_BYTES", 10):
            self.assertIsNone(slash.bound_payload(payload))
        capped = slash.bound_payload(payload)
        self.assertEqual(
            len(capped["harnesses"]["claude"]["folders"]["/a"]["commands"]),
            slash.MAX_LAYER_ENTRIES)

    def test_bound_payload_matches_a_full_recount(self):
        # The running size must give exactly what re-serialising after
        # every drop gave: same folders kept, same flag.
        import random

        def reference(payload, limit):
            out = {"harnesses": {
                h: {"user": slash._capped(sl.get("user")),
                    "folders": {c: slash._capped(l)
                                for c, l in sl["folders"].items()},
                    "auto": sl.get("auto")}
                for h, sl in payload["harnesses"].items()},
                "attached": dict(payload["attached"])}
            folders = sorted(
                ((slash._size(l), h, c) for h, sl in out["harnesses"].items()
                 for c, l in sl["folders"].items()), reverse=True)
            while slash._size(out) > limit:
                if not folders:
                    return None
                _n, h, c = folders.pop(0)
                del out["harnesses"][h]["folders"][c]
                out["truncated"] = True
            return out

        rng = random.Random(7)
        for trial in range(40):
            payload = {"harnesses": {}, "attached": {"k": "tmux"}}
            for h in ("claude", "codex")[: rng.randint(1, 2)]:
                payload["harnesses"][h] = {
                    "user": {"skills": [{"name": "u\u00e9" * rng.randint(1, 9)}]},
                    "folders": {
                        f"/p\u6f22{i}": {"commands": [
                            {"name": "c" * rng.randint(1, 300)}
                            for _ in range(rng.randint(0, 4))]}
                        for i in range(rng.randint(0, 6))},
                    "auto": "tmux",
                }
            full = slash._size(reference(payload, 10**9))
            limit = rng.randint(full // 4, full + 10)
            with patch.object(slash, "MAX_PAYLOAD_BYTES", limit):
                self.assertEqual(slash.bound_payload(payload),
                                 reference(payload, limit), trial)

    def test_bound_payload_serialises_linearly(self):
        # Dropping folder after folder used to re-serialise the whole
        # payload each time.
        row = {"name": "n", "description": "d" * 200, "source": "project"}
        payload = {"harnesses": {"claude": {
            "user": {}, "auto": "tmux",
            "folders": {f"/f{i}": {"commands": [dict(row)] * 5}
                        for i in range(300)},
        }}, "attached": {}}
        real = slash._size
        serialised = []

        def counting(obj):
            n = real(obj)
            serialised.append(n)
            return n

        with patch.object(slash, "_size", side_effect=counting), \
             patch.object(slash, "MAX_PAYLOAD_BYTES", 5000):
            out = slash.bound_payload(payload)
        self.assertTrue(out["truncated"])
        whole = real(payload)
        self.assertLess(sum(serialised), 3 * whole)

    def test_satellite_payload_reads_the_registry_once(self):
        rows = [dict(self.row, key=f"claude:r{i}") for i in range(30)]
        with patch.object(slash, "_auto_channel", return_value="tmux"), \
             patch.object(supervise, "_load_registry",
                          wraps=supervise._load_registry) as load:
            slash.satellite_payload(rows, self.home)
        self.assertEqual(load.call_count, 1)

    def test_oversized_menu_never_blocks_rows(self):
        ag = agent_mod.RemoteAgent(
            "http://h:1", "cred", Path(tempfile.mkdtemp()))
        ag.catalog._fetch = lambda: [
            Session(timestamp=0, agent="Claude Code", path=str(self.proj),
                    title="t", session_id="r1", tool_name="claude")]
        ag.catalog._status_of = lambda ss: [""] * len(ss)
        _write(self.home / ".claude/commands/a.md", "x" * 50 + "\n")
        # The row alone fits the cap; the row plus the menu does not.
        sent = []

        def post(_hub, _path, _cred, body):
            sent.append(body)
            return 200, {"ok": True, "slash": "slash" in body}

        with patch.object(agent_mod, "_post", side_effect=post), \
                patch.object(agent_mod, "CATALOG_BODY_MAX", 10**9), \
                patch.object(slash, "_auto_channel", return_value="tmux"):
            ag._push_catalog()  # measure the row-only body
        sent.clear()
        rows_only = len(json.dumps(
            {"machine_id": ag.machine_id, "rows": ag.catalog.rows()},
            ensure_ascii=False).encode("utf-8"))
        ag._slash_digest = None
        with patch.object(agent_mod, "_post", side_effect=post), \
                patch.object(agent_mod, "CATALOG_BODY_MAX", rows_only + 20), \
                patch.object(slash, "_auto_channel", return_value="tmux"), \
                patch("sys.stderr"):
            self.assertTrue(ag._push_catalog())
            self.assertTrue(ag._push_catalog())
        self.assertEqual(len(sent), 2)
        for body in sent:
            self.assertNotIn("slash", body)
            self.assertEqual(len(body["rows"]), 1)

    def test_agent_sends_commands_only_when_changed(self):
        ag = agent_mod.RemoteAgent(
            "http://h:1", "cred", Path(tempfile.mkdtemp()))
        ag.catalog._fetch = lambda: [
            Session(timestamp=0, agent="Claude Code", path=str(self.proj),
                    title="t", session_id="r1", tool_name="claude")]
        ag.catalog._status_of = lambda ss: [""] * len(ss)
        sent = []

        def post(_hub, _path, _cred, body):
            sent.append(body)
            return 200, {"ok": True, "slash": "slash" in body}

        with patch.object(agent_mod, "_post", side_effect=post), \
                patch.object(slash, "_auto_channel", return_value="tmux"):
            ag._push_catalog()
            ag._push_catalog()
        self.assertIn("slash", sent[0])
        self.assertIn("claude", sent[0]["slash"]["harnesses"])
        self.assertNotIn("slash", sent[1])


if __name__ == "__main__":
    unittest.main()
