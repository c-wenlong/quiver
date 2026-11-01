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

from swe.remote import agent as agent_mod
from swe.remote import detach, inject, protocol, supervise
from swe.sessions.models import Session

from tests._tempdir import temp_dir
from tests.test_remote_hub import _Server


def _session(sid="s1", tool="claude", path="/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   title="test", session_id=sid, tool_name=tool)


def _tmux_meta(name, sid="s1"):
    return {"mode": "tmux", "tmux": name, "tool_name": "claude",
            "session_id": sid, "cwd": "/tmp/proj", "attached_at": 0}


def _until(predicate, timeout=3.0):
    """Poll ``predicate`` until it holds; False once ``timeout`` runs out."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


def _count_polls(detaches):
    """Count the status checks of every when_idle wait ``detaches`` starts.

    A test then waits for a few checks instead of a fixed time, so "it
    has not fired yet" is asserted after the waiter really looked.
    """
    polls = []
    real = detaches.schedule

    def schedule(key, *, status, fire, report):
        def counted():
            polls.append(key)
            return status()
        return real(key, status=counted, fire=fire, report=report)

    detaches.schedule = schedule
    return polls


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
        # It really was polling, and saw "active" each time.
        self.assertTrue(_until(lambda: len(self.statuses) > 2))
        self.assertFalse(self.fired.is_set())
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
        entry = self.pd._pending["k"]
        self.assertEqual(self.pd.cancel("k", "cancelled by attach"),
                         "cancelled")
        self.assertEqual(self.reports.get(timeout=2),
                         (False, "cancelled by attach", True))
        self._live = "done"
        # The waiter thread has exited, so nothing can fire after this.
        self.assertTrue(entry.done.wait(2))
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

    def test_nudge_rechecks_at_once_instead_of_at_the_next_poll(self):
        # A long poll, so only the nudge can explain a prompt fire.
        checked = threading.Event()

        def status():
            checked.set()
            return self._status()

        with patch.object(detach, "WHEN_IDLE_POLL_S", 60):
            self._live = "active"
            self.pd.schedule("k", status=status, fire=self._fire,
                             report=self._report)
            self.assertTrue(checked.wait(2))  # first check: still busy
            self._live = "done"
            self.pd.nudge("k")
            self.assertTrue(self.fired.wait(2))
        self.assertEqual(self.reports.get(timeout=2),
                         (True, "detached", False))

    def test_nudge_does_not_spin_the_waiter(self):
        self._live = "active"
        self._schedule()
        self.pd.nudge("k")
        time.sleep(0.1)
        # About one check per 20 ms poll, not thousands from a wake left set.
        self.assertLess(len(self.statuses), 30)
        self.pd.cancel("k", "x")
        self.pd.nudge("nobody")  # nothing pending: a no-op

    def test_probed_idle_status_nudges_hub_and_satellite_detaches(self):
        from swe.remote.hub import HubState
        state = HubState(temp_dir(self))
        ag = agent_mod.RemoteAgent("http://h:1", "c", temp_dir(self))
        for owner in (state, ag):
            with patch.object(owner.detaches, "nudge") as nudge, \
                 patch.object(owner.catalog, "note_status") as note:
                owner.tail_manager._on_status("claude:s1", "active", 5.0)
                nudge.assert_not_called()
                owner.tail_manager._on_status("claude:s1", "done", 6.0)
                nudge.assert_called_once_with("claude:s1")
                self.assertEqual(note.call_count, 2)


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
            # The registry row says tmux; tmux itself is faked, binary
            # included (CI's Nix sandbox has none).
            patch.object(supervise, "tmux_bin", return_value="/x/tmux"),
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
        self.polls = _count_polls(self.srv.state.detaches)
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
        self.assertTrue(_until(lambda: len(self.polls) > 2))
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
        entry = self.srv.state.detaches._pending[self.key]
        status, data = self._control({"op": "attach"})
        self.assertEqual(status, 200, data)
        self.assertIn("cancelled", data["note"])
        result = self._event("command.result")["data"]
        self.assertFalse(result["ok"])
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["op"], "detach")
        self.live = ["done"]
        self.assertTrue(entry.done.wait(2))  # the waiter has exited
        self.assertEqual(self.kills, [])
        self.assertEqual(self._ownership(), "attached")
        self.assertFalse(self.srv.state.detaches.pending(self.key))

    def test_now_replaces_a_pending_when_idle(self):
        self._control({"op": "detach", "mode": "when_idle"})
        entry = self.srv.state.detaches._pending[self.key]
        status, data = self._control({"op": "detach", "mode": "now"})
        self.assertEqual(status, 200, data)
        result = self._event("command.result")["data"]
        self.assertTrue(result["cancelled"])
        self.assertEqual(self.kills, ["qv-fake"])
        self.live = ["done"]
        self.assertTrue(entry.done.wait(2))  # the waiter has exited
        self.assertEqual(self.kills, ["qv-fake"])  # not killed twice

    def test_when_idle_needs_an_attached_session(self):
        supervise.unmark(self.srv.home, self.key)
        self.srv.state.catalog.refresh()
        status, data = self._control({"op": "detach", "mode": "when_idle"})
        self.assertEqual(status, 409, data)

    def test_keep_leaves_tmux_and_keeps_files(self):
        from swe.remote import attachments
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

    def test_a_kept_session_takes_only_the_pick_it_runs(self):
        # The kept pane is live: the recorded pick is what it already
        # runs, so the same pick adopts — but a different one can't be
        # typed into a possibly mid-turn pane and is refused rather
        # than silently dropped.
        supervise.unmark(self.srv.home, self.key)
        supervise._save_kept(self.srv.home, {self.key: dict(
            _tmux_meta("qv-fake"), model="opus",
            pane_pid=4242, kept_at=time.time())})
        self.srv.state.catalog.refresh()  # the row reads observed now
        with patch.object(supervise, "pane_pid", return_value=4242), \
             patch.object(supervise, "launch_tmux") as launch:
            status, data = self._control(
                {"op": "attach", "model": "sonnet"})
            self.assertEqual(status, 409, data)
            self.assertIn("/model", data["error"]["message"])
            self.assertIn(self.key, supervise.kept(self.srv.home))
            self.assertEqual(self._ownership(), "observed")
            status, data = self._control({"op": "attach", "model": "opus"})
            self.assertEqual(status, 200, data)
            self.assertIn("adopted", data["note"])
            launch.assert_not_called()
            self.assertEqual(
                supervise.meta_for(self.srv.home, self.key)["model"],
                "opus")

    def test_attach_rechecks_after_a_detach_it_waited_for(self):
        # The attach arrives while the when_idle detach is running and a
        # catalog refresh swaps the rows meanwhile: the request's own
        # row still says attached, but the session is observed now.
        firing, release = threading.Event(), threading.Event()
        state = self.srv.state

        def slow_detach(session, op, value):
            firing.set()
            release.wait(3)
            state.catalog.refresh()
            supervise.unmark(self.srv.home, self.key)
            return inject.InjectResult(True, "detached")

        fake = MagicMock()
        fake.control.side_effect = slow_detach
        old_row = state.catalog.row_for(self.key)
        with patch.object(inject, "injector_for", return_value=fake), \
             patch.object(supervise, "attach",
                          return_value=(True, "attached under tmux")) as att:
            status, _ = self._control({"op": "detach", "mode": "when_idle"})
            self.assertEqual(status, 202)
            self.live = ["done"]
            self.assertTrue(firing.wait(3))
            # cancel() waits on the running detach's done event; note
            # when it starts waiting, so the release lands only then.
            waiting = threading.Event()

            class _NotedDone(threading.Event):
                def wait(self, timeout=None):
                    waiting.set()
                    return super().wait(timeout)

            state.detaches._pending[self.key].done = _NotedDone()
            answer = {}
            t = threading.Thread(target=lambda: answer.update(
                zip(("status", "data"), self._control({"op": "attach"}))))
            t.start()
            self.assertTrue(waiting.wait(5))
            release.set()
            t.join(5)
            self.assertFalse(t.is_alive())
        self.assertEqual(answer["status"], 200, answer)
        self.assertEqual(answer["data"]["note"], "attached under tmux")
        att.assert_called_once()
        self.assertIsNot(state.catalog.row_for(self.key), old_row)
        self.assertEqual(old_row["ownership"], "attached")  # the stale copy

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

    def test_satellite_control_carries_the_client_id(self):
        # The agent echoes client_id on its command.result, which is how
        # the phone tells its model switch's failure from another's.
        captured = []
        self.srv.state.queue_command = (
            lambda mid, op, key, data: captured.append(data) or {})
        row = dict(self.srv.state.catalog.row_for(self.key),
                   machine_id="other", ownership="attached")
        with patch.object(self.srv.state.catalog, "row_for",
                          return_value=row):
            status, data = self._control(
                {"op": "model", "value": "sonnet", "client_id": "c-1"})
            self.assertEqual(status, 202, data)
            self.assertEqual(captured[-1], {"ctl": "model", "value": "sonnet",
                                            "client_id": "c-1"})
            status, data = self._control({"op": "interrupt"})
        self.assertEqual(status, 202, data)
        self.assertNotIn("client_id", captured[-1])


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class TestKeptTmuxSessions(unittest.TestCase):
    """keep, adopt and reap against a real, private tmux server."""

    def setUp(self):
        self.home = temp_dir(self)
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


class TestKeptRecordsWithFakes(unittest.TestCase):
    """Record moves and the reaper's edge cases, no tmux needed."""

    def setUp(self):
        self.home = temp_dir(self)
        self.key = "claude:s1"
        self.name = supervise.tmux_name(self.key)
        self.alive = {self.name: True}
        self.pids = {self.name: 4242}
        self.kill_ok = True
        self.kills = []

        def kill(name):
            self.kills.append(name)
            if self.kill_ok:
                self.alive[name] = False
            return self.kill_ok

        for p in (
            patch.object(supervise, "tmux_bin", return_value="/x/tmux"),
            patch.object(supervise, "tmux_alive",
                         side_effect=lambda n: self.alive.get(n, False)),
            patch.object(supervise, "pane_pid",
                         side_effect=lambda n: self.pids.get(n)
                         if self.alive.get(n) else None),
            patch.object(supervise, "tmux_clients", return_value=0),
            patch.object(supervise, "kill", side_effect=kill),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _keep(self):
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        return supervise.keep(self.home, self.key)

    def _attach(self):
        with patch.object(supervise, "_local_writer_pids", return_value=[]):
            return supervise.attach(
                self.home, self.key, _session(), last_append_at=None,
                status="done", mode="tmux")

    def _age(self):
        rows = supervise.kept(self.home)
        rows[self.key]["kept_at"] = time.time() - supervise.KEPT_IDLE_REAP_S - 60
        supervise._save_kept(self.home, rows)

    def _reap(self):
        return supervise.reap_kept(self.home, lambda k: None)

    def test_reaper_keeps_a_foreign_pane_and_attach_keeps_refusing(self):
        self.assertTrue(self._keep()[0])
        self._age()
        self.pids[self.name] = 999  # a new process under our name
        self.assertEqual(self._reap(), [])
        self.assertEqual(self.kills, [])
        self.assertIn(self.key, supervise.kept(self.home))
        with patch.object(supervise, "launch_tmux") as launch:
            ok, note = self._attach()
        self.assertFalse(ok)
        self.assertIn("no longer runs this session", note)
        launch.assert_not_called()
        # Once that session is closed, the record goes and attach runs.
        self.alive[self.name] = False
        self.assertEqual(self._reap(), [])
        self.assertEqual(supervise.kept(self.home), {})

    def test_attach_never_binds_to_a_stray_session_under_the_name(self):
        # No supervised or kept row, but the derived name is taken.
        with patch.object(supervise, "_tmux") as tmux:
            ok, note = self._attach()
        self.assertFalse(ok)
        self.assertIn("already exists and is not supervised", note)
        tmux.assert_not_called()  # no new-session, nothing bound
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_reaper_keeps_the_record_when_kill_fails(self):
        self.assertTrue(self._keep()[0])
        self._age()
        self.kill_ok = False
        self.assertEqual(self._reap(), [])
        self.assertEqual(self.kills, [self.name])
        self.assertIn(self.key, supervise.kept(self.home))
        self.kill_ok = True  # next loop
        self.assertEqual(self._reap(), [self.key])
        self.assertEqual(supervise.kept(self.home), {})

    def test_failed_kill_of_a_session_that_is_gone_anyway_drops_it(self):
        self.assertTrue(self._keep()[0])
        self._age()
        self.kill_ok = False

        def kill(name):
            self.alive[name] = False  # it exited on its own
            return False

        with patch.object(supervise, "kill", side_effect=kill):
            self.assertEqual(self._reap(), [self.key])
        self.assertEqual(supervise.kept(self.home), {})

    def _failing_writes(self, filename):
        real = supervise.atomic_write_text

        def write(path, text):
            if Path(path).name == filename:
                raise OSError("disk full")
            return real(path, text)

        return patch.object(supervise, "atomic_write_text", side_effect=write)

    def test_keep_fails_cleanly_when_the_kept_record_cannot_be_written(self):
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        with self._failing_writes("kept.json"):
            ok, note = supervise.keep(self.home, self.key)
        self.assertFalse(ok)
        self.assertIn("disk full", note)
        self.assertIsNotNone(supervise.meta_for(self.home, self.key))
        self.assertEqual(supervise.kept(self.home), {})

    def test_keep_rolls_back_when_the_registry_cannot_be_written(self):
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        with self._failing_writes("supervised.json"):
            ok, note = supervise.keep(self.home, self.key)
        self.assertFalse(ok)
        self.assertIsNotNone(supervise.meta_for(self.home, self.key))
        self.assertEqual(supervise.kept(self.home), {})

    def test_interrupted_move_settles_on_the_supervised_row(self):
        # A crash between the two writes leaves the key in both.
        supervise.mark(self.home, self.key, _tmux_meta(self.name))
        supervise._save_kept(self.home, {self.key: dict(
            _tmux_meta(self.name), pane_pid=4242, kept_at=time.time())})
        supervise.reconcile_kept(self.home)
        self.assertEqual(supervise.kept(self.home), {})
        self.assertIsNotNone(supervise.meta_for(self.home, self.key))
        # And keep itself settles it first, then keeps normally.
        supervise._save_kept(self.home, {self.key: dict(
            _tmux_meta(self.name), pane_pid=1, kept_at=0)})
        ok, _note = supervise.keep(self.home, self.key)
        self.assertTrue(ok)
        self.assertEqual(supervise.kept(self.home)[self.key]["pane_pid"], 4242)
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_adopt_keeps_the_kept_record_when_the_registry_write_fails(self):
        self.assertTrue(self._keep()[0])
        with self._failing_writes("supervised.json"), \
             patch.object(supervise, "launch_tmux") as launch:
            ok, note = self._attach()
        self.assertFalse(ok)
        self.assertIn("disk full", note)
        launch.assert_not_called()
        self.assertIn(self.key, supervise.kept(self.home))
        self.assertIsNone(supervise.meta_for(self.home, self.key))
        ok, note = self._attach()
        self.assertTrue(ok, note)
        self.assertIn("adopted", note)

    def test_a_fork_or_launch_marker_never_adopts_a_kept_session(self):
        # A fork from the kept session (or any launch) in its folder
        # leaves a pending marker; the kept key must not be bound to
        # that new pane, even when its transcript looks new enough.
        proj = temp_dir(self)
        key = protocol.session_key("claude", "s1", str(proj))
        supervise._save_kept(self.home, {key: dict(
            _tmux_meta(self.name), cwd=str(proj), pane_pid=4242,
            kept_at=time.time())})
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "qv-fork.json").write_text(json.dumps({
            "tmux": "qv-fork", "tool_name": "claude", "cwd": str(proj),
            "exclude": [], "fork_of": "other", "launched_at": time.time(),
        }))
        transcript = proj / "t.jsonl"
        transcript.write_text("{}\n")
        rows = [(key, _session(path=str(proj)), "done")]
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertIsNone(supervise.meta_for(self.home, key))
        self.assertEqual(supervise.kept(self.home)[key]["tmux"], self.name)
        self.assertTrue((folder / "qv-fork.json").exists())

    def test_adopt_with_a_failed_kept_cleanup_still_attaches(self):
        self.assertTrue(self._keep()[0])
        with self._failing_writes("kept.json"):
            ok, note = self._attach()
        self.assertTrue(ok, note)
        # Left in both for a moment; the supervised row wins.
        supervise.reconcile_kept(self.home)
        self.assertEqual(supervise.kept(self.home), {})
        self.assertIsNotNone(supervise.meta_for(self.home, self.key))


class TestSatelliteDetachModes(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.agent = agent_mod.RemoteAgent("http://hub", "cred",
                                           home=self.home)
        self.addCleanup(self.agent._stop.set)
        self.agent.catalog.session_for = MagicMock(return_value=_session())
        self.key = "claude:s1"
        supervise.mark(self.home, self.key, _tmux_meta("qv-fake"))
        self.live = ["active"]
        self.agent.status_of = lambda sessions: list(self.live)
        self.polls = _count_polls(self.agent.detaches)
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
        self.assertTrue(_until(lambda: len(self.polls) > 2))
        self.assertEqual(self.kills, [])
        self.live = ["done"]
        self.assertTrue(_until(self._results))
        result = self._results()[0]
        self.assertTrue(result["ok"])
        self.assertEqual(result["op"], "detach")
        self.assertEqual(self.kills, ["qv-fake"])
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_when_idle_cancelled_by_attach(self):
        self._control("when_idle")
        entry = self.agent.detaches._pending[self.key]
        ok, note, _ = self.agent._execute(self.key, "attach", {})
        self.assertTrue(ok)
        self.assertIn("pending detach is cancelled", note)
        self.assertTrue(self._results()[0]["cancelled"])
        self.live = ["done"]
        self.assertTrue(entry.done.wait(2))  # the waiter has exited
        self.assertEqual(self.kills, [])
        self.assertIsNotNone(supervise.meta_for(self.home, self.key))

    def test_when_idle_needs_an_attached_session(self):
        supervise.unmark(self.home, self.key)
        ok, note, _ = self._control("when_idle")
        self.assertFalse(ok)
        self.assertIn("not attached", note)


if __name__ == "__main__":
    unittest.main()
