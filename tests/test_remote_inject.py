"""R2: supervise registry + tmux/headless injectors + hub write routes."""

import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.remote import attachments, inject, protocol, supervise
from quiver.sessions.models import Session

from tests._tempdir import temp_dir
from tests.test_remote_hub import _Server


def _session(tool="claude", sid="abc-123", path="/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   session_id=sid, tool_name=tool)


class TestSuperviseRegistry(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.key = "claude:abc-123"

    def test_mark_read_unmark(self):
        supervise.mark(self.home, self.key, {"mode": "headless"})
        self.assertIn(self.key, supervise.supervised(self.home))
        supervise.unmark(self.home, self.key)
        self.assertNotIn(self.key, supervise.supervised(self.home))

    def test_dead_tmux_pruned(self):
        supervise.mark(
            self.home, self.key,
            {"mode": "tmux", "tmux": "qv-dead"},
        )
        with patch.object(supervise, "tmux_alive", return_value=False):
            self.assertNotIn(self.key, supervise.supervised(self.home))
        # and the registry file no longer lists it
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_alive_tmux_survives(self):
        supervise.mark(
            self.home, self.key, {"mode": "tmux", "tmux": "qv-live"}
        )
        with patch.object(supervise, "tmux_alive", return_value=True):
            self.assertIn(self.key, supervise.supervised(self.home))


class TestAttach(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.key = "claude:abc-123"
        self.session = _session()

    def _attach(self, **kw):
        kw.setdefault("last_append_at", None)
        kw.setdefault("status", "done")
        with patch.object(supervise, "_stop_local_writers"):
            return supervise.attach(self.home, self.key, self.session, **kw)

    def test_active_session_refused(self):
        ok, note = self._attach(status="active")
        self.assertFalse(ok)
        self.assertIn("idle", note)

    def test_recent_append_refused(self):
        ok, _ = self._attach(last_append_at=time.time() - 1)
        self.assertFalse(ok)

    def test_headless_marks_registry(self):
        with patch("shutil.which", return_value="/x/claude"):
            ok, note = self._attach(mode="headless")
        self.assertTrue(ok, note)
        meta = supervise.meta_for(self.home, self.key)
        self.assertEqual(meta["mode"], "headless")

    def test_headless_nonclaude_refused(self):
        ok, note = supervise.attach(
            self.home, "opencode:x",
            _session(tool="opencode", sid="x"),
            last_append_at=None, status="done", mode="headless",
        )
        self.assertFalse(ok)
        self.assertIn("claude", note)

    def test_tmux_launch_uses_resume_flags(self):
        with patch.object(
            supervise, "launch_tmux", return_value="qv-x"
        ) as launch, \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "tmux_alive", return_value=True):
            ok, note = self._attach(mode="tmux")
        self.assertTrue(ok, note)
        argv = launch.call_args[0][1]
        self.assertEqual(argv[0], "claude")
        self.assertIn("--resume", argv)
        self.assertIn("abc-123", argv)

    def test_unresumable_refused(self):
        ok, note = supervise.attach(
            self.home, "gemini:x",
            _session(tool="gemini", sid="x"),
            last_append_at=None, status="done",
        )
        self.assertFalse(ok)

    def test_double_attach_is_idempotent(self):
        with patch("shutil.which", return_value="/x/claude"):
            self._attach(mode="headless")
            ok, note = self._attach(mode="headless")
        self.assertTrue(ok)
        self.assertIn("already", note)


class TestTmuxInjector(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.key = "claude:abc-123"
        self.session = _session()
        inject.TmuxInjector._answered.clear()  # class-level replay state
        # send() re-checks supervision at write time — an unmarked
        # session is refused even when the route already cleared it.
        supervise.mark(self.home, self.key, {"mode": "tmux", "tmux": "qv-x"})
        self.inj = inject.TmuxInjector(self.home, self.key)

    def test_queue_refuses_active(self):
        out = self.inj.send(self.session, {"text": "hi", "mode": "queue"}, "active")
        self.assertFalse(out.ok)

    def test_send_refuses_after_detach(self):
        supervise.unmark(self.home, self.key)
        out = self.inj.send(
            self.session, {"text": "hi", "mode": "steer"}, "done"
        )
        self.assertFalse(out.ok)
        self.assertIn("detached", out.note)

    def test_steer_sends(self):
        with patch.object(supervise, "send_text", return_value=True) as st:
            out = self.inj.send(self.session, {"text": "hi", "mode": "steer"}, "active")
        self.assertTrue(out.ok)
        self.assertEqual(st.call_args[0][1], "hi")

    def test_attachment_paths_atref(self):
        with patch.object(supervise, "send_text", return_value=True) as st:
            out = self.inj.send(
                self.session,
                {"text": "look", "mode": "queue",
                 "attachments": ["/tmp/a.png", "/tmp/b.png"]},
                "done",
            )
        self.assertTrue(out.ok)
        sent = st.call_args[0][1]
        self.assertEqual(sent, "@/tmp/a.png @/tmp/b.png look")

    def test_attachment_paths_inline_for_other_harness(self):
        # A codex pane types the path plainly: its @ is a TUI picker.
        with patch.object(supervise, "send_text", return_value=True) as st:
            self.inj.send(
                _session(tool="codex"),
                {"text": "look", "mode": "queue",
                 "attachments": ["/tmp/a.png"]},
                "done",
            )
        self.assertEqual(st.call_args[0][1],
                         "[Attached file: /tmp/a.png] look")

    def test_approval_digit(self):
        pane = "Do you want to proceed? 1. Yes 2. Yes, and don't ask again"
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "always")
        self.assertTrue(out.ok)
        self.assertEqual(sk.call_args[0][1], "2")

    def test_approval_devin_menu_labels_win_over_fixed_map(self):
        # "No" sits at row 8 in devin's menu — a fixed deny=3 mapping
        # would approve a wider grant instead.
        pane = (
            "● Running command\n"
            "$ git pull origin main\n"
            ") 1 Yes  (Approve once)\n"
            "  2 Yes, allow `git` commands\n"
            "  5 Yes, switch to bypass mode\n"
            "  8 No\n"
            "  ↑↓ select · ↵ confirm · esc cancel"
        )
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "deny")
        self.assertTrue(out.ok)
        self.assertEqual(sk.call_args[0][1], "8")

    def test_approval_menu_row_digit(self):
        # The card lists the menu verbatim; a click answers with the
        # row's own digit ("5" = bypass here — the semantic names could
        # never reach it).
        pane = (
            "● Running command\n"
            "$ git pull origin main\n"
            ") 1 Yes  (Approve once)\n"
            "  2 Yes, allow `git` commands\n"
            "  5 Yes, switch to bypass mode\n"
            "  8 No\n"
            "  ↑↓ select · ↵ confirm · esc cancel"
        )
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "5")
        self.assertTrue(out.ok)
        self.assertEqual(sk.call_args[0][1], "5")

    DEVIN_QUESTION = (
        " Which engine?\n"
        "\n"
        " ❭ SQLite\n"
        "   Postgres\n"
        "   Redis\n"
        "   Other (type your own)\n"
        "\n"
        " ↑↓ select · ↵ confirm · esc cancel"
    )

    def test_approval_choice_menu_walks_the_cursor(self):
        # An unnumbered arrow-select list takes no digit — "2" walks the
        # cursor from row 1 down one, then confirms.
        with patch.object(supervise, "capture_pane",
                          return_value=self.DEVIN_QUESTION), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "2")
        self.assertTrue(out.ok)
        self.assertEqual([c[0][1] for c in sk.call_args_list],
                         ["Down", "Enter"])

    def test_approval_choice_menu_cursor_delta_up(self):
        pane = self.DEVIN_QUESTION.replace(
            " ❭ SQLite\n   Postgres\n   Redis\n",
            "   SQLite\n   Postgres\n ❭ Redis\n")
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "1")
        self.assertTrue(out.ok)
        self.assertEqual([c[0][1] for c in sk.call_args_list],
                         ["Up", "Up", "Enter"])

    def test_approval_choice_deny_is_escape(self):
        # The semantic names mean nothing on an arrow-select list —
        # only "deny" carries over, as the menu's own cancel.
        with patch.object(supervise, "capture_pane",
                          return_value=self.DEVIN_QUESTION), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "deny")
        self.assertTrue(out.ok)
        self.assertEqual(sk.call_args[0][1], "Escape")

    def test_approval_choice_semantic_allow_refused(self):
        with patch.object(supervise, "capture_pane",
                          return_value=self.DEVIN_QUESTION), \
             patch.object(supervise, "send_key") as sk:
            out = self.inj.approval(self.session, "r1", "allow")
        self.assertFalse(out.ok)
        sk.assert_not_called()

    def test_approval_choice_option_not_on_screen(self):
        with patch.object(supervise, "capture_pane",
                          return_value=self.DEVIN_QUESTION), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "7")
        self.assertFalse(out.ok)
        self.assertIn("not on screen", out.note)
        sk.assert_not_called()

    def test_approval_choice_unreachable_mid_walk(self):
        with patch.object(supervise, "capture_pane",
                          return_value=self.DEVIN_QUESTION), \
             patch.object(supervise, "send_key",
                          side_effect=[True, False]) as sk:
            out = self.inj.approval(self.session, "r1", "3")
        self.assertFalse(out.ok)
        self.assertIn("not reachable", out.note)
        self.assertEqual(sk.call_count, 2)

    def test_approval_digit_not_on_the_menu(self):
        # A digit the visible menu doesn't offer must not reach the
        # pane — it could land in an unrelated prompt.
        pane = "Do you want to proceed?\n1. Yes\n2. No"
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "7")
        self.assertFalse(out.ok)
        self.assertIn("not on screen", out.note)
        sk.assert_not_called()

    def test_approval_refused_without_prompt(self):
        with patch.object(supervise, "capture_pane", return_value="❯ idle"), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.approval(self.session, "r1", "allow")
        self.assertFalse(out.ok)
        self.assertIn("no approval", out.note)
        sk.assert_not_called()

    def test_approval_replay_rejected(self):
        # A retried request_id must not consume a different menu.
        pane = "Do you want to proceed? 1. Yes"
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key", return_value=True) as sk:
            self.assertTrue(
                self.inj.approval(self.session, "r1", "allow").ok
            )
            # A retried approval builds a fresh injector — the replay
            # guard lives on the class, keyed by session.
            again = inject.TmuxInjector(self.home, self.key)
            out = again.approval(self.session, "r1", "deny")
        self.assertFalse(out.ok)
        self.assertIn("already answered", out.note)
        self.assertEqual(sk.call_count, 1)

    def test_approval_rejected_when_prompt_swapped(self):
        # The watcher minted rid for prompt A; prompt B is on screen
        # now — the stale id must not send a key to B.
        key = self.key
        fp_a = supervise.approval_fingerprint(
            "Do you want to run ls?\n1. Yes\n2. No")
        supervise.set_pending_approval(key, "req-a", fp_a)
        try:
            pane_b = "Do you want to run rm -rf?\n1. Yes\n2. No"
            with patch.object(
                supervise, "capture_pane", return_value=pane_b
            ), patch.object(supervise, "send_key") as sk:
                out = self.inj.approval(self.session, "req-a", "allow")
            self.assertFalse(out.ok)
            self.assertIn("changed", out.note)
            sk.assert_not_called()
        finally:
            supervise.set_pending_approval(key, None)

    def test_interrupt_escape(self):
        with patch.object(supervise, "send_key", return_value=True) as sk:
            out = self.inj.control(self.session, "interrupt", "")
        self.assertTrue(out.ok)
        self.assertEqual(sk.call_args[0][1], "Escape")

    def test_compact_slash(self):
        with patch.object(supervise, "send_text", return_value=True) as st:
            out = self.inj.control(self.session, "compact", "")
        self.assertTrue(out.ok)
        self.assertEqual(st.call_args[0][1], "/compact")

    def test_send_empty_and_unreachable(self):
        out = self.inj.send(self.session, {"text": "", "mode": "steer"}, "done")
        self.assertFalse(out.ok)
        self.assertIn("empty", out.note)
        with patch.object(supervise, "send_text", return_value=False):
            out = self.inj.send(
                self.session, {"text": "hi", "mode": "steer"}, "done"
            )
        self.assertFalse(out.ok)
        self.assertIn("not reachable", out.note)

    def test_approval_unknown_and_unreachable(self):
        out = self.inj.approval(self.session, "r1", "bogus")
        self.assertFalse(out.ok)
        self.assertIn("unknown decision", out.note)
        pane = "Do you want to proceed? 1. Yes"
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key", return_value=False):
            out = self.inj.approval(self.session, "r1", "allow")
        self.assertFalse(out.ok)
        self.assertIn("not reachable", out.note)

    def test_control_kill_and_unknown(self):
        supervise.mark(self.home, self.key, {"mode": "tmux", "tmux": "qv-x"})
        with patch.object(supervise, "kill", return_value=True) as k:
            out = self.inj.control(self.session, "kill", "")
        self.assertTrue(out.ok)
        k.assert_called_once_with("qv-x")
        self.assertIsNone(supervise.meta_for(self.home, self.key))
        out = self.inj.control(self.session, "nonsense", "")
        self.assertFalse(out.ok)
        self.assertIn("unsupported", out.note)


class TestHeadless(unittest.TestCase):
    def setUp(self):
        self.session = _session()
        self.inj = inject.ClaudeHeadlessInjector(temp_dir(self), "claude:x")

    def test_argv_shape(self):
        argv = self.inj.argv(self.session, "hello")
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertIn("--resume", argv)
        self.assertIn("abc-123", argv)
        self.assertIn("stream-json", argv)
        self.assertEqual(argv[-1], "hello")

    def test_a_message_that_looks_like_a_flag_stays_the_prompt(self):
        argv = self.inj.argv(self.session, "--help me")
        self.assertEqual(argv[-2:], ["--", "--help me"])

    def test_argv_model_rides_as_a_flag(self):
        argv = self.inj.argv(self.session, "hello", model="opus")
        i = argv.index("--model")
        self.assertEqual(argv[i + 1], "opus")
        self.assertEqual(argv[-1], "hello")

    def test_approval_unsupported(self):
        out = self.inj.approval(self.session, "r1", "allow")
        self.assertFalse(out.ok)
        self.assertIn("approval_not_supported", out.note)


class _FakeStdout:
    def __init__(self, lines):
        self._it = iter(lines)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._it)

    def close(self):
        self.closed = True


class _FakeProc:
    """Popen stand-in: canned stream-json lines + exit code."""

    def __init__(self, lines, rc=0):
        self.stdout = _FakeStdout(lines)
        self._rc = rc
        self.killed = False

    def wait(self, timeout=None):
        return self._rc

    def kill(self):
        self.killed = True


class TestHeadlessRun(unittest.TestCase):
    def setUp(self):
        self.session = _session()
        self.inj = inject.ClaudeHeadlessInjector(
            temp_dir(self), "claude:x"
        )

    def _send(self, lines, rc=0):
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})
        with patch.object(inject.subprocess, "Popen",
                          return_value=_FakeProc(lines, rc)):
            return self.inj.send(
                self.session, {"text": "go", "mode": "queue"}, "done"
            )

    def test_result_text_returned(self):
        lines = [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant"}),
            json.dumps({"type": "result", "result": "PONG"}),
        ]
        out = self._send(lines)
        self.assertTrue(out.ok)
        self.assertEqual(out.note, "PONG")

    def test_error_result_fails(self):
        lines = [json.dumps(
            {"type": "result", "result": "boom", "is_error": True}
        )]
        out = self._send(lines)
        self.assertFalse(out.ok)
        self.assertIn("boom", out.note)

    def test_nonzero_exit(self):
        out = self._send([], rc=2)
        self.assertFalse(out.ok)
        self.assertIn("exited 2", out.note)

    def test_interrupt_kills_inflight(self):
        proc = _FakeProc([])
        with patch.object(inject.subprocess, "Popen", return_value=proc):
            self.inj._procs[self.inj.key] = proc
            out = self.inj.control(self.session, "interrupt", "")
        self.assertTrue(out.ok)
        self.assertTrue(proc.killed)
        # no run in flight → interrupt reports that
        out = self.inj.control(self.session, "interrupt", "")
        self.assertFalse(out.ok)

    def test_model_switch_rides_the_next_run_as_a_flag(self):
        supervise.mark(self.inj.home, self.inj.key,
                       {"mode": "headless", "tool_name": "claude",
                        "model": "sonnet"})
        with patch.object(inject.subprocess, "Popen") as popen:
            out = self.inj.control(self.session, "model", "opus")
        self.assertTrue(out.ok, out.note)
        self.assertIn("next turn", out.note)
        popen.assert_not_called()  # nothing runs until the next send
        self.assertEqual(
            supervise.meta_for(self.inj.home, self.inj.key)["model"], "opus")
        proc = _FakeProc([json.dumps({"type": "result", "result": "ok"})])
        with patch.object(inject.subprocess, "Popen",
                          return_value=proc) as popen:
            out = self.inj.send(
                self.session, {"text": "go", "mode": "queue"}, "done")
        self.assertTrue(out.ok, out.note)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index("--model") + 1], "opus")

    def test_model_switch_refusals(self):
        out = self.inj.control(self.session, "model", "opus")
        self.assertFalse(out.ok)
        self.assertIn("detached", out.note)
        supervise.mark(self.inj.home, self.inj.key,
                       {"mode": "headless", "tool_name": "claude"})
        # empty, a flag smuggled in as the value, a second line
        for bad in ("", "--dangerously-skip-permissions", "opus\n/clear"):
            out = self.inj.control(self.session, "model", bad)
            self.assertFalse(out.ok, bad)
        self.assertNotIn(
            "model", supervise.meta_for(self.inj.home, self.inj.key))

    def test_detach_unmarks(self):
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})
        out = self.inj.control(self.session, "detach", "")
        self.assertTrue(out.ok)
        self.assertIsNone(supervise.meta_for(self.inj.home, self.inj.key))

    def test_send_empty_and_detached(self):
        out = self.inj.send(
            self.session, {"text": "", "mode": "queue"}, "done"
        )
        self.assertFalse(out.ok)
        self.assertIn("empty", out.note)
        # No registry mark → the write-time recheck refuses.
        out = self.inj.send(
            self.session, {"text": "go", "mode": "queue"}, "done"
        )
        self.assertFalse(out.ok)
        self.assertIn("detached", out.note)

    def test_popen_launch_failure(self):
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})
        with patch.object(inject.subprocess, "Popen",
                          side_effect=OSError("no claude")):
            out = self.inj.send(
                self.session, {"text": "go", "mode": "queue"}, "done"
            )
        self.assertFalse(out.ok)
        self.assertIn("failed to launch", out.note)

    def test_detach_between_spawn_and_register(self):
        # A detach landing after Popen() but before _procs registration
        # would orphan the child; the post-register recheck kills it.
        proc = _FakeProc([json.dumps({"type": "result", "result": "x"})])
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})

        def spawn(*_a, **_kw):
            supervise.unmark(self.inj.home, self.inj.key)  # detach lands
            return proc

        with patch.object(inject.subprocess, "Popen", side_effect=spawn):
            out = self.inj.send(
                self.session, {"text": "go", "mode": "queue"}, "done"
            )
        self.assertFalse(out.ok)
        self.assertIn("detached", out.note)
        self.assertTrue(proc.killed)
        self.assertNotIn(self.inj.key, self.inj._procs)

    def test_watchdog_kills_silent_run(self):
        # A run that never emits a line must still die at the deadline;
        # the stdout loop alone cannot bound it.
        class _BlockingStdout:
            def __init__(self, proc):
                self.proc = proc

            def __iter__(self):
                return self

            def __next__(self):
                # Bounded, so a broken watchdog fails the test below
                # instead of hanging the suite.
                self.proc.kill_event.wait(5)
                raise StopIteration

            def close(self):
                pass

        class _SilentProc:
            def __init__(self):
                self.killed = False
                self.kill_event = threading.Event()
                self.stdout = _BlockingStdout(self)

            def wait(self, timeout=None):
                return -9

            def kill(self):
                self.killed = True
                self.kill_event.set()

        proc = _SilentProc()
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})
        with patch.object(inject, "HEADLESS_TIMEOUT_S", 0.05), \
             patch.object(inject.subprocess, "Popen", return_value=proc):
            out = self.inj.send(
                self.session, {"text": "go", "mode": "queue"}, "done"
            )
        self.assertFalse(out.ok)
        self.assertIn("timed out", out.note)
        self.assertTrue(proc.killed)
        self.assertNotIn(self.inj.key, self.inj._procs)

    def test_second_sender_is_refused_while_held(self):
        lock = self.inj._locks[self.inj.key]
        lock.acquire()
        try:
            with patch.object(inject, "_QUEUE_WAIT_S", 0.01):
                out = self.inj.send(
                    self.session, {"text": "go", "mode": "queue"}, "done"
                )
        finally:
            lock.release()
        self.assertFalse(out.ok)
        self.assertIn("in flight", out.note)

    def test_control_kill_reaps_inflight(self):
        proc = _FakeProc([])
        self.inj._procs[self.inj.key] = proc
        supervise.mark(self.inj.home, self.inj.key, {"mode": "headless"})
        out = self.inj.control(self.session, "kill", "")
        self.assertTrue(out.ok)
        self.assertTrue(proc.killed)
        self.assertIsNone(
            supervise.meta_for(self.inj.home, self.inj.key)
        )

    def test_interrupt_with_nothing_in_flight(self):
        out = self.inj.control(self.session, "interrupt", "")
        self.assertFalse(out.ok)
        self.assertIn("no headless run", out.note)


class TestAttachments(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.key = "claude:s1"

    def test_stage_resolve_roundtrip(self):
        payload, err = attachments.stage(self.home, self.key, "shot.png", b"\x89PNG")
        self.assertIsNone(err)
        path = attachments.resolve(self.home, self.key, payload["ref"])
        self.assertEqual(Path(path).read_bytes(), b"\x89PNG")

    def test_staged_files_are_private(self):
        # Uploads are the user's screenshots: 0600 files in 0700
        # folders, under a umask that would otherwise leave them 0644.
        import stat
        old_umask = os.umask(0o022)
        try:
            payload, _ = attachments.stage(self.home, self.key, "s.png", b"x")
            placed, _ = attachments.place(self.home, "claude:s2", "r.png", b"y")
        finally:
            os.umask(old_umask)
        for info in (payload, placed):
            path = Path(info["path"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        inbox = Path(payload["path"]).parent.parent
        self.assertEqual(stat.S_IMODE(inbox.stat().st_mode), 0o700)

    def test_existing_loose_folder_and_file_are_tightened(self):
        # Folders and files from before modes were set get them on the
        # next write, before any bytes land.
        import stat
        folder = attachments.inbox_dir(self.home, self.key)
        folder.mkdir(parents=True, mode=0o755)
        os.chmod(folder, 0o755)
        (folder / "r.png").write_bytes(b"old")
        os.chmod(folder / "r.png", 0o644)
        placed, _ = attachments.place(self.home, self.key, "r.png", b"new")
        self.assertEqual(
            stat.S_IMODE(Path(placed["path"]).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_modes_are_set_before_the_first_byte(self):
        # The point is no readable window: at the first write the file
        # must already be 0600 and its folders 0700, for a new upload
        # and for one replacing a loose file in a loose folder.
        import stat
        real_fdopen = os.fdopen
        seen = []

        class Checked:
            def __init__(self, fh, folder):
                self.fh, self.folder = fh, folder

            def __enter__(self):
                self.fh.__enter__()
                return self

            def __exit__(self, *exc):
                return self.fh.__exit__(*exc)

            def fileno(self):
                return self.fh.fileno()

            def write(self, data):
                seen.append((
                    stat.S_IMODE(os.fstat(self.fh.fileno()).st_mode),
                    stat.S_IMODE(self.folder.stat().st_mode),
                    stat.S_IMODE(self.folder.parent.stat().st_mode)))
                return self.fh.write(data)

        loose = attachments.inbox_dir(self.home, "claude:s2")
        loose.mkdir(parents=True)
        os.chmod(loose.parent, 0o755)
        os.chmod(loose, 0o755)
        (loose / "r.png").write_bytes(b"old")
        os.chmod(loose / "r.png", 0o644)

        def fdopen(fd, mode="r", *a, **kw):
            return Checked(real_fdopen(fd, mode, *a, **kw), self._folder)

        old_umask = os.umask(0o022)
        try:
            with patch.object(attachments.os, "fdopen", side_effect=fdopen):
                self._folder = attachments.inbox_dir(self.home, self.key)
                attachments.stage(self.home, self.key, "s.png", b"x")
                self._folder = loose
                attachments.place(self.home, "claude:s2", "r.png", b"new")
        finally:
            os.umask(old_umask)
        self.assertEqual(seen, [(0o600, 0o700, 0o700)] * 2)

    def test_caps_and_types(self):
        payload, err = attachments.stage(self.home, self.key, "x.exe", b"x")
        self.assertIsNone(payload)
        self.assertIn("type", err)
        payload, err = attachments.stage(
            self.home, self.key, "big.png", b"0" * (attachments.MAX_BYTES + 1)
        )
        self.assertIsNone(payload)
        self.assertIn("20 MB", err)

    def test_traversal_refused(self):
        attachments.stage(self.home, self.key, "a.txt", b"x")
        self.assertIsNone(
            attachments.resolve(self.home, self.key, "../../etc/passwd")
        )

    def test_sweep_and_drop(self):
        payload, _ = attachments.stage(self.home, self.key, "old.txt", b"x")
        path = Path(payload["path"])
        old = time.time() - attachments.MAX_AGE_S - 10
        import os
        os.utime(path, (old, old))
        self.assertEqual(attachments.sweep(self.home, now=time.time()), 1)
        payload, _ = attachments.stage(self.home, self.key, "n.txt", b"y")
        attachments.drop_session(self.home, self.key)
        self.assertFalse(Path(payload["path"]).exists())

    def test_empty_upload_refused(self):
        payload, err = attachments.stage(self.home, self.key, "a.txt", b"")
        self.assertIsNone(payload)
        self.assertIn("empty", err)

    def test_same_name_uploads_get_distinct_refs(self):
        p1, _ = attachments.stage(self.home, self.key, "image.png", b"one")
        p2, _ = attachments.stage(self.home, self.key, "image.png", b"two")
        self.assertNotEqual(p1["ref"], p2["ref"])
        # and both files survive with their own bytes
        self.assertEqual(
            Path(attachments.resolve(self.home, self.key, p1["ref"]))
            .read_bytes(), b"one")
        self.assertEqual(
            Path(attachments.resolve(self.home, self.key, p2["ref"]))
            .read_bytes(), b"two")

    def test_long_names_still_resolve(self):
        name = "a" * 120 + ".png"
        payload, err = attachments.stage(self.home, self.key, name, b"x")
        self.assertIsNone(err)
        staged = attachments.resolve(self.home, self.key, payload["ref"])
        self.assertIsNotNone(staged)
        self.assertEqual(Path(staged).read_bytes(), b"x")

    def test_resolve_missing_returns_none(self):
        self.assertIsNone(
            attachments.resolve(self.home, self.key, "nope-abc-f.txt")
        )

    def test_stage_oserror_reported(self):
        with patch("quiver.remote.attachments.os.open",
                   side_effect=OSError("disk full")):
            payload, err = attachments.stage(
                self.home, self.key, "a.txt", b"x"
            )
        self.assertIsNone(payload)
        self.assertIn("could not stage", err)

    def test_sweep_unreadable_root(self):
        with patch("pathlib.Path.iterdir", side_effect=OSError("denied")):
            self.assertEqual(attachments.sweep(self.home), 0)


class TestHubWrites(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()], statuses=lambda ss: ["done"])
        self.addCleanup(self.srv.stop)
        self.auth = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {self.auth['credential']}"}

    def test_attach_then_message(self):
        # launch_tmux is mocked so the suite never needs (or spawns) a
        # real tmux session — sandboxed CI has neither tmux nor claude.
        with patch.object(supervise, "tmux_bin", return_value="/usr/bin/tmux"), \
             patch.object(supervise, "launch_tmux", return_value="qv-x"), \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "tmux_alive", return_value=True), \
             patch.object(supervise, "_stop_local_writers"):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/control",
                body={"op": "attach"}, headers=self.hdr,
            )
        self.assertEqual(status, 200, data)
        row = self.srv.state.catalog.row_for("claude:abc-123")
        self.assertEqual(row["ownership"], "attached")

        q = self.srv.state.bus.subscribe("claude:abc-123")
        with patch.object(supervise, "send_text", return_value=True) as st:
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/message",
                body={"text": "ship it", "mode": "steer"},
                headers=self.hdr,
            )
            self.assertEqual(status, 202, data)
            ev = q.get(timeout=5)
        self.assertTrue(ev["data"]["ok"])
        self.assertIn("ship it", st.call_args[0][1])

    def test_attach_rejects_when_active(self):
        # status lambda reports active → server-side idle check fires
        srv = _Server(sessions=[_session()], statuses=lambda ss: ["active"])
        self.addCleanup(srv.stop)
        cred = srv.pair()
        status, data = srv.request(
            "POST", "/v1/sessions/claude:abc-123/control",
            body={"op": "attach"},
            headers={"Authorization": f"Bearer {cred['credential']}"},
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(data)["error"]["code"], "not_idle")

    def test_attach_422_without_tmux(self):
        # Auto mode needs SOME channel: with neither tmux nor a claude
        # binary on PATH there is nothing to attach under.
        with patch.object(supervise, "tmux_bin", return_value=None), \
             patch("shutil.which", return_value=None):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/control",
                body={"op": "attach"}, headers=self.hdr,
            )
        self.assertEqual(status, 422)
        self.assertEqual(
            json.loads(data)["error"]["code"], "not_attachable"
        )

    def test_attach_auto_headless_when_tmux_missing(self):
        # The claude fallback: no tmux but claude on PATH resolves the
        # headless channel instead of 422ing.
        with patch.object(supervise, "tmux_bin", return_value=None), \
             patch("shutil.which", return_value="/x/claude"), \
             patch.object(supervise, "_stop_local_writers"):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/control",
                body={"op": "attach"}, headers=self.hdr,
            )
        self.assertEqual(status, 200, data)
        meta = supervise.meta_for(self.srv.home, "claude:abc-123")
        self.assertEqual(meta["mode"], "headless")

    def test_attachment_upload_and_message_ref(self):
        with patch.object(supervise, "tmux_bin", return_value="/usr/bin/tmux"), \
             patch.object(supervise, "launch_tmux", return_value="qv-t"), \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "tmux_alive", return_value=True), \
             patch.object(supervise, "_stop_local_writers"):
            self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/control",
                body={"op": "attach"}, headers=self.hdr,
            )
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        try:
            conn.request(
                "POST",
                "/v1/sessions/claude:abc-123/attachments?name=shot.png",
                body=b"\x89PNG",
                headers={
                    "Authorization": self.hdr["Authorization"],
                    "Content-Type": "application/octet-stream",
                },
            )
            resp = conn.getresponse()
            payload = json.loads(resp.read())
            self.assertEqual(resp.status, 200, payload)
        finally:
            conn.close()
        ref = payload["ref"]
        q = self.srv.state.bus.subscribe("claude:abc-123")
        with patch.object(supervise, "send_text", return_value=True) as st:
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude:abc-123/message",
                body={"text": "see this", "attachments": [ref]},
                headers=self.hdr,
            )
            self.assertEqual(status, 202, data)
            ev = q.get(timeout=5)
        self.assertTrue(ev["data"]["ok"])
        sent = st.call_args[0][1]
        self.assertIn("@", sent)
        self.assertIn("shot.png", sent)


if __name__ == "__main__":
    unittest.main()


class TestAttachmentCapability(unittest.TestCase):
    """``capabilities.attachments`` over ``ATTACH_FALLBACK``."""

    def test_fallback_table(self):
        for name in ("claude", "gemini", "qwen"):
            self.assertTrue(attachments.at_refs(name, {}), name)
        for name in ("codex", "opencode", "cursor", "droid", "pi", ""):
            self.assertFalse(attachments.at_refs(name, {}), name)

    def test_registry_wins(self):
        reg = {
            "codex": {"capabilities": {"attachments": {"at_refs": True}}},
            "claude": {"capabilities": {"attachments": {"supported": False}}},
            "gemini": {"capabilities": {"attachments": {"at_refs": False}}},
            # Malformed declarations fall back to the table.
            "qwen": {"capabilities": {"attachments": "yes"}},
            "pi": {"capabilities": "nope"},
        }
        self.assertTrue(attachments.at_refs("codex", reg))
        self.assertFalse(attachments.at_refs("claude", reg))
        self.assertFalse(attachments.at_refs("gemini", reg))
        self.assertTrue(attachments.at_refs("qwen", reg))
        self.assertFalse(attachments.at_refs("pi", reg))

    def test_wrong_field_types_are_ignored(self):
        for bad in ({"at_refs": "yes"}, {"at_refs": None}, {"at_refs": 1},
                    {"supported": None}, {"supported": 0},
                    {"supported": "false"}):
            reg = {"claude": {"capabilities": {"attachments": bad}},
                   "codex": {"capabilities": {"attachments": bad}}}
            self.assertTrue(attachments.at_refs("claude", reg), bad)
            self.assertFalse(attachments.at_refs("codex", reg), bad)

    def test_prompt_text_shapes(self):
        paths = ["/x/a b.png", "/x/c.pdf"]
        self.assertEqual(
            attachments.prompt_text("hi", paths, "claude", {}),
            "@/x/a b.png @/x/c.pdf hi")
        self.assertEqual(
            attachments.prompt_text("hi", paths, "codex", {}),
            "[Attached file: /x/a b.png] [Attached file: /x/c.pdf] hi")
        # No text: just the refs; no paths: the text untouched.
        self.assertEqual(
            attachments.prompt_text("", ["/x/a.png"], "codex", {}),
            "[Attached file: /x/a.png]")
        self.assertEqual(attachments.prompt_text("hi", [], "codex", {}), "hi")
        # Always one line: a tmux pane would submit at a newline.
        self.assertNotIn(
            "\n", attachments.prompt_text("hi", paths, "codex", {}))

    def test_reads_registry_when_not_given(self):
        from quiver.harness import registry
        reg = {"pi": {"capabilities": {"attachments": {"at_refs": True}}}}
        with patch.object(registry, "load_registry_readonly",
                          return_value=reg):
            self.assertTrue(attachments.at_refs("pi"))
        with patch.object(registry, "load_registry_readonly",
                          side_effect=ValueError):
            self.assertTrue(attachments.at_refs("claude"))
