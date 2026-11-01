"""remote/injected.py: harness-injected context never reaches the chat."""

import json
import unittest

from swe.remote import injected, protocol, tail


def _m(kind, text):
    return protocol.message(kind, text, ts=1.0)


class CleanTest(unittest.TestCase):
    def test_cursor_prompt_unwraps_to_the_query(self):
        text = ("<timestamp>Friday, Oct 2, 2026, 12:27 PM (UTC+8)</timestamp>\n"
                "<user_query>\nExplain the file structure.\n</user_query>")
        self.assertEqual(injected.clean(_m("user", text))["text"], "Explain the file structure.")

    def test_system_reminder_is_stripped_and_an_only_reminder_dropped(self):
        self.assertIsNone(injected.clean(_m("user", "<system-reminder>\nTools are deferred.\n</system-reminder>")))
        out = injected.clean(_m("user", "Fix the bug\n<system-reminder>be careful</system-reminder>"))
        self.assertEqual(out["text"], "Fix the bug")

    def test_task_notification_becomes_one_system_line(self):
        text = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
                "<summary>Background command \"Wait for CI\" completed (exit code 0)</summary>\n"
                "</task-notification>")
        out = injected.clean(_m("user", text))
        self.assertEqual(out["kind"], "system")
        self.assertEqual(out["text"], 'Background command "Wait for CI" completed (exit code 0)')

    def test_ci_monitor_event_is_named_with_its_first_sentence(self):
        out = injected.clean(_m("user", "<ci-monitor-event>Auto-fix is watching PR 1. More detail here.</ci-monitor-event>"))
        self.assertEqual(out["kind"], "system")
        self.assertEqual(out["text"], "CI monitor: Auto-fix is watching PR 1.")

    def test_side_sessions_and_tool_catalogues(self):
        out = injected.clean(_m("user", "<side-sessions-event>None of the sessions is working now. More.</side-sessions-event>"))
        self.assertEqual((out["kind"], out["text"]), ("system", "Side sessions: None of the sessions is working now."))
        self.assertIsNone(injected.clean(_m("user", "<available_subagent_types>a, b</available_subagent_types>")))
        out = injected.clean(_m("user", "<in-app-browser-context>tab: x</in-app-browser-context>\nopen the docs"))
        self.assertEqual(out["text"], "open the docs")

    def test_envelope_after_a_reminder_is_still_recognised(self):
        text = "<system-reminder>\nA session finished.\n</system-reminder>\n<side-sessions-event>None is working now.</side-sessions-event>"
        out = injected.clean(_m("user", text))
        self.assertEqual((out["kind"], out["text"]), ("system", "Side sessions: None is working now."))

    def test_slash_command_shows_what_was_typed(self):
        text = "<command-name>/review</command-name>\n<command-message>review</command-message>\n<command-args>541</command-args>"
        self.assertEqual(injected.clean(_m("user", text))["text"], "/review 541")
        self.assertIsNone(injected.clean(_m("user", "<local-command-stdout></local-command-stdout>")))

    def test_codex_instruction_injections_are_dropped(self):
        self.assertIsNone(injected.clean(_m("user", "# AGENTS.md instructions for /Users/k/proj\n\n<INSTRUCTIONS>\nrules\n</INSTRUCTIONS>")))
        self.assertIsNone(injected.clean(_m("user", '<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>')))
        self.assertIsNone(injected.clean(_m("user", "<environment_context>\n<cwd>/x</cwd>\n</environment_context>")))

    def test_memory_citation_footer_leaves_the_answer(self):
        text = ("Before PR3, we still need to settle field-enum editing.\n\n"
                "<oai-mem-citation>\n<citation_entries>\nMEMORY.md:998-998|note=[x]\n</citation_entries>\n"
                "<rollout_ids>\n</rollout_ids>\n</oai-mem-citation>")
        self.assertEqual(injected.clean(_m("assistant", text))["text"],
                         "Before PR3, we still need to settle field-enum editing.")

    def test_agent_and_typed_text_that_mention_tags_stay_as_written(self):
        prose = "Two defects: the raw `<oai-mem-citation>` block, and `<task-notification>` lines."
        self.assertEqual(injected.clean(_m("assistant", prose))["text"], prose)
        typed = "why does `<system-reminder>` show up in my chat?"
        self.assertEqual(injected.clean(_m("user", typed))["text"], typed)
        quoted = "See <task-notification> below"
        self.assertEqual(injected.clean(_m("user", quoted))["text"], quoted)

    def test_other_kinds_pass_untouched(self):
        tool = protocol.message("tool_use", "", ts=1.0, tool={"name": "Bash", "call_id": "c", "args_summary": "", "args_full": "<x>"})
        self.assertIs(injected.clean(tool), tool)

    def test_clean_event_covers_old_journal_entries(self):
        ev = protocol.event("message.appended", "claude:s", "m", _m("user", "<system-reminder>x</system-reminder>"))
        self.assertIsNone(injected.clean_event(ev))
        status = protocol.event("session.status", "claude:s", "m", {"status": "done"})
        self.assertIs(injected.clean_event(status), status)

    def test_a_second_pass_keeps_what_the_first_pass_rewrote(self):
        # The hub cleans on decode and again on serve; an unwrapped query
        # that starts like an injection must survive the second pass.
        text = "<user_query>\n# AGENTS.md instructions for my repo: draft them\n</user_query>"
        once = injected.clean(_m("user", text))
        self.assertEqual(once["text"], "# AGENTS.md instructions for my repo: draft them")
        self.assertEqual(injected.clean(once), once)
        ev = protocol.event("message.appended", "cursor:s", "m", once)
        self.assertIs(injected.clean_event(ev), ev)

    def test_old_codex_developer_turns_stored_as_agent_text_are_dropped(self):
        for text in ("<skills_instructions>\n## Skills\n</skills_instructions>",
                     "This is a terminal/Codex session. Prefer the shell.",
                     "## Memory\n\nYou have access to a memory folder"):
            ev = protocol.event("message.appended", "codex:s", "m", _m("assistant", text))
            self.assertIsNone(injected.clean_event(ev), text)
        reply = "Composio’s connection failed, so I used the CLI."
        ev = protocol.event("message.appended", "codex:s", "m", _m("assistant", reply))
        self.assertIs(injected.clean_event(ev), ev)
        # Only Codex journals held these; another harness's agent text stays.
        ev = protocol.event("message.appended", "claude:s", "m", _m("assistant", "<tools>listed</tools>"))
        self.assertIs(injected.clean_event(ev), ev)


class DecodeTest(unittest.TestCase):
    def test_codex_developer_turns_are_not_the_agent(self):
        rec = {"type": "response_item", "payload": {"type": "message", "role": "developer",
               "content": [{"type": "input_text", "text": "<skills_instructions>use skills</skills_instructions>"}]}}
        self.assertEqual(tail.decode_record("codex", rec), [])
        rec["payload"]["role"] = "assistant"
        rec["payload"]["content"] = [{"type": "output_text", "text": "Done."}]
        self.assertEqual([m["text"] for m in tail.decode_record("codex", rec)], ["Done."])

    def test_claude_user_record_is_cleaned_on_decode(self):
        rec = {"type": "user", "message": {"role": "user", "content": "<system-reminder>ignore</system-reminder>"}}
        self.assertEqual(tail.decode_record("claude", rec), [])


if __name__ == "__main__":
    unittest.main()
