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


if __name__ == "__main__":
    unittest.main()
