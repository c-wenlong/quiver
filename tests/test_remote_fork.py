"""fork.py: fork a session from the phone, on the hub or a satellite."""

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import agent as agent_mod
from quiver.remote import catalog as catalog_mod
from quiver.remote import fork as fork_mod
from quiver.remote import pairing, protocol, supervise
from quiver.remote.hub import HubState, make_server
from quiver.sessions.models import Session

CODEX_ID = "rollout-2026-10-20T10-55-33-01a1144a-41bf-7123-ae76-1cff676b946f"
CODEX_THREAD = "01a1144a-41bf-7123-ae76-1cff676b946f"


def _session(sid="s1", tool="claude", path="/tmp", ts=0):
    return Session(timestamp=int(ts * 1000), agent="x", path=path,
                   title="t", session_id=sid, tool_name=tool)


class _Hub:
    def __init__(self, sessions):
        self.home = Path(tempfile.mkdtemp())
        self.state = HubState(self.home)
        self.state.catalog._fetch = lambda: list(sessions)
        self.state.catalog._status_of = lambda ss: ["done"] * len(ss)
        self.state.catalog.refresh()
        self.server = make_server(self.state, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()
        token = pairing.mint_pair_token(self.home)
        _status, data = self.request(
            "POST", "/v1/pair", {"token": token, "name": "t"}, auth=False)
        cred = data["credential"]
        self.auth = {"Authorization": f"Bearer {cred}"}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, body=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path,
                         body=json.dumps(body) if body is not None else None,
                         headers=self.auth if auth else {})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read() or b"{}")
        finally:
            conn.close()


class TestForkCommandLines(unittest.TestCase):
    def test_each_supported_harness(self):
        with patch("quiver.remote.fork.launch_tool_name",
                   side_effect=lambda t: t):
            cases = {
                "claude": (_session("abc"), "new-uuid",
                           ["claude", "--resume", "abc", "--fork-session",
                            "--session-id", "new-uuid"]),
                "codex": (_session(CODEX_ID, "codex"), "",
                          ["codex", "fork", CODEX_THREAD]),
                "kilo": (_session("ses_1", "kilo"), "",
                         ["kilo", "--session", "ses_1", "--fork"]),
                "pi": (_session("p1", "pi"), "", ["pi", "--fork", "p1"]),
                "droid": (_session("d1", "droid"), "",
                          ["droid", "--fork", "d1"]),
            }
            for harness, (session, new_id, want) in cases.items():
                with self.subTest(harness=harness):
                    self.assertEqual(
                        fork_mod.fork_argv(session, new_id), want)
                    self.assertTrue(fork_mod.can_fork(session))

    def test_unsupported_and_idless(self):
        for session in (_session("o1", "opencode"), _session("c", "cursor"),
                        _session("", "claude"),
                        _session("rollout-no-uuid", "codex")):
            with self.subTest(session=session.tool_name):
                self.assertEqual(fork_mod.fork_argv(session), [])
                self.assertFalse(fork_mod.can_fork(session))

    def test_every_entry_says_when_it_was_checked(self):
        for name, spec in fork_mod.FORK_SUPPORT.items():
            with self.subTest(harness=name):
                self.assertRegex(spec.checked, r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(spec.version)

    def test_rows_carry_can_fork(self):
        _key, row = catalog_mod.session_to_row(
            _session("abc"), "done", "m", set())
        self.assertTrue(row["can_fork"])
        _key, row = catalog_mod.session_to_row(
            _session("o1", "opencode"), "done", "m", set())
        self.assertFalse(row["can_fork"])

    def test_body_validation(self):
        self.assertEqual(fork_mod.parse_fork_body({}), "")
        self.assertEqual(fork_mod.parse_fork_body({"prompt": "  "}), "")
        self.assertEqual(fork_mod.parse_fork_body({"prompt": "go"}), "go")
        for bad in ({"prompt": 3}, {"prompt": "x" * 40_000}):
            with self.assertRaises(ValueError):
                fork_mod.parse_fork_body(bad)


class TestForkRoute(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()
        self.srv = _Hub([
            _session("abc", path=self.proj),
            _session("o1", "opencode", path=self.proj),
            _session("gone", path="/no/such/folder/anywhere"),
        ])
        self.addCleanup(self.srv.stop)

    def _fork(self, key, body=None):
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch("quiver.remote.fork.shutil.which",
                   return_value="/x/claude"):
            return self.srv.request(
                "POST", f"/v1/sessions/{key}/fork", body or {})

    def _wait(self):
        for t in threading.enumerate():
            if t.name.startswith("remote-fork-"):
                t.join(timeout=5)

    def test_fork_answers_202_and_tracks_the_launch(self):
        with patch.object(fork_mod, "start",
                          return_value=(True, "forked")) as start:
            status, body = self._fork("claude:abc", {"prompt": "next"})
            self._wait()
        self.assertEqual(status, 202, body)
        lid = body["launch_id"]
        args = start.call_args[0]
        self.assertEqual(args[1].session_id, "abc")
        self.assertEqual(args[3], "next")
        self.assertEqual(args[4], lid)
        _s, view = self.srv.request("GET", f"/v1/launches/{lid}")
        self.assertEqual(view["state"], "pending")
        self.assertEqual(view["cwd"], self.proj)

    def test_attached_source_forks_too(self):
        supervise.mark(self.srv.home, "claude:abc", {
            "mode": "headless", "tool_name": "claude",
            "session_id": "abc", "cwd": self.proj, "attached_at": 0})
        self.srv.state.catalog.refresh()
        self.assertEqual(
            self.srv.state.catalog.row_for("claude:abc")["ownership"],
            "attached")
        with patch.object(fork_mod, "start", return_value=(True, "ok")):
            status, _ = self._fork("claude:abc")
            self._wait()
        self.assertEqual(status, 202)

    def test_failure_after_reply_settles_the_launch(self):
        with patch.object(fork_mod, "start",
                          return_value=(False, "claude exited")):
            _s, body = self._fork("claude:abc")
            self._wait()
        launch = self.srv.state.launches[body["launch_id"]]
        self.assertEqual(launch["state"], "failed")
        self.assertEqual(launch["note"], "claude exited")

    def test_refusals(self):
        with patch.object(fork_mod, "start") as start:
            cases = [
                ("claude:nope", {}, 404, "unknown_session"),
                ("opencode:o1", {}, 409, "fork_unsupported"),
                ("claude:gone", {}, 422, "bad_cwd"),
                ("claude:abc", {"prompt": 5}, 422, "bad_command"),
            ]
            for key, body, code, err in cases:
                with self.subTest(key=key):
                    status, data = self._fork(key, body)
                    self.assertEqual(status, code, data)
                    self.assertEqual(data["error"]["code"], err)
        start.assert_not_called()
        self.assertEqual(self.srv.state.launches, {})

    def test_needs_auth(self):
        status, _ = self.srv.request(
            "POST", "/v1/sessions/claude:abc/fork", {}, auth=False)
        self.assertEqual(status, 401)


class TestLaunchFork(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.proj = tempfile.mkdtemp()

    def _launch(self, source, argv, prompt="", **kw):
        with patch.object(supervise.shutil, "which", return_value="/x/bin"), \
             patch.object(supervise, "launch_tmux",
                          return_value="qv-fork") as lt, \
             patch.object(supervise, "boot_pane", return_value="❯") as bp, \
             patch.object(supervise, "prime_pane",
                          return_value=True) as pp:
            out = supervise.launch_fork(self.home, source, argv, prompt, **kw)
        return out, lt, bp, pp

    def test_runs_the_fork_in_the_source_folder_and_leaves_a_marker(self):
        src = _session("abc", path=self.proj)
        argv = ["claude", "--resume", "abc", "--fork-session",
                "--session-id", "n1"]
        (ok, _note, name), lt, bp, pp = self._launch(
            src, argv, "hi", known_ids=["other"], launch_id="l1",
            expect_id="n1")
        self.assertTrue(ok)
        self.assertEqual(name, "qv-fork")
        key, got_argv, cwd = lt.call_args[0]
        self.assertTrue(key.startswith("fork:claude:"))
        self.assertEqual((got_argv, cwd), (argv, self.proj))
        pp.assert_called_once_with("qv-fork", "hi")
        marker = json.loads(
            (supervise._pending_dir(self.home) / "qv-fork.json").read_text())
        self.assertEqual(marker["exclude"], ["abc", "other"])
        self.assertEqual(marker["expect_id"], "n1")
        self.assertEqual(marker["fork_of"], "abc")
        self.assertEqual(marker["launch_id"], "l1")

    def test_no_prompt_still_boots_past_the_trust_gate(self):
        (ok, _n, _name), _lt, bp, pp = self._launch(
            _session("abc", path=self.proj), ["claude"])
        self.assertTrue(ok)
        bp.assert_called_once()
        pp.assert_not_called()

    def test_missing_folder_or_binary(self):
        ok, note, _ = supervise.launch_fork(
            self.home, _session("abc", path="/no/such/dir"), ["sh"], "")
        self.assertFalse(ok)
        self.assertIn("no such folder", note)
        with patch.object(supervise.shutil, "which", return_value=None):
            ok, note, _ = supervise.launch_fork(
                self.home, _session("abc", path=self.proj), ["claude"], "")
        self.assertFalse(ok)
        self.assertIn("not installed", note)

    def test_start_passes_a_fresh_claude_id_to_adoption(self):
        with patch.object(supervise, "launch_fork",
                          return_value=(True, "ok", "qv")) as lf, \
             patch.object(catalog_mod, "known_session_ids",
                          return_value=["abc"]):
            ok, _ = fork_mod.start(
                self.home, _session("abc", path=self.proj), [], "go", "l9")
        self.assertTrue(ok)
        argv = lf.call_args[0][2]
        new_id = lf.call_args[1]["expect_id"]
        self.assertTrue(new_id)
        self.assertNotEqual(new_id, "abc")
        self.assertEqual(argv[argv.index("--session-id") + 1], new_id)
        self.assertEqual(lf.call_args[1]["launch_id"], "l9")

    def test_start_has_no_expected_id_where_the_cli_picks_it(self):
        with patch.object(supervise, "launch_fork",
                          return_value=(True, "ok", "qv")) as lf, \
             patch.object(catalog_mod, "known_session_ids", return_value=[]):
            fork_mod.start(self.home, _session(CODEX_ID, "codex",
                                               path=self.proj), [], "", "l")
        self.assertEqual(lf.call_args[1]["expect_id"], "")


class TestForkAdoption(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.proj = tempfile.mkdtemp()
        self.folder = supervise._pending_dir(self.home)
        self.folder.mkdir(parents=True)

    def _marker(self, **fields):
        base = {"tmux": "qv-f", "cwd": self.proj, "launched_at": time.time(),
                "launch_id": "l1"}
        (self.folder / "qv-f.json").write_text(json.dumps({**base, **fields}))

    def _adopt(self, rows):
        # Every transcript looks freshly created: only ids can decide.
        fresh = MagicMock()
        fresh.stat.return_value = MagicMock(
            st_birthtime=time.time() + 1, st_mtime=time.time() + 1,
            st_ctime=time.time() + 1)
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=fresh):
            return supervise.adopt_pending(self.home, rows)

    def test_claude_fork_adopts_the_new_id_not_the_source(self):
        self._marker(tool_name="claude", exclude=["abc"], fork_of="abc",
                     expect_id="n1")
        rows = [
            ("claude:abc", _session("abc", path=self.proj), "active"),
            ("claude:other", _session("other", path=self.proj), "active"),
        ]
        self.assertEqual(self._adopt(rows), [])  # still waiting for n1
        rows.append(("claude:n1", _session("n1", path=self.proj), "done"))
        self.assertEqual(self._adopt(rows), ["claude:n1"])
        meta = supervise.meta_for(self.home, "claude:n1")
        self.assertEqual((meta["tmux"], meta["launch_id"]), ("qv-f", "l1"))
        self.assertIsNone(supervise.meta_for(self.home, "claude:abc"))

    def test_codex_fork_skips_the_source_and_its_resumes(self):
        resumed = CODEX_ID + "_01a11a8c-92fc-7f62-b972-bccf019fe1fc"
        new = "rollout-2026-10-24T10-00-00-01a1573c-504d-7e03-8652-c203523084a8"
        self._marker(tool_name="codex", exclude=[CODEX_ID], fork_of=CODEX_ID)
        rows = [
            (f"codex:{CODEX_ID}", _session(CODEX_ID, "codex", self.proj),
             "active"),
            (f"codex:{resumed}", _session(resumed, "codex", self.proj),
             "active"),
            (f"codex:{new}", _session(new, "codex", self.proj), "done"),
        ]
        self.assertEqual(self._adopt(rows), [f"codex:{new}"])


class TestForkWritersAndSatellites(unittest.TestCase):
    def test_attaching_the_source_leaves_its_forks_running(self):
        lines = {
            "1": "claude --resume abc",
            "2": "claude --resume abc --fork-session --session-id n1",
        }

        def fake_run(argv, **_kw):
            cp = MagicMock()
            cp.stdout = "1\n2\n" if argv[0] == "pgrep" else lines.get(
                argv[2], "")
            return cp

        with patch.object(supervise.subprocess, "run", side_effect=fake_run):
            self.assertEqual(
                supervise._local_writer_pids(_session("abc")), [1])

    def test_satellite_executes_fork_op(self):
        home = Path(tempfile.mkdtemp())
        ag = agent_mod.RemoteAgent("http://h:1", "cred", home)
        src = _session("abc", path=tempfile.mkdtemp())
        ag.catalog._fetch = lambda: [src]
        ag.catalog._status_of = lambda ss: ["done"]
        ag.catalog.refresh()
        with patch.object(fork_mod, "start",
                          return_value=(True, "forked")) as start:
            ok, note, _deg = ag._execute(
                "claude:abc", "fork", {"prompt": "go", "launch_id": "l5"})
        self.assertTrue(ok)
        self.assertEqual(start.call_args[0][1].session_id, "abc")
        self.assertEqual(start.call_args[0][3:], ("go", "l5"))
        ok, note, _deg = ag._execute("claude:ghost", "fork", {})
        self.assertFalse(ok)
        self.assertIn("unknown session", note)


class TestSatelliteForkRoute(unittest.TestCase):
    def setUp(self):
        self.srv = _Hub([])
        self.addCleanup(self.srv.stop)

    def _push(self, can_fork):
        row = {"key": "claude:r1", "harness": "claude", "cwd": "/r",
               "ownership": "observed"}
        if can_fork is not None:
            row["can_fork"] = can_fork
        self.srv.request("POST", "/v1/agent/catalog",
                         {"machine_id": "sat-m1", "rows": [row]})
        self.srv.state.catalog.refresh()

    def test_routes_through_the_command_journal(self):
        self._push(True)
        status, body = self.srv.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/fork",
            {"prompt": "go"})
        self.assertEqual(status, 202, body)
        self.assertTrue(body["queued"])
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        cmd = replay[-1]
        self.assertEqual(cmd["session"], "claude:r1")
        self.assertEqual(cmd["data"]["op"], "fork")
        self.assertEqual(cmd["data"]["prompt"], "go")
        self.assertEqual(cmd["data"]["launch_id"], body["launch_id"])
        # The satellite's failure settles the launch the phone polls.
        self.srv.request("POST", "/v1/agent/events", {
            "machine_id": "sat-m1", "events": [protocol.event(
                "command.result", "claude:r1", "sat-m1",
                {"op": "fork", "ok": False, "launch_id": body["launch_id"],
                 "note": "no such folder on this machine: /r"})]})
        launch = self.srv.state.launches[body["launch_id"]]
        self.assertEqual(launch["state"], "failed")
        self.assertIn("no such folder", launch["note"])

    def test_adoption_names_the_hub_key(self):
        self._push(True)
        _s, body = self.srv.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/fork", {})
        lid = body["launch_id"]
        self.srv.request("POST", "/v1/agent/catalog", {
            "machine_id": "sat-m1", "rows": [
                {"key": "claude:r1", "can_fork": True},
                {"key": "claude:n1", "ownership": "attached"}]})
        self.srv.state.catalog.refresh()
        self.srv.request("POST", "/v1/agent/events", {
            "machine_id": "sat-m1", "events": [protocol.event(
                "session.launched", "claude:n1", "sat-m1",
                {"launch_id": lid})]})
        _s, view = self.srv.request("GET", f"/v1/launches/{lid}")
        self.assertEqual((view["state"], view["key"]),
                         ("started", "claude:n1@sat-m1"))

    def test_refused_without_can_fork(self):
        for flag in (False, None):  # None: a satellite too old to say
            with self.subTest(can_fork=flag):
                self._push(flag)
                status, body = self.srv.request(
                    "POST", "/v1/sessions/claude%3Ar1%40sat-m1/fork", {})
                self.assertEqual(status, 409, body)
                self.assertEqual(body["error"]["code"], "fork_unsupported")


if __name__ == "__main__":
    unittest.main()
