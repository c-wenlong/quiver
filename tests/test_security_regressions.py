"""Regressions for three defects found in the architecture audit.

Each one had a way of failing quietly: a token sent over an unverified
connection, a credential file widened to world-readable, and skills
deleted with no copy anywhere. None of them raised, so none would have
surfaced without a test pinning the behaviour.
"""

import os
import ssl
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.paths import atomic_write_text, backup_tree


class SslVerificationTest(unittest.TestCase):
    """Requests carry `Authorization: Bearer`, so verification must stay on.

    The old fallback retried with CERT_NONE whenever the first attempt hit
    an SSL error, which is exactly what an interception looks like.
    """

    def test_no_code_path_disables_verification(self):
        source = Path("src/quiver/harness/rate_limits.py").read_text()
        code = "\n".join(
            ln for ln in source.splitlines()
            if not ln.lstrip().startswith("#")
        )
        # Keep docstrings out of it; this is about executable statements.
        for banned in ("verify_mode = ssl.CERT_NONE", "check_hostname = False"):
            self.assertNotIn(banned, code, f"{banned} is back in rate_limits")

    def test_fallback_context_still_verifies(self):
        from quiver.harness.rate_limits import _verified_context

        ctx = _verified_context()
        if ctx is None:
            self.skipTest("no CA bundle available here")
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_gives_up_when_no_ca_bundle_exists(self):
        from quiver.harness.rate_limits import _verified_context

        real = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def no_certifi(name, *a, **k):
            if name == "certifi":
                raise ImportError(name)
            return real(name, *a, **k)

        with mock.patch("builtins.__import__", no_certifi):
            self.assertIsNone(
                _verified_context(),
                "must return None (give up) rather than an unverified context",
            )


class AtomicWritePermissionTest(unittest.TestCase):
    """tmp+rename created the temp file under the umask, so a 0600 config
    came back 0644. Harness MCP configs hold resolved API tokens."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def _mode(self, p):
        return stat.S_IMODE(p.stat().st_mode)

    def test_existing_private_mode_survives(self):
        p = self.d / "mcp.json"
        p.write_text("{}")
        os.chmod(p, 0o600)
        atomic_write_text(p, '{"a": 1}', private=True)
        self.assertEqual(self._mode(p), 0o600)

    def test_new_private_file_is_not_world_readable(self):
        p = self.d / "new.json"
        atomic_write_text(p, "{}", private=True)
        self.assertEqual(self._mode(p) & 0o077, 0, "group/other bits set")

    def test_non_secret_file_keeps_its_own_mode(self):
        p = self.d / "plain.json"
        p.write_text("{}")
        os.chmod(p, 0o644)
        atomic_write_text(p, "{}")
        self.assertEqual(self._mode(p), 0o644)

    def test_content_is_replaced_and_no_temp_is_left(self):
        p = self.d / "x.json"
        atomic_write_text(p, "second")
        self.assertEqual(p.read_text(), "second")
        self.assertEqual([q for q in self.d.iterdir() if ".tmp" in q.name], [])

    def test_a_failed_write_leaves_no_temp_behind(self):
        p = self.d / "y.json"
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                atomic_write_text(p, "data")
        self.assertEqual([q for q in self.d.iterdir() if ".tmp" in q.name], [])

    def test_the_mcp_writer_uses_it(self):
        from quiver.mcp.cli import save_json

        p = self.d / "harness.json"
        p.write_text("{}")
        os.chmod(p, 0o600)
        save_json(p, {"mcpServers": {}})
        self.assertEqual(self._mode(p), 0o600, "swe mcp sync widened the config")


class ForcedLinkBacksUpTest(unittest.TestCase):
    """`swe skills link --force` used to rmtree a real skills root with no
    copy anywhere, while the same decision in `swe init` backed up first."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".quiver" / "skills").mkdir(parents=True)
        self.root = self.home / ".codex" / "skills"
        self.root.mkdir(parents=True)
        (self.root / "only-copy").mkdir()
        (self.root / "only-copy" / "SKILL.md").write_text("irreplaceable")

    def _link(self, **kw):
        from quiver.skills.link_ops import link_skill_root

        return link_skill_root("codex", home=self.home, cwd=self.home, **kw)

    def test_refuses_without_force(self):
        from quiver.skills.link_ops import SkillLayoutError

        with self.assertRaises(SkillLayoutError):
            self._link()
        self.assertTrue((self.root / "only-copy" / "SKILL.md").exists())

    def test_force_backs_up_before_deleting(self):
        from quiver.paths import backups_dir_for

        self._link(force=True)
        saved = list(backups_dir_for(self.home).rglob("SKILL.md"))
        self.assertEqual(len(saved), 1, "nothing was backed up")
        self.assertEqual(saved[0].read_text(), "irreplaceable")

    def test_force_still_creates_the_link(self):
        self._link(force=True)
        self.assertTrue(self.root.is_symlink())


class BackupTreeHardeningTest(unittest.TestCase):
    """Three ways the old _backup aborted a run partway through."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        (self.home / ".quiver" / "backups").mkdir(parents=True)
        self.tree = self.home / ".h" / "skills"
        self.tree.mkdir(parents=True)
        (self.tree / "ok").mkdir()
        (self.tree / "ok" / "SKILL.md").write_text("x")

    def test_a_broken_symlink_does_not_abort_the_backup(self):
        (self.tree / "broken").symlink_to(self.home / "nowhere")
        dest = backup_tree(self.tree, self.home)
        self.assertTrue((dest / "ok" / "SKILL.md").exists())

    def test_symlinks_are_copied_as_links_not_followed(self):
        (self.tree / "link").symlink_to(self.tree / "ok")
        dest = backup_tree(self.tree, self.home)
        self.assertTrue((dest / "link").is_symlink())

    def test_a_path_outside_home_does_not_raise(self):
        outside = Path(tempfile.mkdtemp()) / "skills"
        outside.mkdir(parents=True)
        (outside / "s").mkdir()
        self.assertTrue(backup_tree(outside, self.home).exists())

    def test_two_backups_in_the_same_second_do_not_collide(self):
        a = backup_tree(self.tree, self.home)
        b = backup_tree(self.tree, self.home)
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists() and b.exists())


if __name__ == "__main__":
    unittest.main()
