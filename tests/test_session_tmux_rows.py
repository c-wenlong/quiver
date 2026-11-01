"""Catalog rows carry the supervised fields (mode/tmux/kept) and the
Sessions page plugin opens a terminal on a tmux pane."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.remote import catalog as catalog_mod
from swe.remote import supervise
from swe.sessions.models import Session
from swe.webapp.pages import PageContext, PageError
from swe.webapp.pages import sessions as sessions_page


def _session(sid="s1", tool="claude", path="/tmp", ts=0):
    return Session(timestamp=int(ts * 1000), agent="x", path=path,
                   title="t", session_id=sid, tool_name=tool)


class TestRowSupervisedFields(unittest.TestCase):
    def test_attached_tmux_row(self):
        s = _session()
        key, row = catalog_mod.session_to_row(
            s, "done", "m",
            {"claude:s1": {"mode": "tmux", "tmux": "qv-abc"}})
        self.assertEqual(row["ownership"], "attached")
        self.assertEqual(row["mode"], "tmux")
        self.assertEqual(row["tmux"], "qv-abc")
        self.assertNotIn("kept", row)

    def test_attached_nontmux_row(self):
        _key, row = catalog_mod.session_to_row(
            _session(), "done", "m",
            {"claude:s1": {"mode": "headless"}})
        self.assertEqual(row["mode"], "headless")
        self.assertNotIn("tmux", row)

    def test_kept_row(self):
        _key, row = catalog_mod.session_to_row(
            _session(), "done", "m", {},
            {"claude:s1": {"mode": "tmux", "tmux": "qv-kept"}})
        self.assertEqual(row["ownership"], "observed")
        self.assertTrue(row["kept"])
        self.assertEqual(row["tmux"], "qv-kept")
        # A kept pane is adopted without resume flags: attachable anyway.
        self.assertTrue(row["attachable"])

    def test_observed_row(self):
        _key, row = catalog_mod.session_to_row(
            _session(), "done", "m", {})
        self.assertEqual(row["ownership"], "observed")
        self.assertNotIn("mode", row)
        self.assertNotIn("tmux", row)
        self.assertNotIn("kept", row)


class TestOpenTerminal(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.ctx = PageContext(home=self.home, state=None)

    def test_rejects_non_tmux(self):
        with patch.object(sys, "platform", "darwin"), \
                self.assertRaises(PageError) as cm:
            sessions_page.post(self.ctx, "open_terminal", {"key": "claude:s1"})
        self.assertEqual(cm.exception.status, 409)

    def test_rejects_unknown_action(self):
        with self.assertRaises(PageError) as cm:
            sessions_page.post(self.ctx, "bogus", {})
        self.assertEqual(cm.exception.status, 404)

    def test_rejects_non_darwin(self):
        supervise.mark(self.home, "claude:s1",
                       {"mode": "tmux", "tmux": "qv-abc"})
        with patch.object(sys, "platform", "linux"):
            with self.assertRaises(PageError) as cm:
                sessions_page.post(
                    self.ctx, "open_terminal", {"key": "claude:s1"})
        self.assertEqual(cm.exception.status, 422)

    def test_opens_supervised(self):
        supervise.mark(self.home, "claude:s1",
                       {"mode": "tmux", "tmux": "qv-abc"})
        with patch.object(sys, "platform", "darwin"), \
                patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            out = sessions_page.post(
                self.ctx, "open_terminal", {"key": "claude:s1"})
        self.assertEqual(out["tmux"], "qv-abc")
        script = " ".join(run.call_args[0][0])
        self.assertIn("tmux attach -t qv-abc", script)

    def test_opens_kept(self):
        supervise._save_kept(self.home, {"claude:s1": dict(
            mode="tmux", tmux="qv-kept", pane_pid=1)})
        with patch.object(sys, "platform", "darwin"), \
                patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            out = sessions_page.post(
                self.ctx, "open_terminal", {"key": "claude:s1"})
        self.assertEqual(out["tmux"], "qv-kept")

    def test_osascript_failure(self):
        supervise.mark(self.home, "claude:s1",
                       {"mode": "tmux", "tmux": "qv-abc"})
        with patch.object(sys, "platform", "darwin"), \
                patch.object(subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "boom")
            with self.assertRaises(PageError) as cm:
                sessions_page.post(
                    self.ctx, "open_terminal", {"key": "claude:s1"})
        self.assertEqual(cm.exception.status, 502)


if __name__ == "__main__":
    unittest.main()
