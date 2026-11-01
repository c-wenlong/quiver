import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.harness.catalog import HARNESS_CATALOG
from swe.harness.installers import (
    GUI_APPS,
    INSTALLERS,
    METHOD_TOOL,
    METHODS,
    WEBSITES,
    Installer,
    Uninstaller,
    installed_app,
    installed_cask,
    installers_for,
    uninstaller_for,
)


class InstallerTableTest(unittest.TestCase):
    def test_every_known_installer_is_a_catalog_harness(self):
        # jules and mastracode predate the catalog; zcode is a GUI app with
        # no PATH binary, so discovery has nothing to recognise either.
        exempt = {"jules", "mastracode", "zcode"}
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
                         ["npm", "nix"])
        self.assertEqual(installers_for("nosuch"), [])
        self.assertEqual([i.via for i in installers_for("antigravity")],
                         ["cask", "nix", "script"])

    def test_provider_entries_checked_against_their_registries(self):
        # The exact tokens/packages each registry uses; a bare name would
        # be somebody else's package (nixpkgs grok-cli, npm kimi).
        self.assertEqual(INSTALLERS["kimi"],
                         {"brew": "kimi-code", "npm": "@moonshot-ai/kimi-code",
                          "script": "https://code.kimi.com/kimi-code/install.sh"})
        self.assertEqual(INSTALLERS["grok"],
                         {"npm": "@xai-official/grok",
                          "script": "https://x.ai/cli/install.sh"})
        self.assertEqual(INSTALLERS["antigravity"],
                         {"cask": "antigravity", "nix": "antigravity-cli",
                          "script": "https://antigravity.google/cli/install.sh"})
        self.assertEqual(INSTALLERS["commandcode"], {"npm": "command-code"})
        self.assertEqual(INSTALLERS["qoder"],
                         {"cask": "qoderai/qoder/qodercli",
                          "npm": "@qoder-ai/qodercli",
                          "script": "https://qoder.com/install"})
        self.assertEqual(INSTALLERS["qoder-cn"],
                         {"script": "https://qoder.com.cn/install"})
        self.assertEqual(INSTALLERS["zcode"], {"cask": "zcode"})
        # Pinned by the 2026-10 registry audit: brew's gemini-cli is
        # deprecated upstream (like cline's, which has no brew entry),
        # nixpkgs renamed kilocode-cli to kilo, and block/goose moved to
        # aaif-goose/goose.
        self.assertNotIn("brew", INSTALLERS["gemini"])
        self.assertNotIn("brew", INSTALLERS["cline"])
        self.assertEqual(INSTALLERS["kilo"]["nix"], "kilo")
        self.assertEqual(INSTALLERS["goose"]["script"],
                         "https://github.com/aaif-goose/goose/releases/download/"
                         "stable/download_cli.sh")

    def test_every_provider_the_sheet_can_show_has_a_website(self):
        # The sheet dead-ends on a provider with no methods and no website;
        # the audit keeps the two tables naming the same provider set.
        self.assertEqual(set(WEBSITES), set(INSTALLERS))
        for name, url in WEBSITES.items():
            self.assertTrue(url.startswith("https://"), name)
        # Spot checks against the vendors' own pages.
        self.assertEqual(WEBSITES["kiro"], "https://kiro.dev")
        self.assertEqual(WEBSITES["gemini"], "https://geminicli.com")
        self.assertEqual(WEBSITES["droid"], "https://factory.com")

    def test_gui_apps_have_installers_and_websites(self):
        # The bundle check exists so the sheet can show state for a GUI
        # provider; it only matters for names swe can install or link out.
        for name in GUI_APPS:
            self.assertIn(name, INSTALLERS, name)
            self.assertIn(name, WEBSITES, name)

    def test_installed_app_finds_the_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "Applications" / "ZCode.app"
            bundle.mkdir(parents=True)
            with patch.dict(GUI_APPS, {"zcode": (str(bundle),)}):
                self.assertEqual(installed_app("zcode"), str(bundle))
        with patch.dict(GUI_APPS, {"zcode": ("/nonexistent/ZCode.app",)}):
            self.assertIsNone(installed_app("zcode"))
        self.assertIsNone(installed_app("gemini"))  # no GUI_APPS entry

    def test_installed_cask_reads_the_caskroom(self):
        with tempfile.TemporaryDirectory() as tmp:
            room = Path(tmp) / "Caskroom" / "zcode"
            room.mkdir(parents=True)
            with patch.dict(os.environ, {"HOMEBREW_PREFIX": tmp}):
                self.assertEqual(installed_cask("zcode"), str(room))
                # Tap-qualified tokens record under the bare cask name.
                qoder = Path(tmp) / "Caskroom" / "qodercli"
                qoder.mkdir()
                self.assertEqual(installed_cask("qoder"), str(qoder))
            self.assertIsNone(installed_cask("gemini"))  # no cask entry

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

    def test_a_formula_bundling_node_modules_is_brew(self):
        path = ("/opt/homebrew/Cellar/gemini-cli/0.9.0/libexec/lib/node_modules/"
                "@google/gemini-cli/dist/index.js")
        self.assertEqual(uninstaller_for("gemini", path), Uninstaller("brew", "gemini-cli"))

    def test_npm_globals_inside_the_node_keg_are_npm(self):
        path = "/opt/homebrew/Cellar/node/22.1.0/lib/node_modules/@openai/codex/bin/codex.js"
        self.assertEqual(uninstaller_for("codex", path), Uninstaller("npm", "@openai/codex"))
        path = "/usr/local/Cellar/node@20/20.1.0/lib/node_modules/opencode-ai/bin/opencode"
        self.assertEqual(uninstaller_for("opencode", path), Uninstaller("npm", "opencode-ai"))
        # A Cellar path with nothing after node_modules has nothing to name.
        self.assertIsNone(uninstaller_for("x", "/opt/homebrew/Cellar/node/22.1.0/lib/node_modules"))

    def test_nix_removes_by_store_path(self):
        # An element may be named a renamed attr (nixpkgs renamed
        # kilocode-cli to kilo), so the store path — which `nix profile
        # remove` matches whatever the element is called — is the remover,
        # and no table entry is needed to name it.
        path = "/nix/store/abc-codex-0.1/bin/codex"
        remover = Uninstaller("nix", "/nix/store/abc-codex-0.1")
        self.assertEqual(uninstaller_for("codex", path), remover)
        self.assertEqual(uninstaller_for("forge", path), remover)
        self.assertEqual(remover.display(),
                         "nix profile remove /nix/store/abc-codex-0.1")

    def test_script_installs_and_missing_paths_have_none(self):
        self.assertIsNone(uninstaller_for("claude", "/Users/me/.local/share/claude/versions/2.1/claude"))
        self.assertIsNone(uninstaller_for("claude", None))
        self.assertIsNone(uninstaller_for("claude", ""))
