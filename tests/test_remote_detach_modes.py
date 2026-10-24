"""Detach modes: ``now``, ``when_idle``, ``keep``; kept-session adoption
and reaping (issue #656).

Real tmux runs only on a private server (``tmux -L <name>``, set through
``supervise._TMUX_SOCKET``) and is skipped where tmux is missing; the
rest uses fakes.
"""

import json
import os
import queue
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import agent as agent_mod
from quiver.remote import detach, inject, protocol, supervise
from quiver.sessions.models import Session

from tests.test_remote_hub import _Server


def _session(sid="s1", tool="claude", path="/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   title="test", session_id=sid, tool_name=tool)


def _tmux_meta(name, sid="s1"):
    return {"mode": "tmux", "tmux": name, "tool_name": "claude",
            "session_id": sid, "cwd": "/tmp/proj", "attached_at": 0}


class TestDetachModeParsing(unittest.TestCase):
    def test_mode_defaults_to_now(self):
        self.assertEqual(
            protocol.parse_control_command({"op": "detach"})["mode"], "now")
        self.assertEqual(protocol.parse_control_command(
            {"op": "detach", "mode": None})["mode"], "now")

    def test_every_mode_parses(self):
        for mode in ("now", "when_idle", "keep"):
            out = protocol.parse_control_command(
                {"op": "detach", "mode": mode})
            self.assertEqual(out["mode"], mode)

    def test_bad_modes_refused(self):
        for mode in ("later", [], {}, 3):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                protocol.parse_control_command({"op": "detach", "mode": mode})

    def test_other_ops_ignore_mode(self):
        # attach carries its own mode (tmux, headless, ...).
        out = protocol.parse_control_command({"op": "attach", "mode": "tmux"})
        self.assertNotIn("mode", out)


class TestPendingDetaches(unittest.TestCase):
    def setUp(self):
        for p in (patch.object(detach, "WHEN_IDLE_POLL_S", 0.02),):
            p.start()
            self.addCleanup(p.stop)
        self.stop = threading.Event()
        self.pd = detach.PendingDetaches(self.stop)
        self.statuses = []
        self.fired = threading.Event()
        self.reports = queue.Queue()

    def _status(self):
        self.statuses.append(self._live)
        return self._live

    def _fire(self):
        self.fired.set()
        return True, "detached"

    def _report(self, ok, note, cancelled):
        self.reports.put((ok, note, cancelled))

    def _schedule(self, key="k"):
        return self.pd.schedule(key, status=self._status, fire=self._fire,
                                report=self._report)

    def test_fires_on_the_idle_transition_and_not_before(self):
        self._live = "active"
        self.assertTrue(self._schedule())
        time.sleep(0.2)
        self.assertFalse(self.fired.is_set())
        self.assertGreater(len(self.statuses), 2)  # it really was polling
        self.assertTrue(self.pd.pending("k"))
        self._live = "done"
        self.assertTrue(self.fired.wait(2))
        self.assertEqual(self.reports.get(timeout=2),
                         (True, "detached", False))
        self.assertFalse(self.pd.pending("k"))

    def test_second_schedule_is_refused_while_one_waits(self):
        self._live = "active"
        self.assertTrue(self._schedule())
        self.assertFalse(self._schedule())
        self.pd.cancel("k", "x")

    def test_cancel_reports_once_and_never_fires(self):
        self._live = "active"
        self._schedule()
        self.assertEqual(self.pd.cancel("k", "cancelled by attach"),
                         "cancelled")
        self.assertEqual(self.reports.get(timeout=2),
                         (False, "cancelled by attach", True))
        self._live = "done"
        time.sleep(0.15)
        self.assertFalse(self.fired.is_set())
        self.assertTrue(self.reports.empty())
        self.assertEqual(self.pd.cancel("k", "again"), "none")

    def test_cancel_during_fire_waits_for_it(self):
        release = threading.Event()

        def slow_fire():
            self.fired.set()
            release.wait(2)
            return True, "detached"

        self._live = "done"
        self.pd.schedule("k", status=self._status, fire=slow_fire,
                         report=self._report)
        self.assertTrue(self.fired.wait(2))
        threading.Timer(0.1, release.set).start()
        self.assertEqual(self.pd.cancel("k", "late"), "fired")
        self.assertEqual(self.reports.get(timeout=2),
                         (True, "detached", False))

    def test_wait_is_bounded(self):
        self._live = "active"
        with patch.object(detach, "WHEN_IDLE_MAX_S", 0.1):
            self._schedule()
            ok, note, cancelled = self.reports.get(timeout=2)
        self.assertFalse(ok)
        self.assertFalse(cancelled)
        self.assertIn("still attached", note)
        self.assertFalse(self.fired.is_set())
        self.assertFalse(self.pd.pending("k"))

    def test_stop_reports_and_never_fires(self):
        self._live = "active"
        self._schedule()
        self.stop.set()
        self.pd.close()
        ok, note, _ = self.reports.get(timeout=2)
        self.assertFalse(ok)
        self.assertIn("still attached", note)
        self.assertFalse(self.fired.is_set())


class _TmuxAttachedServer(_Server):
    """Hub with claude:s1 supervised in tmux mode (tmux faked)."""

    def __init__(self):
        super().__init__(sessions=[_session()],
                         statuses=lambda ss: ["done"])
        supervise.mark(self.home, "claude:s1", _tmux_meta("qv-fake"))
        self.state.catalog.refresh()


class TestHubDetachRoutes(unittest.TestCase):
    def setUp(self):
        self.kills = []
        for p in (
            patch.object(detach, "WHEN_IDLE_POLL_S", 0.02),
            # The registry row says tmux; tmux itself is faked.
            patch.object(supervise, "tmux_alive", return_value=True),
            patch.object(supervise, "kill",
                         side_effect=lambda n: self.kills.append(n) or True),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.srv = _TmuxAttachedServer()
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        self.key = "claude:s1"
        self.live = ["active"]
        self.srv.state.status_of = lambda sessions: list(self.live)
        self.events = self.srv.state.bus.subscribe(self.key)
        self.addCleanup(self.srv.state.detaches.close)
        self.addCleanup(self.srv.state._stop.set)

    def _control(self, body):
        status, data = self.srv.request(
            "POST", f"/v1/sessions/{self.key}/control", body=body,
            headers=self.hdr)
        return status, json.loads(data)

    def _event(self, etype):
        deadline = time.time() + 3
        while time.time() < deadline:
            ev = self.events.get(timeout=3)
            if ev["type"] == etype:
                return ev
        self.fail(f"no {etype} event")

    def _ownership(self):
        return self.srv.state.catalog.row_for(self.key)["ownership"]

    def test_now_is_unchanged_and_the_default(self):
        for body in ({"op": "detach"}, {"op": "detach", "mode": "now"}):
            with self.subTest(body=body):
                supervise.mark(self.srv.home, self.key, _tmux_meta("qv-fake"))
                self.srv.state.catalog.refresh()
                status, data = self._control(body)
                self.assertEqual(status, 200, data)
                self.assertEqual(self.kills[-1], "qv-fake")
                self.assertIsNone(supervise.meta_for(self.srv.home, self.key))
                self.assertEqual(self._ownership(), "observed")
                self.assertEqual(
                    self._event("session.ownership")["data"]["ownership"],
                    "observed")
        self.assertEqual(supervise.kept(self.srv.home), {})

    def test_bad_mode_is_422(self):
        status, data = self._control({"op": "detach", "mode": "soon"})
        self.assertEqual(status, 422, data)

    def test_when_idle_waits_for_the_turn_then_detaches(self):
        status, data = self._control({"op": "detach", "mode": "when_idle"})
        self.assertEqual(status, 202, data)
        self.assertTrue(data["queued"])
        self.assertEqual(data["mode"], "when_idle")
        time.sleep(0.2)
        # Still mid-turn: nothing killed, still writable.
        self.assertEqual(self.kills, [])
        self.assertEqual(self._ownership(), "attached")
        self.assertIsNotNone(supervise.meta_for(self.srv.home, self.key))
        # A second request while one waits is not a second detach.
        status, data = self._control({"op": "detach", "mode": "when_idle"})
        self.assertEqual(status, 202, data)
        self.assertIn("already", data["note"])
        self.live = ["done"]
        own = self._event("session.ownership")
        self.assertEqual(own["data"]["ownership"], "observed")
        result = self._event("command.result")["data"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["op"], "detach")
        self.assertEqual(result["mode"], "when_idle")
        self.assertEqual(self.kills, ["qv-fake"])
        self.assertIsNone(supervise.meta_for(self.srv.home, self.key))

    def test_when_idle_cancelled_by_attach(self):
        status, _ = self._control({"op": "detach", "mode": "when_idle"})
        self.assertEqual(status, 202)
        status, data = self._control({"op": "attach"})
        self.assertEqual(status, 200, data)
        self.assertIn("cancelled", data["note"])
        result = self._event("command.result")["data"]
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["op"], "detach")
        self.live = ["done"]
        time.sleep(0.2)
        self.assertEqual(self.kills, [])
        self.assertEqual(self._ownership(), "attached")
        self.assertFalse(self.srv.state.detaches.pending(self.key))

    def test_now_replaces_a_pending_when_idle(self):
        self._control({"op": "detach", "mode": "when_idle"})
        status, data = self._control({"op": "detach", "mode": "now"})
        self.assertEqual(status, 200, data)
        result = self._event("command.result")["data"]
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.kills, ["qv-fake"])
        self.live = ["done"]
        time.sleep(0.2)
        self.assertEqual(self.kills, ["qv-fake"])  # not killed twice

    def test_when_idle_needs_an_attached_session(self):
        supervise.unmark(self.srv.home, self.key)
        self.srv.state.catalog.refresh()
        status, data = self._control({"op": "detach", "mode": "when_idle"})
        self.assertEqual(status, 409, data)

    def test_keep_leaves_tmux_and_keeps_files(self):
        from quiver.remote import attachments
        staged, _ = attachments.stage(
            self.srv.home, self.key, "a.png", b"\x89PNG\r\n\x1a\n")
        with patch.object(supervise, "pane_pid", return_value=4242):
            status, data = self._control({"op": "detach", "mode": "keep"})
        self.assertEqual(status, 200, data)
        self.assertIn("tmux attach -t qv-fake", data["note"])
        self.assertEqual(self.kills, [])
        self.assertEqual(self._ownership(), "observed")
        kept = supervise.kept(self.srv.home)[self.key]
        self.assertEqual(kept["tmux"], "qv-fake")
        self.assertEqual(kept["pane_pid"], 4242)
        self.assertGreater(kept["kept_at"], 0)
        self.assertIsNotNone(
            attachments.resolve(self.srv.home, self.key, staged["ref"]))

    def test_keep_refuses_non_tmux_sessions(self):
        supervise.mark(self.srv.home, self.key, dict(
            _tmux_meta("qv-fake"), mode="headless"))
        status, data = self._control({"op": "detach", "mode": "keep"})
        self.assertEqual(status, 409, data)
        self.assertIn("no tmux session to keep", data["error"]["message"])
        self.assertIsNotNone(supervise.meta_for(self.srv.home, self.key))

    def test_attach_on_a_kept_session_is_allowed_mid_turn(self):
        # Adoption starts nothing, so a busy kept session can be taken
        # back; a non-kept busy one still gets not_idle.
        supervise.unmark(self.srv.home, self.key)
        self.srv.state.catalog._status_of = lambda ss: ["active"]
        self.srv.state.catalog.refresh()
        status, data = self._control({"op": "attach"})
        self.assertEqual(status, 409, data)
        self.assertEqual(data["error"]["code"], "not_idle")
        supervise._save_kept(self.srv.home, {self.key: dict(
            _tmux_meta("qv-fake"), pane_pid=4242, kept_at=time.time())})
        with patch.object(supervise, "pane_pid", return_value=4242), \
             patch.object(supervise, "launch_tmux") as launch:
            status, data = self._control({"op": "attach"})
        self.assertEqual(status, 200, data)
        self.assertIn("adopted", data["note"])
        launch.assert_not_called()
        self.assertEqual(self._ownership(), "attached")

    def test_satellite_detach_carries_its_mode(self):
        captured = []
        self.srv.state.queue_command = (
            lambda mid, op, key, data: captured.append(data) or {})
        row = dict(self.srv.state.catalog.row_for(self.key),
                   machine_id="other")
        with patch.object(self.srv.state.catalog, "row_for",
                          return_value=row):
            status, data = self._control({"op": "detach", "mode": "keep"})
        self.assertEqual(status, 202, data)
        self.assertEqual(captured[-1]["ctl"], "detach")
        self.assertEqual(captured[-1]["mode"], "keep")


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class TestKeptTmuxSessions(unittest.TestCase):
    """keep, adopt and reap against a real, private tmux server."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.socket = f"qv-test-{secrets.token_hex(4)}"
        # The socket file goes in a scratch folder, removed afterwards.
        sockets = tempfile.mkdtemp(prefix="qvt", dir="/tmp")
        self.addCleanup(shutil.rmtree, sockets, True)
        for p in (
            patch.object(supervise, "_TMUX_SOCKET", self.socket),
            patch.dict(os.environ, {"TMUX_TMPDIR": sockets}),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._kill_server)
        self.key = "claude:s1"
        self.session = _session()
        self.name = supervise.tmux_name(self.key)

    def _kill_server(self):
        subprocess.run(["tmux", "-L", self.socket, "kill-server"],
                       capture_output=True)

    def _run_harness(self, name=None):
        """A stand-in harness: a long sleep in its own tmux session."""
        name = name or self.name
        out = subprocess.run(
            ["tmux", "-L", self.socket, "new-session", "-d", "-s", name,
             "sleep", "600"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return name

    def _attach(self, status="done"):
        return supervise.attach(
            self.home, self.key, self.session, last_append_at=None,
            status=status, mode="tmux")

    def test_keep_leaves_tmux_alive_and_reattach_adopts_it(self):
        self._run_harness()
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        pid = supervise.pane_pid(self.name)
        self.assertIsNotNone(pid)
        ok, note = supervise.keep(self.home, self.key)
        self.assertTrue(ok, note)
        self.assertTrue(supervise.tmux_alive(self.name))
        self.assertNotIn(self.key, supervise.supervised(self.home))
        row = supervise.kept(self.home)[self.key]
        self.assertEqual((row["tmux"], row["pane_pid"]), (self.name, pid))
        # Re-attach, even mid-turn: no second launch, same pane.
        with patch.object(supervise, "launch_tmux") as launch, \
             patch.object(supervise, "_stop_local_writers") as stop:
            ok, note = self._attach(status="active")
        self.assertTrue(ok, note)
        self.assertIn("adopted", note)
        launch.assert_not_called()
        stop.assert_not_called()
        meta = supervise.meta_for(self.home, self.key)
        self.assertEqual(meta["tmux"], self.name)
        self.assertEqual(meta["mode"], "tmux")
        self.assertNotIn("pane_pid", meta)
        self.assertEqual(supervise.kept(self.home), {})
        self.assertEqual(supervise.pane_pid(self.name), pid)
        # And `now` after that still closes it.
        self.assertTrue(supervise.detach(self.home, self.key))
        self.assertFalse(supervise.tmux_alive(self.name))

    def test_gone_kept_session_falls_back_to_a_normal_attach(self):
        self._run_harness()
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        supervise.keep(self.home, self.key)
        self._kill_server()
        with patch.object(supervise, "launch_tmux",
                          return_value=None) as launch:
            ok, _note = self._attach()
        launch.assert_called_once()  # the normal path ran
        self.assertFalse(ok)
        self.assertEqual(supervise.kept(self.home), {})

    def test_a_different_process_under_the_name_is_not_adopted(self):
        self._run_harness()
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        supervise.keep(self.home, self.key)
        subprocess.run(["tmux", "-L", self.socket, "kill-session", "-t",
                        self.name], capture_output=True)
        self._run_harness()  # same name, new pane process
        with patch.object(supervise, "launch_tmux") as launch:
            ok, note = self._attach()
        self.assertFalse(ok)
        self.assertIn("no longer runs this session", note)
        launch.assert_not_called()
        self.assertIsNone(supervise.meta_for(self.home, self.key))
        self.assertTrue(supervise.tmux_alive(self.name))

    def test_reaper_kills_only_idle_clientless_kept_sessions(self):
        keys = {k: supervise.tmux_name(k)
                for k in ("claude:old", "claude:young", "claude:watched",
                          "claude:busy")}
        for key, name in keys.items():
            self._run_harness(name)
            supervise.mark(self.home, key, _tmux_meta(name, key[7:]))
            self.assertTrue(supervise.keep(self.home, key)[0])
        # And one that is still supervised, never kept: not touched.
        live = self._run_harness(supervise.tmux_name("claude:live"))
        supervise.mark(self.home, "claude:live", _tmux_meta(live, "live"))
        now = time.time()
        entries = supervise.kept(self.home)
        for key in ("claude:old", "claude:watched", "claude:busy"):
            entries[key]["kept_at"] = now - supervise.KEPT_IDLE_REAP_S - 60
        entries["claude:young"]["kept_at"] = now - 3600
        supervise._save_kept(self.home, entries)

        transcript_at = {"claude:busy": now - 60}
        real_clients = supervise.tmux_clients

        def clients(name):
            # A real attached client needs a terminal; fake that one.
            if name == keys["claude:watched"]:
                return 1
            return real_clients(name)

        with patch.object(supervise, "tmux_clients", side_effect=clients), \
             patch.object(supervise, "_last_activity",
                          side_effect=lambda s: transcript_at.get(
                              f"claude:{s.session_id}", 0.0) if s else 0.0):
            reaped = supervise.reap_kept(
                self.home, lambda key: _session(sid=key[7:]), now=now)
        self.assertEqual(reaped, ["claude:old"])
        self.assertFalse(supervise.tmux_alive(keys["claude:old"]))
        for key in ("claude:young", "claude:watched", "claude:busy"):
            self.assertTrue(supervise.tmux_alive(keys[key]), key)
        self.assertTrue(supervise.tmux_alive(live))
        self.assertEqual(set(supervise.kept(self.home)),
                         {"claude:young", "claude:watched", "claude:busy"})
        self.assertIn("claude:live", supervise.supervised(self.home))

    def test_reaper_drops_rows_whose_tmux_is_gone(self):
        self._run_harness()
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        supervise.keep(self.home, self.key)
        self._kill_server()
        self.assertEqual(supervise.reap_kept(self.home, lambda k: None), [])
        self.assertEqual(supervise.kept(self.home), {})

    def test_keep_of_a_dead_pane_just_detaches(self):
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        ok, note = supervise.keep(self.home, self.key)
        self.assertTrue(ok)
        self.assertIn("already exited", note)
        self.assertEqual(supervise.kept(self.home), {})
        self.assertIsNone(supervise.meta_for(self.home, self.key))


class TestSatelliteDetachModes(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.agent = agent_mod.RemoteAgent("http://hub", "cred",
                                           home=self.home)
        self.addCleanup(self.agent._stop.set)
        self.agent.catalog.session_for = MagicMock(return_value=_session())
        self.key = "claude:s1"
        supervise.mark(self.home, self.key, _tmux_meta("qv-fake"))
        self.live = ["active"]
        self.agent.status_of = lambda sessions: list(self.live)
        self.kills = []
        for p in (
            patch.object(detach, "WHEN_IDLE_POLL_S", 0.02),
            patch.object(supervise, "tmux_alive", return_value=True),
            patch.object(supervise, "kill",
                         side_effect=lambda n: self.kills.append(n) or True),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _control(self, mode):
        return self.agent._execute(
            self.key, "control", {"ctl": "detach", "mode": mode})

    def _results(self):
        return [e["data"] for e in self.agent.outbox
                if e["type"] == "command.result"]

    def test_now_is_the_default(self):
        ok, _note, _ = self.agent._execute(
            self.key, "control", {"ctl": "detach"})
        self.assertTrue(ok)
        self.assertEqual(self.kills, ["qv-fake"])
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_keep(self):
        with patch.object(supervise, "pane_pid", return_value=99):
            ok, note, _ = self._control("keep")
        self.assertTrue(ok, note)
        self.assertEqual(self.kills, [])
        self.assertEqual(supervise.kept(self.home)[self.key]["pane_pid"], 99)
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_when_idle_fires_after_the_turn(self):
        ok, note, _ = self._control("when_idle")
        self.assertTrue(ok)
        self.assertIn("turn ends", note)
        time.sleep(0.2)
        self.assertEqual(self.kills, [])
        self.live = ["done"]
        deadline = time.time() + 3
        while not self._results() and time.time() < deadline:
            time.sleep(0.02)
        result = self._results()[0]
        self.assertTrue(result["ok"])
        self.assertEqual(result["op"], "detach")
        self.assertEqual(self.kills, ["qv-fake"])
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_when_idle_cancelled_by_attach(self):
        self._control("when_idle")
        ok, note, _ = self.agent._execute(self.key, "attach", {})
        self.assertTrue(ok)
        self.assertIn("pending detach is cancelled", note)
        self.assertTrue(self._results()[0]["cancelled"])
        self.live = ["done"]
        time.sleep(0.2)
        self.assertEqual(self.kills, [])
        self.assertIsNotNone(supervise.meta_for(self.home, self.key))

    def test_when_idle_needs_an_attached_session(self):
        supervise.unmark(self.home, self.key)
        ok, note, _ = self._control("when_idle")
        self.assertFalse(ok)
        self.assertIn("not attached", note)


if __name__ == "__main__":
    unittest.main()
