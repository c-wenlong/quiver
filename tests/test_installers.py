import unittest

from quiver.harness.catalog import HARNESS_CATALOG
from quiver.harness.installers import (
    INSTALLERS,
    METHOD_TOOL,
    METHODS,
    Installer,
    Uninstaller,
    installers_for,
    uninstaller_for,
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


class UninstallerTest(unittest.TestCase):
    def test_reads_the_package_off_the_resolved_path(self):
        cases = {
            "/opt/homebrew/Caskroom/claude-code@latest/2.1.301/claude":
                ("cask", "claude-code@latest", "brew uninstall --cask claude-code@latest"),
            "/opt/homebrew/Cellar/gemini-cli/0.9.0/bin/gemini":
                ("brew", "gemini-cli", "brew uninstall gemini-cli"),
            "/Users/me/.nvm/versions/node/v22.1.0/lib/node_modules/@google/gemini-cli/dist/index.js":
                ("npm", "@google/gemini-cli", "npm uninstall -g @google/gemini-cli"),
            "/opt/homebrew/lib/node_modules/opencode-ai/bin/opencode":
                ("npm", "opencode-ai", "npm uninstall -g opencode-ai"),
            "/Users/me/.local/share/uv/tools/aider-chat/bin/aider":
                ("uv", "aider-chat", "uv tool uninstall aider-chat"),
        }
        for path, (via, package, command) in cases.items():
            remover = uninstaller_for("x", path)
            self.assertEqual((remover.via, remover.package, remover.display()),
                             (via, package, command), path)

    def test_npm_under_a_homebrew_node_is_npm(self):
        path = "/opt/homebrew/Cellar/node/22.1.0/lib/node_modules/@openai/codex/bin/codex.js"
        self.assertEqual(uninstaller_for("codex", path), Uninstaller("npm", "@openai/codex"))

    def test_nix_uses_the_table_attr(self):
        path = "/nix/store/abc-codex-0.1/bin/codex"
        self.assertEqual(uninstaller_for("codex", path), Uninstaller("nix", "codex"))
        self.assertIsNone(uninstaller_for("forge", path))

    def test_script_installs_and_missing_paths_have_none(self):
        self.assertIsNone(uninstaller_for("claude", "/Users/me/.local/share/claude/versions/2.1/claude"))
        self.assertIsNone(uninstaller_for("claude", None))
        self.assertIsNone(uninstaller_for("claude", ""))
