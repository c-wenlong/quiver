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

    def test_corrupt_marker_removed(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "bad.json").write_text("{not json")
        self.assertEqual(supervise.adopt_pending(self.home, []), [])
        self.assertFalse((folder / "bad.json").exists())


class TestLaunchNew(unittest.TestCase):
    def test_launch_writes_pending_and_primes(self):
        home = Path(tempfile.mkdtemp())
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise, "prime_pane") as prime:
            ok, note, name = supervise.launch_new(home, "claude", "/tmp", "hi")
        self.assertTrue(ok)
        prime.assert_called_once_with("qv-n", "hi")
        marker = supervise._pending_dir(home) / "qv-n.json"
        self.assertTrue(marker.exists())
        self.assertEqual(
            json.loads(marker.read_text())["tool_name"], "claude"
        )

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


if __name__ == "__main__":
    unittest.main()
