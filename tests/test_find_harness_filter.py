"""`swe find --harness=active|all`: archived harnesses default to hidden.

Every `swe find` view enumerates harnesses somehow, and every one of them
should agree on which are worth showing by default — that agreement is
``quiver.find.roots.harness_filter``, built on top of the same
``harness.json`` state ``swe list`` already reads (see
tests/test_registry_state.py for the accessor this pins).

Two behaviours matter beyond the plain active/archived split:

* a row that cannot be mapped to a registry harness must never be filtered
  — hiding an unrecognised row would be exactly the silent hide this
  feature promises never to do.
* the mapping itself should prefer a harness's ``capabilities.*.root``
  over guessing from the directory name, because droid installs to
  ``~/.factory`` while the registry calls it "droid" — a plain directory
  scan derives the label "factory", which does not match the registry key
  until a capability root says the two are the same harness.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from quiver.find.plugins import discover_plugins
from quiver.find.roots import agents_roots, harness_footer_text, skills_roots
from quiver.harness import registry


class _RegistrySandbox(unittest.TestCase):
    """Point harness.json at a throwaway file, the same isolation
    tests/test_registry_state.py uses, so nothing here reads (or writes)
    the real machine's ~/.quiver/config/harness.json."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config_dir = self.home / ".quiver" / "config"
        self.harness_file = self.config_dir / "harness.json"

        patches = [
            mock.patch.object(registry, "CONFIG_DIR", self.config_dir),
            mock.patch.object(registry, "HARNESS_FILE", self.harness_file),
            mock.patch.object(registry, "TOOLS_FILE", self.config_dir / "tools.json"),
            mock.patch.object(registry, "STARS_FILE", self.config_dir / "stars.json"),
            mock.patch.object(registry, "ARCHIVE_FILE", self.config_dir / "archived.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _set_registry(self, reg: dict) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.harness_file.write_text(json.dumps(reg))

    def _agents_md(self, rel: str) -> None:
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("rules\n")

    def _skill(self, rel: str, name: str = "alpha") -> None:
        d = self.home / rel / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


class AgentsHarnessFilterTest(_RegistrySandbox):
    """agents_roots is the simplest view: INSTRUCTION_TARGETS already
    labels each row with the harness's canonical registry name."""

    def setUp(self):
        super().setUp()
        (self.home / ".quiver").mkdir(parents=True)
        (self.home / ".quiver" / "AGENTS.md").write_text("rules\n")
        self._agents_md(".claude/CLAUDE.md")
        self._agents_md(".codex/AGENTS.md")

    def test_archived_harness_hidden_by_default(self):
        self._set_registry({"claude": {"command": "claude", "state": "archived"},
                            "codex": {"command": "codex"}})
        labels = {e.detail.split(",")[0] for e in agents_roots(home=self.home)}
        self.assertNotIn("claude", labels)
        self.assertIn("codex", labels)

    def test_archived_harness_shown_with_harness_all(self):
        self._set_registry({"claude": {"command": "claude", "state": "archived"},
                            "codex": {"command": "codex"}})
        labels = {e.detail.split(",")[0] for e in agents_roots(home=self.home, harness="all")}
        self.assertIn("claude", labels)
        self.assertIn("codex", labels)

    def test_starred_counts_as_active(self):
        # Starred is still in daily rotation, just pinned — it must not be
        # swept up by the same filter that hides archived rows.
        self._set_registry({"claude": {"command": "claude", "state": "starred", "pin": 1},
                            "codex": {"command": "codex"}})
        labels = {e.detail.split(",")[0] for e in agents_roots(home=self.home)}
        self.assertIn("claude", labels)

    def test_footer_counts_the_archived_harnesses_hidden(self):
        self._set_registry({"claude": {"command": "claude", "state": "archived"},
                            "codex": {"command": "codex", "state": "archived"}})
        entries = agents_roots(home=self.home)
        footer = entries[-1]
        self.assertIsNone(footer.path)
        self.assertIn(harness_footer_text(2), footer.label)

    def test_no_footer_when_nothing_is_hidden(self):
        self._set_registry({"claude": {"command": "claude"},
                            "codex": {"command": "codex"}})
        entries = agents_roots(home=self.home)
        self.assertFalse(any("archived" in e.label for e in entries))

    def test_a_harness_absent_from_the_registry_is_never_filtered(self):
        # "codex" never appears in this registry at all — not archived,
        # simply unknown to it. It must stay visible under the default,
        # the same as the shared quiver copy that names no harness.
        self._set_registry({"claude": {"command": "claude", "state": "archived"}})
        labels = {e.detail.split(",")[0] for e in agents_roots(home=self.home)}
        self.assertIn("codex", labels)

    def test_an_empty_registry_hides_nothing(self):
        # No harness.json on disk at all: load_registry_if_present() must
        # return {} rather than seeding (and writing) the defaults — a
        # read-only `swe find` must not create ~/.quiver/config as a
        # side effect of being asked what is archived.
        labels = {e.detail.split(",")[0] for e in agents_roots(home=self.home)}
        self.assertIn("claude", labels)
        self.assertIn("codex", labels)
        self.assertFalse(self.harness_file.exists())


class SkillsCapabilityMappingTest(_RegistrySandbox):
    """droid installs to ~/.factory, so a bare directory scan derives the
    label "factory" — a mismatch with the registry key "droid" that only
    ``capabilities.skills.root`` can resolve."""

    def setUp(self):
        super().setUp()
        self._skill(".factory/skills")

    def test_capability_root_maps_the_directory_label_to_the_registry_name(self):
        self._set_registry({"droid": {
            "command": "droid", "state": "archived",
            "capabilities": {"skills": {"supported": True, "root": "~/.factory/skills"}},
        }})
        entries = skills_roots(home=self.home)
        shown = [e for e in entries if e.path is not None]
        self.assertFalse(any(self.home / ".factory" / "skills" == e.path for e in shown),
                         "droid's skills root should resolve via capabilities and hide")
        self.assertTrue(any("archived" in e.label for e in entries), "footer missing")

    def test_the_hidden_root_reappears_under_harness_all(self):
        self._set_registry({"droid": {
            "command": "droid", "state": "archived",
            "capabilities": {"skills": {"supported": True, "root": "~/.factory/skills"}},
        }})
        entries = skills_roots(home=self.home, harness="all")
        shown_paths = {e.path for e in entries if e.path is not None}
        self.assertIn(self.home / ".factory" / "skills", shown_paths)

    def test_without_a_capability_root_the_drift_is_not_fixed(self):
        # Same archived droid, but no capabilities this time: "factory" has
        # no way back to "droid", so the row is unknown and stays visible.
        # This is the baseline the capability-driven mapping improves on,
        # not a bug in the filter itself.
        self._set_registry({"droid": {"command": "droid", "state": "archived"}})
        entries = skills_roots(home=self.home)
        shown_paths = {e.path for e in entries if e.path is not None}
        self.assertIn(self.home / ".factory" / "skills", shown_paths)


class PluginCapabilitySetTest(_RegistrySandbox):
    """discover_plugins() decides which harnesses to walk from
    capabilities.plugins first, PLUGIN_FALLBACK only for a harness the
    registry has never heard of."""

    def _grok_cache(self) -> None:
        cache = self.home / ".grok" / "marketplace-cache" / "mk"
        plugin = cache / "demo-plugin"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "demo", "version": "1.0"}))

    def _opencode_plugin(self) -> None:
        d = self.home / ".opencode" / "plugins"
        d.mkdir(parents=True)
        (d / "hooks.ts").write_text("export default {}\n")

    def test_capabilities_put_opencode_in_and_grok_out(self):
        self._grok_cache()
        self._opencode_plugin()
        self._set_registry({
            "grok": {"command": "grok",
                    "capabilities": {"plugins": {"supported": False}}},
            "opencode": {"command": "opencode",
                        "capabilities": {"plugins": {"supported": True,
                                                     "root": "~/.opencode/plugins"}}},
        })
        found = discover_plugins(self.home)
        harnesses = {p.harness for p in found}
        self.assertIn("opencode", harnesses)
        self.assertNotIn("grok", harnesses)

    def test_fallback_scans_grok_and_skips_opencode_when_registry_is_empty(self):
        # No harness.json at all: PLUGIN_FALLBACK is the old hardcoded
        # five, which never included opencode, so discovery reverts to
        # exactly what it did before capabilities existed.
        self._grok_cache()
        self._opencode_plugin()
        found = discover_plugins(self.home)
        harnesses = {p.harness for p in found}
        self.assertIn("grok", harnesses)
        self.assertNotIn("opencode", harnesses)
        self.assertFalse(self.harness_file.exists())


class InlineHelpDocumentsFlagsTest(unittest.TestCase):
    def test_print_find_help_mentions_every_find_flag(self):
        # swe find has TWO help surfaces: help_text.py's topic (swe find
        # --help) and print_find_help (swe find <view> help). The --harness
        # flag shipped documented in the first and absent from the second,
        # and nothing caught it until a person did. Capture the inline help
        # and require each user-facing find flag to appear in it.
        import contextlib
        import io

        from quiver.find.commands import print_find_help

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_find_help()
        text = buf.getvalue()
        for flag in ("--scope", "--harness", "--interactive", "--root"):
            self.assertIn(flag, text, msg=f"{flag} missing from print_find_help")


if __name__ == "__main__":
    unittest.main()
