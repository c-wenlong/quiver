import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.catalog import HARNESS_CATALOG
from quiver.harness.discover import apply_findings, discover_harnesses
from quiver.harness.registry import load_registry, save_registry


def _registry_patches(config_dir: Path, registry_file: Path):
    return (
        patch("quiver.harness.registry.CONFIG_DIR", config_dir),
        patch("quiver.harness.registry.HARNESS_FILE", registry_file),
        patch("quiver.harness.registry.TOOLS_FILE", config_dir / "tools.json"),
    )


class HarnessDiscoverTest(unittest.TestCase):
    def _make_fake_bin(self, bindir: Path, name: str) -> Path:
        exe = bindir / name
        exe.write_text("#!/bin/sh\necho test\n")
        exe.chmod(0o755)
        return exe

    def test_discovers_catalog_entry_not_in_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "kiro-cli")

            config_dir = tmp_path / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            minimal = {"claude": dict(HARNESS_CATALOG["claude"])}

            p1, p2, p3 = _registry_patches(config_dir, registry_file)
            with p1, p2, p3:
                save_registry(minimal)
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                kiro = [f for f in findings if f.name == "kiro"]
                self.assertEqual(len(kiro), 1)
                self.assertEqual(kiro[0].status, "new")
                self.assertEqual(kiro[0].confidence, "high")
                self.assertEqual(kiro[0].source, "catalog")

    def test_path_scan_finds_unknown_cli_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "my-tool-code")

            config_dir = tmp_path / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            minimal = {"claude": dict(HARNESS_CATALOG["claude"])}

            p1, p2, p3 = _registry_patches(config_dir, registry_file)
            with p1, p2, p3:
                save_registry(minimal)
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                scanned = [f for f in findings if f.command == "my-tool-code"]
                self.assertEqual(len(scanned), 1)
                self.assertEqual(scanned[0].status, "new")
                self.assertEqual(scanned[0].confidence, "medium")
                self.assertEqual(scanned[0].source, "path_scan")

    def test_apply_adds_new_findings_to_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "kiro-cli")

            config_dir = tmp_path / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            minimal = {"claude": dict(HARNESS_CATALOG["claude"])}

            p1, p2, p3 = _registry_patches(config_dir, registry_file)
            with p1, p2, p3, patch("quiver.harness.discover.live_version", return_value="9.9.9"):
                save_registry(minimal)
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                added = apply_findings(findings, min_confidence="high")
                self.assertIn("kiro", added)
                registry = load_registry()
                self.assertIn("kiro", registry)
                self.assertEqual(registry["kiro"]["command"], "kiro-cli")
                self.assertEqual(registry["kiro"]["version"], "9.9.9")
                self.assertEqual(registry["kiro"]["discovered_via"], "catalog")

    def test_skips_registered_entries_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "claude")

            config_dir = tmp_path / ".quiver" / "config"
            registry_file = config_dir / "harness.json"

            p1, p2, p3 = _registry_patches(config_dir, registry_file)
            with p1, p2, p3:
                save_registry(dict(HARNESS_CATALOG))
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                claude = [f for f in findings if f.name == "claude"]
                self.assertEqual(claude, [])

    def test_include_registered_shows_installed_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "claude")

            config_dir = tmp_path / ".quiver" / "config"
            registry_file = config_dir / "harness.json"

            p1, p2, p3 = _registry_patches(config_dir, registry_file)
            with p1, p2, p3:
                save_registry(dict(HARNESS_CATALOG))
                findings = discover_harnesses(
                    path_env=str(bindir),
                    home=tmp_path,
                    include_registered=True,
                )
                claude = [f for f in findings if f.name == "claude"]
                self.assertEqual(len(claude), 1)
                self.assertEqual(claude[0].status, "registered")


if __name__ == "__main__":
    unittest.main()


class HomeScanTest(unittest.TestCase):
    """The ~ and ~/.config sweep for agent-shaped homes without a binary.

    EXTRA_BIN_DIRS and live_version are patched out: without that, every
    test scans the real machine's bin dirs and shells out --version per
    find, which made this file take a minute and depend on what happens
    to be installed.
    """

    def _setup(self, tmp_path: Path):
        config_dir = tmp_path / ".quiver" / "config"
        registry_file = config_dir / "harness.json"
        return (
            *_registry_patches(config_dir, registry_file),
            patch("quiver.harness.discover.EXTRA_BIN_DIRS", ()),
            patch("quiver.harness.discover.live_version", lambda cmd: None),
        )

    def test_finds_dotdir_with_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".fancytool" / "skills").mkdir(parents=True)
            patches = self._setup(home)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                found = {f.name: f for f in discover_harnesses(path_env="", home=home)}
            self.assertIn("fancytool", found)
            self.assertEqual(found["fancytool"].source, "home_scan")
            self.assertEqual(found["fancytool"].confidence, "low")

    def test_finds_config_subdir_and_deep_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            # marker two levels down, the traycer shape
            (home / ".config" / "newtool" / "inner" / "skills").mkdir(parents=True)
            patches = self._setup(home)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                found = {f.name for f in discover_harnesses(path_env="", home=home)}
            self.assertIn("newtool", found)

    def test_plain_config_dotdir_is_not_agent_shaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            docker = home / ".dockerlike"
            docker.mkdir()
            (docker / "config.json").write_text("{}")
            patches = self._setup(home)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                found = {f.name for f in discover_harnesses(path_env="", home=home)}
            self.assertNotIn("dockerlike", found)

    def test_registered_homes_are_skipped_including_capability_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude" / "skills").mkdir(parents=True)
            (home / ".factory" / "skills").mkdir(parents=True)  # droid's home
            registry = {
                "claude": dict(HARNESS_CATALOG["claude"]),
                "droid": {"command": "droid", "description": "", "tags": [], "aliases": [],
                          "capabilities": {"plugins": {"supported": True,
                                                       "root": "~/.factory/plugins"}}},
            }
            patches = self._setup(home)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry(registry)
                found = {f.name for f in discover_harnesses(path_env="", home=home)}
            self.assertNotIn("claude", found)
            self.assertNotIn("factory", found)

    def test_backupish_names_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".tool.pre-bootstrap-20260101" / "skills").mkdir(parents=True)
            patches = self._setup(home)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                found = {f.name for f in discover_harnesses(path_env="", home=home)}
            self.assertFalse(any("pre-bootstrap" in n for n in found))

    def test_home_scan_applies_as_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".fancytool" / "skills").mkdir(parents=True)
            patches = self._setup(home)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                findings = discover_harnesses(path_env="", home=home)
                added = apply_findings(findings, min_confidence="low")
                registry = load_registry()
            self.assertIn("fancytool", added)
            entry = registry["fancytool"]
            self.assertEqual(entry["state"], "archived")
            self.assertIn("reason", entry["archived"])


class ConfidenceFloorTest(unittest.TestCase):
    """min_confidence is a floor. The map was inverted before the home scan
    introduced the first low-confidence source, so --apply (high) silently
    accepted every tier."""

    def test_high_floor_rejects_low_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".fancytool" / "skills").mkdir(parents=True)
            config_dir = home / ".quiver" / "config"
            patches = (*_registry_patches(config_dir, config_dir / "harness.json"),
                       patch("quiver.harness.discover.EXTRA_BIN_DIRS", ()),
                       patch("quiver.harness.discover.live_version", lambda cmd: None))
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                findings = discover_harnesses(path_env="", home=home)
                added = apply_findings(findings, min_confidence="high")
                registry = load_registry()
            self.assertNotIn("fancytool", added)
            self.assertNotIn("fancytool", registry)
