"""Launching with ``provider=<slug>``: only that provider's variables
reach the harness, and no key value reaches argv, markers, launch
records, machine rows, the command journal or the hub's replies."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.paths import provider_keys_file_for
from swe.remote import catalog as catalog_mod
from swe.remote import launch_provider, protocol, provider_exec, supervise

from tests._tempdir import temp_dir
from tests.test_remote_fork import _Hub

GROQ = "gsk-test-groq-secret-0001"
OPENAI = "sk-test-openai-secret-0002"
SECRETS = (GROQ, OPENAI)


def _write_keys(home: Path, text: str | None = None) -> Path:
    path = provider_keys_file_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else (
        f'# Groq\nexport GROQ_API_KEY="{GROQ}"\n\n'
        f'# OpenAI\nexport OPENAI_API_KEY="{OPENAI}"\n'))
    os.chmod(path, 0o600)
    return path


def _assert_no_secret(case, blob):
    text = blob if isinstance(blob, str) else json.dumps(blob, default=str)
    for secret in SECRETS:
        case.assertNotIn(secret, text)


class TestProtocol(unittest.TestCase):
    BASE = {"harness": "opencode", "cwd": "/tmp", "prompt": "hi"}

    def test_provider_slug(self):
        self.assertIsNone(protocol.parse_launch_command(self.BASE)["provider"])
        cmd = protocol.parse_launch_command({**self.BASE, "provider": "groq"})
        self.assertEqual(cmd["provider"], "groq")
        for bad in ("Groq", "-x", "a b", 3, "x" * 80, "$(id)"):
            with self.assertRaises(ValueError):
                protocol.parse_launch_command({**self.BASE, "provider": bad})


class TestProviderExec(unittest.TestCase):
    """The wrapper the pane runs: reads the keys file itself, execs the
    harness with exactly the provider's variables."""

    def setUp(self):
        self.home = temp_dir(self)
        self.keys = _write_keys(self.home)

    def _run(self, provider="groq", harness="opencode", extra_env=None):
        argv = launch_provider.wrap_argv(
            harness, provider, ["/usr/bin/env"], keys_file=self.keys, reg={})
        _assert_no_secret(self, argv)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.home),
               **(extra_env or {})}
        return subprocess.run(argv, capture_output=True, text=True,
                              env=env, timeout=30)

    def test_env_has_only_the_providers_key(self):
        # The hub inherited every key (a shell that sourced the file):
        # the pane still gets only Groq's.
        out = self._run(extra_env={"OPENAI_API_KEY": OPENAI,
                                   "GROQ_API_KEY": "stale"})
        self.assertEqual(out.returncode, 0, out.stderr)
        env = dict(line.split("=", 1) for line in out.stdout.splitlines()
                   if "=" in line)
        self.assertEqual(env["GROQ_API_KEY"], GROQ)
        self.assertNotIn("OPENAI_API_KEY", env)
        file_names = set(launch_provider.read_keys(self.keys))
        self.assertEqual(file_names & set(env), {"GROQ_API_KEY"})

    def test_names_with_unreadable_values_are_dropped_too(self):
        # A shell that sourced this file resolved OPENAI_API_KEY; we
        # can't read its value, but the name still goes.
        _write_keys(self.home, f'export GROQ_API_KEY="{GROQ}"\n'
                               'export OPENAI_API_KEY="$OTHER"\n')
        out = self._run(extra_env={"OPENAI_API_KEY": OPENAI})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("OPENAI_API_KEY", out.stdout)
        self.assertIn(f"GROQ_API_KEY={GROQ}", out.stdout)

    def test_missing_key_exits_without_the_command(self):
        _write_keys(self.home, f'export OPENAI_API_KEY="{OPENAI}"\n')
        out = self._run()
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout, "")
        self.assertIn("GROQ_API_KEY", out.stderr)
        _assert_no_secret(self, out.stderr)

    def test_base_url_entry(self):
        _write_keys(self.home, 'export DEEPSEEK_API_KEY="ds-secret"\n'
                               'export ANTHROPIC_API_KEY="anthropic-secret"\n')
        out = self._run("deepseek", "claude",
                        extra_env={"ANTHROPIC_API_KEY": "anthropic-secret"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ANTHROPIC_AUTH_TOKEN=ds-secret", out.stdout)
        self.assertIn("ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic",
                      out.stdout)
        self.assertNotIn("anthropic-secret", out.stdout)

    def test_bad_invocations(self):
        self.assertEqual(provider_exec.main(["--keys=x"]), 2)
        self.assertEqual(provider_exec.main(["--", "env"]), 2)

    def test_build_env(self):
        env = provider_exec.build_env(
            {"PATH": "/bin", "OPENAI_API_KEY": OPENAI},
            {"keys": {"GROQ_API_KEY": ["GROQ_API_KEY"]}, "set": {"X": "1"}},
            {"GROQ_API_KEY": GROQ, "OPENAI_API_KEY": OPENAI})
        self.assertEqual(env, {"PATH": "/bin", "GROQ_API_KEY": GROQ, "X": "1"})


class TestLaunchNew(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.proj = str(temp_dir(self))
        _write_keys(self.home)

    def _launch(self, **kw):
        with patch.object(supervise.shutil, "which", return_value="/x/bin"), \
             patch.object(supervise, "launch_tmux",
                          return_value="qv-p") as lt, \
             patch.object(supervise, "prime_pane", return_value=True), \
             patch("swe.harness.registry.load_registry_readonly",
                   return_value={}):
            out = supervise.launch_new(self.home, "opencode", self.proj, "hi",
                                       binary="opencode", **kw)
        return out, lt

    def test_provider_wraps_the_argv_without_secrets(self):
        (ok, note, _name), lt = self._launch(provider="groq", model="groq/m")
        self.assertTrue(ok, note)
        argv = lt.call_args[0][1]
        env = lt.call_args[0][3] if len(lt.call_args[0]) > 3 else \
            lt.call_args[1].get("env")
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[2].endswith("provider_exec.py"))
        self.assertEqual(argv[argv.index("--") + 1:],
                         ["opencode", "mini", "-m", "groq/m"])
        _assert_no_secret(self, argv)
        _assert_no_secret(self, env or {})
        marker = (supervise._pending_dir(self.home) / "qv-p.json").read_text()
        self.assertEqual(json.loads(marker)["provider"], "groq")
        _assert_no_secret(self, marker)

    def test_no_provider_leaves_the_argv_alone(self):
        (ok, _n, _), lt = self._launch()
        self.assertTrue(ok)
        self.assertEqual(lt.call_args[0][1], ["opencode"])

    def test_missing_key_or_undeclared_provider_is_refused(self):
        for slug, words in (("mistral", "MISTRAL_API_KEY"),
                            ("nope", "can't take the provider")):
            (ok, note, _), lt = self._launch(provider=slug)
            self.assertFalse(ok)
            self.assertIn(words, note)
            _assert_no_secret(self, note)
            lt.assert_not_called()


class TestMachineRow(unittest.TestCase):
    def test_row_lists_slugs_never_values(self):
        home = temp_dir(self)
        _write_keys(home)
        launchable = [{"name": "opencode"}, {"name": "claude"},
                      {"name": "codex"}]
        with patch("swe.harness.registry.load_registry_readonly",
                   return_value={}):
            fields = catalog_mod.provider_fields(launchable, home)
        self.assertEqual(fields["providers_at_launch"],
                         {"opencode": ["openai", "groq"]})
        self.assertEqual(fields["provider_names"],
                         {"openai": "OpenAI", "groq": "Groq"})
        _assert_no_secret(self, fields)

    def test_unreadable_keys_file_offers_nothing(self):
        home = temp_dir(self)
        provider_keys_file_for(home).mkdir(parents=True)
        fields = catalog_mod.provider_fields([{"name": "opencode"}], home)
        self.assertEqual(fields["providers_at_launch"], {})


class TestHubRoutes(unittest.TestCase):
    def setUp(self):
        self.srv = _Hub([])
        self.addCleanup(self.srv.stop)
        self.sat = self.srv.satellite("sat-m1")
        _write_keys(self.srv.home)

    def _local(self, body):
        with patch("swe.remote.catalog.launch_command",
                   return_value="opencode"), \
             patch("swe.remote.catalog.launchable_harnesses",
                   return_value=[]), \
             patch("swe.remote.catalog.known_session_ids", return_value=[]), \
             patch("swe.remote.supervise.tmux_bin",
                   return_value="/usr/bin/tmux"), \
             patch("swe.harness.registry.load_registry_readonly",
                   return_value={}), \
             patch("swe.remote.supervise.launch_new",
                   return_value=(True, "ok", "qv-t")) as launch:
            status, data = self.srv.request("POST", "/v1/sessions", {
                "harness": "opencode", "cwd": "/tmp", "prompt": "hi",
                **body})
            import threading
            for t in threading.enumerate():
                if t.name.startswith("remote-launch-"):
                    t.join(timeout=5)
        return status, data, launch

    def test_local_launch_passes_the_slug(self):
        status, data, launch = self._local({"provider": "groq"})
        self.assertEqual(status, 202, data)
        self.assertEqual(launch.call_args[1]["provider"], "groq")
        _assert_no_secret(self, data)
        _assert_no_secret(self, self.srv.state.launches)

    def test_local_launch_refuses_missing_key_with_422(self):
        status, data, launch = self._local({"provider": "mistral"})
        self.assertEqual((status, data["error"]["code"]),
                         (422, "unsupported_provider"))
        self.assertIn("MISTRAL_API_KEY", data["error"]["message"])
        launch.assert_not_called()

    def test_satellite_gets_the_slug_only(self):
        catalog_mod.upsert_machine(self.srv.home, {
            "id": "sat-m1", "launchable": ["opencode"],
            "model_at_launch": [], "yolo_at_launch": [],
            "providers_at_launch": {"opencode": ["groq"]}})
        status, data = self.srv.request("POST", "/v1/sessions", {
            "harness": "opencode", "cwd": "/r", "prompt": "hi",
            "machine": "sat-m1", "provider": "groq"})
        self.assertEqual(status, 202, data)
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        cmd = [c for c in replay if c["data"]["op"] == "launch"][-1]
        self.assertEqual(cmd["data"]["provider"], "groq")
        _assert_no_secret(self, replay)
        # An unlisted slug (no key there, or undeclared) is a 422.
        status, data = self.srv.request("POST", "/v1/sessions", {
            "harness": "opencode", "cwd": "/r", "prompt": "hi",
            "machine": "sat-m1", "provider": "openai"})
        self.assertEqual((status, data["error"]["code"]),
                         (422, "unsupported_provider"))

    def test_old_satellite_is_refused(self):
        catalog_mod.upsert_machine(self.srv.home, {
            "id": "sat-m1", "launchable": ["opencode"]})
        status, data = self.srv.request("POST", "/v1/sessions", {
            "harness": "opencode", "cwd": "/r", "prompt": "hi",
            "machine": "sat-m1", "provider": "groq"})
        self.assertEqual((status, data["error"]["code"]),
                         (409, "unsupported_launch_option"))


class TestSweUse(unittest.TestCase):
    def test_use_with_provider_execs_with_only_its_key(self):
        from swe.harness import commands

        home = temp_dir(self)
        _write_keys(home)
        reg = {"opencode": {"command": "opencode"}}
        with patch.object(commands, "load_registry", return_value=reg), \
             patch.object(commands, "is_installed", return_value=True), \
             patch("swe.remote.launch_provider.keys_path",
                   return_value=provider_keys_file_for(home)), \
             patch.dict(os.environ, {"OPENAI_API_KEY": OPENAI}), \
             patch.object(commands.os, "execvpe") as execvpe, \
             patch("builtins.print"):
            commands.cmd_use(["--provider=groq", "opencode", "run", "x"])
        execvpe.assert_called_once()
        cmd, argv, env = execvpe.call_args[0]
        self.assertEqual(argv, ["opencode", "run", "x"])
        # Booleans only: a failure message must never print the env,
        # which holds whatever keys the test process inherited.
        self.assertTrue(env.get("GROQ_API_KEY") == GROQ)
        self.assertFalse("OPENAI_API_KEY" in env)

    def test_use_refuses_a_missing_key(self):
        from swe.harness import commands

        home = temp_dir(self)
        _write_keys(home, "")
        reg = {"opencode": {"command": "opencode"}}
        with patch.object(commands, "load_registry", return_value=reg), \
             patch.object(commands, "is_installed", return_value=True), \
             patch("swe.remote.launch_provider.keys_path",
                   return_value=provider_keys_file_for(home)), \
             patch.object(commands.os, "execvpe") as execvpe, \
             patch("builtins.print"):
            self.assertEqual(commands.cmd_use(["--provider=groq", "opencode"]),
                             1)
        execvpe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
