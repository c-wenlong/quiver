import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe.mcp.discover import apply_mcp_findings, discover_mcp_servers


def _registry_patches(config_dir: Path, registry_file: Path, mcp_file: Path):
    return (
        patch("swe.harness.registry.CONFIG_DIR", config_dir),
        patch("swe.harness.registry.HARNESS_FILE", registry_file),
        patch("swe.harness.registry.TOOLS_FILE", config_dir / "tools.json"),
        patch("swe.paths.CONFIG_DIR", config_dir),
        patch("swe.paths.MCP_SOURCE_FILE", mcp_file),
        patch("swe.mcp.discover.MCP_SOURCE_FILE", mcp_file),
    )


class McpDiscoverTest(unittest.TestCase):
    def test_discovers_servers_not_in_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".swe" / "config"
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
                "swe.mcp.cli.MCP_CONFIG_MAP", mcp_map
            ), patch("swe.mcp.discover.get_mcp_tools") as mock_tools:
                # discover_mcp_servers calls get_mcp_tools through its own
                # module binding, so the patch lives on mcp.discover.
                mock_tools.return_value = {"opencode": mcp_map["opencode"], "claude": mcp_map["claude"]}
                findings = discover_mcp_servers()
                notion = [f for f in findings if f.name == "notion"]
                self.assertEqual(len(notion), 1)
                self.assertEqual(notion[0].status, "new")
                self.assertIn("opencode", notion[0].tools)

    def test_apply_writes_to_mcp_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_dir = tmp_path / ".swe" / "config"
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
                "swe.mcp.cli.MCP_CONFIG_MAP", mcp_map
            ), patch("swe.mcp.discover.get_mcp_tools") as mock_tools:
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

        from swe.mcp import discover as D

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

    def test_gateway_settings_in_a_serverless_hub_are_not_a_server(self):
        import contextlib
        import json

        with contextlib.ExitStack() as stack:
            D, path = self._sandbox(stack)
            # What `swe mcp gateway install` leaves in a hub with no servers.
            path.write_text(json.dumps({
                "gateway": {"enabled": True, "port": 8478},
                "updated": "2020-01-01T00:00:00"}))
            findings = D.discover_mcp_servers(include_in_source=True)
            D.apply_mcp_findings(findings)
            hub = json.loads(path.read_text())

        self.assertNotIn("gateway", {f.name for f in findings})
        self.assertNotIn("gateway", hub["mcpServers"])
        self.assertEqual(hub["gateway"], {"enabled": True, "port": 8478})

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

        from swe.mcp import discover as D

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


class ScannedServersTest(unittest.TestCase):
    """_scanned_servers must not report the hub file itself as an
    unmanaged config: after the ~/.quiver -> ~/.swe move the hub can show
    up under either dir name, so the skip is by resolved path."""

    def _scan(self, configs, mcp_file):
        import contextlib
        from unittest.mock import patch

        from swe.mcp import discover as D

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(D, "MCP_SOURCE_FILE", mcp_file))
            stack.enter_context(
                patch("swe.find.mcps.scan_configs", return_value=configs))
            return D._scanned_servers({})

    def test_the_hub_file_is_skipped_by_path_and_name(self):
        import tempfile
        from pathlib import Path

        from swe.find.mcps import FoundConfig

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            mcp_file = home / ".swe" / "mcp.json"
            mcp_file.parent.mkdir()
            mcp_file.write_text('{"mcpServers": {}}')
            configs = [
                # The hub scanned at its real path.
                FoundConfig(path=mcp_file,
                            servers={"hub_srv": {"command": "x"}},
                            harness="swe"),
                # The hub seen through the compat symlink's name.
                FoundConfig(path=home / ".quiver" / "mcp.json",
                            servers={"old_srv": {"command": "x"}},
                            harness="quiver"),
                # A dir merely named .swe elsewhere is also not a finding.
                FoundConfig(path=home / ".config" / "swe" / "mcp.json",
                            servers={"name_srv": {"command": "x"}},
                            harness="swe"),
                FoundConfig(path=home / ".cursor" / "mcp.json",
                            servers={"stray": {"command": "cur"}},
                            harness="cursor"),
            ]
            out = self._scan(configs, mcp_file)
        self.assertEqual(sorted(out), ["stray"])
        self.assertEqual(out["stray"]["tool"], "cursor")

    def test_an_unresolvable_path_is_skipped(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import MagicMock

        from swe.find.mcps import FoundConfig

        with tempfile.TemporaryDirectory() as tmp:
            mcp_file = Path(tmp) / "mcp.json"
            dead = MagicMock()
            dead.resolve.side_effect = OSError("gone")
            bad = MagicMock()
            # First resolve (registered-path check) answers, second fails.
            bad.resolve.side_effect = [Path("/elsewhere"), OSError("gone")]
            configs = [FoundConfig(path=dead,
                                   servers={"d": {"command": "x"}},
                                   harness="dead"),
                       FoundConfig(path=bad,
                                   servers={"x": {"command": "x"}},
                                   harness="x"),
                       FoundConfig(path=Path(tmp) / "mcp2.json",
                                   servers={"y": {"command": "y"},
                                            "weird": "not-a-dict"},
                                   harness="y")]
            out = self._scan(configs, mcp_file)
        self.assertEqual(sorted(out), ["y"])


class ShieldCredentialsTest(unittest.TestCase):
    """A literal credential in a harness config must never reach mcp.json.

    redact() only substitutes store values, so `env.API_KEY = "sk-live"`
    — the common case — landed in the versioned hub in plaintext. Apply
    now moves credential-shaped fields into the store as ${NAME} refs.
    """

    def _hub(self, stack):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from swe.mcp import discover as D

        tmp = stack.enter_context(tempfile.TemporaryDirectory())
        home = Path(tmp)
        path = home / ".swe" / "mcp.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"mcpServers": {}}))
        stack.enter_context(patch.object(D, "MCP_SOURCE_FILE", path))
        # set_secret resolves Path.home() — point it at the fixture home so
        # the store lands beside the hub file under test.
        import os

        stack.enter_context(patch.dict(os.environ, {"HOME": str(home)}))
        return home, path

    def test_env_credential_moves_to_the_store_as_a_reference(self):
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets, resolve

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            res = apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"command": "npx",
                            "env": {"API_KEY": "sk-live-token-12345",
                                    "NODE_ENV": "production"}},
                )])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            self.assertEqual(res.added, ["svc"])
            self.assertNotIn("sk-live-token-12345", json.dumps(hub))
            self.assertEqual(hub["env"]["API_KEY"], "${API_KEY}")
            # Ordinary env is not credential-shaped and stays literal.
            self.assertEqual(hub["env"]["NODE_ENV"], "production")
            self.assertEqual(load_secrets(home)["API_KEY"], "sk-live-token-12345")
            # Shielding is output-equivalent: resolve() reproduces it.
            self.assertEqual(resolve(hub["env"], home=home)["API_KEY"],
                             "sk-live-token-12345")

    def test_header_scheme_and_url_query_credentials_are_shielded(self):
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets, resolve

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="remote", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"url": "https://h/mcp?key=TOKEN123&x=1",
                            "headers": {"Authorization": "Bearer head-tok-987"}})])
            hub = json.loads(path.read_text())["mcpServers"]["remote"]
            blob = json.dumps(hub)
            self.assertNotIn("TOKEN123", blob)
            self.assertNotIn("head-tok-987", blob)
            self.assertIn("key=${KEY}", hub["url"])
            self.assertIn("${AUTHORIZATION}", hub["headers"]["Authorization"])
            resolved = resolve(hub, home=home)
            self.assertEqual(resolved["url"], "https://h/mcp?key=TOKEN123&x=1")
            self.assertEqual(resolved["headers"]["Authorization"],
                             "Bearer head-tok-987")
            self.assertTrue(load_secrets(home))

    def test_existing_references_and_nonsecret_fields_are_untouched(self):
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings

        with contextlib.ExitStack() as stack:
            _home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"command": "npx",
                            "env": {"API_KEY": "${API_KEY}",
                                    "COLOR": "blue"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            self.assertEqual(hub["env"], {"API_KEY": "${API_KEY}", "COLOR": "blue"})

    def test_same_literal_reuses_the_existing_secret_name(self):
        # Two fields carrying the same token share one store entry rather
        # than minting NAME and NAME_2.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"url": "https://h/mcp?key=same-token-value",
                            "env": {"API_KEY": "same-token-value"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            self.assertEqual(hub["env"]["API_KEY"], "${API_KEY}")
            self.assertIn("key=${API_KEY}", hub["url"])
            self.assertEqual(load_secrets(home)["API_KEY"], "same-token-value")

    def test_malformed_url_and_bare_query_pairs_do_not_crash_shielding(self):
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings

        with contextlib.ExitStack() as stack:
            _home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    # A query pair with no `=` and a non-str header value
                    # used to be uncovered edge cases.
                    server={"url": "https://h/mcp?key=&flag&token=TOKEN9",
                            "headers": {"X-Api-Key": 42,
                                        "Authorization": "Bearer ${AUTH}"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            self.assertIn("token=${TOKEN}", hub["url"])
            self.assertIn("flag", hub["url"])
            self.assertEqual(hub["headers"]["X-Api-Key"], 42)
            # A scheme-prefixed value whose token is already a ref is left.
            self.assertEqual(hub["headers"]["Authorization"], "Bearer ${AUTH}")

    def test_a_secret_name_collision_gets_a_suffix(self):
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets, set_secret

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            set_secret("API_KEY", "other-value", home)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"env": {"API_KEY": "new-different-value"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            self.assertEqual(hub["env"]["API_KEY"], "${API_KEY_2}")
            self.assertEqual(load_secrets(home)["API_KEY_2"], "new-different-value")
            self.assertEqual(load_secrets(home)["API_KEY"], "other-value")

    def test_a_value_embedding_a_reference_is_left_alone(self):
        # redact() can hand discover "prefix-${TOKEN}"; storing that whole
        # string under a new name would leave ${TOKEN} unexpanded — resolve
        # substitutes once.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings

        with contextlib.ExitStack() as stack:
            _home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"env": {"API_KEY": "prefix-${TOKEN}"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            self.assertEqual(hub["env"]["API_KEY"], "prefix-${TOKEN}")

    def test_an_unstorable_credential_skips_the_server_not_the_run(self):
        # A multiline value (a PEM block) can't be a secrets-file line:
        # the rest of the findings still apply, this one is reported and
        # kept out of the hub rather than written half-shielded.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            res = apply_mcp_findings([
                McpFinding(
                    name="bad", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"env": {"API_KEY": "-----BEGIN-----\nline2\n-----END-----"}}),
                McpFinding(
                    name="good", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"command": "npx", "env": {}})])
            servers = json.loads(path.read_text())["mcpServers"]
            self.assertIn("good", servers)
            self.assertNotIn("bad", servers)
            self.assertEqual(res.added, ["good"])
            self.assertEqual(load_secrets(home), {})

    def test_a_literal_next_to_a_reference_is_still_shielded(self):
        # `session=${SESSION}; refresh=literal-secret` has a reference in
        # it, but the refresh literal would still land in versioned
        # mcp.json if the whole value were skipped. The reference stays,
        # the literal moves into the store.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets, resolve

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"headers": {
                        "Cookie": "session=${SESSION}; "
                                  "refresh=literal-secret-99"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            cookie = hub["headers"]["Cookie"]
            self.assertNotIn("literal-secret-99", cookie)
            self.assertIn("session=${SESSION}", cookie)
            self.assertIn("refresh=${", cookie)
            resolved = resolve(hub, home=home)
            # ${SESSION} is not in the store, so it passes through; the
            # stored refresh literal resolves back to the real value.
            self.assertEqual(
                resolved["headers"]["Cookie"],
                "session=${SESSION}; refresh=literal-secret-99")
            self.assertIn("literal-secret-99", load_secrets(home).values())

    def test_a_stored_literal_cannot_steal_a_referenced_name(self):
        # `session=${SESSION}; session=other-token`: storing the literal
        # as SESSION would rebind the existing reference to the new value.
        # The literal must land under a different name.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets, resolve

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"headers": {
                        "Cookie": "session=${SESSION}; "
                                  "session=other-token-7"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            cookie = hub["headers"]["Cookie"]
            self.assertIn("session=${SESSION}", cookie)
            self.assertIn("session=${SESSION_2}", cookie)
            self.assertNotIn("other-token-7", cookie)
            self.assertEqual(load_secrets(home)["SESSION_2"], "other-token-7")
            resolved = resolve(hub, home=home)
            self.assertEqual(
                resolved["headers"]["Cookie"],
                "session=${SESSION}; session=other-token-7")

    def test_an_env_segment_literal_next_to_a_reference_is_stored(self):
        # Same rule inside env: `a=${T}; b=literal` keeps the reference
        # and stores only the b literal.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings
        from swe.mcp.secrets import load_secrets

        with contextlib.ExitStack() as stack:
            home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"env": {"API_KEY": "a=${T}; b=literal-9x"}})])
            hub = json.loads(path.read_text())["mcpServers"]["svc"]
            value = hub["env"]["API_KEY"]
            self.assertNotIn("literal-9x", value)
            self.assertIn("a=${T}", value)
            self.assertIn("b=${", value)
            self.assertIn("literal-9x", load_secrets(home).values())

    def test_an_unstorable_segment_literal_skips_the_server(self):
        # A k=v segment whose literal can't be a secrets-file line blocks
        # the server rather than writing it half-shielded.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings

        with contextlib.ExitStack() as stack:
            _home, path = self._hub(stack)
            apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"env": {"API_KEY": "a=${T}; key=line1\nline2"}})])
            servers = json.loads(path.read_text())["mcpServers"]
            self.assertNotIn("svc", servers)

    def test_an_opaque_literal_next_to_a_reference_skips_the_server(self):
        # A credential-shaped run that is not a k=v segment (no safe way
        # to isolate it) keeps the whole server out of the hub.
        import contextlib
        import json

        from swe.mcp.discover import McpFinding, apply_mcp_findings

        with contextlib.ExitStack() as stack:
            _home, path = self._hub(stack)
            res = apply_mcp_findings([
                McpFinding(
                    name="svc", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"headers": {
                        "Authorization":
                            "Bearer ${TOKEN} abc123def456ghi789jkl012"}}),
                McpFinding(
                    name="ok", tools=("claude",), status="new",
                    source_tool="claude",
                    server={"command": "npx", "env": {}})])
            servers = json.loads(path.read_text())["mcpServers"]
            self.assertNotIn("svc", servers)
            self.assertIn("ok", servers)
            self.assertEqual(res.added, ["ok"])


class ServerSummaryScrubTest(unittest.TestCase):
    """discover --json must obey the same secrecy contract as list --json."""

    def test_credential_bearing_url_is_masked(self):
        from swe.mcp.cli import server_summary

        out = server_summary({"url": "https://h/mcp?key=TOKEN123&x=1"})
        self.assertNotIn("TOKEN123", out)
        self.assertIn("key=***", out)

    def test_store_backed_url_still_shows_its_reference(self):
        from unittest.mock import patch as _patch

        from swe.mcp.cli import server_summary

        with _patch("swe.mcp.secrets.load_secrets",
                    return_value={"K": "TOKEN12345678901234567890"}):
            out = server_summary({"url": "https://h/mcp?key=TOKEN12345678901234567890"})
        self.assertIn("${K}", out)
        self.assertNotIn("TOKEN12345678901234567890", out)


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

        from swe.mcp import discover as D

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
                "swe.mcp.secrets.load_secrets",
                return_value={"MY_KEY": "sk-live-SECRET"}))
            found = D.discover_mcp_servers()

        blob = json.dumps(found[0].server)
        self.assertNotIn("sk-live-SECRET", blob, "credential would reach the hub")
        self.assertIn("${MY_KEY}", blob)


class VersionPinnedTest(unittest.TestCase):
    """A config can sit somewhere ordinary while launching from a versioned
    extension directory, which the next upgrade deletes."""

    def test_a_versioned_extension_path_is_flagged(self):
        from swe.find.mcps import version_pinned

        server = {"command": "node",
                  "args": ["/h/.ide/extensions/vendor.thing-0.7.2/cli/bundle.js"]}
        self.assertTrue(version_pinned(server))

    def test_an_ordinary_command_is_not_flagged(self):
        from swe.find.mcps import version_pinned

        self.assertEqual(version_pinned({"command": "npx", "args": ["-y", "pkg"]}), "")

    def test_a_remote_server_is_not_flagged(self):
        from swe.find.mcps import version_pinned

        self.assertEqual(version_pinned({"url": "https://x/mcp"}), "")

    def test_a_version_outside_a_vendored_directory_is_not_flagged(self):
        from swe.find.mcps import version_pinned

        self.assertEqual(
            version_pinned({"command": "/opt/tool-1.2.3/bin/run"}), "")

    def test_a_malformed_entry_does_not_raise(self):
        from swe.find.mcps import version_pinned

        self.assertEqual(version_pinned(None), "")
        self.assertEqual(version_pinned({"args": [None, 3]}), "")
