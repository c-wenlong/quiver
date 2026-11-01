"""find/roots.py: the browser's opening rows and its path helpers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.find.plugins import Plugin
from swe.find.roots import (
    _label_from_capability_root,
    _is_dir,
    _exists,
    _normalise,
    _plural,
    _short,
    dir_label,
    harness_footer_text,
    normalise_harness,
    plugins_roots,
    resolve_harness,
    skills_roots,
    SCOPE_ALL,
)
from swe.find.tree import Node


class PathHelpersTest(unittest.TestCase):
    def test_label_from_capability_root(self):
        self.assertEqual(_label_from_capability_root("~/.factory/skills"), "factory")
        self.assertEqual(_label_from_capability_root("~/.config/opencode/skills"), "opencode")
        self.assertEqual(_label_from_capability_root("~/work/skills"), "work")
        self.assertIsNone(_label_from_capability_root(""))
        self.assertIsNone(_label_from_capability_root(None))
        self.assertIsNone(_label_from_capability_root("~"))

    def test_dir_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(dir_label(home / ".claude" / "x", home), "claude")
            self.assertEqual(dir_label(home / ".config" / "opencode" / "x", home), "opencode")
            self.assertIsNone(dir_label(home / "plain" / "x", home))
            self.assertIsNone(dir_label(home, home))
            self.assertIsNone(dir_label(Path("/outside/x"), home))

    def test_resolve_harness_uses_capability_aliases(self):
        reg = {"droid": {"capabilities": {"skills": {"root": "~/.factory/skills"}}}}
        aliases = {"factory": "droid"}
        self.assertEqual(resolve_harness("factory", reg, aliases), "droid")
        self.assertEqual(resolve_harness("droid", reg, aliases), "droid")
        self.assertIsNone(resolve_harness(None, reg, aliases))

    def test_normalise_falls_back_to_defaults(self):
        self.assertEqual(_normalise("bogus"), "global")
        self.assertEqual(_normalise(SCOPE_ALL), SCOPE_ALL)
        self.assertEqual(normalise_harness("bogus"), "active")
        self.assertEqual(normalise_harness("all"), "all")

    def test_is_dir_and_exists_tolerate_none_and_errors(self):
        self.assertFalse(_is_dir(None))
        self.assertFalse(_exists(None))
        self.assertFalse(_is_dir(Path("/definitely/not/here")))
        self.assertFalse(_exists(Path("/definitely/not/here")))

    def test_short_and_plural(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.assertEqual(_short(home / "x", home), "~/x")
            self.assertEqual(_short(Path("/outside"), home), "/outside")
        self.assertEqual(_plural(1, "skill"), "1 skill")
        self.assertEqual(_plural(2, "skill"), "2 skills")

    def test_footer_text_pluralises(self):
        self.assertIn("1 archived harness", harness_footer_text(1))
        self.assertIn("2 archived harnesses", harness_footer_text(2))


class SkillsRootsTest(unittest.TestCase):
    def _node(self, label, path, state="keep", count=0):
        return Node(label=label, path=path, kind="directory", state=state,
                    target=None, count=count)

    def test_unsynced_then_synced_grouping(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            shared = home / ".quiver" / "skills"
            shared.mkdir(parents=True)
            lone = home / ".lone" / "skills"
            lone.mkdir(parents=True)
            (home / ".claude" / "skills").mkdir(parents=True)
            nodes = [
                self._node("lone", lone, state="keep"),
                self._node("claude", home / ".claude" / "skills", state="linked"),
            ]
            with patch("swe.find.roots.skills_tree", return_value=(shared, nodes)), \
                    patch("swe.find.roots.count_skills", return_value=3), \
                    patch("swe.find.roots.load_registry_if_present", return_value={}):
                out = skills_roots(home, scope=SCOPE_ALL)
            self.assertEqual(out[0].detail, "3 skills, the shared tree")
            self.assertIn("not synced", out[1].detail)
            self.assertEqual(out[2].label, "synced roots")
            self.assertEqual(len(out[2].children), 1)

    def test_missing_shared_dir_and_archived_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".old" / "skills").mkdir(parents=True)
            nodes = [self._node("old", home / ".old" / "skills", state="keep")]
            reg = {"old": {"state": "archived", "command": "old"}}
            with patch("swe.find.roots.skills_tree",
                       return_value=(home / ".quiver" / "skills", nodes)), \
                    patch("swe.find.roots.count_skills", return_value=0), \
                    patch("swe.find.roots.load_registry_if_present", return_value=reg):
                out = skills_roots(home, scope=SCOPE_ALL)
            # shared dir does not exist; archived harness folds into the footer
            self.assertEqual(len(out), 1)
            self.assertIn("archived harness hidden", out[0].label)


class PluginsRootsTest(unittest.TestCase):
    def _plugin(self, name, harness="claude", marketplace="mp", version="1.0",
                enabled=True, path=None, components=None):
        return Plugin(harness=harness, name=name, marketplace=marketplace,
                      version=version, enabled=enabled, path=path,
                      components=components or {})

    def test_nests_harness_marketplace_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            pdir = home / "plugdir"
            pdir.mkdir()
            plugins = [
                self._plugin("alpha", path=pdir, components={"skill": 2, "agent": 1}),
                self._plugin("beta", marketplace="", version="", enabled=None),
            ]
            with patch("swe.find.roots.discover_plugins", return_value=plugins), \
                    patch("swe.find.roots.load_registry_if_present", return_value={}):
                out = plugins_roots(home, scope=SCOPE_ALL)
            self.assertEqual(len(out), 1)
            harness_row = out[0]
            self.assertEqual(harness_row.label, "claude")
            self.assertIn("2 plugins", harness_row.detail)
            # no-marketplace plugins group under the fallback bucket
            markets = {row.label: row for row in harness_row.children}
            alpha = markets["mp"].children[0]
            self.assertEqual(alpha.path, pdir)
            self.assertIn("1.0", alpha.detail)
            self.assertIn("2 skill", alpha.detail)

    def test_missing_plugin_dir_and_no_install_record_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            plugins = [self._plugin("ghost", enabled=None)]
            with patch("swe.find.roots.discover_plugins", return_value=plugins), \
                    patch("swe.find.roots.load_registry_if_present", return_value={}):
                out = plugins_roots(home, scope=SCOPE_ALL)
            plugin_row = out[0].children[0].children[0]
            self.assertIn("no directory on disk", plugin_row.detail)
            self.assertIn("no install record", out[0].detail)

    def test_archived_harness_hides_with_footer(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            reg = {"ghostcli": {"state": "archived", "command": "ghost"}}
            plugins = [self._plugin("x", harness="ghostcli")]
            with patch("swe.find.roots.discover_plugins", return_value=plugins), \
                    patch("swe.find.roots.load_registry_if_present", return_value=reg):
                out = plugins_roots(home, scope=SCOPE_ALL)
            self.assertEqual(len(out), 1)
            self.assertIn("archived harness hidden", out[0].label)


if __name__ == "__main__":
    unittest.main()
