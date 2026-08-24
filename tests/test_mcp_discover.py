import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.mcp.discover import apply_mcp_findings, discover_mcp_servers


def _registry_patches(config_dir: Path, registry_file: Path, mcp_file: Path):
    return (
        patch("quiver.harness.registry.CONFIG_DIR", config_dir),
        patch("quiver.harness.registry.HARNESS_FILE", registry_file),
        patch("quiver.harness.registry.TOOLS_FILE", config_dir / "tools.json"),
        patch("quiver.paths.CONFIG_DIR", config_dir),
        patch("quiver.paths.MCP_SOURCE_FILE", mcp_file),
        patch("quiver.mcp.discover.MCP_SOURCE_FILE", mcp_file),
    )


class McpDiscoverTest(unittest.TestCase):
    def test_discovers_servers_not_in_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".quiver" / "config"
            config_dir.mkdir(parents=True)
            registry_file = config_dir / "harness.json"
            mcp_file = config_dir / "mcp.json"
            registry_file.write_text(
                json.dumps({"opencode": {"aliases": ["oc"]}, "claude": {"aliases": ["cc"]}})
            )
            mcp_file.write_text(json.dumps({"mcpServers": {}}, indent=2))

            opencode_cfg = tmp_path / ".config" / "opencode"
            opencode_cfg.mkdir(parents=True)
            opencode_json = opencode_cfg / "opencode.json"
            opencode_json.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "notion": {
                                "command": ["node", "/tmp/notion.js"],
                                "environment": {},
                                "enabled": True,
                                "type": "local",
                            }
                        }
                    }
                )
            )
            claude_json = tmp_path / ".claude.json"
            claude_json.write_text(json.dumps({"mcpServers": {}}, indent=2))

            mcp_map = {
                "opencode": {
                    "path": opencode_json,
                    "key": "mcp",
                    "label": "opencode",
                    "format": "opencode",
                },
                "claude": {
                    "path": claude_json,
                    "key": "mcpServers",
                    "label": "Claude Code",
                },
            }

            p1, p2, p3, p4, p5, p6 = _registry_patches(config_dir, registry_file, mcp_file)
            with p1, p2, p3, p4, p5, p6, patch(
                "quiver.mcp.cli.MCP_CONFIG_MAP", mcp_map
            ), patch("quiver.mcp.cli.get_mcp_tools") as mock_tools:
                mock_tools.return_value = {"opencode": mcp_map["opencode"], "claude": mcp_map["claude"]}
                findings = discover_mcp_servers()
                notion = [f for f in findings if f.name == "notion"]
                self.assertEqual(len(notion), 1)
                self.assertEqual(notion[0].status, "new")
                self.assertIn("opencode", notion[0].tools)

    def test_apply_writes_to_mcp_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".quiver" / "config"
            config_dir.mkdir(parents=True)
            registry_file = config_dir / "harness.json"
            mcp_file = config_dir / "mcp.json"
            registry_file.write_text(json.dumps({"opencode": {"aliases": ["oc"]}}))
            mcp_file.write_text(json.dumps({"mcpServers": {}}, indent=2))

            opencode_cfg = tmp_path / ".config" / "opencode"
            opencode_cfg.mkdir(parents=True)
            opencode_json = opencode_cfg / "opencode.json"
            opencode_json.write_text(
                json.dumps(
                    {
                        "mcp": {
                            "linear": {
                                "command": ["npx", "mcp-remote", "https://example.com"],
                                "enabled": True,
                                "type": "local",
                            }
                        }
                    }
                )
            )

            mcp_map = {
                "opencode": {
                    "path": opencode_json,
                    "key": "mcp",
                    "label": "opencode",
                    "format": "opencode",
                },
            }

            p1, p2, p3, p4, p5, p6 = _registry_patches(config_dir, registry_file, mcp_file)
            with p1, p2, p3, p4, p5, p6, patch(
                "quiver.mcp.cli.MCP_CONFIG_MAP", mcp_map
            ), patch("quiver.mcp.cli.get_mcp_tools") as mock_tools:
                mock_tools.return_value = {"opencode": mcp_map["opencode"]}
                findings = discover_mcp_servers()
                added = apply_mcp_findings(findings).added
                self.assertIn("linear", added)
                data = json.loads(mcp_file.read_text())
                self.assertIn("linear", data["mcpServers"])


if __name__ == "__main__":
    unittest.main()


class ThreeWayMergeTest(unittest.TestCase):
    """The hub must track harness edits, not just grow.

    The original merge skipped anything already present by name, so a rotated
    token in a harness config never reached the hub, and a later
    `sync quiver --all` would push the stale value back out over it.
    """

    HARNESS = {
        "keeps_same": {"command": "a"},
        "gets_edited": {"command": "NEW"},
        "brand_new": {"command": "fresh"},
    }
    HUB = {
        "keeps_same": {"command": "a"},
        "gets_edited": {"command": "OLD"},
        "no_longer_anywhere": {"command": "ghost"},
    }

    def _sandbox(self, stack, hub=None):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from quiver.mcp import discover as D

        tmp = stack.enter_context(tempfile.TemporaryDirectory())
        path = Path(tmp) / "mcp.json"
        path.write_text(json.dumps(
            {"mcpServers": hub if hub is not None else self.HUB,
             "updated": "2020-01-01T00:00:00"}))
        for target, kwargs in (
            ("MCP_SOURCE_FILE", {"new": path}),
            ("get_mcp_tools", {"return_value": ["claude"]}),
            ("load_registry", {"return_value": {}}),
            ("get_tool_servers_canonical", {"return_value": self.HARNESS}),
            ("redact_secrets", {"side_effect": lambda x: x}),
            # discover also walks the disk now. These tests are about the
            # three-way merge, so the walk is stubbed: leaving it live made
            # the result depend on what happens to be installed.
            ("_scanned_servers", {"return_value": {}}),
        ):
            stack.enter_context(patch.object(D, target, **kwargs))
        return D, path

    def test_classifies_new_changed_same_and_orphaned(self):
        import contextlib

        with contextlib.ExitStack() as stack:
            D, _ = self._sandbox(stack)
            got = {f.name: f.status
                   for f in D.discover_mcp_servers(include_in_source=True)}
        self.assertEqual(got, {
            "brand_new": "new",
            "gets_edited": "changed",
            "keeps_same": "in_source",
            "no_longer_anywhere": "orphaned",
        })

    def test_in_source_is_hidden_unless_asked_for(self):
        import contextlib

        with contextlib.ExitStack() as stack:
            D, _ = self._sandbox(stack)
            names = {f.name for f in D.discover_mcp_servers()}
        self.assertNotIn("keeps_same", names)
        self.assertIn("gets_edited", names)

    def test_merge_adds_and_updates(self):
        import contextlib
        import json

        with contextlib.ExitStack() as stack:
            D, path = self._sandbox(stack)
            res = D.apply_mcp_findings(D.discover_mcp_servers(include_in_source=True))
            hub = json.loads(path.read_text())["mcpServers"]

        self.assertEqual(res.added, ["brand_new"])
        self.assertEqual(res.updated, ["gets_edited"])
        # The whole point: the harness edit reached the hub.
        self.assertEqual(hub["gets_edited"]["command"], "NEW")

    def test_orphans_are_reported_but_kept(self):
        import contextlib
        import json

        with contextlib.ExitStack() as stack:
            D, path = self._sandbox(stack)
            res = D.apply_mcp_findings(D.discover_mcp_servers(include_in_source=True))
            hub = json.loads(path.read_text())["mcpServers"]

        self.assertEqual(res.orphaned, ["no_longer_anywhere"])
        self.assertEqual(res.pruned, [])
        # An unreadable harness config looks the same as a deletion, and the
        # hub entry carries the credentials, so removal must be deliberate.
        self.assertIn("no_longer_anywhere", hub)

    def test_prune_removes_orphans(self):
        import contextlib
        import json

        with contextlib.ExitStack() as stack:
            D, path = self._sandbox(stack)
            res = D.apply_mcp_findings(
                D.discover_mcp_servers(include_in_source=True), prune=True)
            hub = json.loads(path.read_text())["mcpServers"]

        self.assertEqual(res.pruned, ["no_longer_anywhere"])
        self.assertNotIn("no_longer_anywhere", hub)

    def test_updated_timestamp_is_refreshed_not_only_seeded(self):
        import contextlib
        import json

        with contextlib.ExitStack() as stack:
            D, path = self._sandbox(stack)
            D.apply_mcp_findings(D.discover_mcp_servers(include_in_source=True))
            stamp = json.loads(path.read_text())["updated"]
        # setdefault only ever stamped the first write, so the file claimed to
        # be years old while its contents had just changed.
        self.assertNotEqual(stamp, "2020-01-01T00:00:00")

    def test_no_write_when_nothing_differs(self):
        import contextlib

        with contextlib.ExitStack() as stack:
            D, path = self._sandbox(stack, hub=dict(self.HARNESS))
            before = path.read_text()
            res = D.apply_mcp_findings(D.discover_mcp_servers(include_in_source=True))
            self.assertFalse(res.wrote)
            self.assertEqual(path.read_text(), before)


class DiscoverReadsTheDiskTest(unittest.TestCase):
    """discover enumerated only the config paths quiver had registered.

    That is a clean bill of health for anything nobody registered, which is
    exactly where an unmanaged server hides. Here it was four config files
    holding three unknown servers, including a whole harness (LM Studio)
    with no registry entry at all.
    """

    def _run(self, stack, scanned, hub, harness=None):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from quiver.mcp import discover as D

        tmp = stack.enter_context(tempfile.TemporaryDirectory())
        path = Path(tmp) / "mcp.json"
        path.write_text(json.dumps({"mcpServers": hub}))
        for target, kwargs in (
            ("MCP_SOURCE_FILE", {"new": path}),
            ("get_mcp_tools", {"return_value": ["claude"]}),
            ("load_registry", {"return_value": {}}),
            ("get_tool_servers_canonical", {"return_value": harness or {}}),
            ("redact_secrets", {"side_effect": lambda x: x}),
            ("_scanned_servers", {"return_value": scanned}),
        ):
            stack.enter_context(patch.object(D, target, **kwargs))
        return D.discover_mcp_servers(include_in_source=True)

    def test_a_scanned_server_absent_from_the_hub_is_reported_new(self):
        import contextlib

        with contextlib.ExitStack() as stack:
            found = self._run(
                stack,
                scanned={"exa": {"server": {"url": "https://x"}, "tool": "lmstudio"}},
                hub={},
            )
        self.assertEqual([(f.name, f.status) for f in found], [("exa", "new")])

    def test_it_names_the_harness_the_config_belonged_to(self):
        import contextlib

        with contextlib.ExitStack() as stack:
            found = self._run(
                stack,
                scanned={"exa": {"server": {"url": "https://x"}, "tool": "lmstudio"}},
                hub={},
            )
        self.assertEqual(found[0].tools, ("lmstudio",))
        self.assertEqual(found[0].source_tool, "lmstudio")

    def test_a_scanned_server_already_in_the_hub_is_not_new(self):
        import contextlib

        server = {"url": "https://x"}
        with contextlib.ExitStack() as stack:
            found = self._run(
                stack,
                scanned={"exa": {"server": dict(server), "tool": "lmstudio"}},
                hub={"exa": server},
            )
        self.assertEqual(found[0].status, "in_source")

    def test_a_registered_config_wins_over_a_scanned_one(self):
        """The registered read is canonicalised through the tool's format
        handler, so it is the better copy when both see a server."""
        import contextlib

        with contextlib.ExitStack() as stack:
            found = self._run(
                stack,
                scanned={"dup": {"server": {"command": "scanned"}, "tool": "other"}},
                hub={},
                harness={"dup": {"command": "registered"}},
            )
        entry = next(f for f in found if f.name == "dup")
        self.assertEqual(entry.server["command"], "registered")
        self.assertIn("other", entry.tools)

    def test_a_scanned_server_no_longer_counts_as_orphaned(self):
        """An orphan is a hub server no config declares. One found only by
        the scan is still declared, so reporting it as orphaned would
        invite a --prune that deletes a live server."""
        import contextlib

        with contextlib.ExitStack() as stack:
            found = self._run(
                stack,
                scanned={"only_scanned": {"server": {"command": "x"}, "tool": "t"}},
                hub={"only_scanned": {"command": "x"}},
            )
        self.assertNotIn("orphaned", [f.status for f in found])


class ScannedServersAreRedactedTest(unittest.TestCase):
    """Scanned entries are raw config, so they take the same redact path.

    Skipping it would write live credentials into a versioned file.
    """

    def test_a_literal_credential_is_swapped_for_a_reference(self):
        import contextlib
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from quiver.mcp import discover as D

        with contextlib.ExitStack() as stack:
            tmp = stack.enter_context(tempfile.TemporaryDirectory())
            path = Path(tmp) / "mcp.json"
            path.write_text(json.dumps({"mcpServers": {}}))
            for target, kwargs in (
                ("MCP_SOURCE_FILE", {"new": path}),
                ("get_mcp_tools", {"return_value": []}),
                ("load_registry", {"return_value": {}}),
                ("_scanned_servers", {"return_value": {
                    "s": {"server": {"url": "https://x",
                                     "headers": {"Authorization": "sk-live-SECRET"}},
                          "tool": "t"}}}),
            ):
                stack.enter_context(patch.object(D, target, **kwargs))
            stack.enter_context(patch(
                "quiver.mcp.secrets.load_secrets",
                return_value={"MY_KEY": "sk-live-SECRET"}))
            found = D.discover_mcp_servers()

        blob = json.dumps(found[0].server)
        self.assertNotIn("sk-live-SECRET", blob, "credential would reach the hub")
        self.assertIn("${MY_KEY}", blob)


class VersionPinnedTest(unittest.TestCase):
    """A config can sit somewhere ordinary while launching from a versioned
    extension directory, which the next upgrade deletes."""

    def test_a_versioned_extension_path_is_flagged(self):
        from quiver.find.mcps import version_pinned

        server = {"command": "node",
                  "args": ["/h/.ide/extensions/vendor.thing-0.7.2/cli/bundle.js"]}
        self.assertTrue(version_pinned(server))

    def test_an_ordinary_command_is_not_flagged(self):
        from quiver.find.mcps import version_pinned

        self.assertEqual(version_pinned({"command": "npx", "args": ["-y", "pkg"]}), "")

    def test_a_remote_server_is_not_flagged(self):
        from quiver.find.mcps import version_pinned

        self.assertEqual(version_pinned({"url": "https://x/mcp"}), "")

    def test_a_version_outside_a_vendored_directory_is_not_flagged(self):
        from quiver.find.mcps import version_pinned

        self.assertEqual(
            version_pinned({"command": "/opt/tool-1.2.3/bin/run"}), "")

    def test_a_malformed_entry_does_not_raise(self):
        from quiver.find.mcps import version_pinned

        self.assertEqual(version_pinned(None), "")
        self.assertEqual(version_pinned({"args": [None, 3]}), "")
