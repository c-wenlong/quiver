import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.harness.catalog import HARNESS_CATALOG
from swe.harness.discover import apply_findings, discover_harnesses
from swe.harness.registry import load_registry, save_registry


def _registry_patches(config_dir: Path, registry_file: Path):
    return (
        patch("swe.harness.registry.CONFIG_DIR", config_dir),
        patch("swe.harness.registry.HARNESS_FILE", registry_file),
        patch("swe.harness.registry.TOOLS_FILE", config_dir / "tools.json"),
    )


class HarnessDiscoverTest(unittest.TestCase):
    def setUp(self):
        # Without these, every test also scans the real machine's bin dirs
        # (~/.local/bin, /opt/homebrew/bin, ...) and runs --version on each
        # harness found there. Tests that check a version patch it inside.
        for target, value in (("EXTRA_BIN_DIRS", ()),
                              ("live_version", lambda cmd: None)):
            p = patch(f"swe.harness.discover.{target}", value)
            p.start()
            self.addCleanup(p.stop)

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

    def test_discovers_devin_binary_from_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "devin")

            config_dir = tmp_path / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            minimal = {"claude": dict(HARNESS_CATALOG["claude"])}

            p1, p2, p3 = _registry_patches(config_dir, registry_file)
            with p1, p2, p3:
                save_registry(minimal)
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                found = [f for f in findings if f.name == "devin"]
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0].status, "new")
                self.assertEqual(found[0].confidence, "high")
                self.assertEqual(found[0].source, "catalog")
                self.assertEqual(found[0].command, "devin")

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
            with p1, p2, p3, patch("swe.harness.discover.live_version", return_value="9.9.9"):
                save_registry(minimal)
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                added, _ = apply_findings(findings, min_confidence="high")
                self.assertIn("kiro", added)
                registry = load_registry()
                self.assertIn("kiro", registry)
                self.assertEqual(registry["kiro"]["command"], "kiro-cli")
                self.assertEqual(registry["kiro"]["version"], "9.9.9")
                self.assertEqual(registry["kiro"]["discovered_via"], "catalog")

    def test_apply_copies_the_catalog_primary_color(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bindir = tmp_path / "bin"
            bindir.mkdir()
            self._make_fake_bin(bindir, "opencode")
            self._make_fake_bin(bindir, "kiro-cli")

            config_dir = tmp_path / ".quiver" / "config"
            registry_file = config_dir / "harness.json"
            minimal = {"claude": dict(HARNESS_CATALOG["claude"])}

            p1, p2, p3 = _registry_patches(config_dir, registry_file)
            with p1, p2, p3, patch("swe.harness.discover.live_version", return_value="1.0"):
                save_registry(minimal)
                findings = discover_harnesses(path_env=str(bindir), home=tmp_path)
                added, _ = apply_findings(findings, min_confidence="high")
                self.assertIn("opencode", added)
                self.assertIn("kiro", added)
                registry = load_registry()
                self.assertEqual(registry["opencode"]["primary_color"], "#8B5CF6")
                # A catalog entry with no colour leaves the field out.
                self.assertNotIn("primary_color", registry["kiro"])

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
            patch("swe.harness.discover.EXTRA_BIN_DIRS", ()),
            patch("swe.harness.discover.live_version", lambda cmd: None),
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
                added, _ = apply_findings(findings, min_confidence="low")
                registry = load_registry()
            self.assertIn("fancytool", added)
            entry = registry["fancytool"]
            self.assertEqual(entry["state"], "archived")
            self.assertIn("reason", entry["archived"])

    def test_home_scan_with_a_live_binary_stays_active(self):
        # #68: --apply-all shelved hermes even though `hermes --version`
        # answers — archiving keyed on source alone, not on whether the
        # probe succeeded. A live version is install evidence; shelving
        # hides the tool the moment it registers.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".hermes" / "skills").mkdir(parents=True)
            config_dir = home / ".quiver" / "config"
            which = lambda name, **kw: "/nix/bin/hermes" if name == "hermes" else None
            patches = (*_registry_patches(config_dir, config_dir / "harness.json"),
                       patch("swe.harness.discover.EXTRA_BIN_DIRS", ()),
                       patch("swe.harness.discover.shutil.which", side_effect=which),
                       patch("swe.harness.discover.live_version", lambda cmd: "0.20.5"))
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                findings = discover_harnesses(path_env="", home=home)
                added, skipped = apply_findings(findings, min_confidence="low")
                registry = load_registry()
            self.assertEqual(added, ["hermes"])
            self.assertEqual(skipped, [])
            entry = registry["hermes"]
            self.assertNotIn("state", entry)  # active — never shelved
            self.assertEqual(entry["command"], "hermes")
            self.assertEqual(entry["version"], "0.20.5")
            self.assertEqual(entry["discovered_via"], "home_scan")


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
                       patch("swe.harness.discover.EXTRA_BIN_DIRS", ()),
                       patch("swe.harness.discover.live_version", lambda cmd: None))
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                findings = discover_harnesses(path_env="", home=home)
                added, _ = apply_findings(findings, min_confidence="high")
                registry = load_registry()
            self.assertNotIn("fancytool", added)
            self.assertNotIn("fancytool", registry)

    def test_floor_skips_are_named_not_silent(self):
        # #68: --apply used to drop a low-confidence find with no output,
        # so the dry run listed a tool the apply then pretended never
        # existed. The refusal comes back named, with its remedy.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".fancytool" / "skills").mkdir(parents=True)
            config_dir = home / ".quiver" / "config"
            patches = (*_registry_patches(config_dir, config_dir / "harness.json"),
                       patch("swe.harness.discover.EXTRA_BIN_DIRS", ()),
                       patch("swe.harness.discover.live_version", lambda cmd: None))
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                findings = discover_harnesses(path_env="", home=home)
                added, skipped = apply_findings(findings, min_confidence="high")
            self.assertNotIn("fancytool", added)
            [skip] = [s for s in skipped if s["name"] == "fancytool"]
            self.assertIn("confidence is low", skip["reason"])
            self.assertIn("--apply-all", skip["reason"])


class NotHarnessTest(unittest.TestCase):
    """Apps that keep a skills/ dir but are not harnesses never show up."""

    def test_home_scan_skips_known_non_harnesses(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for name in ("aside", "pinokio", "tokentracker", "fancytool"):
                (home / f".{name}" / "skills").mkdir(parents=True)
            config_dir = home / ".quiver" / "config"
            patches = (*_registry_patches(config_dir, config_dir / "harness.json"),
                       patch("swe.harness.discover.EXTRA_BIN_DIRS", ()),
                       patch("swe.harness.discover.live_version", lambda cmd: None))
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                found = {f.name for f in discover_harnesses(path_env="", home=home)}
            self.assertIn("fancytool", found)
            for name in ("aside", "pinokio", "tokentracker"):
                self.assertNotIn(name, found)


class ApplyByNameTest(unittest.TestCase):
    """--apply NAME… picks findings by name and bypasses the floor."""

    def _findings(self, home: Path):
        (home / ".fancytool" / "skills").mkdir(parents=True)
        (home / ".othertool" / "skills").mkdir(parents=True)
        return discover_harnesses(path_env="", home=home)

    def test_names_bypass_the_floor_and_add_only_those(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".quiver" / "config"
            patches = (*_registry_patches(config_dir, config_dir / "harness.json"),
                       patch("swe.harness.discover.EXTRA_BIN_DIRS", ()),
                       patch("swe.harness.discover.live_version", lambda cmd: None))
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                findings = self._findings(home)
                added, _ = apply_findings(findings, min_confidence="high",
                                       names={"fancytool"})
                registry = load_registry()
            self.assertEqual(added, ["fancytool"])
            self.assertIn("fancytool", registry)
            self.assertNotIn("othertool", registry)


class DiscoverCommandSelectionTest(unittest.TestCase):
    """Flag handling for --apply NAME… and --pick, with the engine mocked."""

    def _finding(self, name, confidence="low", status="new"):
        from swe.harness.discover import HarnessFinding
        return HarnessFinding(name=name, command="", path=f"/h/.{name}",
                              confidence=confidence, source="home_scan",
                              status=status, description="", tags=(), aliases=())

    def _run(self, args, findings, *, supported=False, chosen=None, apply_ret=None):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest import mock
        from swe.harness import discover_commands as dc
        apply = mock.Mock(
            return_value=apply_ret if apply_ret is not None else ([], []))
        out = StringIO()
        with patch.object(dc, "discover_harnesses", lambda **k: findings), \
                patch.object(dc, "apply_findings", apply), \
                patch.object(dc, "_supported", lambda: supported), \
                patch.object(dc, "multiselect", mock.Mock(return_value=chosen)), \
                redirect_stdout(out):
            code = dc.cmd_discover(args)
        return code, out.getvalue(), apply

    def test_apply_with_names_passes_only_those(self):
        found = [self._finding("a"), self._finding("b")]
        code, _, apply = self._run(["--apply", "b"], found)
        self.assertEqual(code, 0)
        self.assertEqual(apply.call_args.kwargs["names"], {"b"})

    def test_unknown_name_registers_nothing(self):
        code, out, apply = self._run(["--apply", "nope"], [self._finding("a")])
        self.assertEqual(code, 1)
        self.assertIn("Not a new match: nope", out)
        apply.assert_not_called()

    def test_names_without_apply_are_rejected(self):
        code, out, apply = self._run(["a"], [self._finding("a")])
        self.assertEqual(code, 1)
        apply.assert_not_called()

    def test_plain_apply_keeps_the_high_floor(self):
        code, _, apply = self._run(["--apply"], [self._finding("a")])
        self.assertEqual(code, 0)
        self.assertIsNone(apply.call_args.kwargs["names"])
        self.assertEqual(apply.call_args.kwargs["min_confidence"], "high")

    def test_apply_prints_floor_skips_out_loud(self):
        # #68: the refusal a floor creates is printed, not swallowed —
        # before, --apply of a low-confidence find ended in silence.
        skip = {"name": "a",
                "reason": "confidence is low; name it "
                          "(swe discover --apply a) or use --apply-all"}
        code, out, _ = self._run(["--apply"], [self._finding("a")],
                                 apply_ret=([], [skip]))
        self.assertEqual(code, 0)
        self.assertIn("! a:", out)
        self.assertIn("confidence is low", out)

    def test_apply_of_a_refused_name_returns_one(self):
        # Naming a find apply refuses (already registered, or an alias of
        # something else) is a failure, not a quiet no-op.
        skip = {"name": "a", "reason": "already registered under 'claude'"}
        code, out, _ = self._run(["--apply", "a"], [self._finding("a")],
                                 apply_ret=([], [skip]))
        self.assertEqual(code, 1)
        self.assertIn("already registered", out)

    def test_pick_needs_a_terminal(self):
        code, out, apply = self._run(["--pick"], [self._finding("a")], supported=False)
        self.assertEqual(code, 1)
        apply.assert_not_called()

    def test_pick_applies_the_ticked_names(self):
        found = [self._finding("a", "high"), self._finding("b")]
        code, _, apply = self._run(["--pick"], found, supported=True, chosen=["b"])
        self.assertEqual(code, 0)
        self.assertEqual(apply.call_args.kwargs["names"], {"b"})

    def test_pick_cancel_adds_nothing(self):
        code, out, apply = self._run(["--pick"], [self._finding("a")],
                                     supported=True, chosen=None)
        self.assertEqual(code, 0)
        apply.assert_not_called()

    def test_pick_and_apply_together_are_rejected(self):
        code, out, apply = self._run(["--pick", "--apply"], [self._finding("a")],
                                     supported=True)
        self.assertEqual(code, 1)
        apply.assert_not_called()

    def test_pick_with_nothing_new_opens_no_picker(self):
        from swe.harness import discover_commands as dc
        self.assertEqual(dc._pick([self._finding("a", status="registered")]), set())

    def test_pick_with_nothing_ticked_says_so(self):
        code, out, apply = self._run(["--pick"], [self._finding("a")],
                                     supported=True, chosen=[])
        self.assertEqual(code, 0)
        self.assertEqual(apply.call_args.kwargs["names"], set())
        self.assertIn("nothing ticked", out)


    def test_a_refused_pick_prints_why_and_fails(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest import mock
        from swe.harness import discover_commands as dc
        refused = [{"name": "cc", "reason": "already registered under 'claude'"}]
        out = StringIO()
        with patch.object(dc, "discover_harnesses", lambda **k: [self._finding("cc")]), \
                patch.object(dc, "apply_findings", mock.Mock(return_value=([], refused))), \
                redirect_stdout(out):
            code = dc.cmd_discover(["--apply", "cc"])
        self.assertEqual(code, 1)
        self.assertIn("cc: already registered under 'claude'", out.getvalue())
        self.assertNotIn("nothing ticked", out.getvalue())

class OllamaIsNotAHarnessTest(unittest.TestCase):
    def test_ollama_on_path_is_not_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bindir = home / "bin"
            bindir.mkdir()
            exe = bindir / "ollama"
            exe.write_text("#!/bin/sh\n")
            exe.chmod(0o755)
            config_dir = home / ".quiver" / "config"
            patches = (*_registry_patches(config_dir, config_dir / "harness.json"),
                       patch("swe.harness.discover.EXTRA_BIN_DIRS", ()),
                       patch("swe.harness.discover.live_version", lambda cmd: None))
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                save_registry({"claude": dict(HARNESS_CATALOG["claude"])})
                found = {f.name for f in discover_harnesses(path_env=str(bindir), home=home)}
            self.assertNotIn("ollama", found)
            self.assertNotIn("ollama", HARNESS_CATALOG)

class ApplySkipTest(unittest.TestCase):
    """apply_findings never replaces a saved entry or steals an alias."""

    def setUp(self):
        # The registry is passed in, but a save would still hit the real
        # harness.json; keep every write in memory.
        saver = patch("swe.harness.discover.save_registry")
        saver.start()
        self.addCleanup(saver.stop)
        # live_version would spawn the made-up command.
        version = patch("swe.harness.discover.live_version", return_value=None)
        version.start()
        self.addCleanup(version.stop)

    def _finding(self, name, aliases=()):
        from swe.harness.discover import HarnessFinding
        return HarnessFinding(name=name, command=name + "-cli",
                              path="/bin/" + name, confidence="high",
                              source="catalog", status="new", aliases=aliases)

    def test_registered_name_is_skipped_not_overwritten(self):
        reg = {"factory": {"command": "custom-cmd", "description": "mine"}}
        added, skipped = apply_findings([self._finding("factory")], registry=reg)
        self.assertEqual(added, [])
        self.assertEqual(skipped[0]["name"], "factory")
        self.assertIn("already registered", skipped[0]["reason"])
        self.assertEqual(reg["factory"]["command"], "custom-cmd")

    def test_registered_alias_owner_is_skipped(self):
        reg = {"claude": {"command": "claude", "aliases": ["cc"]}}
        added, skipped = apply_findings([self._finding("cc")], registry=reg)
        self.assertEqual(added, [])
        self.assertIn("'claude'", skipped[0]["reason"])

    def test_colliding_alias_is_dropped_not_taken(self):
        reg = {"claude": {"command": "claude", "aliases": ["cc"]}}
        added, skipped = apply_findings(
            [self._finding("newcli", aliases=("cc", "nc"))], registry=reg)
        self.assertEqual(added, ["newcli"])
        self.assertEqual(reg["newcli"]["aliases"], ["nc"])
        self.assertTrue(any("cc" in s["reason"] for s in skipped))
        self.assertEqual(reg["claude"]["aliases"], ["cc"])
