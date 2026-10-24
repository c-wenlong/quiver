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

    def test_message_client_id(self):
        out = protocol.parse_message_command({"text": "hi"})
        self.assertEqual(out["client_id"], "")
        out = protocol.parse_message_command(
            {"text": "hi", "client_id": "abc-DEF_123"}
        )
        self.assertEqual(out["client_id"], "abc-DEF_123")

    def test_message_client_id_invalid(self):
        with self.assertRaises(ValueError):
            protocol.parse_message_command(
                {"text": "hi", "client_id": "x" * 65}
            )
        for bad in ("has space", "dot.id", "", "abc\n", 42):
            with self.assertRaises(ValueError):
                protocol.parse_message_command(
                    {"text": "hi", "client_id": bad}
                )

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

    def test_unhashable_values_are_value_errors(self):
        # A JSON list or object in a field checked against a set must
        # raise ValueError (the route's 422), never TypeError.
        cases = [
            (protocol.parse_message_command, {"text": "hi"}, "mode"),
            (protocol.parse_approval_command, {"request_id": "r1"},
             "decision"),
            (protocol.parse_control_command, {}, "op"),
        ]
        for parse, base, field in cases:
            for bad in ([], ["queue"], {}, {"a": 1}):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ValueError):
                        parse({**base, field: bad})

    def test_message_mode_default_is_only_for_missing_null_or_empty(self):
        self.assertEqual(
            protocol.parse_message_command({"text": "hi"})["mode"], "queue")
        for empty in (None, ""):
            with self.subTest(mode=empty):
                out = protocol.parse_message_command(
                    {"text": "hi", "mode": empty})
                self.assertEqual(out["mode"], "queue")
        # Other falsy values are not "missing": they are bad modes.
        for falsy in (False, 0, 0.0):
            with self.subTest(mode=falsy):
                with self.assertRaises(ValueError):
                    protocol.parse_message_command(
                        {"text": "hi", "mode": falsy})

    def test_launch(self):
        out = protocol.parse_launch_command(
            {"harness": "codex", "cwd": "/srv/app", "prompt": "go"})
        self.assertEqual(out, {"machine": "", "harness": "codex",
                               "cwd": "/srv/app", "prompt": "go"})
        bad = [
            {"harness": "Bad Name", "cwd": "/a", "prompt": "go"},
            {"harness": ["claude"], "cwd": "/a", "prompt": "go"},
            {"harness": {"x": 1}, "cwd": "/a", "prompt": "go"},
            {"harness": "claude", "cwd": "~/a", "prompt": "go"},
            {"harness": "claude", "cwd": "/a\x00b", "prompt": "go"},
            {"harness": "claude", "cwd": "/a", "prompt": ""},
            {"harness": "claude", "cwd": "/a",
             "prompt": "x" * (protocol.MAX_LAUNCH_PROMPT + 1)},
            {"harness": "claude", "cwd": "/a", "prompt": "go", "machine": 3},
            [],
        ]
        for body in bad:
            with self.assertRaises(ValueError, msg=body):
                protocol.parse_launch_command(body)


if __name__ == "__main__":
    unittest.main()
