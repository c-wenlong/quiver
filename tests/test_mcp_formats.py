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


if __name__ == "__main__":
    unittest.main()
