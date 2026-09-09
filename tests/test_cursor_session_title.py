"""Title derivation for Cursor CLI sessions.

The Cursor CLI stores every prompt wrapped in a ``<timestamp>`` element and a
``<user_query>`` element. The title must drop the timestamp (with its content),
unwrap the query, and prefer the CLI's own ``title`` from ``meta.json``.
"""

import unittest

from quiver.sessions.parsers import _cursor_session_title

WRAPPED = (
    "<timestamp>Wednesday, Sep 2, 2026, 12:15 PM (UTC+8)</timestamp>\n"
    "<user_query>\n"
    "launch subagents to look into this repo and understand its structure\n"
    "</user_query>"
)


class CursorSessionTitleTests(unittest.TestCase):
    def test_meta_title_wins_over_transcript(self):
        self.assertEqual(_cursor_session_title(WRAPPED, {"title": "ego"}), "ego")

    def test_meta_title_is_cleaned(self):
        title = _cursor_session_title(WRAPPED, {"title": "  toolbox \n  v2  "})
        self.assertEqual(title, "toolbox v2")

    def test_empty_meta_title_falls_back_to_transcript(self):
        for meta in ({}, {"title": ""}, {"title": "   "}, {"title": None}):
            with self.subTest(meta=meta):
                self.assertEqual(
                    _cursor_session_title(WRAPPED, meta),
                    "launch subagents to look into this repo and understand its structure",
                )

    def test_wrapped_prompt_drops_timestamp_and_unwraps_query(self):
        title = _cursor_session_title(WRAPPED, {})
        self.assertEqual(
            title,
            "launch subagents to look into this repo and understand its structure",
        )
        self.assertNotIn("Wednesday", title)

    def test_multiline_timestamp_element(self):
        text = (
            "<timestamp>\nWednesday, Sep 2, 2026,\n12:15 PM (UTC+8)\n</timestamp>\n"
            "<user_query>\nfix the parser\n</user_query>"
        )
        self.assertEqual(_cursor_session_title(text, {}), "fix the parser")

    def test_timestamp_appearing_more_than_once(self):
        text = (
            "<timestamp>Monday, Sep 1, 2026, 9:00 AM (UTC+8)</timestamp>\n"
            "<user_query>first part</user_query>\n"
            "<timestamp>Monday, Sep 1, 2026, 9:01 AM (UTC+8)</timestamp>\n"
            "<user_query>second part</user_query>"
        )
        self.assertEqual(_cursor_session_title(text, {}), "first part second part")

    def test_unwrapped_legacy_text_unchanged(self):
        self.assertEqual(
            _cursor_session_title("Repo: /tmp/quiver. Read AGENTS.md first.", {}),
            "Repo: /tmp/quiver. Read AGENTS.md first.",
        )
        self.assertEqual(
            _cursor_session_title("You are a helpful reviewer.", {}),
            "You are a helpful reviewer.",
        )

    def test_empty_input_gives_empty_string(self):
        self.assertEqual(_cursor_session_title("", {}), "")
        self.assertEqual(_cursor_session_title(None, {}), "")

    def test_wrapper_with_no_query_text_gives_empty_string(self):
        text = (
            "<timestamp>Tuesday, Sep 1, 2026, 8:00 AM (UTC+8)</timestamp>\n"
            "<user_query>\n</user_query>"
        )
        self.assertEqual(_cursor_session_title(text, {}), "")

    def test_long_title_truncated_like_clean_title(self):
        long_prompt = "word " * 40
        text = "<user_query>" + long_prompt + "</user_query>"
        title = _cursor_session_title(text, {})
        expected = long_prompt.strip()[:80] + "..."
        self.assertEqual(title, expected)
        self.assertEqual(len(title), 83)
        self.assertTrue(title.endswith("..."))

    def test_long_meta_title_truncated_like_clean_title(self):
        long_title = "x" * 100
        title = _cursor_session_title("", {"title": long_title})
        self.assertEqual(title, "x" * 80 + "...")


if __name__ == "__main__":
    unittest.main()
