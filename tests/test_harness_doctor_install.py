"""harness.doctor.install_method: how swe doctor's Run reinstalls an
off-PATH harness."""

import unittest
from unittest.mock import patch

from swe.harness.doctor import install_method
from swe.harness.path_health import OffPathHit

NVM_CODEX = "/home/u/.nvm/versions/node/v22/lib/node_modules/@openai/codex/bin/codex.js"


class InstallMethodTest(unittest.TestCase):
    def method(self, on_path, hits):
        with patch("shutil.which", side_effect=lambda t: f"/usr/bin/{t}" if t in on_path else None), \
                patch("swe.harness.path_health.search_dirs_for_command", return_value=hits), \
                patch("swe.harness.registry.load_registry_readonly",
                      return_value={"codex": {"command": "codex"}}):
            return install_method("codex")

    def test_the_method_that_put_the_stray_copy_there(self):
        hits = [OffPathHit("codex", "/usr/bin/codex", "path"), OffPathHit("codex", NVM_CODEX, "nvm")]
        self.assertEqual(self.method({"brew", "npm"}, hits), "npm")

    def test_else_the_first_usable_one(self):
        self.assertEqual(self.method({"brew", "npm"}, [OffPathHit("codex", "/opt/x/codex", "extra")]), "cask")

    def test_a_copy_from_a_tool_off_path_falls_back(self):
        self.assertEqual(self.method({"brew"}, [OffPathHit("codex", NVM_CODEX, "nvm")]), "cask")

    def test_none_when_no_installer_tool_is_on_path(self):
        self.assertIsNone(self.method(set(), [OffPathHit("codex", NVM_CODEX, "nvm")]))


if __name__ == "__main__":
    unittest.main()
