"""Protocol shape tests: events, messages, command validation, keys."""

import unittest

from quiver.remote import protocol


class TestSessionKey(unittest.TestCase):
    def test_tool_and_id(self):
        self.assertEqual(protocol.session_key("claude", "abc"), "claude:abc")

    def test_empty_id_falls_back_to_path_digest(self):
        k1 = protocol.session_key("pi", "", "/tmp/a")
        k2 = protocol.session_key("pi", "", "/tmp/b")
        self.assertTrue(k1.startswith("pi:path-"))
        self.assertNotEqual(k1, k2)

    def test_slug_is_filesystem_safe(self):
        slug = protocol.key_slug("claude:/Users/x/y.jsonl")
        self.assertEqual(len(slug), 24)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in slug))


class TestEvents(unittest.TestCase):
    def test_event_shape(self):
        ev = protocol.event("message.appended", "claude:x", "mbp", {"k": "v"})
        self.assertEqual(ev["v"], protocol.PROTOCOL_VERSION)
        self.assertEqual(ev["type"], "message.appended")
        self.assertEqual(ev["session"], "claude:x")
        self.assertEqual(ev["machine"], "mbp")
        self.assertEqual(ev["seq"], 0)
        self.assertIn("ts", ev)
        self.assertEqual(ev["data"], {"k": "v"})

    def test_message_payload(self):
        m = protocol.message(
            "tool_use", "", tool={"name": "Edit", "call_id": "c1",
                                  "args_summary": "x", "args_full": "{}"},
        )
        self.assertEqual(m["kind"], "tool_use")
        self.assertEqual(m["tool"]["call_id"], "c1")
        self.assertIsNone(m["result_ref"])

    def test_error_shape(self):
        self.assertEqual(
            protocol.error("nope", "broken")["error"]["code"], "nope"
        )


class TestCommandValidation(unittest.TestCase):
    def test_message_defaults(self):
        out = protocol.parse_message_command({"text": "hi"})
        self.assertEqual(out["mode"], "queue")
        self.assertEqual(out["attachments"], [])

    def test_message_requires_text(self):
        with self.assertRaises(ValueError):
            protocol.parse_message_command({"text": "  "})
        with self.assertRaises(ValueError):
            protocol.parse_message_command({})
        with self.assertRaises(ValueError):
            protocol.parse_message_command({"text": "x", "mode": "nope"})

    def test_approval(self):
        out = protocol.parse_approval_command(
            {"request_id": "r1", "decision": "allow"}
        )
        self.assertEqual(out["request_id"], "r1")
        with self.assertRaises(ValueError):
            protocol.parse_approval_command(
                {"request_id": "r1", "decision": "maybe"}
            )

    def test_control(self):
        out = protocol.parse_control_command({"op": "attach"})
        self.assertEqual(out["op"], "attach")
        with self.assertRaises(ValueError):
            protocol.parse_control_command({"op": "detonate"})


if __name__ == "__main__":
    unittest.main()
