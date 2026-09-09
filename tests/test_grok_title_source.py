"""title_source for grok sessions: summary.json title_is_manual / generated_title."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _write_session(base: Path, sid: str, summary) -> Path:
    sess = base / "%2FUsers%2Ftest" / sid
    sess.mkdir(parents=True)
    body = summary if isinstance(summary, str) else json.dumps(summary)
    (sess / "summary.json").write_text(body)
    (sess / "prompt_context.json").write_text(
        json.dumps({"working_directory": "/Users/test"})
    )
    return sess


def _parse(base: Path):
    with mock.patch(
        "quiver.sessions.parsers.os.path.expanduser",
        side_effect=lambda p: str(base) if p.endswith("sessions") else p,
    ):
        from quiver.sessions.parsers import parse_grok

        return {s.session_id: s for s in parse_grok()}


class GrokTitleSourceTest(unittest.TestCase):
    def test_title_is_manual_true_is_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_session(
                base,
                "manual-bool",
                {
                    "generated_title": "My renamed session",
                    "session_summary": "some summary",
                    "title_is_manual": True,
                    "updated_at": "2026-07-06T12:19:08.646503Z",
                },
            )
            _write_session(
                base,
                "manual-str",
                {"generated_title": "Renamed via string", "title_is_manual": "true"},
            )
            _write_session(
                base,
                "manual-one",
                {"generated_title": "Renamed via one", "title_is_manual": "1"},
            )
            _write_session(
                base,
                "manual-summary-only",
                {"session_summary": "Renamed summary", "title_is_manual": True},
            )
            got = _parse(base)
        self.assertEqual(got["manual-bool"].title, "My renamed session")
        self.assertEqual(got["manual-bool"].title_source, "rename")
        self.assertEqual(got["manual-str"].title_source, "rename")
        self.assertEqual(got["manual-one"].title_source, "rename")
        self.assertEqual(got["manual-summary-only"].title_source, "rename")

    def test_title_is_manual_false_with_generated_title_is_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_session(
                base,
                "auto-false",
                {
                    "generated_title": "Generated title",
                    "session_summary": "summary text",
                    "title_is_manual": False,
                },
            )
            _write_session(
                base,
                "auto-false-str",
                {"generated_title": "Generated title", "title_is_manual": "false"},
            )
            got = _parse(base)
        self.assertEqual(got["auto-false"].title, "Generated title")
        self.assertEqual(got["auto-false"].title_source, "auto")
        self.assertEqual(got["auto-false-str"].title_source, "auto")

    def test_generated_title_without_flag_is_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_session(
                base,
                "no-flag",
                {"generated_title": "hello grok", "session_summary": "hello grok"},
            )
            got = _parse(base)
        self.assertEqual(got["no-flag"].title, "hello grok")
        self.assertEqual(got["no-flag"].title_source, "auto")

    def test_session_summary_only_is_first_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_session(base, "summary-only", {"session_summary": "just a summary"})
            _write_session(
                base,
                "summary-only-false",
                {"session_summary": "just a summary", "title_is_manual": False},
            )
            _write_session(
                base,
                "empty-generated",
                {"generated_title": "", "session_summary": "fallback summary"},
            )
            got = _parse(base)
        self.assertEqual(got["summary-only"].title, "just a summary")
        self.assertEqual(got["summary-only"].title_source, "")
        self.assertEqual(got["summary-only-false"].title_source, "")
        self.assertEqual(got["empty-generated"].title, "fallback summary")
        self.assertEqual(got["empty-generated"].title_source, "")

    def test_no_title_leaves_source_empty_even_when_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_session(base, "no-title", {"title_is_manual": True})
            got = _parse(base)
        self.assertEqual(got["no-title"].title, "")
        self.assertEqual(got["no-title"].title_source, "")

    def test_malformed_summary_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            _write_session(base, "broken", "{not json")
            _write_session(base, "list-body", "[1, 2, 3]")
            _write_session(base, "ok", {"generated_title": "still fine"})
            got = _parse(base)
        self.assertIn("broken", got)
        self.assertEqual(got["broken"].title, "")
        self.assertEqual(got["broken"].title_source, "")
        self.assertIn("list-body", got)
        self.assertEqual(got["list-body"].title_source, "")
        self.assertEqual(got["ok"].title_source, "auto")


if __name__ == "__main__":
    unittest.main()
