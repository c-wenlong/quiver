import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.path_health import (
    find_off_path_tools,
    is_dir_on_path,
    resolve_npm_package,
    search_dirs_for_command,
)


class PathHealthTest(unittest.TestCase):
    def test_resolve_npm_package_map(self):
        self.assertEqual(resolve_npm_package("jules"), "@google/jules")
        self.assertEqual(resolve_npm_package("mastracode"), "mastracode")
        self.assertEqual(resolve_npm_package("jules", package="@other/jules"), "@other/jules")
        self.assertEqual(resolve_npm_package("customtool"), "customtool")

    def test_search_dirs_finds_nvm_binary(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            nvm_bin = home / ".nvm" / "versions" / "node" / "v22.0.0" / "bin"
            nvm_bin.mkdir(parents=True)
            binary = nvm_bin / "mastracode"
            binary.write_text("#!/bin/sh\necho hi\n")
            binary.chmod(0o755)

            with patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "NVM_DIR": str(home / ".nvm")}):
                with patch("quiver.harness.path_health.shutil.which", return_value=None):
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
                with patch("quiver.harness.path_health.shutil.which", return_value=None):
                    with patch("quiver.harness.path_health.Path.home", return_value=home):
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


if __name__ == "__main__":
    unittest.main()
