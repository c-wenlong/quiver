"""Protocol shape tests: events, messages, command validation, keys."""

import unittest

from swe.remote import protocol


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
        # A menu row's own digit answers it — the 8-row devin menu has
        # rows the semantic names can't reach.
        self.assertEqual(
            protocol.parse_approval_command(
                {"request_id": "r1", "decision": "5"}
            )["decision"], "5")
        with self.assertRaises(ValueError):
            protocol.parse_approval_command(
                {"request_id": "r1", "decision": "maybe"}
            )
        with self.assertRaises(ValueError):
            protocol.parse_approval_command(
                {"request_id": "r1", "decision": "123"}
            )

    def test_control(self):
        out = protocol.parse_control_command({"op": "attach"})
        self.assertEqual(out["op"], "attach")
        with self.assertRaises(ValueError):
            protocol.parse_control_command({"op": "detonate"})

    def test_model_and_effort_values_are_one_token(self):
        """They are typed as ``/model <value>``: a newline or space would
        put a second line (or prompt text) into the pane."""
        ok = protocol.parse_control_command({"op": "model", "value": "opus"})
        self.assertEqual(ok["value"], "opus")
        bare = protocol.parse_control_command({"op": "effort", "value": ""})
        self.assertIsNone(bare["value"])
        for bad in ("sonnet\r!rm -rf ~", "a b", "-x", "x" * 200):
            for op in ("model", "effort"):
                with self.assertRaises(ValueError, msg=(op, bad)):
                    protocol.parse_control_command({"op": op, "value": bad})

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
                               "cwd": "/srv/app", "prompt": "go",
                               "model": None, "effort": None,
                               "yolo": False})
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
            {"harness": "claude", "cwd": "/a", "prompt": "go", "model": 3},
            {"harness": "claude", "cwd": "/a", "prompt": "go",
             "model": ["opus"]},
            {"harness": "claude", "cwd": "/a", "prompt": "go",
             "model": "two words"},
            {"harness": "claude", "cwd": "/a", "prompt": "go",
             "model": "/clear now"},
            {"harness": "claude", "cwd": "/a", "prompt": "go",
             "effort": {"x": 1}},
            {"harness": "claude", "cwd": "/a", "prompt": "go",
             "effort": "m" * 200},
            [],
        ]
        for body in bad:
            with self.assertRaises(ValueError, msg=body):
                protocol.parse_launch_command(body)

    def test_launch_yolo(self):
        for value, want in ((None, False), (False, False), (True, True)):
            with self.subTest(yolo=value):
                out = protocol.parse_launch_command(
                    {"harness": "claude", "cwd": "/a", "prompt": "go",
                     "yolo": value})
                self.assertIs(out["yolo"], want)
        for bad in ("true", "yes", 1, 0, [], {}):
            with self.subTest(yolo=bad):
                with self.assertRaises(ValueError):
                    protocol.parse_launch_command(
                        {"harness": "claude", "cwd": "/a", "prompt": "go",
                         "yolo": bad})

    def test_launch_model_and_effort(self):
        out = protocol.parse_launch_command(
            {"harness": "claude", "cwd": "/srv/app", "prompt": "go",
             "model": "anthropic/claude-opus-4-6", "effort": "high"})
        self.assertEqual(out["model"], "anthropic/claude-opus-4-6")
        self.assertEqual(out["effort"], "high")
        # Absent, null and empty all mean "no pick".
        for empty in ({}, {"model": None}, {"model": ""}):
            with self.subTest(body=empty):
                out = protocol.parse_launch_command(
                    {"harness": "claude", "cwd": "/a", "prompt": "go",
                     **empty})
                self.assertIsNone(out["model"])
                self.assertIsNone(out["effort"])


if __name__ == "__main__":
    unittest.main()
