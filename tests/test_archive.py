"""Archiving a harness: a verdict you keep, not a delete.

`swe remove` forgets a harness, which loses the fact that it was evaluated,
so it reads as untried later and gets reinstalled. Archiving keeps what was
decided, when, and why, and takes it out of the default listing.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.console import strip_ansi


class ArchiveStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.file = self.dir / "archived.json"
        for target, value in (("ARCHIVE_FILE", self.file), ("CONFIG_DIR", self.dir)):
            p = mock.patch(f"quiver.harness.archive.{target}", value)
            p.start()
            self.addCleanup(p.stop)
        from quiver.harness import archive

        self.mod = archive

    def test_nothing_archived_reads_as_empty(self):
        self.assertEqual(self.mod.load_archive(), {})

    def test_archiving_records_a_reason_and_a_timestamp(self):
        entry = self.mod.archive("kiro", "thin wrapper")
        self.assertEqual(entry["reason"], "thin wrapper")
        self.assertTrue(entry["archived_at"], "no timestamp recorded")
        self.assertTrue(self.mod.is_archived("kiro"))

    def test_the_reason_is_optional(self):
        entry = self.mod.archive("mimo")
        self.assertEqual(entry["reason"], "")
        self.assertTrue(entry["archived_at"])

    def test_it_round_trips_through_the_file(self):
        self.mod.archive("kiro", "no MCP support")
        again = self.mod.load_archive()
        self.assertEqual(again["kiro"]["reason"], "no MCP support")

    def test_re_archiving_updates_the_reason(self):
        self.mod.archive("kiro", "first take")
        self.mod.archive("kiro", "actually, no MCP support")
        self.assertEqual(self.mod.load_archive()["kiro"]["reason"],
                         "actually, no MCP support")

    def test_re_archiving_without_a_reason_keeps_the_old_one(self):
        self.mod.archive("kiro", "considered and rejected")
        self.mod.archive("kiro")
        self.assertEqual(self.mod.load_archive()["kiro"]["reason"],
                         "considered and rejected")

    def test_unarchiving_returns_what_it_removed(self):
        """The caller shows the reason being discarded; dropping it
        silently would make the record unreliable."""
        self.mod.archive("kiro", "thin wrapper")
        old = self.mod.unarchive("kiro")
        self.assertEqual(old["reason"], "thin wrapper")
        self.assertFalse(self.mod.is_archived("kiro"))

    def test_unarchiving_something_that_is_not_archived_is_none(self):
        self.assertIsNone(self.mod.unarchive("never-seen"))

    def test_a_corrupt_file_hides_nothing(self):
        """Failing toward showing more is the safe direction here."""
        self.file.write_text("{ not json")
        self.assertEqual(self.mod.load_archive(), {})

    def test_a_hand_written_bare_string_is_tolerated(self):
        self.file.write_text(json.dumps({"kiro": "typed by hand"}))
        self.assertEqual(self.mod.load_archive()["kiro"]["reason"], "typed by hand")

    def test_a_non_dict_file_reads_as_empty(self):
        self.file.write_text(json.dumps(["kiro"]))
        self.assertEqual(self.mod.load_archive(), {})

    def test_entries_survive_an_unrelated_archive(self):
        self.mod.archive("kiro", "a")
        self.mod.archive("mimo", "b")
        self.assertEqual(set(self.mod.load_archive()), {"kiro", "mimo"})


class ListScopeTest(unittest.TestCase):
    """--scope mirrors swe find: active by default, archived, or all."""

    REGISTRY = {
        "claude": {"command": "claude", "description": "Claude Code",
                   "aliases": ["cc"], "tags": []},
        "kiro": {"command": "kiro", "description": "Kiro",
                 "aliases": ["kr"], "tags": []},
    }

    def _run(self, args, archived):
        from quiver.harness import commands

        with mock.patch.object(commands, "load_registry", return_value=dict(self.REGISTRY)), \
             mock.patch("quiver.harness.archive.load_archive", return_value=archived):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = commands.cmd_list(list(args))
        return code, strip_ansi(buf.getvalue())

    ARCHIVED = {"kiro": {"reason": "thin wrapper", "archived_at": "2026-08-21T10:00:00"}}

    def test_active_is_the_default_and_hides_archived(self):
        _, out = self._run([], self.ARCHIVED)
        self.assertIn("claude", out)
        self.assertNotIn("kiro", out)

    def test_archived_scope_shows_only_those(self):
        _, out = self._run(["--scope=archived"], self.ARCHIVED)
        self.assertIn("kiro", out)
        self.assertNotIn("claude", out)

    def test_all_shows_both(self):
        _, out = self._run(["--scope=all"], self.ARCHIVED)
        self.assertIn("claude", out)
        self.assertIn("kiro", out)

    def test_all_marks_the_archived_row(self):
        """Without a marker a shelved harness reads as one still in play."""
        _, out = self._run(["--scope=all"], self.ARCHIVED)
        row = next(ln for ln in out.splitlines() if "kiro" in ln)
        self.assertIn("▪", row)

    def test_an_unknown_scope_is_rejected(self):
        code, out = self._run(["--scope=nonsense"], {})
        self.assertEqual(code, 1)
        self.assertIn("Unknown scope", out)

    def test_the_footer_says_how_many_are_hidden(self):
        _, out = self._run([], self.ARCHIVED)
        self.assertIn("1 hidden", out)

    def test_nothing_archived_leaves_the_listing_untouched(self):
        _, out = self._run([], {})
        self.assertIn("claude", out)
        self.assertIn("kiro", out)


class ArchiveCommandTest(unittest.TestCase):
    def setUp(self):
        self.entries = {}
        self.registry = {"kiro": {"command": "kiro", "description": "Kiro",
                                  "aliases": ["kr"], "tags": []}}

        def _archive(name, reason="", when=None):
            entry = {"reason": reason or self.entries.get(name, {}).get("reason", ""),
                     "archived_at": when or "2026-08-21T10:00:00"}
            self.entries[name] = entry
            return entry

        def _unarchive(name):
            return self.entries.pop(name, None)

        from quiver.harness import commands

        for target, fn in (("load_archive", lambda: dict(self.entries)),
                           ("archive", _archive), ("unarchive", _unarchive)):
            p = mock.patch(f"quiver.harness.archive.{target}", fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(commands, "load_registry", lambda: dict(self.registry))
        p.start()
        self.addCleanup(p.stop)
        self.commands = commands

    def _run(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = self.commands.cmd_archive(list(args))
        return code, strip_ansi(buf.getvalue())

    def test_archiving_with_a_reason_reports_it_back(self):
        _, out = self._run(["kiro", "thin", "wrapper"])
        self.assertIn("Archived", out)
        self.assertIn("thin wrapper", out)

    def test_the_reason_may_be_several_words(self):
        self._run(["kiro", "no", "MCP", "support", "at", "all"])
        self.assertEqual(self.entries["kiro"]["reason"], "no MCP support at all")

    def test_archiving_without_a_reason_says_how_to_add_one(self):
        _, out = self._run(["kiro"])
        self.assertIn("no reason given", out)

    def test_running_it_again_restores_and_shows_what_it_had(self):
        self._run(["kiro", "thin wrapper"])
        _, out = self._run(["kiro"])
        self.assertIn("Restored", out)
        self.assertIn("thin wrapper", out, "discarded the reason without saying so")
        self.assertNotIn("kiro", self.entries)

    def test_a_new_reason_re_archives_rather_than_restoring(self):
        self._run(["kiro", "first"])
        _, out = self._run(["kiro", "second"])
        self.assertIn("second", out)
        self.assertIn("kiro", self.entries)

    def test_listing_shows_the_date_and_reason(self):
        self._run(["kiro", "thin wrapper"])
        _, out = self._run([])
        self.assertIn("kiro", out)
        self.assertIn("2026-08-21", out)
        self.assertIn("thin wrapper", out)

    def test_an_empty_archive_says_so(self):
        _, out = self._run([])
        self.assertIn("nothing archived", out)

    def test_an_unknown_harness_is_an_error(self):
        code, out = self._run(["no-such-tool"])
        self.assertEqual(code, 1)
        self.assertIn("not found", out)

    def test_an_archived_harness_that_left_the_registry_can_still_be_restored(self):
        self.entries["ghost"] = {"reason": "gone", "archived_at": "2026-01-01T00:00:00"}
        code, out = self._run(["ghost"])
        self.assertEqual(code, 0)
        self.assertIn("Restored", out)

    def test_an_alias_resolves(self):
        self._run(["kr", "via alias"])
        self.assertIn("kiro", self.entries)


class HarnessRouterTest(unittest.TestCase):
    """star and archive moved under `swe harness` (alias `swe hs`) to keep
    the first layer of commands short."""

    def test_the_old_top_level_names_are_gone(self):
        from quiver.cli import COMMANDS

        for name in ("star", "archive", "unstar", "favourite"):
            self.assertNotIn(name, COMMANDS, name)

    def test_hs_is_an_alias_for_harness(self):
        from quiver.cli import COMMANDS

        self.assertIn("hs", COMMANDS)
        self.assertIs(COMMANDS["hs"], COMMANDS["harness"])

    def test_harness_star_routes_to_the_star_command(self):
        from quiver.setup.commands import cmd_harness

        with mock.patch("quiver.harness.commands.cmd_star", return_value=0) as m:
            with redirect_stdout(io.StringIO()):
                cmd_harness(["star", "claude"])
        m.assert_called_once_with(["claude"])

    def test_harness_archive_routes_to_the_archive_command(self):
        from quiver.setup.commands import cmd_harness

        with mock.patch("quiver.harness.commands.cmd_archive", return_value=0) as m:
            with redirect_stdout(io.StringIO()):
                cmd_harness(["archive", "kiro", "why"])
        m.assert_called_once_with(["kiro", "why"])

    def test_an_unknown_subcommand_is_an_error(self):
        from quiver.setup.commands import cmd_harness

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cmd_harness(["nonsense"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown harness subcommand", strip_ansi(buf.getvalue()))

    def test_the_help_lists_both_verbs(self):
        from quiver.setup.commands import cmd_harness

        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_harness([])
        out = strip_ansi(buf.getvalue())
        self.assertIn("star", out)
        self.assertIn("archive", out)
        self.assertIn("swe hs", out)


if __name__ == "__main__":
    unittest.main()


class StatePickerTest(unittest.TestCase):
    """Tri-state rows: active, starred, archived."""

    def test_the_three_states_cycle_and_wrap(self):
        from quiver.multiselect import STATES

        s, seen = "active", []
        for _ in range(len(STATES)):
            seen.append(s)
            s = STATES[(STATES.index(s) + 1) % len(STATES)]
        self.assertEqual(seen, list(STATES))
        self.assertEqual(s, "active", "should wrap to the start")

    def test_it_cycles_backwards_too(self):
        from quiver.multiselect import STATES

        self.assertEqual(STATES[(STATES.index("active") - 1) % len(STATES)],
                         "archived")

    def test_every_state_has_a_glyph(self):
        from quiver.multiselect import STATE_GLYPH, STATES

        for state in STATES:
            self.assertIn(state, STATE_GLYPH)

    def test_keys_map_to_the_states_they_name(self):
        import os

        from quiver.multiselect import _read_state_key

        for seq, want in ((b"s", "starred"), (b"x", "archived"), (b"c", "active"),
                          (b" ", "next"), (b"\x1b[C", "next"), (b"\x1b[D", "prev"),
                          (b"\r", "enter"), (b"q", "cancel")):
            r, w = os.pipe()
            os.write(w, seq)
            os.close(w)
            self.assertEqual(_read_state_key(r), want, seq)
            os.close(r)

    def test_a_long_list_is_windowed_rather_than_drawn_whole(self):
        """Drawing more rows than the terminal has scrolls it, after which
        rewinding by a fixed count smears the widget down the screen."""
        from quiver.multiselect import StateChoice, _state_render

        rows = [StateChoice(key=f"h{i}", label=f"h{i}") for i in range(40)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            drawn = _state_render(rows, 0, "T", 0, height=10)
        self.assertLessEqual(drawn, 13, "drew more lines than the window allows")
        self.assertIn("of 40", strip_ansi(buf.getvalue()))

    def test_the_window_follows_the_cursor(self):
        from quiver.multiselect import StateChoice, _state_render

        rows = [StateChoice(key=f"h{i}", label=f"h{i}") for i in range(40)]
        buf = io.StringIO()
        with redirect_stdout(buf):
            _state_render(rows, 38, "T", 0, height=10)
        self.assertIn("h38", strip_ansi(buf.getvalue()))

    def test_an_empty_registry_returns_nothing_rather_than_hanging(self):
        from quiver.multiselect import statepicker

        self.assertEqual(statepicker([]), [])


class HarnessEditTest(unittest.TestCase):
    """`swe hs edit` reviews everything at once, then asks why for archives.

    A blank reason cancels that archive: an archive with no reason is the
    one you cannot act on later, which defeats keeping the record.
    """

    REGISTRY = {
        "amp": {"command": "amp", "description": "", "aliases": [], "tags": []},
        "kiro": {"command": "kiro", "description": "", "aliases": [], "tags": []},
        "mimo": {"command": "mimo", "description": "", "aliases": [], "tags": []},
    }

    def setUp(self):
        self.archived = {}
        self.stars = []

        def _archive(name, reason="", when=None):
            self.archived[name] = {"reason": reason, "archived_at": "2026-08-21T10:00:00"}
            return self.archived[name]

        from quiver.harness import commands

        self.commands = commands
        patches = [
            mock.patch.object(commands, "load_registry", lambda: dict(self.REGISTRY)),
            mock.patch.object(commands, "load_stars", lambda: list(self.stars)),
            mock.patch.object(commands, "_session_counts", lambda *a, **k: {}),
            mock.patch.object(commands, "star_name",
                              lambda n: self.stars.append(n)),
            mock.patch.object(commands, "unstar_name",
                              lambda n: self.stars.remove(n) if n in self.stars else None),
            mock.patch("quiver.harness.archive.load_archive", lambda: dict(self.archived)),
            mock.patch("quiver.harness.archive.archive", _archive),
            mock.patch("quiver.harness.archive.unarchive",
                       lambda n: self.archived.pop(n, None)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _run(self, states, answers):
        """Drive the editor: `states` is the state each row ends on."""
        def fake_picker(choices, **kw):
            if states is None:
                return None
            for ch in choices:
                if ch.key in states:
                    ch.state = states[ch.key]
            return choices

        replies = list(answers)
        with mock.patch("quiver.multiselect.statepicker", fake_picker), \
             mock.patch.object(self.commands, "read_line",
                               side_effect=lambda *a, **k: replies.pop(0)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = self.commands.cmd_harness_edit([])
        return code, strip_ansi(buf.getvalue())

    def test_cancelling_changes_nothing(self):
        _, out = self._run(None, [])
        self.assertIn("cancelled", out)
        self.assertEqual(self.archived, {})

    def test_no_changes_says_so(self):
        _, out = self._run({}, [])
        self.assertIn("no changes", out)

    def test_starring_applies_without_asking_for_a_reason(self):
        _, out = self._run({"amp": "starred"}, [])
        self.assertIn("amp", self.stars)
        self.assertNotIn("Why are you archiving", out)

    def test_archiving_asks_why_and_records_the_answer(self):
        _, out = self._run({"kiro": "archived"}, ["thin wrapper"])
        self.assertIn("Why are you archiving", out)
        self.assertEqual(self.archived["kiro"]["reason"], "thin wrapper")

    def test_a_blank_reason_cancels_that_archive(self):
        _, out = self._run({"kiro": "archived"}, [""])
        self.assertEqual(self.archived, {}, "archived with no reason")
        self.assertIn("no reason given", out)
        self.assertIn("left active", out)

    def test_whitespace_only_counts_as_blank(self):
        self._run({"kiro": "archived"}, ["   "])
        self.assertEqual(self.archived, {})

    def test_each_archive_is_asked_separately(self):
        self._run({"kiro": "archived", "mimo": "archived"},
                  ["no MCP support", "too slow"])
        self.assertEqual(self.archived["kiro"]["reason"], "no MCP support")
        self.assertEqual(self.archived["mimo"]["reason"], "too slow")

    def test_one_blank_does_not_cancel_the_others(self):
        self._run({"kiro": "archived", "mimo": "archived"}, ["a real reason", ""])
        self.assertEqual(set(self.archived), {"kiro"})

    def test_restoring_from_archived_needs_no_reason(self):
        self.archived["kiro"] = {"reason": "old", "archived_at": "2026-01-01T00:00:00"}
        _, out = self._run({"kiro": "active"}, [])
        self.assertNotIn("kiro", self.archived)
        self.assertNotIn("Why are you archiving", out)

    def test_unstarring_applies(self):
        self.stars.append("amp")
        self._run({"amp": "active"}, [])
        self.assertNotIn("amp", self.stars)

    def test_a_row_can_move_from_starred_to_archived(self):
        self.stars.append("amp")
        self._run({"amp": "archived"}, ["changed my mind"])
        self.assertNotIn("amp", self.stars)
        self.assertIn("amp", self.archived)

    def test_ctrl_c_during_the_reasons_leaves_the_rest_active(self):
        def boom(*a, **k):
            raise KeyboardInterrupt

        with mock.patch("quiver.multiselect.statepicker",
                        lambda choices, **kw: [
                            setattr(ch, "state", "archived") or ch
                            for ch in choices if ch.key == "kiro"
                        ] + [ch for ch in choices if ch.key != "kiro"]), \
             mock.patch.object(self.commands, "read_line", boom):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.commands.cmd_harness_edit([])
        self.assertEqual(self.archived, {}, "recorded despite the interrupt")

    def test_help_explains_the_blank_rule(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.commands.cmd_harness_edit(["--help"])
        self.assertIn("blank reason", strip_ansi(buf.getvalue()).lower())
