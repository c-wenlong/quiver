import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import swe.harness.path_health as path_health
from swe.harness.path_health import (
    find_off_path_tools,
    is_dir_on_path,
    search_dirs_for_command,
)


class PathHealthTest(unittest.TestCase):
    def test_search_dirs_finds_nvm_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            nvm_bin = home / ".nvm" / "versions" / "node" / "v22.0.0" / "bin"
            nvm_bin.mkdir(parents=True)
            binary = nvm_bin / "mastracode"
            binary.write_text("#!/bin/sh\necho hi\n")
            binary.chmod(0o755)

            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "NVM_DIR": str(home / ".nvm")}):
                with patch("swe.harness.path_health.shutil.which", return_value=None):
                    hits = search_dirs_for_command("mastracode", home=home)

            self.assertTrue(hits)
            self.assertEqual(hits[0].source, "nvm")
            self.assertTrue(hits[0].path.endswith("mastracode"))

    def test_find_off_path_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            nvm_bin = home / ".nvm" / "versions" / "node" / "v22.0.0" / "bin"
            nvm_bin.mkdir(parents=True)
            binary = nvm_bin / "ghostcli"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)

            registry = {"ghost": {"command": "ghostcli"}}
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "NVM_DIR": str(home / ".nvm")}):
                with patch("swe.harness.path_health.shutil.which", return_value=None):
                    with patch("swe.harness.path_health.Path.home", return_value=home):
                        orphans = find_off_path_tools(registry)

            self.assertEqual(len(orphans), 1)
            self.assertEqual(orphans[0][0], "ghost")

    def test_is_dir_on_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "bin"
            d.mkdir()
            with patch.dict(os.environ, {"PATH": f"{d}:/usr/bin"}):
                self.assertTrue(is_dir_on_path(d))
            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
                self.assertFalse(is_dir_on_path(d))


class NodeEnvProbeTest(unittest.TestCase):
    """#68: `npm prefix -g` answers a prefix, and POSIX globals land in
    <prefix>/bin — a dir npm only creates on the first `npm i -g`. Falling
    back to the bare prefix aimed doctor's PATH advice at a directory that
    never holds binaries.
    """

    def _env(self, prefix: str, *, os_name: str = "posix"):
        def fake_run(cmd, timeout=3.0):
            if cmd[1:] == ["prefix", "-g"]:
                return prefix
            if cmd[1:] == ["-v"]:
                return "10.0.0"
            return None

        with patch.object(path_health.shutil, "which", return_value=None), \
                patch.object(path_health, "preferred_npm_bin", return_value="/fake/npm"), \
                patch.object(path_health, "_run", side_effect=fake_run), \
                patch.object(os, "name", os_name):
            return path_health.probe_node_env()

    def test_global_bin_is_prefix_bin_before_the_dir_exists(self):
        # npm creates <prefix>/bin lazily; reporting the prefix itself as
        # the bin dir was the Linux + Nix misdiagnosis.
        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / ".npm-global")
            env = self._env(prefix)
            self.assertEqual(env.global_prefix, prefix)
            self.assertEqual(env.global_bin, str(Path(prefix) / "bin"))
            self.assertFalse(env.global_bin_on_path)

    def test_global_bin_is_prefix_bin_when_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / ".npm-global"
            (prefix / "bin").mkdir(parents=True)
            env = self._env(str(prefix))
            self.assertEqual(env.global_bin, str(prefix / "bin"))

    def test_windows_keeps_the_prefix_as_the_bin_dir(self):
        # npm's own convention differs there: globals sit in the prefix.
        with tempfile.TemporaryDirectory() as tmp:
            prefix = str(Path(tmp) / "npm-prefix")
            env = self._env(prefix, os_name="nt")
            self.assertEqual(env.global_bin, prefix)


if __name__ == "__main__":
    unittest.main()
