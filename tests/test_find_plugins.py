"""`swe find plugins` reads five different registry formats.

claude and factory keep an installed_plugins.json, codex declares
[plugins."ref"] blocks with an explicit enabled flag, cursor and grok expose
no install record at all. The scope words carry over from the rest of
swe find, but a plugin has no file sitting in a harness root, so:

  global   installed AND enabled
  local    installed but disabled
  all      every manifest on disk, cached-but-uninstalled included
"""

import json
import tempfile
import unittest
from pathlib import Path

from quiver.find.plugins import (
    count_components,
    discover_plugins,
    filter_plugins,
)


def _plugin(root: Path, name: str, skills=(), commands=(), version="1.0.0"):
    (root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}))
    for s in skills:
        d = root / "skills" / s
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {s}\n---\n")
    for cmd in commands:
        d = root / "commands"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cmd}.md").write_text("cmd\n")


def _claude_home(tmp: str, enabled: dict, installs: dict) -> Path:
    home = Path(tmp)
    pl = home / ".claude" / "plugins"
    pl.mkdir(parents=True)
    (pl / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": installs}))
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"enabledPlugins": enabled}))
    return home


class ComponentCountTest(unittest.TestCase):
    def test_counts_each_component_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            _plugin(root, "p", skills=("a", "b"), commands=("go",))
            self.assertEqual(count_components(root), {"skills": 2, "commands": 1})

    def test_counts_skills_nested_deeper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            (root / "skills" / "group" / "deep").mkdir(parents=True)
            (root / "skills" / "group" / "deep" / "SKILL.md").write_text("x")
            self.assertEqual(count_components(root), {"skills": 1})

    def test_falls_back_to_the_marketplace_source(self):
        # A Directory-source marketplace installs by copying, and a copy of a
        # symlinked skill lands as an empty directory. The installed lazyweb
        # plugin has zero SKILL.md while Claude reports seven.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "p"
            (cache / "skills" / "hollow").mkdir(parents=True)   # no SKILL.md
            source = Path(tmp) / "src" / "p"
            _plugin(source, "p", skills=("real",))
            self.assertEqual(count_components(cache), {})
            self.assertEqual(count_components(cache, source), {"skills": 1})

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(count_components(None), {})
        self.assertEqual(count_components(Path("/nope/nothing")), {})


class RegistryReadTest(unittest.TestCase):
    def test_reads_claude_installs_and_enabled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _claude_home(
                tmp,
                enabled={"a@dv": True, "b@dv": False},
                installs={"a@dv": [{"version": "1.0", "installPath": tmp + "/x"}],
                          "b@dv": [{"version": "2.0", "installPath": tmp + "/y"}]},
            )
            got = {p.ref: p for p in discover_plugins(home)}
            self.assertEqual(got["a@dv"].enabled, True)
            self.assertEqual(got["b@dv"].enabled, False)
            self.assertEqual(got["a@dv"].harness, "claude")
            self.assertEqual(got["a@dv"].version, "1.0")

    def test_reads_codex_toml_enabled_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            (home / ".codex" / "config.toml").write_text(
                '[plugins."on@mk"]\nenabled = true\n\n'
                '[plugins."off@mk"]\nenabled = false\n')
            got = {p.ref: p.enabled for p in discover_plugins(home)}
            self.assertEqual(got, {"on@mk": True, "off@mk": False})

    def test_a_harness_with_no_registry_is_skipped_quietly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(discover_plugins(Path(tmp)), [])

    def test_cached_plugins_report_unknown_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            cache = home / ".cursor" / "plugins" / "cache" / "mk" / "p"
            _plugin(cache, "p", skills=("s",))
            got = discover_plugins(home)
            self.assertEqual(len(got), 1)
            self.assertIsNone(got[0].enabled)
            self.assertEqual(got[0].harness, "cursor")


class ScopeTest(unittest.TestCase):
    def _mixed(self):
        from quiver.find.plugins import Plugin
        return [
            Plugin("claude", "on", "dv", enabled=True),
            Plugin("claude", "off", "dv", enabled=False),
            Plugin("cursor", "cached", "mk", enabled=None),
        ]

    def test_global_is_only_confirmed_enabled(self):
        # Unknown state must not count as enabled: cursor and grok cannot say,
        # and treating cached copies as running overstates what is loaded.
        shown, hidden = filter_plugins(self._mixed(), "global")
        self.assertEqual([p.name for p in shown], ["on"])
        self.assertEqual(hidden, 2)

    def test_local_is_installed_but_disabled(self):
        shown, _ = filter_plugins(self._mixed(), "local")
        self.assertEqual([p.name for p in shown], ["off"])

    def test_all_hides_nothing(self):
        shown, hidden = filter_plugins(self._mixed(), "all")
        self.assertEqual(len(shown), 3)
        self.assertEqual(hidden, 0)

    def test_ref_joins_name_and_marketplace(self):
        from quiver.find.plugins import Plugin
        self.assertEqual(Plugin("h", "n", "mk").ref, "n@mk")
        self.assertEqual(Plugin("h", "n", "").ref, "n")


if __name__ == "__main__":
    unittest.main()
