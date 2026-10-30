"""fork.py: fork a session from the phone, on the hub or a satellite."""

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
from quiver.remote import protocol, supervise
from quiver.sessions.models import Session

from tests.test_remote_hub import _Server

CODEX_ID = "rollout-2026-10-20T10-55-33-01a1144a-41bf-7123-ae76-1cff676b946f"
CODEX_THREAD = "01a1144a-41bf-7123-ae76-1cff676b946f"


def _session(sid="s1", tool="claude", path="/tmp", ts=0):
    return Session(timestamp=int(ts * 1000), agent="x", path=path,
                   title="t", session_id=sid, tool_name=tool)


class _Hub:
    """test_remote_hub's _Server (no tailnet, no price download) with a
    paired phone; ``request`` answers parsed JSON."""

    def __init__(self, sessions):
        self.srv = _Server(sessions=sessions,
                           statuses=lambda ss: ["done"] * len(ss))
        self.home, self.state = self.srv.home, self.srv.state
        cred = self.srv.pair()["credential"]
        self.auth = {"Authorization": f"Bearer {cred}"}

    def satellite(self, mid):
        """Pair a satellite credential and bind it to ``mid``."""
        return self.srv.satellite(mid)

    def stop(self):
        self.srv.stop()

    def request(self, method, path, body=None, auth=True):
        # auth=True sends the phone credential; a dict is sent verbatim;
        # False sends nothing (e.g. /v1/pair).
        headers = self.auth if auth is True else (auth or {})
        status, data = self.srv.request(method, path, body=body,
                                        headers=headers)
        return status, json.loads(data or b"{}")


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
            _session("abc"), "done", "m", {})
        self.assertTrue(row["can_fork"])
        _key, row = catalog_mod.session_to_row(
            _session("o1", "opencode"), "done", "m", {})
        self.assertFalse(row["can_fork"])

    def test_body_validation(self):
        self.assertEqual(fork_mod.parse_fork_body({}), {
            "prompt": "", "model": None, "effort": None})
        self.assertEqual(fork_mod.parse_fork_body({"prompt": "  "}), {
            "prompt": "", "model": None, "effort": None})
        self.assertEqual(
            fork_mod.parse_fork_body(
                {"prompt": "go", "model": "opus", "effort": "high"}),
            {"prompt": "go", "model": "opus", "effort": "high"})
        for bad in ({"prompt": 3}, {"prompt": "x" * 40_000},
                    {"model": 5}, {"effort": "two words"}):
            with self.assertRaises(ValueError):
                fork_mod.parse_fork_body(bad)


class TestForkRoute(unittest.TestCase):
    def setUp(self):
        self.proj = tempfile.mkdtemp()
        self.srv = _Hub([
            _session("abc", path=self.proj),
            _session("o1", "opencode", path=self.proj),
            _session(CODEX_ID, "codex", path=self.proj),
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

    def test_a_fork_that_raises_still_settles_the_launch(self):
        with patch.object(fork_mod, "start",
                          side_effect=RuntimeError("tmux vanished")):
            _s, body = self._fork("claude:abc")
            self._wait()
        launch = self.srv.state.launches[body["launch_id"]]
        self.assertEqual(launch["state"], "failed")
        self.assertIn("tmux vanished", launch["note"])

    def test_fork_carries_the_pick(self):
        with patch.object(fork_mod, "start",
                          return_value=(True, "forked")) as start:
            status, body = self._fork("claude:abc", {
                "prompt": "next", "model": "opus", "effort": "high"})
            self._wait()
        self.assertEqual(status, 202, body)
        kw = start.call_args[1]
        self.assertEqual((kw["model"], kw["effort"]), ("opus", "high"))
        _s, view = self.srv.request(
            "GET", f"/v1/launches/{body['launch_id']}")
        self.assertEqual((view["model"], view["effort"]), ("opus", "high"))

    def test_refusals(self):
        with patch.object(fork_mod, "start") as start:
            cases = [
                ("claude:nope", {}, 404, "unknown_session"),
                ("opencode:o1", {}, 409, "fork_unsupported"),
                ("claude:gone", {}, 422, "bad_cwd"),
                ("claude:abc", {"prompt": 5}, 422, "bad_command"),
                ("claude:abc", {"model": "two words"}, 422, "bad_command"),
                # codex forks, but its TUI has no verified /effort.
                (f"codex:{CODEX_ID}", {"effort": "high"},
                 409, "unsupported_launch_option"),
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
             patch.object(supervise, "tmux_name", return_value="qv-fork"), \
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


class TestOverlappingForks(unittest.TestCase):
    """Harnesses that pick their own id fork one at a time per folder."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.proj = tempfile.mkdtemp()

    def _fork(self, source, argv, expect_id="", tmux_up=False):
        names = iter(["qv-a", "qv-b", "qv-c"])
        with patch.object(supervise.shutil, "which", return_value="/x/bin"), \
             patch.object(supervise, "launch_tmux", return_value="qv"), \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "tmux_alive", return_value=tmux_up), \
             patch.object(supervise, "tmux_name",
                          side_effect=lambda _k: next(names)):
            return supervise.launch_fork(self.home, source, argv, "",
                                         expect_id=expect_id)

    def test_second_codex_fork_waits_for_the_first(self):
        src = _session(CODEX_ID, "codex", self.proj)
        ok, _n, _ = self._fork(src, ["codex", "fork", CODEX_THREAD])
        self.assertTrue(ok)
        ok, note, _ = self._fork(src, ["codex", "fork", CODEX_THREAD])
        self.assertFalse(ok)
        self.assertIn("still waiting", note)
        markers = list(supervise._pending_dir(self.home).glob("*.json"))
        self.assertEqual(len(markers), 1)
        # Once the first is adopted (its marker gone) the next one runs.
        markers[0].unlink()
        ok, _n, _ = self._fork(src, ["codex", "fork", CODEX_THREAD])
        self.assertTrue(ok)

    def _age_markers(self):
        for marker in supervise._pending_dir(self.home).glob("*.json"):
            data = json.loads(marker.read_text())
            data["launched_at"] -= 600
            marker.write_text(json.dumps(data))

    def test_a_stale_marker_with_a_dead_pane_does_not_block(self):
        src = _session("d1", "droid", self.proj)
        self._fork(src, ["droid", "--fork", "d1"])
        self._age_markers()
        ok, _n, _ = self._fork(src, ["droid", "--fork", "d1"])
        self.assertTrue(ok)
        # A fresh marker blocks on its own ...
        ok, note, _ = self._fork(src, ["droid", "--fork", "d1"])
        self.assertFalse(ok)
        self.assertIn("still waiting", note)
        # ... and a live pane still holds the folder once every marker
        # is past the reservation window.
        self._age_markers()
        ok, note, _ = self._fork(src, ["droid", "--fork", "d1"],
                                 tmux_up=True)
        self.assertFalse(ok)
        self.assertIn("still waiting", note)

    def test_claude_forks_name_their_id_and_never_wait(self):
        src = _session("abc", path=self.proj)
        for new in ("n1", "n2"):
            ok, _n, _ = self._fork(src, ["claude"], expect_id=new)
            self.assertTrue(ok)

    def test_overlapping_launches_never_claim_a_named_fork(self):
        # A launch_new marker (no id) and a claude fork (named n1) wait in
        # one folder: the launch must not take n1 by creation time.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        now = time.time()
        (folder / "qv-new.json").write_text(json.dumps({
            "tmux": "qv-new", "tool_name": "claude", "cwd": self.proj,
            "launched_at": now, "launch_id": "lnew", "exclude": ["abc"]}))
        (folder / "qv-f.json").write_text(json.dumps({
            "tmux": "qv-f", "tool_name": "claude", "cwd": self.proj,
            "launched_at": now, "launch_id": "lfork", "expect_id": "n1",
            "exclude": ["abc"]}))
        fresh = MagicMock()
        fresh.stat.return_value = MagicMock(
            st_birthtime=now + 1, st_mtime=now + 1, st_ctime=now + 1)
        rows = [("claude:n1", _session("n1", path=self.proj), "done"),
                ("claude:x9", _session("x9", path=self.proj), "done")]
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=fresh):
            adopted = supervise.adopt_pending(self.home, rows)
        self.assertEqual(sorted(adopted), ["claude:n1", "claude:x9"])
        self.assertEqual(
            supervise.meta_for(self.home, "claude:n1")["launch_id"], "lfork")
        self.assertEqual(
            supervise.meta_for(self.home, "claude:x9")["launch_id"], "lnew")

    def test_route_answers_409_while_a_fork_is_pending(self):
        srv = _Hub([_session(CODEX_ID, "codex", self.proj)])
        self.addCleanup(srv.stop)
        folder = supervise._pending_dir(srv.home)
        folder.mkdir(parents=True)
        (folder / "qv-a.json").write_text(json.dumps({
            "tmux": "qv-a", "tool_name": "codex", "cwd": self.proj,
            "launched_at": time.time()}))
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch("quiver.remote.fork.shutil.which", return_value="/x/c"), \
             patch.object(fork_mod, "start") as start:
            status, body = srv.request(
                "POST", f"/v1/sessions/codex:{CODEX_ID}/fork", {})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "fork_pending")
        start.assert_not_called()


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

    def test_attaching_a_fork_stops_its_own_writer(self):
        lines = {
            "1": "claude --resume abc",
            "2": "claude --resume abc --fork-session --session-id n1",
            "3": "claude --resume n1",
        }

        def fake_run(argv, **_kw):
            cp = MagicMock()
            cp.stdout = "1\n2\n3\n" if argv[0] == "pgrep" else lines.get(
                argv[2], "")
            return cp

        with patch.object(supervise.subprocess, "run", side_effect=fake_run):
            self.assertEqual(
                supervise._local_writer_pids(_session("n1")), [2, 3])

    def test_a_slow_satellite_fork_does_not_hold_up_later_commands(self):
        ag = agent_mod.RemoteAgent("http://h:1", "cred",
                                   Path(tempfile.mkdtemp()))
        release = threading.Event()
        ran = []

        def execute(key, op, data):
            if op == "fork":
                release.wait(5)
            ran.append(op)
            return True, op, False

        with patch.object(ag, "_execute", side_effect=execute):
            ag._handle_frame(json.dumps({"type": "command", "seq": 1,
                "session": "claude:abc", "data": {"op": "fork"}}).encode())
            ag._handle_frame(json.dumps({"type": "command", "seq": 2,
                "session": "claude:abc",
                "data": {"op": "control", "ctl": "interrupt"}}).encode())
            self.assertEqual(ran, ["control"])
            # The cursor waits for the fork, so a crash would replay it.
            self.assertEqual(ag._done_cmd_seq, 0)
            release.set()
            for t in threading.enumerate():
                if t.name.startswith("agent-fork-"):
                    t.join(timeout=5)
        self.assertEqual(ran, ["control", "fork"])
        self.assertEqual(ag._done_cmd_seq, 2)

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
                "claude:abc", "fork",
                {"prompt": "go", "launch_id": "l5",
                 "model": "opus", "effort": "high"})
        self.assertTrue(ok)
        self.assertEqual(start.call_args[0][1].session_id, "abc")
        self.assertEqual(start.call_args[0][3:], ("go", "l5"))
        self.assertEqual(start.call_args[1]["model"], "opus")
        self.assertEqual(start.call_args[1]["effort"], "high")
        ok, note, _deg = ag._execute("claude:ghost", "fork", {})
        self.assertFalse(ok)
        self.assertIn("unknown session", note)


class TestSatelliteForkRoute(unittest.TestCase):
    def setUp(self):
        self.srv = _Hub([])
        self.addCleanup(self.srv.stop)
        self.sat = self.srv.satellite("sat-m1")

    def _push(self, can_fork):
        row = {"key": "claude:r1", "harness": "claude", "cwd": "/r",
               "ownership": "observed"}
        if can_fork is not None:
            row["can_fork"] = can_fork
        self.srv.request("POST", "/v1/agent/catalog",
                         {"machine_id": "sat-m1", "rows": [row]},
                         auth=self.sat)
        self.srv.state.catalog.refresh()

    def test_routes_through_the_command_journal(self):
        self._push(True)
        # The satellite advertises launch-option support, the same
        # fields its own agent reports.
        catalog_mod.upsert_machine(self.srv.home, {
            "id": "sat-m1", "model_at_launch": ["claude"],
            "effort_at_launch": ["claude"]})
        status, body = self.srv.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/fork",
            {"prompt": "go", "model": "opus", "effort": "high"})
        self.assertEqual(status, 202, body)
        self.assertTrue(body["queued"])
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        cmd = replay[-1]
        self.assertEqual(cmd["session"], "claude:r1")
        self.assertEqual(cmd["data"]["op"], "fork")
        self.assertEqual(cmd["data"]["prompt"], "go")
        self.assertEqual(cmd["data"]["model"], "opus")
        self.assertEqual(cmd["data"]["effort"], "high")
        self.assertEqual(cmd["data"]["launch_id"], body["launch_id"])
        # The satellite's failure settles the launch the phone polls.
        self.srv.request("POST", "/v1/agent/events", {
            "machine_id": "sat-m1", "events": [protocol.event(
                "command.result", "claude:r1", "sat-m1",
                {"op": "fork", "ok": False, "launch_id": body["launch_id"],
                 "note": "no such folder on this machine: /r"})]},
            auth=self.sat)
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
                {"key": "claude:n1", "ownership": "attached"}]},
            auth=self.sat)
        self.srv.state.catalog.refresh()
        self.srv.request("POST", "/v1/agent/events", {
            "machine_id": "sat-m1", "events": [protocol.event(
                "session.launched", "claude:n1", "sat-m1",
                {"launch_id": lid})]},
            auth=self.sat)
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

    def test_pick_refused_when_the_satellite_is_too_old(self):
        # The agent reads picks only since the release that reports
        # model_at_launch; without it a fork would drop them silently.
        self._push(True)
        status, body = self.srv.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/fork",
            {"prompt": "go", "model": "opus"})
        self.assertEqual(status, 409, body)
        self.assertEqual(body["error"]["code"], "unsupported_launch_option")
        self.assertIn("too old", body["error"]["message"])
        self.assertEqual(self.srv.state.launches, {})
        replay, _ = self.srv.state.journal.read_since("_sat_sat-m1", 0)
        self.assertEqual(
            [c for c in replay if c["data"]["op"] == "fork"], [])
        # No pick: the same old agent forks as before.
        status, body = self.srv.request(
            "POST", "/v1/sessions/claude%3Ar1%40sat-m1/fork", {})
        self.assertEqual(status, 202, body)


if __name__ == "__main__":
    unittest.main()
