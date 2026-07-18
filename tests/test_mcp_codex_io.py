"""Tests for ``quiver.mcp.codex_io`` — the pure TOML engine.

Covers the byte-level splitter/parser/renderer, ``apply_merges`` for combining
a pre/post region around a new ``[mcp_servers*]`` block, and the
``load_codex_servers`` / ``save_codex_servers`` thin slice helpers used by the
per-tool IO dispatch in ``quiver.mcp.cli``.
"""

from __future__ import annotations

import pathlib
import tempfile
import tomllib
import unittest

from quiver.mcp.codex_io import (
    apply_merges,
    load_codex_servers,
    parse_codex_mcp_region,
    render_codex_server,
    save_codex_servers,
    split_codex_toml,
)


SAMPLE_CODEX_PRE_POST = """\
# ── header comment ─────────────────────────────────
model = "gpt-5.5"
personality = 'friendly'

[features]
multi_agent = true

[plugins]
[plugins."github@openai-curated"]
enabled = true
"""

SAMPLE_CODEX_MCP_BLOCK = """\
[mcp_servers]
[mcp_servers.fathom]
args = ['--directory', '/tmp/fathom', 'run', 'python3', 'server.py']
command = 'uv'

[mcp_servers.existing_only_in_codex]
url = 'https://mcp.only-in-codex.example'
"""

SAMPLE_CODEX_FULL = SAMPLE_CODEX_PRE_POST + SAMPLE_CODEX_MCP_BLOCK


# ── splitter ─────────────────────────────────────────────────────────


class CodexRegionSplitterTest(unittest.TestCase):
    def test_splits_three_sections_preserving_pre_post(self):
        pre, region, post = split_codex_toml(SAMPLE_CODEX_FULL)
        self.assertIn('model = "gpt-5.5"', pre)
        self.assertIn('[plugins."github@openai-curated"]', pre)
        self.assertNotIn("[mcp_servers.fathom]", pre)
        self.assertIn("[mcp_servers.fathom]", region)
        self.assertEqual(post.strip(), "")

    def test_returns_empty_region_when_no_mcp_servers_header(self):
        text = 'model = "x"\n[plugins]\n'
        pre, region, post = split_codex_toml(text)
        self.assertEqual(pre, text)
        self.assertEqual(region, "")
        self.assertEqual(post, "")

    def test_handles_nested_mcp_servers_subkey(self):
        text = "model = \"x\"\n[mcp_servers.x]\nurl = 'a'\n[mcp_servers.x.env]\nK = \"v\"\n[y]\nv = 1\n"
        pre, region, post = split_codex_toml(text)
        self.assertIn("[y]", post)
        self.assertIn("[mcp_servers.x.env]", region)
        self.assertEqual(pre.strip(), 'model = "x"')

    def test_empty_text_returns_all_empty(self):
        pre, region, post = split_codex_toml("")
        self.assertEqual(pre, "")
        self.assertEqual(region, "")
        self.assertEqual(post, "")


# ── parser ───────────────────────────────────────────────────────────


class CodexRegionParserTest(unittest.TestCase):
    def test_parses_known_servers(self):
        servers = parse_codex_mcp_region(SAMPLE_CODEX_MCP_BLOCK)
        self.assertEqual(set(servers), {"fathom", "existing_only_in_codex"})
        self.assertEqual(servers["fathom"]["command"], "uv")
        self.assertEqual(
            servers["fathom"]["args"],
            ["--directory", "/tmp/fathom", "run", "python3", "server.py"],
        )
        self.assertEqual(
            servers["existing_only_in_codex"]["url"],
            "https://mcp.only-in-codex.example",
        )

    def test_empty_region(self):
        self.assertEqual(parse_codex_mcp_region(""), {})

    def test_preserves_unknown_keys(self):
        """`startup_timeout_sec` and bespoke fields must survive the round-trip."""
        region = """
[mcp_servers.node_repl]
args = []
command = "/Applications/Codex.app/.../node_repl"
startup_timeout_sec = 120

[mcp_servers.node_repl.env]
NODE_REPL_NODE_PATH = "/Applications/Codex.app/..."
"""
        servers = parse_codex_mcp_region(region)
        node_repl = servers["node_repl"]
        self.assertEqual(node_repl["startup_timeout_sec"], 120)
        self.assertEqual(node_repl["env"]["NODE_REPL_NODE_PATH"], "/Applications/Codex.app/...")

    def test_parser_returns_deep_copies(self):
        """Caller mutating nested dicts must not affect parse internals."""
        servers = parse_codex_mcp_region(SAMPLE_CODEX_MCP_BLOCK)
        servers["fathom"]["args"].append("EXTRA")
        # Re-parse from a fresh region parse: called regions must be independent.
        servers2 = parse_codex_mcp_region(SAMPLE_CODEX_MCP_BLOCK)
        self.assertEqual(servers2["fathom"]["args"], [
            "--directory", "/tmp/fathom", "run", "python3", "server.py"
        ])


# ── renderer ─────────────────────────────────────────────────────────


class ServerRendererTest(unittest.TestCase):
    def test_local_server_with_env(self):
        canonical = {
            "command": "node",
            "args": ["/tmp/server.js"],
            "env": {"API_KEY": "secret-value-1", "DEBUG": "true"},
        }
        text = render_codex_server("std_local", canonical)
        parsed = tomllib.loads("" + text)
        srv = parsed["mcp_servers"]["std_local"]
        self.assertEqual(srv["command"], "node")
        self.assertEqual(srv["args"], ["/tmp/server.js"])
        self.assertEqual(srv["env"]["API_KEY"], "secret-value-1")

    def test_remote_server_with_headers(self):
        canonical = {
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer abcdef"},
        }
        text = render_codex_server("remote_url", canonical)
        parsed = tomllib.loads("" + text)
        srv = parsed["mcp_servers"]["remote_url"]
        self.assertEqual(srv["url"], "https://mcp.example.com/mcp")
        self.assertEqual(srv["headers"]["Authorization"], "Bearer abcdef")

    def test_quotes_server_name_with_special_chars(self):
        text = render_codex_server("weird-name.x", {"url": "https://x"})
        self.assertIn('[mcp_servers."weird-name.x"]', text)
        parsed = tomllib.loads("" + text)
        self.assertIn("weird-name.x", parsed["mcp_servers"])

    def test_escapes_quote_and_backslash(self):
        text = render_codex_server(
            "escape", {"command": 'has "quote" and \\ backslash'}
        )
        parsed = tomllib.loads("" + text)
        self.assertEqual(
            parsed["mcp_servers"]["escape"]["command"],
            'has "quote" and \\ backslash',
        )

    def test_preserves_unicode(self):
        text = render_codex_server("unicode", {"command": "服务器 🚀"})
        parsed = tomllib.loads("" + text)
        self.assertTrue(parsed["mcp_servers"]["unicode"]["command"].startswith("服务器"))

    def test_emits_known_extra_scalar_at_top_level(self):
        """Round-trip preserves `startup_timeout_sec = 120` and similar scalars."""
        canonical = {
            "command": "/Applications/Codex.app/.../node_repl",
            "args": [],
            "startup_timeout_sec": 120,
        }
        text = render_codex_server("node_repl", canonical)
        parsed = tomllib.loads("" + text + "\n[mcp_servers.node_repl.env]\nK = \"v\"")
        self.assertEqual(parsed["mcp_servers"]["node_repl"]["startup_timeout_sec"], 120)
        self.assertEqual(parsed["mcp_servers"]["node_repl"]["command"], "/Applications/Codex.app/.../node_repl")


# ── merging ──────────────────────────────────────────────────────────


class ApplyMergesPreservationTest(unittest.TestCase):
    def test_pre_and_post_unchanged_on_normal_sync(self):
        result = apply_merges(
            SAMPLE_CODEX_FULL,
            to_write={
                "std_local": {"command": "node", "args": ["/tmp/server.js"]},
            },
        )
        original_pre, _, original_post = split_codex_toml(SAMPLE_CODEX_FULL)
        new_pre, new_region, new_post = split_codex_toml(result)
        self.assertEqual(original_pre, new_pre)
        self.assertEqual(original_post, new_post)
        parsed = tomllib.loads("" + new_region)
        self.assertIn("std_local", parsed["mcp_servers"])

    def test_no_double_blank_line_when_subsequent_section(self):
        text = 'model = "x"\n[mcp_servers.foo]\nurl = "y"\n[plugins]\nfoo = true\n'
        result = apply_merges(text, {"bar": {"url": "https://z"}})
        self.assertNotIn("\n\n\n[plugins]", result)
        self.assertIn("[plugins]", result)
        self.assertIn("foo = true", result)
        parsed = tomllib.loads("" + split_codex_toml(result)[1])
        self.assertIn("bar", parsed["mcp_servers"])

    def test_empty_text_grows_first_block(self):
        result = apply_merges("", {"x": {"url": "https://x"}})
        parsed = tomllib.loads("" + result)
        self.assertIn("x", parsed["mcp_servers"])

    def test_stub_section_kept_when_caller_passes_empty_dict(self):
        """If the caller already filtered its target set to empty, a `[mcp_servers]\n`
        stub is appended after the pre bytes so the file remains valid TOML."""
        result = apply_merges(SAMPLE_CODEX_FULL, to_write={})
        # the heading footer should be present so subsequent syncs still work
        self.assertIn("[mcp_servers]", result)
        # and the existing servers should have been evicted by the caller
        self.assertNotIn("[mcp_servers.fathom]", result)
        self.assertNotIn("[mcp_servers.existing_only_in_codex]", result)
        # pre/post are still preserved
        self.assertIn('model = "gpt-5.5"', result)
        self.assertIn('[plugins."github@openai-curated"]', result)


# ── public slice IO ─────────────────────────────────────────────────


class CodexSliceIOTest(unittest.TestCase):
    def test_load_returns_empty_when_file_missing(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                load_codex_servers(pathlib.Path(td) / "nope.toml"),
                {},
            )

    def test_load_returns_empty_when_file_has_no_mcp_region(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "codex.toml"
            p.write_text('model = "x"\n[plugins]\nfoo = true\n')
            self.assertEqual(load_codex_servers(p), {})

    def test_load_returns_servers_with_unknown_fields_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "codex.toml"
            p.write_text(SAMPLE_CODEX_PRE_POST + SAMPLE_CODEX_MCP_BLOCK)
            servers = load_codex_servers(p)
        self.assertEqual(set(servers), {"fathom", "existing_only_in_codex"})

    def test_save_preserves_pre_and_post_bytes(self):
        """Calling save with a server set that overlays existing codex entries
        must leave every non-mcp_servers byte untouched."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "codex.toml"
            p.write_text(SAMPLE_CODEX_PRE_POST + SAMPLE_CODEX_MCP_BLOCK)
            before = p.read_text()
            current = load_codex_servers(p)
            current["std_local"] = {"command": "node", "args": ["/tmp/server.js"]}
            result = save_codex_servers(current, p)
            self.assertTrue(result)
            after = p.read_text()
            # Pre/post lines preserved.
            self.assertIn('model = "gpt-5.5"', after)
            self.assertIn('[plugins."github@openai-curated"]', after)
            # New server present.
            self.assertIn("[mcp_servers.std_local]", after)
            # Existing servers preserved by the caller-passing-loaded-dict pattern.
            self.assertIn("[mcp_servers.fathom]", after)
            self.assertIn("[mcp_servers.existing_only_in_codex]", after)
            self.assertNotEqual(before, after)

    def test_save_atomic_writes_via_tmp_then_rename(self):
        """No `.tmp` sibling should survive the rename."""
        with tempfile.TemporaryDirectory() as td:
            td_path = pathlib.Path(td)
            p = td_path / "codex.toml"
            p.write_text(SAMPLE_CODEX_FULL)
            result = save_codex_servers({"x": {"url": "https://x"}}, p)
            self.assertTrue(result)
            self.assertFalse((p.parent / (p.name + ".tmp")).exists())

    def test_save_is_no_op_when_content_matches_input(self):
        """After a save, re-saving the loaded content back to the same file must
        be a no-op (bytes match). This guards against any drift between the
        load → save round-trip once the in-memory state is the source of truth."""
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "codex.toml"
            p.write_text(SAMPLE_CODEX_PRE_POST + SAMPLE_CODEX_MCP_BLOCK)
            current = load_codex_servers(p)
            # First save rewrites in canonical (double-quote) form.
            save_codex_servers(current, p)
            after_first = p.read_text()
            # Second load + save should produce no change.
            current2 = load_codex_servers(p)
            result = save_codex_servers(current2, p)
            self.assertFalse(result)
            self.assertEqual(after_first, p.read_text())


if __name__ == "__main__":
    unittest.main()
