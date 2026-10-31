import unittest

from quiver.mcp.formats import (
    convert_server_between_formats,
    get_conversion_issues,
    get_format_handler,
    normalize_server,
)


class McpFormatsTest(unittest.TestCase):
    def test_standard_parse_emit_roundtrip(self):
        raw = {
            "command": "node",
            "args": ["server.js"],
            "env": {"API_KEY": "x"},
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer x"},
        }
        handler = get_format_handler("standard")
        canonical = handler.parse(raw)
        emitted = handler.emit(canonical)
        self.assertEqual(emitted, raw)

    def test_opencode_parse_to_canonical(self):
        raw = {
            "command": ["node", "server.js", "--stdio"],
            "environment": {"TOKEN": "abc"},
            "type": "local",
            "enabled": True,
        }
        canonical = get_format_handler("opencode").parse(raw)
        self.assertEqual(canonical["command"], "node")
        self.assertEqual(canonical["args"], ["server.js", "--stdio"])
        self.assertEqual(canonical["env"], {"TOKEN": "abc"})

    def test_convert_standard_to_opencode(self):
        standard = {
            "command": "node",
            "args": ["server.js"],
            "env": {"TOKEN": "abc"},
        }
        converted = convert_server_between_formats(
            standard,
            source_format="standard",
            target_format="opencode",
        )
        self.assertEqual(converted["command"], ["node", "server.js"])
        self.assertEqual(converted["environment"], {"TOKEN": "abc"})
        self.assertEqual(converted["type"], "local")
        self.assertTrue(converted["enabled"])

    def test_convert_opencode_remote_to_standard(self):
        opencode_remote = {
            "url": "https://mcp.example.com",
            "headers": {"x-api-key": "k"},
            "type": "remote",
        }
        converted = convert_server_between_formats(
            opencode_remote,
            source_format="opencode",
            target_format="standard",
        )
        self.assertEqual(
            converted,
            {
                "url": "https://mcp.example.com",
                "headers": {"x-api-key": "k"},
            },
        )

    def test_normalize_server_detects_mixed_formats(self):
        self.assertEqual(
            normalize_server({"command": ["node", "s.js"], "environment": {"A": "1"}}),
            {"command": "node", "args": ["s.js"], "env": {"A": "1"}},
        )
        self.assertEqual(
            normalize_server({"command": "node", "args": ["s.js"], "env": {"A": "1"}}),
            {"command": "node", "args": ["s.js"], "env": {"A": "1"}},
        )

    def test_conversion_issues_detect_unparseable_source(self):
        issues = get_conversion_issues(
            {"unsupported": True},
            source_format="standard",
            target_format="opencode",
        )
        self.assertTrue(issues)
        self.assertIn("could not be parsed", issues[0])

    def test_conversion_issues_empty_for_known_good_mapping(self):
        issues = get_conversion_issues(
            {"command": "node", "args": ["server.js"], "env": {"A": "1"}},
            source_format="standard",
            target_format="opencode",
        )
        self.assertEqual(issues, [])


    def test_claude_marks_remote_servers_with_their_type(self):
        # Claude Code skips a url entry without "type".
        self.assertEqual(
            convert_server_between_formats({"url": "https://a/mcp"}, "standard", "claude"),
            {"url": "https://a/mcp", "type": "http"},
        )
        self.assertEqual(
            convert_server_between_formats({"url": "https://a/sse", "type": "sse"},
                                           "standard", "claude")["type"],
            "sse",
        )
        self.assertNotIn("type", convert_server_between_formats(
            {"command": "node", "args": ["s.js"]}, "standard", "claude"))
        self.assertEqual(get_conversion_issues({"url": "https://a/mcp"}, "standard", "claude"), [])


class McpFormatEdgeCasesTest(unittest.TestCase):
    def test_base_handler_is_abstract(self):
        from quiver.mcp.formats import McpFormatHandler
        with self.assertRaises(NotImplementedError):
            McpFormatHandler().parse({})
        with self.assertRaises(NotImplementedError):
            McpFormatHandler().emit({})

    def test_template_handler_contract(self):
        # The template is documentation for new handlers; its methods still
        # honour the parse/emit contract (return a dict, tolerate junk).
        from quiver.mcp.formats import TemplateMcpFormatHandler
        handler = TemplateMcpFormatHandler()
        self.assertEqual(handler.parse("not a dict"), {})
        self.assertEqual(handler.parse({"command": "x"}), {})
        self.assertEqual(handler.emit("not a dict"), {})
        self.assertEqual(handler.emit({"command": "x"}), {})

    def test_standard_parse_list_command_and_environment_alias(self):
        handler = get_format_handler("standard")
        parsed = handler.parse({"command": ["node", "s.js", "-v"],
                                "environment": {"A": "1"}})
        self.assertEqual(parsed["command"], "node")
        self.assertEqual(parsed["args"], ["s.js", "-v"])
        self.assertEqual(parsed["env"], {"A": "1"})
        self.assertEqual(handler.parse(42), {})
        self.assertEqual(handler.emit(42), {})

    def test_opencode_emit_remote_and_local(self):
        handler = get_format_handler("opencode")
        remote = handler.emit({"url": "https://x/mcp", "headers": {"a": "b"}})
        self.assertEqual(remote["type"], "remote")
        self.assertEqual(remote["url"], "https://x/mcp")
        local = handler.emit({"command": "node", "args": ["s.js"],
                              "env": {"A": "1"}, "url": "u", "headers": {"h": "v"}})
        self.assertEqual(local["type"], "local")
        self.assertEqual(local["command"], ["node", "s.js"])
        self.assertEqual(local["environment"], {"A": "1"})
        self.assertTrue(local["enabled"])
        self.assertEqual(handler.emit({}), {})

    def test_opencode_parse_environment_and_env(self):
        handler = get_format_handler("opencode")
        via_env = handler.parse({"command": ["n"], "env": {"A": "1"}})
        self.assertEqual(via_env["env"], {"A": "1"})
        self.assertEqual(handler.parse([]), {})
        self.assertEqual(handler.parse({"command": []})["command"], "")

    def test_register_format_handler_override(self):
        from quiver.mcp.formats import register_format_handler, _FORMAT_MCP_HANDLERS
        sentinel = get_format_handler("standard")
        register_format_handler("demo-format", sentinel)
        try:
            self.assertIs(get_format_handler("demo-format"), sentinel)
        finally:
            del _FORMAT_MCP_HANDLERS["demo-format"]
        self.assertIs(get_format_handler("demo-format"), _FORMAT_MCP_HANDLERS["standard"])

    def test_conversion_issues_flag_lossy_fields(self):
        issues = get_conversion_issues(
            {"command": ["node", "s.js"], "env": {"A": "1"}},
            source_format="standard", target_format="opencode",
        )
        self.assertEqual(issues, [])
        non_dict = get_conversion_issues("junk", "standard", "opencode")
        self.assertIn("not a JSON object", non_dict[0])


if __name__ == "__main__":
    unittest.main()
