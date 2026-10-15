import unittest

from quiver.harness.catalog import HARNESS_CATALOG
from quiver.harness.installers import (
    INSTALLERS,
    METHOD_TOOL,
    METHODS,
    Installer,
    installers_for,
)


class InstallerTableTest(unittest.TestCase):
    def test_every_known_installer_is_a_catalog_harness(self):
        # jules and mastracode predate the catalog; discovery does not see them.
        exempt = {"jules", "mastracode"}
        for name in INSTALLERS:
            if name in exempt:
                continue
            self.assertIn(name, HARNESS_CATALOG, f"{name} missing from HARNESS_CATALOG")

    def test_every_method_key_is_a_known_method(self):
        for name, spec in INSTALLERS.items():
            for via in spec:
                self.assertIn(via, METHODS, f"{name}: unknown method {via}")
                self.assertIn(via, METHOD_TOOL, f"{name}: {via} has no tool")

    def test_npm_packages_are_not_bare_harness_names(self):
        # A bare name on npm usually belongs to someone else; only a few
        # verified exceptions may equal the harness name.
        allowed = {"cline", "devin", "droid", "freebuff", "mastracode"}
        for name, spec in INSTALLERS.items():
            pkg = spec.get("npm")
            if pkg is None:
                continue
            if pkg == name:
                self.assertIn(name, allowed, f"{name}: unverified bare npm name {pkg}")

    def test_installers_for_orders_by_methods(self):
        self.assertEqual([i.via for i in installers_for("gemini")],
                         ["brew", "npm", "nix"])
        self.assertEqual(installers_for("nosuch"), [])

    def test_display_per_method(self):
        self.assertEqual(Installer("brew", "gemini-cli").display(),
                         "brew install gemini-cli")
        self.assertEqual(Installer("cask", "claude-code").display(),
                         "brew install --cask claude-code")
        self.assertEqual(Installer("npm", "@google/gemini-cli").display(),
                         "npm install -g @google/gemini-cli")
        self.assertEqual(Installer("npm", "pkg").argv("/opt/npm"),
                         ["/opt/npm", "install", "-g", "pkg"])
        self.assertEqual(Installer("uv", "aider-chat").display(),
                         "uv tool install aider-chat")
        self.assertEqual(Installer("nix", "codex").display(),
                         "nix profile install nixpkgs#codex")
        self.assertEqual(Installer("script", "https://opencode.ai/install").display(),
                         "curl -fsSL https://opencode.ai/install | bash")
        self.assertEqual(Installer("script", "https://aider.chat/install.sh").display(),
                         "curl -fsSL https://aider.chat/install.sh | sh")


if __name__ == "__main__":
    unittest.main()
