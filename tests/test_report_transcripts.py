import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.reports.transcripts import EXPECTED_READER_TOOLS, READER_REGISTRY, read_transcript
from quiver.sessions.aggregator import PARSER_REGISTRY
from quiver.sessions.models import Session


def _session(tool, sid, path="/work/project", timestamp=1):
    return Session(timestamp=timestamp, agent=tool, path=path, session_id=sid, tool_name=tool)


class TranscriptReaderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.home_patch = patch.dict(os.environ, {"HOME": str(self.home)})
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.temp.cleanup()

    def _jsonl(self, relative, records):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
        return path

    def test_registry_covers_every_session_parser(self):
        parser_tools = {name for name, _parser, _aliases in PARSER_REGISTRY}
        self.assertEqual(parser_tools, EXPECTED_READER_TOOLS)
        self.assertEqual(parser_tools, set(READER_REGISTRY))

    def test_codex_strips_injected_envelopes_but_preserves_semantic_text(self):
        self._jsonl(
            ".codex/sessions/2026/07/30/codex-1.jsonl",
            [
                {"type": "session_meta", "payload": {"id": "codex-1", "cwd": "/work/project"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": (
                            "<recommended_plugins>irrelevant plugin catalog</recommended_plugins>\n"
                            "<environment_context>runtime details</environment_context>\n"
                            "Fix the system parser while preserving {literal: braces}."
                        )}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({"command": "python3 -m unittest"}),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Fixed it; 12 tests pass."}],
                    },
                },
            ],
        )
        transcript = read_transcript(_session("codex", "codex-1"))
        self.assertTrue(transcript.readable)
        self.assertEqual([m.role for m in transcript.messages], ["human", "tool", "assistant"])
        self.assertIn("system parser", transcript.messages[0].text)
        self.assertIn("{literal: braces}", transcript.messages[0].text)
        self.assertNotIn("recommended_plugins", transcript.normalized_text)
        self.assertIn("python3 -m unittest", transcript.messages[1].text)
        self.assertNotIn('{"command"', transcript.messages[1].text)

    def test_unclosed_envelope_does_not_swallow_adjacent_semantic_markup(self):
        self._jsonl(
            ".codex/sessions/2026/07/30/codex-markup.jsonl",
            [{
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": "<recommended_plugins>unclosed\n<task>Preserve this task</task>",
                    }],
                },
            }],
        )
        transcript = read_transcript(_session("codex", "codex-markup"))
        self.assertIn("<task>Preserve this task</task>", transcript.messages[0].text)

    def test_file_edit_tools_preserve_common_structured_filename_fields(self):
        records = []
        expected = []
        for key, filename in (
            ("file_path", "src/file_path.py"),
            ("filePath", "src/filePath.py"),
            ("filename", "src/filename.py"),
        ):
            records.append({
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": json.dumps({key: filename, "patch": "omitted"}),
                },
            })
            expected.append(filename)
        self._jsonl(".codex/sessions/2026/07/30/codex-edits.jsonl", records)

        transcript = read_transcript(_session("codex", "codex-edits"))
        self.assertEqual(
            [message.text for message in transcript.messages],
            [f"apply_patch: {filename}" for filename in expected],
        )

    def test_unknown_nonempty_session_id_never_reads_newest_unrelated_file(self):
        older = self._jsonl(
            ".codex/sessions/2026/07/30/known-old.jsonl",
            [{
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "old session"},
            }],
        )
        newer = self._jsonl(
            ".codex/sessions/2026/07/30/known-new.jsonl",
            [{
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "new session"},
            }],
        )
        os.utime(older, (1, 1))
        os.utime(newer, (2, 2))

        transcript = read_transcript(_session("codex", "unknown-session"))
        self.assertFalse(transcript.readable)
        self.assertEqual(transcript.messages, [])
        self.assertIn("not found", transcript.error)

    def test_claude_drops_local_command_caveat_and_keeps_tool_outcome(self):
        self._jsonl(
            ".claude/projects/-work-project/claude-1.jsonl",
            [
                {
                    "type": "user",
                    "message": {"role": "user", "content": (
                        "<local-command-caveat>Caveat: generated locally.</local-command-caveat>\n"
                        "Update the parser."
                    )},
                },
                {
                    "type": "assistant",
                    "message": {"role": "assistant", "content": [
                        {"type": "text", "text": "I will patch it."},
                        {"type": "tool_use", "name": "Edit", "input": {"path": "parser.py"}},
                    ]},
                },
                {
                    "type": "user",
                    "message": {"role": "user", "content": [
                        {"type": "tool_result", "content": "Updated parser.py"}
                    ]},
                },
            ],
        )
        transcript = read_transcript(_session("claude", "claude-1"))
        self.assertEqual([m.role for m in transcript.messages], ["human", "assistant", "tool", "tool"])
        self.assertEqual(transcript.messages[0].text, "Update the parser.")
        self.assertIn("parser.py", transcript.normalized_text)

    def test_secrets_are_redacted_from_human_assistant_and_tool_content(self):
        private_key = (
            "-----BEGIN PRIVATE KEY-----\n"
            "c3VwZXItc2VjcmV0LWtleS1tYXRlcmlhbA==\n"
            "-----END PRIVATE KEY-----"
        )
        self._jsonl(
            ".codex/sessions/2026/07/30/codex-secrets.jsonl",
            [
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": (
                            "Use sk-proj-abcdefghijklmnopqrstuv and\n"
                            "Authorization: Bearer bearer-secret-value\n"
                            "export OPENAI_API_KEY='env-secret-value'"
                        ),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": (
                            'Configured {"GITHUB_TOKEN": "json-secret-value"}\n'
                            "JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
                        ),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "name": "read_file",
                        "output": private_key,
                    },
                },
            ],
        )

        transcript = read_transcript(_session("codex", "codex-secrets"))

        self.assertEqual([m.role for m in transcript.messages], ["human", "assistant", "tool"])
        self.assertGreaterEqual(transcript.normalized_text.count("[REDACTED"), 6)
        for secret in (
            "sk-proj-abcdefghijklmnopqrstuv",
            "bearer-secret-value",
            "env-secret-value",
            "json-secret-value",
            "eyJhbGciOiJIUzI1NiJ9",
            "c3VwZXItc2VjcmV0LWtleS1tYXRlcmlhbA==",
        ):
            self.assertNotIn(secret, transcript.normalized_text)

    def test_secret_redaction_does_not_mask_secret_related_prose(self):
        self._jsonl(
            ".codex/sessions/2026/07/30/codex-prose.jsonl",
            [{
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": "Document the token budget, password policy, and placeholder sk-short.",
                },
            }],
        )

        transcript = read_transcript(_session("codex", "codex-prose"))

        self.assertEqual(
            transcript.messages[0].text,
            "Document the token budget, password policy, and placeholder sk-short.",
        )

    def test_digest_is_stable_when_only_secret_values_change(self):
        path = self._jsonl(
            ".codex/sessions/2026/07/30/codex-digest.jsonl",
            [{
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": "TOKEN=first-secret-value",
                },
            }],
        )
        first = read_transcript(_session("codex", "codex-digest"))
        path.write_text(
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": "TOKEN=second-secret-value",
                },
            }) + "\n",
            encoding="utf-8",
        )

        second = read_transcript(_session("codex", "codex-digest"))

        self.assertEqual(first.messages[0].text, "TOKEN=[REDACTED]")
        self.assertEqual(first.digest, second.digest)

    def test_antigravity_overview_assigns_roles_for_current_jsonl_records(self):
        overview = self.home / (
            ".gemini/antigravity/brain/ag-current/.system_generated/logs/overview.txt"
        )
        overview.parent.mkdir(parents=True)
        overview.write_text(
            "\n".join([
                json.dumps({
                    "step_index": 0,
                    "source": "USER_EXPLICIT",
                    "type": "USER_INPUT",
                    "status": "DONE",
                    "content": "Fix the Antigravity parser.",
                }),
                json.dumps({
                    "step_index": 5,
                    "source": "MODEL",
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "content": "The parser is fixed.",
                }),
            ]),
            encoding="utf-8",
        )

        transcript = read_transcript(_session("antigravity", "ag-current"))

        self.assertEqual([m.role for m in transcript.messages], ["human", "assistant"])
        self.assertEqual(
            [m.text for m in transcript.messages],
            ["Fix the Antigravity parser.", "The parser is fixed."],
        )

    def test_antigravity_legacy_prompt_response_and_message_roles(self):
        overview = self.home / (
            ".gemini/antigravity/brain/ag-legacy/.system_generated/logs/overview.txt"
        )
        overview.parent.mkdir(parents=True)
        overview.write_text(
            '{"Prompt":"Investigate the failure","Response":"Found the cause"}\n'
            '{"Message":"Applied the focused fix"}\n',
            encoding="utf-8",
        )

        transcript = read_transcript(_session("antigravity", "ag-legacy"))

        self.assertEqual([m.role for m in transcript.messages], ["human", "assistant", "assistant"])
        self.assertEqual(
            [m.text for m in transcript.messages],
            ["Investigate the failure", "Found the cause", "Applied the focused fix"],
        )

    def test_empty_droid_and_pi_noop_sessions_are_readable(self):
        self._jsonl(
            ".factory/sessions/project/droid-empty.jsonl",
            [{"type": "session_start", "cwd": "/work/project", "title": "New session"}],
        )
        droid = read_transcript(_session("droid", "droid-empty"))
        self.assertTrue(droid.readable)
        self.assertEqual(droid.messages, [])

        for sid, prompt in (("hello", "hello"), ("version", "version"), ("exit", "/exit")):
            self._jsonl(
                f".pi/agent/sessions/--work-project--/{sid}.jsonl",
                [
                    {"type": "session", "cwd": "/work/project"},
                    {"type": "message", "message": {"role": "user", "content": prompt}},
                    {"type": "message", "message": {"role": "assistant", "content": "ok"}},
                ],
            )
            transcript = read_transcript(_session("pi", sid))
            self.assertEqual(transcript.messages[0].text, prompt)

    def test_opencode_sqlite_reader_preserves_text_and_tool_activity(self):
        db = self.home / ".local/share/opencode/opencode.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
            "CREATE TABLE part (message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT);"
        )
        conn.execute("INSERT INTO message VALUES (?, ?, ?, ?)", ("m1", "oc-1", 1, json.dumps({"role": "user"})))
        conn.execute("INSERT INTO message VALUES (?, ?, ?, ?)", ("m2", "oc-1", 2, json.dumps({"role": "assistant"})))
        conn.execute("INSERT INTO part VALUES (?, ?, ?, ?)", ("m1", "oc-1", 1, json.dumps({"type": "text", "text": "Fix tests"})))
        conn.execute("INSERT INTO part VALUES (?, ?, ?, ?)", ("m2", "oc-1", 2, json.dumps({"type": "tool", "tool": "bash", "state": {"input": {"command": "python3 -m unittest"}, "output": "OK"}})))
        conn.commit()
        conn.close()

        transcript = read_transcript(_session("opencode", "oc-1"))
        self.assertTrue(transcript.readable)
        self.assertEqual([m.role for m in transcript.messages], ["human", "tool"])
        self.assertIn("OK", transcript.messages[1].text)

    def test_forge_unwraps_typed_text_envelopes_and_drops_system_messages(self):
        db = self.home / ".forge/.forge.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE conversations (conversation_id TEXT, context TEXT)")
        context = {"messages": [
            {"message": {"text": {"role": "System", "content": "internal policy"}}},
            {"message": {"text": {"role": "User", "content": "Fix the build"}}},
            {"message": {"text": {"role": "Assistant", "content": "Build fixed"}}},
        ]}
        conn.execute("INSERT INTO conversations VALUES (?, ?)", ("forge-1", json.dumps(context)))
        conn.commit()
        conn.close()

        transcript = read_transcript(_session("forge", "forge-1"))
        self.assertTrue(transcript.readable)
        self.assertEqual([m.text for m in transcript.messages], ["Fix the build", "Build fixed"])

    def test_tau_uses_index_path_when_filename_does_not_match_session_id(self):
        project = self.home / ".tau/sessions/project"
        transcript_path = project / "custom-name.jsonl"
        self._jsonl(
            ".tau/sessions/project/custom-name.jsonl",
            [
                {"type": "message", "message": {"role": "user", "content": "Fix Tau"}},
                {"type": "message", "message": {"role": "assistant", "content": "Done"}},
            ],
        )
        self._jsonl(
            ".tau/sessions/project/index.jsonl",
            [{"id": "tau-id", "path": str(transcript_path), "cwd": "/work/project"}],
        )
        transcript = read_transcript(_session("tau", "tau-id"))
        self.assertTrue(transcript.readable)
        self.assertEqual([m.text for m in transcript.messages], ["Fix Tau", "Done"])

    def test_missing_source_is_unreadable_not_empty(self):
        transcript = read_transcript(_session("continue", "missing"))
        self.assertFalse(transcript.readable)
        self.assertIn("not found", transcript.error)
        self.assertEqual(transcript.messages, [])


if __name__ == "__main__":
    unittest.main()
