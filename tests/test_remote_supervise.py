"""supervise.py: tmux lifecycle, pending-launch adoption, priming."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.remote import protocol, supervise
from quiver.sessions.models import Session


def _session(tool="claude", sid="s1", path="/private/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   session_id=sid, tool_name=tool)


class TestTmuxHelpers(unittest.TestCase):
    def _cp(self, rc=0, stdout=""):
        cp = MagicMock()
        cp.returncode = rc
        cp.stdout = stdout
        return cp

    def test_send_text_sequence(self):
        with patch.object(supervise, "_tmux", return_value=self._cp()) as t:
            self.assertTrue(supervise.send_text("qv-x", "hello"))
        calls = [list(c[0]) for c in t.call_args_list]
        self.assertEqual(calls[0], ["send-keys", "-t", "qv-x", "-l", "hello"])
        self.assertEqual(calls[1], ["send-keys", "-t", "qv-x", "Enter"])

    def test_send_text_failure(self):
        with patch.object(supervise, "_tmux", return_value=self._cp(rc=1)):
            self.assertFalse(supervise.send_text("qv-x", "hello"))

    def test_send_key_and_capture(self):
        with patch.object(supervise, "_tmux",
                          return_value=self._cp(stdout="pane text")) as t:
            self.assertTrue(supervise.send_key("qv-x", "Escape"))
            self.assertEqual(supervise.capture_pane("qv-x"), "pane text")

    def test_helpers_tolerate_tmux_errors(self):
        # _tmux raising OSError/SubprocessError degrades each helper to
        # its False/"" result instead of propagating.
        import subprocess as sp

        with patch.object(supervise, "_tmux", side_effect=OSError("gone")):
            self.assertFalse(supervise.send_text("qv-x", "hi"))
            self.assertFalse(supervise.send_key("qv-x", "Escape"))
            self.assertEqual(supervise.capture_pane("qv-x"), "")
            self.assertFalse(supervise.kill("qv-x"))
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "_tmux",
                          side_effect=sp.TimeoutExpired("tmux", 10)):
            self.assertFalse(supervise.tmux_alive("qv-x"))

    def test_capture_pane_nonzero_rc(self):
        with patch.object(supervise, "_tmux",
                          return_value=self._cp(rc=1, stdout="x")):
            self.assertEqual(supervise.capture_pane("qv-x"), "")

    def test_capture_pane_visible_only_by_default(self):
        # Approval detection reads the live menu — scrollback is opt-in
        # because an answered prompt lingering in history must not look
        # pending.
        with patch.object(supervise, "_tmux",
                          return_value=self._cp(stdout="x")) as t:
            supervise.capture_pane("qv-x")
            supervise.capture_pane("qv-x", lines=200)
        self.assertEqual(
            list(t.call_args_list[0][0]),
            ["capture-pane", "-p", "-t", "qv-x"])
        self.assertEqual(
            list(t.call_args_list[1][0]),
            ["capture-pane", "-p", "-t", "qv-x", "-S", "-200"])

    def test_pane_shows_approval_needs_stacked_choices(self):
        # Scattered "No errors found"/"No files changed" prose is not a
        # menu — the choices must be a consecutive block.
        scattered = (
            "working…\nNo errors found\nstill working\n"
            "2 warnings\nNo files changed\ndone")
        self.assertFalse(supervise.pane_shows_approval(scattered))
        menu = "Do you want to proceed?\n1. Yes\n2. No"
        self.assertTrue(supervise.pane_shows_approval(menu))
        stacked = "Choose:\n❯ Allow once\nDeny"
        self.assertTrue(supervise.pane_shows_approval(stacked))

    def test_approval_fingerprint_tracks_prompt_text(self):
        a = supervise.approval_fingerprint(
            "Do you want to run ls?\n1. Yes\n2. No")
        b = supervise.approval_fingerprint(
            "Do you want to run rm -rf?\n1. Yes\n2. No")
        # Same menu re-rendered (spinner moved elsewhere) keeps its id.
        a2 = supervise.approval_fingerprint(
            "spinner | working\nDo you want to run ls?\n1. Yes\n2. No")
        self.assertEqual(a, a2)
        self.assertNotEqual(a, b)

    def test_tmux_real_call(self):
        cp = self._cp(rc=0)
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise.subprocess, "run",
                          return_value=cp) as run:
            self.assertTrue(supervise.tmux_alive("qv-x"))
        self.assertEqual(run.call_args[0][0][:3],
                         ["/x/tmux", "has-session", "-t"])

    def test_kill_and_alive(self):
        with patch.object(supervise, "_tmux",
                          return_value=self._cp()), \
             patch.object(supervise, "tmux_bin", return_value="/usr/bin/tmux"):
            self.assertTrue(supervise.kill("qv-x"))
            self.assertTrue(supervise.tmux_alive("qv-x"))
        with patch.object(supervise, "tmux_bin", return_value=None):
            self.assertFalse(supervise.tmux_alive("qv-x"))

    def test_launch_tmux_argv(self):
        with patch.object(supervise, "tmux_bin", return_value="/usr/bin/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=False), \
             patch.object(supervise, "_tmux", return_value=self._cp()) as t:
            name = supervise.launch_tmux("claude:s1", ["claude", "--resume", "s1"], "/tmp")
        self.assertIsNotNone(name)
        args = list(t.call_args[0])
        self.assertEqual(args[:4], ["new-session", "-d", "-s", name])
        self.assertIn("--resume s1", args[-1])
        self.assertIn("claude", args[-1])

    def test_launch_tmux_edges(self):
        # No tmux → None. Existing pane → reuse its name. Failed
        # new-session → None. _tmux blowing up → None.
        with patch.object(supervise, "tmux_bin", return_value=None):
            self.assertIsNone(supervise.launch_tmux("k", ["claude"], "/tmp"))
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=True):
            self.assertEqual(
                supervise.launch_tmux("claude:s1", ["claude"], "/tmp"),
                supervise.tmux_name("claude:s1"),
            )
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=False), \
             patch.object(supervise, "_tmux", return_value=self._cp(rc=1)):
            self.assertIsNone(
                supervise.launch_tmux("claude:s1", ["claude"], "/tmp")
            )
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=False), \
             patch.object(supervise, "_tmux", side_effect=OSError):
            self.assertIsNone(
                supervise.launch_tmux("claude:s1", ["claude"], "/tmp")
            )

    def test_shquote_quotes_specials(self):
        self.assertEqual(supervise._shquote("plain-1.x"), "plain-1.x")
        self.assertEqual(
            supervise._shquote("it's a path"), "'it'\\''s a path'"
        )
        self.assertEqual(supervise._shquote(""), "''")


class TestPrimePane(unittest.TestCase):
    def test_trust_dialog_down_enter(self):
        pane = "Do you trust the files in this folder? > No, exit / Yes, I trust this folder"
        with patch.object(supervise, "capture_pane", return_value=pane), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text") as st, \
             patch.object(time, "sleep"):
            supervise.prime_pane("qv-x", "hello")
        keys = [c[0][1] for c in sk.call_args_list]
        self.assertEqual(keys, ["Down", "Enter"])
        st.assert_called_once_with("qv-x", "hello")

    def test_no_dialog_sends_directly(self):
        with patch.object(supervise, "capture_pane", return_value="❯"), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text") as st, \
             patch.object(time, "sleep"):
            supervise.prime_pane("qv-x", "hello")
        sk.assert_not_called()
        st.assert_called_once_with("qv-x", "hello")

    def test_confirm_dialog_bare_enter(self):
        with patch.object(
            supervise, "capture_pane",
            return_value="Warning! Press Enter to confirm"
        ), patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text") as st, \
             patch.object(time, "sleep"):
            supervise.prime_pane("qv-x", "hello")
        sk.assert_called_once_with("qv-x", "Enter")
        st.assert_called_once_with("qv-x", "hello")

    def test_blank_pane_times_out_without_sending(self):
        # A pane that never draws must not eat the prompt — the launch
        # reports "could not be delivered" instead of typing into void.
        with patch.object(supervise, "capture_pane", return_value=""), \
             patch.object(supervise, "send_text") as st, \
             patch.object(time, "sleep"), \
             patch.object(time, "time",
                          side_effect=[0, 0, 100]):  # past deadline
            self.assertFalse(supervise.prime_pane("qv-x", "hello"))
        st.assert_not_called()

    def test_prompt_send_failure_propagates_false(self):
        with patch.object(supervise, "capture_pane", return_value="❯"), \
             patch.object(supervise, "send_text", return_value=False):
            self.assertFalse(supervise.prime_pane("qv-x", "hello"))


class TestAdoptPending(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_adopts_matching_new_session(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = Path(tempfile.mkdtemp())  # realpath-resolvable cwd
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))

        sess = _session(path=str(proj))
        transcript = proj / "sess.jsonl"
        transcript.write_text("{}\n")
        rows = [(protocol.session_key("claude", "s1", str(proj)), sess, "active")]
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            adopted = supervise.adopt_pending(self.home, rows)
        self.assertEqual(len(adopted), 1)
        meta = supervise.meta_for(self.home, adopted[0])
        self.assertEqual(meta["tmux"], "qv-new")
        self.assertFalse((folder / "qv-new.json").exists())

    def test_skips_mismatched_and_old(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "old.json").write_text(json.dumps(
            {"tmux": "qv-old", "tool_name": "claude", "cwd": "/x",
             "launched_at": time.time() - 90000}
        ))
        (folder / "other.json").write_text(json.dumps(
            {"tmux": "qv-o", "tool_name": "codex", "cwd": "/y",
             "launched_at": time.time()}
        ))
        rows = [(protocol.session_key("claude", "s1", "/tmp/z"),
                 _session(path="/tmp/z"), "done")]
        self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertFalse((folder / "old.json").exists())   # expired
        self.assertTrue((folder / "other.json").exists())  # unmatched stays

    def test_ambiguous_candidates_never_adopt(self):
        # Two same-cwd sessions with fresh transcripts: adopting either
        # could bind a human session to our pane — stay pending.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = Path(tempfile.mkdtemp())
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        rows = [
            (protocol.session_key("claude", "s1", str(proj)),
             _session(path=str(proj)), "active"),
            (protocol.session_key("claude", "s2", str(proj)),
             _session(path=str(proj), sid="s2"), "active"),
        ]
        transcript = proj / "t.jsonl"
        transcript.write_text("{}\n")
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertTrue((folder / "qv-new.json").exists())

    def test_preexisting_recently_written_session_not_adopted(self):
        # A human's same-cwd session written to right after our launch
        # has a fresh mtime but an old birth time — mtime alone would
        # adopt it and bind their transcript to our pane.
        import types

        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = Path(tempfile.mkdtemp())
        now = time.time()
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": now}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        rows = [(protocol.session_key("claude", "s1", str(proj)),
                 _session(path=str(proj)), "done")]
        fake_stat = types.SimpleNamespace(
            st_birthtime=now - 3600,  # created an hour before launch
            st_mtime=now,             # but written to just now
            st_ctime=now,
        )
        transcript = MagicMock()
        transcript.stat.return_value = fake_stat
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertTrue((folder / "qv-new.json").exists())  # stays pending

    def test_exclude_ids_never_adopt(self):
        # The marker's exclude list snapshots the catalog at launch: a
        # session it names can never be claimed as ours even with a
        # fresh transcript and unambiguous candidacy.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = Path(tempfile.mkdtemp())
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "exclude": ["s1"],
                   "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        rows = [(protocol.session_key("claude", "s1", str(proj)),
                 _session(path=str(proj)), "done")]
        transcript = proj / "t.jsonl"
        transcript.write_text("{}\n")  # created now — mtime alone would pass
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])

    def test_corrupt_marker_removed(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "bad.json").write_text("{not json")
        self.assertEqual(supervise.adopt_pending(self.home, []), [])
        self.assertFalse((folder / "bad.json").exists())

    def test_no_pending_dir_returns_empty(self):
        self.assertEqual(supervise.adopt_pending(self.home, []), [])

    def test_registered_keys_are_skipped(self):
        # A row already in the registry can't be adopted by a pending
        # marker — supervision wins over the pending claim.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = Path(tempfile.mkdtemp())
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        key = protocol.session_key("claude", "s1", str(proj))
        supervise.mark(self.home, key, {"mode": "tmux", "tmux": "qv-other"})
        rows = [(key, _session(path=str(proj)), "active")]
        transcript = proj / "t.jsonl"
        transcript.write_text("{}\n")
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertTrue((folder / "qv-new.json").exists())  # stays pending


class TestRegistryEdges(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_load_filters_nondict_values(self):
        supervise._registry_file(self.home).parent.mkdir(
            parents=True, exist_ok=True
        )
        supervise._registry_file(self.home).write_text(
            json.dumps({"k": {"mode": "tmux"}, "junk": 5, "s": "x"})
        )
        self.assertEqual(set(supervise._load_registry(self.home)), {"k"})

    def test_unmark_missing_key_is_noop(self):
        # pops nothing, writes nothing — no crash, no file churn
        supervise.unmark(self.home, "never-marked")

    def test_save_registry_chmod_failure_tolerated(self):
        with patch.object(supervise.os, "chmod", side_effect=OSError):
            supervise.mark(self.home, "k1", {"mode": "headless"})
        self.assertIn("k1", supervise._load_registry(self.home))


class TestLaunchNew(unittest.TestCase):
    def test_launch_writes_pending_and_primes(self):
        home = Path(tempfile.mkdtemp())
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise.shutil, "which", return_value="/usr/bin/claude"), \
             patch.object(supervise, "prime_pane") as prime:
            ok, note, name = supervise.launch_new(home, "claude", "/tmp", "hi")
        self.assertTrue(ok)
        prime.assert_called_once_with("qv-n", "hi")
        marker = supervise._pending_dir(home) / "qv-n.json"
        self.assertTrue(marker.exists())
        self.assertEqual(
            json.loads(marker.read_text())["tool_name"], "claude"
        )

    def test_same_second_launches_get_distinct_names(self):
        home = Path(tempfile.mkdtemp())
        seen = []
        with patch.object(supervise.shutil, "which", return_value="/x/claude"), \
             patch.object(supervise, "launch_tmux",
                          side_effect=lambda key, *a: seen.append(
                              supervise.tmux_name(key)) or
                              supervise.tmux_name(key)), \
             patch.object(supervise, "prime_pane"):
            supervise.launch_new(home, "claude", "/tmp", "one")
            supervise.launch_new(home, "claude", "/tmp", "two")
        self.assertEqual(len(set(seen)), 2)

    def test_unknown_harness(self):
        ok, note, name = supervise.launch_new(
            Path(tempfile.mkdtemp()), "nope-tool", "/tmp", ""
        )
        self.assertFalse(ok)
        self.assertIsNone(name)

    def test_undelivered_prompt_is_reported(self):
        home = Path(tempfile.mkdtemp())
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise.shutil, "which",
                          return_value="/usr/bin/claude"), \
             patch.object(supervise, "prime_pane", return_value=False):
            ok, note, name = supervise.launch_new(home, "claude", "/tmp", "hi")
        self.assertTrue(ok)   # the pane exists; only the prompt failed
        self.assertIn("could not be delivered", note)

    def test_known_ids_land_in_marker(self):
        home = Path(tempfile.mkdtemp())
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise.shutil, "which",
                          return_value="/usr/bin/claude"), \
             patch.object(supervise, "prime_pane", return_value=True):
            supervise.launch_new(
                home, "claude", "/tmp", "hi", known_ids=["old-1", "old-2"]
            )
        marker = supervise._pending_dir(home) / "qv-n.json"
        self.assertEqual(
            json.loads(marker.read_text())["exclude"], ["old-1", "old-2"]
        )

    def test_detach_kills_tmux(self):
        home = Path(tempfile.mkdtemp())
        supervise.mark(home, "k1", {"mode": "tmux", "tmux": "qv-z"})
        with patch.object(supervise, "kill", return_value=True) as k:
            self.assertTrue(supervise.detach(home, "k1"))
        k.assert_called_once_with("qv-z")
        self.assertIsNone(supervise.meta_for(home, "k1"))


class TestStopLocalWriters(unittest.TestCase):
    """Attach SIGTERMs the original resumed writer when identifiable."""

    def _run_with(self, pgrep_out, ps_map):
        """ps_map: pid-string -> command line (or None for no process)."""
        calls = []

        def fake_run(argv, **_kw):
            cp = MagicMock()
            if argv[0] == "pgrep":
                cp.stdout = pgrep_out
            elif argv[0] == "ps":
                cp.stdout = ps_map.get(argv[2], "") or ""
            return cp

        with patch.object(supervise.subprocess, "run", side_effect=fake_run), \
             patch.object(supervise.os, "kill") as kill:
            supervise._stop_local_writers(_session(sid="abc-123"))
            calls.extend(c[0] for c in kill.call_args_list)
        return calls

    def test_kills_matching_writer_only(self):
        calls = self._run_with(
            "1001\n1002\n1003\n",
            {
                "1001": "claude --resume abc-123",
                "1002": "vim notes-about-abc-123",   # wrong binary
            },
        )
        # 1003's ps lookup returns "" → skipped by the binary check
        self.assertEqual(calls, [(1001, supervise.signal.SIGTERM)])

    def test_curl_with_session_url_not_killed(self):
        # `curl .../claude:abc-123/control` matches the pgrep -f sweep
        # but argv[0] is curl, not the harness — it must survive.
        calls = self._run_with(
            "2001\n2002\n",
            {
                "2001": "curl -X POST http://hub/v1/sessions/claude:abc-123/control",
                "2002": "claude --resume abc-123",
            },
        )
        self.assertEqual(calls, [(2002, supervise.signal.SIGTERM)])

    def test_codex_writer_matched_by_thread_uuid(self):
        # codex resume takes the thread uuid lifted out of the rollout
        # stem — the stored session_id never appears on the cmdline.
        sid = ("rollout-2026-01-02T03-04-05-"
               "deadbeef-1234-1234-1234-123456789abc")
        sess = _session(tool="codex", sid=sid)
        calls = []

        def fake_run(argv, **_kw):
            cp = MagicMock()
            if argv[0] == "pgrep":
                cp.stdout = "3001\n3002\n"
            else:
                cp.stdout = {
                    "3001": "codex resume "
                            "deadbeef-1234-1234-1234-123456789abc",
                    "3002": f"curl http://x/{sid}",
                }[argv[2]]
            return cp

        with patch.object(supervise.subprocess, "run",
                          side_effect=fake_run), \
             patch.object(supervise.os, "kill") as kill:
            supervise._stop_local_writers(sess)
            calls.extend(c[0] for c in kill.call_args_list)
        self.assertEqual(calls, [(3001, supervise.signal.SIGTERM)])

    def test_writer_with_id_only_in_env_not_killed(self):
        # The id as a substring of an unrelated token ("log-abc-123")
        # must not qualify — only whole argv tokens count.
        calls = self._run_with(
            "4001\n",
            {"4001": "claude --resume log-abc-123-other"},
        )
        self.assertEqual(calls, [])

    def test_skips_self_pid(self):
        calls = self._run_with(
            f"{supervise.os.getpid()}\n",
            {str(supervise.os.getpid()): "claude --resume abc-123"},
        )
        self.assertEqual(calls, [])

    def test_pgrep_failure_tolerated(self):
        with patch.object(
            supervise.subprocess, "run", side_effect=OSError("no pgrep")
        ), patch.object(supervise.os, "kill") as kill:
            supervise._stop_local_writers(_session(sid="abc-123"))
        kill.assert_not_called()

    def test_empty_sid_returns_early(self):
        with patch.object(
            supervise.subprocess, "run", side_effect=AssertionError("pgrep ran")
        ):
            supervise._stop_local_writers(_session(sid=""))


class TestAttachMtime(unittest.TestCase):
    """The fresh-mtime recheck: cached status lies, the file does not."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.key = "claude:abc-123"
        self.session = _session(sid="abc-123", path="/tmp/proj")

    def test_fresh_transcript_refused(self):
        fresh = Path(tempfile.mkdtemp()) / "live.jsonl"
        fresh.write_text("{}\n")  # mtime = now
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=fresh), \
             patch.object(supervise, "_stop_local_writers"):
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done",
            )
        self.assertFalse(ok)
        self.assertIn("idle", note)

    def test_stale_transcript_passes(self):
        old = Path(tempfile.mkdtemp()) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "_stop_local_writers"):
            ok, _ = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="headless",
            )
        self.assertTrue(ok)

    def test_tmux_handoff_failure_leaves_writer_alive(self):
        # When the tmux replacement can't be stood up the original
        # writer must stay: killing it first would leave the user with
        # a dead session and no supervision.
        old = Path(tempfile.mkdtemp()) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "launch_tmux",
                          return_value=None) as launch, \
             patch.object(supervise, "_stop_local_writers") as stop:
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="tmux",
            )
        self.assertFalse(ok)
        self.assertIn("tmux", note)
        launch.assert_called_once()
        stop.assert_not_called()
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_second_attach_is_noop_under_lock(self):
        # Two attaches racing the same session: the loser sees the
        # winner's registry row instead of relaunching over it.
        supervise.mark(self.home, self.key,
                       {"mode": "tmux", "tmux": "qv-winner"})
        with patch.object(supervise, "launch_tmux") as launch, \
             patch.object(supervise, "_stop_local_writers") as stop:
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done",
            )
        self.assertTrue(ok)
        self.assertIn("already", note)
        launch.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(
            supervise.meta_for(self.home, self.key)["tmux"], "qv-winner"
        )

    def test_tmux_handoff_replaces_then_marks(self):
        # Happy path: new pane stands up, original writer is stopped,
        # registry claims the session — in that order.
        old = Path(tempfile.mkdtemp()) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        order = []
        with patch("quiver.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "launch_tmux",
                          side_effect=lambda *a, **k: order.append(
                              "launch") or "qv-new"), \
             patch.object(supervise, "_stop_local_writers",
                          side_effect=lambda *a, **k: order.append("stop")):
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="tmux",
            )
        self.assertTrue(ok)
        self.assertEqual(order, ["launch", "stop"])
        meta = supervise.meta_for(self.home, self.key)
        self.assertEqual(meta["tmux"], "qv-new")
        self.assertEqual(meta["mode"], "tmux")


if __name__ == "__main__":
    unittest.main()
