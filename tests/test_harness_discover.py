import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.defaults import DEFAULT_TOOLS
from quiver.harness.discover import apply_findings, discover_harnesses
from quiver.harness.registry import load_registry, save_registry


def _registry_patches(config_dir: Path, registry_file: Path):
    return (
        patch("quiver.harness.registry.CONFIG_DIR", config_dir),
        patch("quiver.harness.registry.REGISTRY_FILE", registry_file),
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

            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "tools.json"
            minimal = {"claude": dict(DEFAULT_TOOLS["claude"])}

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
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

            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "tools.json"
            minimal = {"claude": dict(DEFAULT_TOOLS["claude"])}

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
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

            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "tools.json"
            minimal = {"claude": dict(DEFAULT_TOOLS["claude"])}

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2, patch("quiver.harness.discover.live_version", return_value="9.9.9"):
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

            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "tools.json"

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                save_registry(dict(DEFAULT_TOOLS))
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                claude = [f for f in findings if f.name == "claude"]
                self.assertEqual(claude, [])

    def test_include_registered_shows_installed_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "claude")

            config_dir = tmp_path / ".config" / "swe"
            registry_file = config_dir / "tools.json"

            p1, p2 = _registry_patches(config_dir, registry_file)
            with p1, p2:
                save_registry(dict(DEFAULT_TOOLS))
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
