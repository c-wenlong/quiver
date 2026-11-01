"""supervise.py: tmux lifecycle, pending-launch adoption, priming."""

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from swe.remote import protocol, supervise
from swe.sessions.models import Session

from tests._tempdir import temp_dir


def _session(tool="claude", sid="s1", path="/private/tmp/proj"):
    return Session(timestamp=0, agent="Claude Code", path=path,
                   session_id=sid, tool_name=tool)


class TestTmuxHelpers(unittest.TestCase):
    def _cp(self, rc=0, stdout=""):
        cp = MagicMock()
        cp.returncode = rc
        cp.stdout = stdout
        return cp

    def test_send_text_sequence(self):
        with patch.object(supervise, "_tmux", return_value=self._cp()) as t:
            self.assertTrue(supervise.send_text("qv-x", "hello"))
        calls = [list(c[0]) for c in t.call_args_list]
        self.assertEqual(calls[0],
                         ["send-keys", "-t", "qv-x", "-l", "--", "hello"])
        self.assertEqual(calls[1], ["send-keys", "-t", "qv-x", "Enter"])

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_send_text_types_a_leading_dash(self):
        # tmux reads "- item" after -l as a flag unless "--" ends them:
        # a pasted list sent from the phone failed as "not reachable".
        socket = f"qv-test-{secrets.token_hex(4)}"
        sockets = tempfile.mkdtemp(prefix="qvt", dir="/tmp")
        self.addCleanup(shutil.rmtree, sockets, True)
        # The server's socket lives under this TMUX_TMPDIR, and the patch
        # below is gone by cleanup time: name it here, or kill-server
        # looks in the default folder and leaves the server running.
        self.addCleanup(subprocess.run, ["tmux", "-L", socket, "kill-server"],
                        capture_output=True,
                        env={**os.environ, "TMUX_TMPDIR": sockets})
        with patch.object(supervise, "_TMUX_SOCKET", socket), \
             patch.dict(os.environ, {"TMUX_TMPDIR": sockets}):
            out = subprocess.run(
                ["tmux", "-L", socket, "new-session", "-d", "-s", "qv-x",
                 "cat"], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue(supervise.send_text("qv-x", "- item one"))
            self.assertTrue(supervise.send_text("qv-x", "-x"))
            for _ in range(50):
                pane = supervise.capture_pane("qv-x")
                if "-x" in pane:
                    break
                time.sleep(0.05)
        self.assertIn("- item one", pane)
        self.assertIn("-x", pane)

    def test_send_text_failure(self):
        with patch.object(supervise, "_tmux", return_value=self._cp(rc=1)):
            self.assertFalse(supervise.send_text("qv-x", "hello"))

    def test_send_key_and_capture(self):
        with patch.object(supervise, "_tmux",
                          return_value=self._cp(stdout="pane text")) as t:
            self.assertTrue(supervise.send_key("qv-x", "Escape"))
            self.assertEqual(supervise.capture_pane("qv-x"), "pane text")

    def test_helpers_tolerate_tmux_errors(self):
        # _tmux raising OSError/SubprocessError degrades each helper to
        # its False/"" result instead of propagating.
        import subprocess as sp

        with patch.object(supervise, "_tmux", side_effect=OSError("gone")):
            self.assertFalse(supervise.send_text("qv-x", "hi"))
            self.assertFalse(supervise.send_key("qv-x", "Escape"))
            self.assertEqual(supervise.capture_pane("qv-x"), "")
            self.assertFalse(supervise.kill("qv-x"))
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "_tmux",
                          side_effect=sp.TimeoutExpired("tmux", 10)):
            self.assertFalse(supervise.tmux_alive("qv-x"))

    def test_capture_pane_nonzero_rc(self):
        with patch.object(supervise, "_tmux",
                          return_value=self._cp(rc=1, stdout="x")):
            self.assertEqual(supervise.capture_pane("qv-x"), "")

    def test_capture_pane_visible_only_by_default(self):
        # Approval detection reads the live menu — scrollback is opt-in
        # because an answered prompt lingering in history must not look
        # pending.
        with patch.object(supervise, "_tmux",
                          return_value=self._cp(stdout="x")) as t:
            supervise.capture_pane("qv-x")
            supervise.capture_pane("qv-x", lines=200)
        self.assertEqual(
            list(t.call_args_list[0][0]),
            ["capture-pane", "-p", "-t", "qv-x"])
        self.assertEqual(
            list(t.call_args_list[1][0]),
            ["capture-pane", "-p", "-t", "qv-x", "-S", "-200"])

    def test_pane_shows_approval_needs_stacked_choices(self):
        # Scattered "No errors found"/"No files changed" prose is not a
        # menu — the choices must be a consecutive block.
        scattered = (
            "working…\nNo errors found\nstill working\n"
            "2 warnings\nNo files changed\ndone")
        self.assertFalse(supervise.pane_shows_approval(scattered))
        menu = "Do you want to proceed?\n1. Yes\n2. No"
        self.assertTrue(supervise.pane_shows_approval(menu))
        stacked = "Choose:\n❯ Allow once\nDeny"
        self.assertTrue(supervise.pane_shows_approval(stacked))

    # Devin's permission menu, verbatim shape: options are numbered
    # with a bare space and the footer reads "esc cancel".
    DEVIN_PANE = (
        "● Running command\n"
        "$ git -C /repo pull origin main && git -C /repo log\n"
        ") 1 Yes  (Approve once)\n"
        "  2 Yes, allow `git` commands\n"
        "  3 Yes, always allow `git` commands in `repo`\n"
        "  4 Yes, always allow `git` commands in all projects\n"
        "  5 Yes, switch to bypass mode\n"
        "  6 Edit command\n"
        "  7 Describe change to command\n"
        "  8 No\n"
        "  ↑↓ select · ↵ confirm · esc cancel"
    )

    def test_pane_shows_approval_devin_menu(self):
        # The footer marker alone catches it even when option rows
        # wrap, and the stacked space-numbered rows would too.
        self.assertTrue(supervise.pane_shows_approval(self.DEVIN_PANE))
        no_footer = self.DEVIN_PANE.rsplit("\n", 1)[0]
        self.assertTrue(supervise.pane_shows_approval(no_footer))

    def test_menu_key_for_devin_labels(self):
        pane = self.DEVIN_PANE
        self.assertEqual(supervise.menu_key_for(pane, "allow"), "1")
        # The standing grant is row 2 — never the bypass row at 5.
        self.assertEqual(supervise.menu_key_for(pane, "always"), "2")
        self.assertEqual(supervise.menu_key_for(pane, "deny"), "8")

    def test_menu_key_for_claude_labels(self):
        pane = "Do you want to proceed?\n1. Yes\n2. Yes, don't ask again\n3. No"
        self.assertEqual(supervise.menu_key_for(pane, "allow"), "1")
        self.assertEqual(supervise.menu_key_for(pane, "always"), "2")
        self.assertEqual(supervise.menu_key_for(pane, "deny"), "3")

    def test_menu_key_for_unnumbered_or_unknown(self):
        # Bullet menus without digits stay on the fixed fallback map.
        self.assertIsNone(
            supervise.menu_key_for("Choose:\n❯ Allow once\nDeny", "deny"))
        self.assertIsNone(
            supervise.menu_key_for(self.DEVIN_PANE, "bogus"))

    def test_menu_options_skips_transcript_numbering(self):
        # "1. No errors found" above the menu is output, not a row —
        # deny must still land on the menu's own 8.
        pane = "1. No errors found\n2. Looks good\n\n" + self.DEVIN_PANE
        self.assertEqual(supervise.menu_key_for(pane, "deny"), "8")
        # Same when the noise line sits flush against the menu rows —
        # one consecutive run, won by the bare "No" row over
        # "No errors found".
        flush = (
            "1. No errors found\n"
            + "\n".join(self.DEVIN_PANE.splitlines()[2:])
        )
        self.assertEqual(supervise.menu_key_for(flush, "deny"), "8")

    def test_menu_options_folds_wrapped_labels(self):
        # A label the pane wraps keeps its row: the unnumbered
        # continuation folds back instead of splitting the menu in two
        # (losing "Approve once" from the options the card lists).
        wrapped = self.DEVIN_PANE.replace(
            "  2 Yes, allow `git` commands\n",
            "  2 Yes, allow `git` commands in the\n     current folder\n")
        opts = supervise.menu_options(wrapped)
        self.assertEqual([n for n, _ in opts],
                         ["1", "2", "3", "4", "5", "6", "7", "8"])
        self.assertEqual(opts[1], ("2", "Yes, allow `git` commands in the current folder"))
        # The full menu drives both semantic and digit answers.
        self.assertEqual(supervise.menu_key_for(wrapped, "allow"), "1")
        self.assertEqual(supervise.menu_key_for(wrapped, "deny"), "8")

    def test_menu_options_blank_line_still_splits_blocks(self):
        # Only unnumbered non-blank gaps fold: a blank line still ends
        # the block, so scrolled-up numbering stays transcript text.
        pane = "1. No errors found\n\n" + self.DEVIN_PANE
        self.assertEqual(
            [n for n, _ in supervise.menu_options(pane)],
            ["1", "2", "3", "4", "5", "6", "7", "8"])

    def test_menu_key_allow_never_picks_a_standing_grant(self):
        # A clipped pane missing row 1 gives allow nothing it may send —
        # the injector's fallback digit stays the weakest grant.
        clipped = "\n".join(self.DEVIN_PANE.splitlines()[3:])
        self.assertIsNone(supervise.menu_key_for(clipped, "allow"))

    def test_approval_fingerprint_binds_devin_command(self):
        other = self.DEVIN_PANE.replace(
            "git -C /repo pull origin main", "git -C /repo push --force")
        self.assertNotEqual(
            supervise.approval_fingerprint(self.DEVIN_PANE),
            supervise.approval_fingerprint(other))

    def test_approval_fingerprint_binds_wrapped_command_tail(self):
        # Same first row, different wrapped tail — still two prompts.
        wrapped_a = self.DEVIN_PANE.replace(
            "$ git -C /repo pull origin main && git -C /repo log\n",
            "$ git -C /repo pull\n  origin main\n")
        wrapped_b = self.DEVIN_PANE.replace(
            "$ git -C /repo pull origin main && git -C /repo log\n",
            "$ git -C /repo pull\n  origin dev\n")
        self.assertNotEqual(
            supervise.approval_fingerprint(wrapped_a),
            supervise.approval_fingerprint(wrapped_b))

    def test_approval_fingerprint_tracks_prompt_text(self):
        a = supervise.approval_fingerprint(
            "Do you want to run ls?\n1. Yes\n2. No")
        b = supervise.approval_fingerprint(
            "Do you want to run rm -rf?\n1. Yes\n2. No")
        # Same menu re-rendered (spinner moved elsewhere) keeps its id.
        a2 = supervise.approval_fingerprint(
            "spinner | working\nDo you want to run ls?\n1. Yes\n2. No")
        self.assertEqual(a, a2)
        self.assertNotEqual(a, b)

    # Devin's select widget (login menu, ask-question panel): the
    # selected row carries a "❭" cursor (U+276D), options number with a
    # bare space, and each option's description sits one indent deeper.
    DEVIN_SELECT = (
        " How would you like to log in?\n"
        "\n"
        " ❭ 1 Log in with browser\n"
        "     Recommended for most users\n"
        "   2 Paste a token manually\n"
        "     For SSH or remote sessions without browser access\n"
        "   3 Back\n"
        "\n"
        " ↑↓ select · ↵ confirm · esc cancel"
    )

    # The same widget unnumbered — how the ask-question panel draws its
    # options when the rows have no digits to key on.
    DEVIN_QUESTION = (
        " Which storage engine should we use?\n"
        "\n"
        " ❭ SQLite\n"
        "   Postgres\n"
        "   Keep as files\n"
        "   Other (type your own)\n"
        "\n"
        " ↑↓ select · ↵ confirm · esc cancel"
    )

    def test_menu_options_includes_devin_select_cursor_row(self):
        # "❭" is a different glyph from claude's "❯" — the selected row
        # used to drop out of the parsed menu, taking option 1 with it.
        opts = supervise.menu_options(self.DEVIN_SELECT)
        self.assertEqual([n for n, _ in opts], ["1", "2", "3"])
        self.assertTrue(opts[0][1].startswith("Log in with browser"))

    def test_pane_shows_approval_question_panel(self):
        self.assertTrue(supervise.pane_shows_approval(self.DEVIN_QUESTION))
        # The Other row alone names the panel even with no footer left.
        no_footer = "\n".join(self.DEVIN_QUESTION.splitlines()[:-2])
        self.assertTrue(supervise.pane_shows_approval(no_footer))

    def test_choice_block_devin_question(self):
        rows, start, cursor = supervise.choice_block(self.DEVIN_QUESTION)
        self.assertEqual(
            rows,
            [("1", "SQLite"), ("2", "Postgres"),
             ("3", "Keep as files"), ("4", "Other (type your own)")])
        self.assertEqual(start, 2)
        self.assertEqual(cursor, 0)

    def test_choice_block_needs_a_marker(self):
        # Stacked lines with no menu furniture are output text, never
        # an answerable list — the glyph alone cannot prove a menu.
        pane = " ❭ Alpha\n   Beta\n   Gamma\n"
        self.assertEqual(supervise.choice_block(pane), ([], -1, None))

    def test_choice_block_needs_a_cursor_and_two_rows(self):
        marker = "\n ↑↓ select · ↵ confirm · esc cancel"
        self.assertEqual(
            supervise.choice_block("   Alpha\n   Beta" + marker),
            ([], -1, None))
        self.assertEqual(
            supervise.choice_block(" ❭ Alpha" + marker),
            ([], -1, None))

    def test_choice_block_skips_deeper_indented_descriptions(self):
        # A description line's indent is not the label column — it
        # falls out of the block instead of gluing onto an option.
        pane = (
            " Pick?\n\n"
            " ❭ Alpha\n"
            "     the default\n"
            "   Beta\n"
            "     the fast one\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        rows, _start, cursor = supervise.choice_block(pane)
        self.assertEqual(rows, [("1", "Alpha"), ("2", "Beta")])
        self.assertEqual(cursor, 0)

    def test_choice_block_question_lines_stay_out(self):
        pane = (
            " Pick one:\n"
            " SQLite, Postgres or files?\n"
            " ❭ SQLite\n"
            "   Postgres\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        rows, _start, _c = supervise.choice_block(pane)
        self.assertEqual([l for _n, l in rows], ["SQLite", "Postgres"])

    def test_choice_block_furniture_cannot_split_the_list(self):
        # A description ending in ":" and the key-hint footer stay in
        # the zone but never become rows — options after them still
        # parse, and punctuation labels at the label column are real.
        pane = (
            " Pick?\n\n"
            " ❭ Alpha\n"
            "     reads: the default\n"
            "   Beta\n"
            "   Continue anyway?\n"
            "   Something else:\n"
            "   note: esc cancels\n"
            "   Gamma\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        rows, _s, _c = supervise.choice_block(pane)
        self.assertEqual(
            [l for _n, l in rows],
            ["Alpha", "Beta", "Continue anyway?",
             "Something else:", "Gamma"])

    def test_choice_block_prefers_the_bottom_list(self):
        pane = (
            " ❭ Old alpha\n"
            "   Old beta\n"
            "\n"
            "text in between\n"
            "\n"
            " ❭ New alpha\n"
            "   New beta\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        rows, _s, _c = supervise.choice_block(pane)
        self.assertEqual([l for _n, l in rows], ["New alpha", "New beta"])

    def test_option_menu_prefers_numbered_rows(self):
        rows, kind, cursor = supervise.option_menu(self.DEVIN_SELECT)
        self.assertEqual((kind, cursor), ("numbered", None))
        self.assertEqual([n for n, _ in rows], ["1", "2", "3"])
        rows, kind, cursor = supervise.option_menu(self.DEVIN_QUESTION)
        self.assertEqual((kind, cursor), ("choices", 0))
        self.assertEqual([n for n, _ in rows], ["1", "2", "3", "4"])

    def test_option_menu_prefers_the_lowest_block(self):
        # A numbered menu scrolled above a live choice list is stale —
        # the card offers the rows the pane is asking on now.
        pane = (
            " Pick a branch?\n"
            " 1. main\n"
            " 2. dev\n"
            "\n"
            " Which engine?\n"
            " ❭ SQLite\n"
            "   Postgres\n"
            " ↑↓ select · ↵ confirm · esc cancel"
        )
        rows, kind, _c = supervise.option_menu(pane)
        self.assertEqual(kind, "choices")
        self.assertEqual([l for _n, l in rows], ["SQLite", "Postgres"])

    def test_approval_fingerprint_binds_choice_labels(self):
        # Same question, different options — a new prompt, a new id.
        other = self.DEVIN_QUESTION.replace("Keep as files", "Redis")
        self.assertNotEqual(
            supervise.approval_fingerprint(self.DEVIN_QUESTION),
            supervise.approval_fingerprint(other))

    def test_approval_fingerprint_ignores_choice_cursor_moves(self):
        # Arrowing over the same rows leaves the outstanding id alone.
        moved = self.DEVIN_QUESTION.replace(
            " ❭ SQLite\n   Postgres\n", "   SQLite\n ❭ Postgres\n")
        self.assertEqual(
            supervise.approval_fingerprint(self.DEVIN_QUESTION),
            supervise.approval_fingerprint(moved))
        # The Other row carries an approval marker, so its cursor used
        # to ride into the hash — selecting it must not rotate the id.
        on_other = self.DEVIN_QUESTION.replace(
            " ❭ SQLite\n", "   SQLite\n").replace(
            "   Other (type your own)", " ❭ Other (type your own)")
        self.assertEqual(
            supervise.approval_fingerprint(self.DEVIN_QUESTION),
            supervise.approval_fingerprint(on_other))

    def test_tmux_real_call(self):
        cp = self._cp(rc=0)
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise.subprocess, "run",
                          return_value=cp) as run:
            self.assertTrue(supervise.tmux_alive("qv-x"))
        self.assertEqual(run.call_args[0][0][:3],
                         ["/x/tmux", "has-session", "-t"])

    def test_kill_and_alive(self):
        with patch.object(supervise, "_tmux",
                          return_value=self._cp()), \
             patch.object(supervise, "tmux_bin", return_value="/usr/bin/tmux"):
            self.assertTrue(supervise.kill("qv-x"))
            self.assertTrue(supervise.tmux_alive("qv-x"))
        with patch.object(supervise, "tmux_bin", return_value=None):
            self.assertFalse(supervise.tmux_alive("qv-x"))

    def test_launch_tmux_argv(self):
        # The harness is exec'd by absolute path with our PATH — no shell
        # in between to rebuild PATH from zsh startup files.
        with patch.object(supervise, "tmux_bin", return_value="/usr/bin/tmux"), \
             patch.object(supervise, "tmux_alive", side_effect=[False, True]), \
             patch.object(supervise.shutil, "which", return_value="/opt/bin/claude"), \
             patch.object(supervise.time, "sleep"), \
             patch.dict(supervise.os.environ, {"PATH": "/opt/bin:/usr/bin"}), \
             patch.object(supervise, "_tmux", return_value=self._cp()) as t:
            name = supervise.launch_tmux("claude:s1", ["claude", "--resume", "s1"], "/tmp")
        self.assertIsNotNone(name)
        self.assertEqual(list(t.call_args[0]), [
            "new-session", "-d", "-s", name,
            "-e", "PATH=/opt/bin:/usr/bin", "-c", "/tmp",
            "/opt/bin/claude", "--resume", "s1",
        ])

    def test_launch_tmux_never_passes_a_lone_command(self):
        # One command argument makes tmux run it through default-shell
        # -c (zsh rebuilds PATH; a space in the path splits it). A
        # launch with no extra args must still reach tmux as an argv.
        with patch.object(supervise, "tmux_bin", return_value="/usr/bin/tmux"), \
             patch.object(supervise, "tmux_alive", side_effect=[False, True]), \
             patch.object(supervise.shutil, "which",
                          return_value="/Users/a b/bin/claude"), \
             patch.object(supervise.time, "sleep"), \
             patch.dict(supervise.os.environ, {"PATH": "/usr/bin"}), \
             patch.object(supervise, "_tmux", return_value=self._cp()) as t:
            name = supervise.launch_tmux("claude:s1", ["claude"], None)
        self.assertEqual(list(t.call_args[0]), [
            "new-session", "-d", "-s", name, "-e", "PATH=/usr/bin",
            "/usr/bin/env", "/Users/a b/bin/claude",
        ])

    def test_launch_tmux_edges(self):
        # No tmux → None. A live session under the name is not ours
        # (callers launch only when nothing is supervised or kept for
        # the key) → None, never a reuse. Failed new-session → None.
        # _tmux blowing up → None. Pane gone by the settle check → None.
        with patch.object(supervise, "tmux_bin", return_value=None):
            self.assertIsNone(supervise.launch_tmux("k", ["claude"], "/tmp"))
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=True), \
             patch.object(supervise, "_tmux") as tmux:
            self.assertIsNone(
                supervise.launch_tmux("claude:s1", ["claude"], "/tmp"))
            tmux.assert_not_called()
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=False), \
             patch.object(supervise, "_tmux", return_value=self._cp(rc=1)):
            self.assertIsNone(
                supervise.launch_tmux("claude:s1", ["claude"], "/tmp")
            )
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=False), \
             patch.object(supervise, "_tmux", side_effect=OSError):
            self.assertIsNone(
                supervise.launch_tmux("claude:s1", ["claude"], "/tmp")
            )
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"), \
             patch.object(supervise, "tmux_alive", return_value=False), \
             patch.object(supervise.time, "sleep"), \
             patch.object(supervise, "_tmux", return_value=self._cp()):
            self.assertIsNone(
                supervise.launch_tmux("claude:s1", ["claude"], "/tmp")
            )


def _typing_pane(pane):
    """A pane that shows ``pane`` until text is typed, then the typed
    text, as a real TUI echoes it. Returns ``(capture, send_text)``."""
    typed = []

    def capture(*_a):
        return f"{pane}\n> {typed[-1]}" if typed else pane

    send = MagicMock(side_effect=lambda _n, t: typed.append(t) or True)
    return capture, send


class TestPrimePane(unittest.TestCase):
    def test_trust_dialog_down_enter(self):
        pane = "Do you trust the files in this folder? > No, exit / Yes, I trust this folder"
        capture, send = _typing_pane(pane)
        with patch.object(supervise, "capture_pane", side_effect=capture), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text", send) as st, \
             patch.object(time, "sleep"):
            supervise.prime_pane("qv-x", "hello")
        keys = [c[0][1] for c in sk.call_args_list]
        self.assertEqual(keys, ["Down", "Enter"])
        st.assert_called_once_with("qv-x", "hello")

    def test_no_dialog_sends_directly(self):
        capture, send = _typing_pane("❯")
        with patch.object(supervise, "capture_pane", side_effect=capture), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text", send) as st, \
             patch.object(time, "sleep"):
            supervise.prime_pane("qv-x", "hello")
        sk.assert_not_called()
        st.assert_called_once_with("qv-x", "hello")

    def test_confirm_dialog_bare_enter(self):
        capture, send = _typing_pane("Warning! Press Enter to confirm")
        with patch.object(supervise, "capture_pane", side_effect=capture), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text", send) as st, \
             patch.object(time, "sleep"):
            supervise.prime_pane("qv-x", "hello")
        sk.assert_called_once_with("qv-x", "Enter")
        st.assert_called_once_with("qv-x", "hello")

    def test_blank_pane_times_out_without_sending(self):
        # A pane that never draws must not eat the prompt — the launch
        # reports "could not be delivered" instead of typing into void.
        with patch.object(supervise, "capture_pane", return_value=""), \
             patch.object(supervise, "send_text") as st, \
             patch.object(time, "sleep"), \
             patch.object(time, "time",
                          side_effect=[0, 0, 100]):  # past deadline
            self.assertFalse(supervise.prime_pane("qv-x", "hello"))
        st.assert_not_called()

    def test_swallowed_enter_is_pressed_again(self):
        rule = "─" * 20
        unsent = f"Claude Code\n{rule}\n❯ hello there\n{rule}\n  auto mode"
        sent = f"❯ hello there\n⏺ hi\n{rule}\n❯ \n{rule}\n  auto mode"
        panes = iter(["❯", "❯", unsent, sent])
        with patch.object(supervise, "capture_pane",
                          side_effect=lambda *_a: next(panes)), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text", return_value=True), \
             patch.object(time, "sleep"):
            self.assertTrue(supervise.prime_pane("qv-x", "hello there"))
        sk.assert_called_once_with("qv-x", "Enter")

    def test_prompt_stuck_in_box_reports_false(self):
        rule = "─" * 20
        unsent = f"{rule}\n❯ hello there\n{rule}"
        panes = iter(["❯", "❯"] + [unsent] * 4)
        with patch.object(supervise, "capture_pane",
                          side_effect=lambda *_a: next(panes)), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text", return_value=True), \
             patch.object(time, "sleep"):
            self.assertFalse(supervise.prime_pane("qv-x", "hello there"))
        self.assertEqual(sk.call_count, 3)

    def test_waits_for_the_splash_to_settle_and_retypes_a_lost_prompt(self):
        # opencode draws its splash for a moment; text typed then is
        # dropped and the frame is unchanged, so the prompt goes again.
        ready = "Ask anything..."
        panes = iter(["loading", "loading 2", ready, ready, ready,
                      "> hello there"])
        typed = []
        with patch.object(supervise, "capture_pane",
                          side_effect=lambda *_a: next(panes)), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise, "send_text",
                          side_effect=lambda n, t: typed.append(t) or True), \
             patch.object(time, "sleep"):
            self.assertTrue(supervise.prime_pane("qv-x", "hello there"))
        self.assertEqual(typed, ["hello there", "hello there"])
        sk.assert_not_called()

    def test_a_pane_that_never_takes_the_prompt_is_not_sent(self):
        ready = "Ask anything..."
        with patch.object(supervise, "capture_pane", return_value=ready), \
             patch.object(supervise, "send_key"), \
             patch.object(supervise, "send_text", return_value=True) as st, \
             patch.object(time, "sleep"):
            self.assertFalse(supervise.prime_pane("qv-x", "hello there"))
        self.assertEqual(st.call_count, 2)

    def test_prompt_send_failure_propagates_false(self):
        with patch.object(supervise, "capture_pane", return_value="❯"), \
             patch.object(supervise, "send_text", return_value=False), \
             patch.object(time, "sleep"):
            self.assertFalse(supervise.prime_pane("qv-x", "hello"))


class TestAdoptPending(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)

    def test_pending_launch_tracks_markers(self):
        self.assertFalse(supervise.pending_launch(self.home))  # no dir
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "qv-a.json").write_text(
            json.dumps({"launched_at": time.time()}))
        self.assertTrue(supervise.pending_launch(self.home))
        (folder / "qv-a.json").unlink()
        self.assertFalse(supervise.pending_launch(self.home))

    def test_pending_launch_ignores_abandoned_markers(self):
        # A launch that never wrote a transcript expires like
        # adopt_pending's day cutoff — otherwise it would keep the
        # session scan uncached for a day.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "qv-old.json").write_text(json.dumps(
            {"launched_at": time.time() - supervise.PENDING_MAX_AGE_S - 1}))
        self.assertFalse(supervise.pending_launch(self.home))
        (folder / "qv-new.json").write_text(
            json.dumps({"launched_at": time.time()}))
        self.assertTrue(supervise.pending_launch(self.home))

    def test_adopts_matching_new_session(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = temp_dir(self)  # realpath-resolvable cwd
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))

        sess = _session(path=str(proj))
        transcript = proj / "sess.jsonl"
        transcript.write_text("{}\n")
        rows = [(protocol.session_key("claude", "s1", str(proj)), sess, "active")]
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            adopted = supervise.adopt_pending(self.home, rows)
        self.assertEqual(len(adopted), 1)
        meta = supervise.meta_for(self.home, adopted[0])
        self.assertEqual(meta["tmux"], "qv-new")
        self.assertFalse((folder / "qv-new.json").exists())

    def test_database_harness_adopts_by_activity_time(self):
        # opencode keeps sessions in SQLite: no JSONL birth time, so a
        # session the catalog didn't know at launch, active since, is ours;
        # one active before the launch is not.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = temp_dir(self)
        now = time.time()
        (folder / "qv-oc.json").write_text(json.dumps(
            {"tmux": "qv-oc", "tool_name": "opencode", "cwd": str(proj),
             "launched_at": now, "exclude": ["known"]}))
        def oc(sid, ts):
            return Session(timestamp=int(ts * 1000), agent="OpenCode",
                           path=str(proj), session_id=sid,
                           tool_name="opencode")
        rows = [
            ("opencode:known", oc("known", now + 5), "active"),
            ("opencode:stale", oc("stale", now - 600), "done"),
            ("opencode:new", oc("new", now + 3), "active"),
        ]
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=None):
            self.assertEqual(supervise.adopt_pending(self.home, rows),
                             ["opencode:new"])

    def test_skips_mismatched_and_old(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "old.json").write_text(json.dumps(
            {"tmux": "qv-old", "tool_name": "claude", "cwd": "/x",
             "launched_at": time.time() - 90000}
        ))
        (folder / "other.json").write_text(json.dumps(
            {"tmux": "qv-o", "tool_name": "codex", "cwd": "/y",
             "launched_at": time.time()}
        ))
        rows = [(protocol.session_key("claude", "s1", "/tmp/z"),
                 _session(path="/tmp/z"), "done")]
        self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertFalse((folder / "old.json").exists())   # expired
        self.assertTrue((folder / "other.json").exists())  # unmatched stays

    def test_ambiguous_candidates_never_adopt(self):
        # Two same-cwd sessions with fresh transcripts: adopting either
        # could bind a human session to our pane — stay pending.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = temp_dir(self)
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        rows = [
            (protocol.session_key("claude", "s1", str(proj)),
             _session(path=str(proj)), "active"),
            (protocol.session_key("claude", "s2", str(proj)),
             _session(path=str(proj), sid="s2"), "active"),
        ]
        transcript = proj / "t.jsonl"
        transcript.write_text("{}\n")
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertTrue((folder / "qv-new.json").exists())

    def test_preexisting_recently_written_session_not_adopted(self):
        # A human's same-cwd session written to right after our launch
        # has a fresh mtime but an old birth time — mtime alone would
        # adopt it and bind their transcript to our pane.
        import types

        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = temp_dir(self)
        now = time.time()
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": now}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        rows = [(protocol.session_key("claude", "s1", str(proj)),
                 _session(path=str(proj)), "done")]
        fake_stat = types.SimpleNamespace(
            st_birthtime=now - 3600,  # created an hour before launch
            st_mtime=now,             # but written to just now
            st_ctime=now,
        )
        transcript = MagicMock()
        transcript.stat.return_value = fake_stat
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertTrue((folder / "qv-new.json").exists())  # stays pending

    def test_exclude_ids_never_adopt(self):
        # The marker's exclude list snapshots the catalog at launch: a
        # session it names can never be claimed as ours even with a
        # fresh transcript and unambiguous candidacy.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = temp_dir(self)
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "exclude": ["s1"],
                   "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        rows = [(protocol.session_key("claude", "s1", str(proj)),
                 _session(path=str(proj)), "done")]
        transcript = proj / "t.jsonl"
        transcript.write_text("{}\n")  # created now — mtime alone would pass
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])

    def test_corrupt_marker_removed(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        bad = folder / "bad.json"
        bad.write_text("{not json")
        old = time.time() - supervise._PENDING_UNREADABLE_GRACE_S - 5
        os.utime(bad, (old, old))
        self.assertEqual(supervise.adopt_pending(self.home, []), [])
        self.assertFalse(bad.exists())

    def test_young_unreadable_marker_kept(self):
        # Read mid-write: deleting it would strand the launch for a
        # day. A marker this young is skipped, not removed.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "half.json").write_text('{"tmux": "q-')
        self.assertEqual(supervise.adopt_pending(self.home, []), [])
        self.assertTrue((folder / "half.json").exists())

    def test_no_pending_dir_returns_empty(self):
        self.assertEqual(supervise.adopt_pending(self.home, []), [])

    def test_registered_keys_are_skipped(self):
        # A row already in the registry can't be adopted by a pending
        # marker — supervision wins over the pending claim.
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        proj = temp_dir(self)
        pending = {"tmux": "qv-new", "tool_name": "claude",
                   "cwd": str(proj), "launched_at": time.time()}
        (folder / "qv-new.json").write_text(json.dumps(pending))
        key = protocol.session_key("claude", "s1", str(proj))
        supervise.mark(self.home, key, {"mode": "tmux", "tmux": "qv-other"})
        rows = [(key, _session(path=str(proj)), "active")]
        transcript = proj / "t.jsonl"
        transcript.write_text("{}\n")
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=transcript):
            self.assertEqual(supervise.adopt_pending(self.home, rows), [])
        self.assertTrue((folder / "qv-new.json").exists())  # stays pending


class TestRegistryEdges(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)

    def test_load_filters_nondict_values(self):
        supervise._registry_file(self.home).parent.mkdir(
            parents=True, exist_ok=True
        )
        supervise._registry_file(self.home).write_text(
            json.dumps({"k": {"mode": "tmux"}, "junk": 5, "s": "x"})
        )
        self.assertEqual(set(supervise._load_registry(self.home)), {"k"})

    def test_unmark_missing_key_is_noop(self):
        # pops nothing, writes nothing — no crash, no file churn
        supervise.unmark(self.home, "never-marked")
        self.assertFalse(supervise._registry_file(self.home).exists())

    def test_save_registry_chmod_failure_tolerated(self):
        with patch.object(supervise.os, "chmod", side_effect=OSError):
            supervise.mark(self.home, "k1", {"mode": "headless"})
        self.assertIn("k1", supervise._load_registry(self.home))


class TestLaunchNew(unittest.TestCase):
    def test_launch_writes_pending_and_primes(self):
        home = temp_dir(self)
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise.shutil, "which", return_value="/usr/bin/claude"), \
             patch.object(supervise, "prime_pane") as prime:
            ok, note, name = supervise.launch_new(home, "claude", "/tmp", "hi", binary="claude")
        self.assertTrue(ok)
        prime.assert_called_once_with("qv-n", "hi")
        marker = supervise._pending_dir(home) / "qv-n.json"
        self.assertTrue(marker.exists())
        self.assertEqual(
            json.loads(marker.read_text())["tool_name"], "claude"
        )

    def test_same_second_launches_get_distinct_names(self):
        home = temp_dir(self)
        seen = []
        with patch.object(supervise.shutil, "which", return_value="/x/claude"), \
             patch.object(supervise, "launch_tmux",
                          side_effect=lambda key, *a: seen.append(
                              supervise.tmux_name(key)) or
                              supervise.tmux_name(key)), \
             patch.object(supervise, "prime_pane"):
            supervise.launch_new(home, "claude", "/tmp", "one", binary="claude")
            supervise.launch_new(home, "claude", "/tmp", "two", binary="claude")
        self.assertEqual(len(set(seen)), 2)

    def test_missing_folder_fails_before_tmux(self):
        with patch.object(supervise, "launch_tmux") as tmux, \
             patch.object(supervise.shutil, "which",
                          return_value="/usr/bin/claude"):
            ok, note, name = supervise.launch_new(
                temp_dir(self), "claude", "/no/such/dir", "hi", binary="claude")
        self.assertFalse(ok)
        self.assertIn("no such folder", note)
        tmux.assert_not_called()

    def test_launch_id_rides_marker_into_adoption(self):
        home = temp_dir(self)
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise.shutil, "which",
                          return_value="/usr/bin/claude"), \
             patch.object(supervise, "prime_pane", return_value=True):
            supervise.launch_new(home, "claude", "/tmp", "hi", launch_id="l9", binary="claude")
        marker = supervise._pending_dir(home) / "qv-n.json"
        self.assertEqual(json.loads(marker.read_text())["launch_id"], "l9")
        with patch.object(supervise, "_pending_match",
                          return_value="claude:new"):
            adopted = supervise.adopt_pending(home, [])
        self.assertEqual(adopted, ["claude:new"])
        self.assertEqual(
            supervise.meta_for(home, "claude:new")["launch_id"], "l9")

    def test_binary_overrides_tool_name(self):
        home = temp_dir(self)
        with patch.object(supervise, "launch_tmux",
                          return_value="qv-n") as tmux, \
             patch.object(supervise.shutil, "which",
                          return_value="/usr/bin/cursor-agent"), \
             patch.object(supervise, "prime_pane", return_value=True):
            supervise.launch_new(home, "cursor", "/tmp", "hi",
                                 binary="cursor-agent")
        self.assertEqual(tmux.call_args[0][1], ["cursor-agent"])

    def test_binary_is_required(self):
        # Guessing the command from the tool name launched the wrong
        # binary for harnesses whose registry command differs (#510).
        with self.assertRaises(TypeError):
            supervise.launch_new(
                temp_dir(self), "cursor", "/tmp", "hi")

    def test_unknown_harness(self):
        ok, note, name = supervise.launch_new(
            temp_dir(self), "nope-tool", "/tmp", "",
            binary="nope-tool")
        self.assertFalse(ok)
        self.assertIsNone(name)

    def test_undelivered_prompt_fails_the_launch(self):
        # Nothing was delivered, so no turn will ever start: the record
        # must not sit pending while the phone polls it. The pane itself
        # stays up for a terminal attach.
        home = temp_dir(self)
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise.shutil, "which",
                          return_value="/usr/bin/claude"), \
             patch.object(supervise, "prime_pane", return_value=False):
            ok, note, name = supervise.launch_new(home, "claude", "/tmp", "hi", binary="claude")
        self.assertFalse(ok)
        self.assertEqual(name, "qv-n")
        self.assertIn("could not be delivered", note)
        self.assertIn("pane is still up", note)

    def test_known_ids_land_in_marker(self):
        home = temp_dir(self)
        with patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise.shutil, "which",
                          return_value="/usr/bin/claude"), \
             patch.object(supervise, "prime_pane", return_value=True):
            supervise.launch_new(
                home, "claude", "/tmp", "hi", known_ids=["old-1", "old-2"],
                binary="claude")
        marker = supervise._pending_dir(home) / "qv-n.json"
        self.assertEqual(
            json.loads(marker.read_text())["exclude"], ["old-1", "old-2"]
        )

    def test_detach_kills_tmux(self):
        home = temp_dir(self)
        supervise.mark(home, "k1", {"mode": "tmux", "tmux": "qv-z"})
        with patch.object(supervise, "kill", return_value=True) as k:
            self.assertTrue(supervise.detach(home, "k1"))
        k.assert_called_once_with("qv-z")
        self.assertIsNone(supervise.meta_for(home, "k1"))


class TestLaunchTuning(unittest.TestCase):
    """``model``/``effort`` picks: what a launch (or attach) can apply."""

    def setUp(self):
        self.home = temp_dir(self)
        self.proj = str(temp_dir(self))

    def test_tuning_error_names_the_ops_the_tui_cannot_take(self):
        self.assertEqual(
            supervise.launch_tuning_error("claude", "opus", "high"), "")
        self.assertEqual(
            supervise.launch_tuning_error("codex", "gpt-5", None), "")
        self.assertEqual(supervise.launch_tuning_error("pi", None, None), "")
        self.assertIn(
            "/effort", supervise.launch_tuning_error("codex", None, "high"))
        self.assertIn(
            "/model", supervise.launch_tuning_error("opencode", "x", None))
        self.assertIn(
            "/effort", supervise.launch_tuning_error("opencode", None, "x"))

    def test_channel_tuning_error_per_mode(self):
        # tmux is the launch pane: the TUI-builtin rule applies.
        self.assertEqual(
            supervise.channel_tuning_error("tmux", "claude", "m", "e"), "")
        self.assertIn("/model", supervise.channel_tuning_error(
            "tmux", "gemini", "m", None))
        # headless: --model rides the argv; effort has no flag.
        self.assertEqual(
            supervise.channel_tuning_error("headless", "claude", "m", None),
            "")
        self.assertIn("effort", supervise.channel_tuning_error(
            "headless", "claude", None, "high"))
        # serve takes provider/model (its {providerID, modelID} field)
        # plus an effort variant; a bare model name can't map and is
        # refused rather than silently dropped.
        self.assertIn("provider/model", supervise.channel_tuning_error(
            "serve", "opencode", "m", None))
        self.assertEqual(supervise.channel_tuning_error(
            "serve", "opencode", "acme/m", "high"), "")
        # appserver's turn/start overrides take both.
        self.assertEqual(supervise.channel_tuning_error(
            "appserver", "codex", "gpt-5", "high"), "")
        self.assertEqual(supervise.channel_tuning_error(
            "serve", "opencode", None, None), "")

    def _launch(self, tool="claude", **kw):
        with patch.object(supervise.shutil, "which",
                          return_value="/x/bin"), \
             patch.object(supervise, "launch_tmux",
                          return_value="qv-n") as lt, \
             patch.object(supervise, "prime_pane", return_value=True) as pp, \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "capture_pane",
                          return_value="pane moved"), \
             patch.object(supervise.time, "sleep"), \
             patch.object(supervise, "send_text", return_value=True) as st:
            kw.setdefault("binary", tool)
            out = supervise.launch_new(self.home, tool, self.proj, "hi", **kw)
        self.argv = lt.call_args[0][1] if lt.call_args else None
        return out, pp, st

    def test_launch_puts_the_model_on_argv_and_types_effort(self):
        (ok, _n, name), pp, st = self._launch(model="opus", effort="high")
        self.assertTrue(ok)
        self.assertEqual(name, "qv-n")
        # The model rides the command line; only /effort is typed, before
        # the first prompt.
        self.assertEqual(self.argv, ["claude", "--model", "opus"])
        self.assertEqual([c[0][1] for c in st.call_args_list],
                         ["/effort high"])
        pp.assert_called_once_with("qv-n", "hi")
        marker = json.loads(
            (supervise._pending_dir(self.home) / "qv-n.json").read_text())
        self.assertEqual(
            (marker["model"], marker["effort"]), ("opus", "high"))

    def test_launch_model_argv_per_harness(self):
        cases = {
            "codex": ["codex", "-m", "gpt-5"],
            "cursor": ["cursor", "--model", "gpt-5"],
            "pi": ["pi", "--model", "gpt-5"],
            "cline": ["cline", "-m", "gpt-5"],
            "devin": ["devin", "--model", "gpt-5"],
        }
        for tool, argv in cases.items():
            (ok, _n, _name), _pp, st = self._launch(tool, model="gpt-5")
            self.assertTrue(ok, tool)
            self.assertEqual(self.argv, argv)
            st.assert_not_called()
        (ok, _n, _), _pp, _st = self._launch(
            "opencode", model="anthropic/claude-opus-5")
        self.assertTrue(ok)
        self.assertEqual(self.argv, ["opencode", "mini", "-m",
                                     "anthropic/claude-opus-5"])
        # No pick: the plain harness, the full TUI for opencode.
        self._launch("opencode")
        self.assertEqual(self.argv, ["opencode"])

    def test_droid_model_goes_through_a_settings_file(self):
        (ok, _n, _), _pp, _st = self._launch("droid", model="glm-5")
        self.assertTrue(ok)
        self.assertEqual(self.argv[:2], ["droid", "--settings"])
        written = json.loads(Path(self.argv[2]).read_text())
        self.assertEqual(written,
                         {"sessionDefaultSettings": {"model": "glm-5"}})
        self.assertTrue(Path(self.argv[2]).is_relative_to(
            supervise._launch_settings_dir(self.home)))

    def test_launch_yolo_appends_the_bypass_argv(self):
        cases = {
            "claude": ["claude", "--dangerously-skip-permissions"],
            "codex": ["codex", "--dangerously-bypass-approvals-and-sandbox"],
            "cursor": ["cursor", "--yolo"],
            "devin": ["devin", "--permission-mode", "dangerous"],
            "opencode": ["opencode", "--auto"],
            "droid": ["droid", "--auto", "high"],
            "cline": ["cline", "--yolo"],
            "kilo": ["kilo", "--auto"],
            "qwen-code": ["qwen-code", "--yolo"],
        }
        for tool, argv in cases.items():
            (ok, _n, _name), _pp, st = self._launch(tool, yolo=True)
            self.assertTrue(ok, tool)
            self.assertEqual(self.argv, argv, tool)
            st.assert_not_called()
        # Model and yolo ride the same argv, model's tokens first.
        (ok, _n, _), _pp, _st = self._launch("claude", model="opus", yolo=True)
        self.assertTrue(ok)
        self.assertEqual(self.argv, ["claude", "--model", "opus",
                                     "--dangerously-skip-permissions"])
        marker = json.loads(
            (supervise._pending_dir(self.home) / "qv-n.json").read_text())
        self.assertTrue(marker["yolo"])
        self.assertEqual(marker["model"], "opus")

    def test_launch_refuses_yolo_the_harness_cannot_take(self):
        with patch.object(supervise, "launch_tmux") as lt:
            ok, note, _ = supervise.launch_new(
                self.home, "gemini", self.proj, "hi", yolo=True,
                binary="gemini")
            self.assertFalse(ok)
            self.assertIn("capabilities.yolo", note)
        lt.assert_not_called()
        # harness.json wins over the fallback either way.
        reg = {"gemini": {"capabilities": {"yolo": {"argv": ["--yes-all"]}}},
               "claude": {"capabilities": {"yolo": {"supported": False}}}}
        with patch("swe.harness.registry.load_registry_readonly",
                   return_value=reg):
            (ok, _n, _), _pp, _st = self._launch("gemini", yolo=True)
            self.assertTrue(ok)
            self.assertEqual(self.argv, ["gemini", "--yes-all"])
            ok, note, _ = supervise.launch_new(
                self.home, "claude", self.proj, "hi", yolo=True,
                binary="claude")
            self.assertFalse(ok)
            self.assertIn("capabilities.yolo", note)

    def test_fresh_launch_error_yolo(self):
        self.assertEqual(
            supervise.fresh_launch_error("claude", None, None, yolo=True), "")
        self.assertIn(
            "capabilities.yolo",
            supervise.fresh_launch_error("gemini", None, None, yolo=True))
        # No pick: never a refusal.
        self.assertEqual(
            supervise.fresh_launch_error("gemini", None, None, yolo=False), "")

    def test_launch_refuses_a_model_the_harness_cannot_take(self):
        with patch.object(supervise, "launch_tmux") as lt:
            ok, note, _ = supervise.launch_new(
                self.home, "gemini", self.proj, "hi", model="x", binary="gemini")
            self.assertFalse(ok)
            self.assertIn("capabilities.model", note)
            # opencode mini needs provider/model to pick a model at all.
            ok, note, _ = supervise.launch_new(
                self.home, "opencode", self.proj, "hi", model="x", binary="opencode")
            self.assertFalse(ok)
            self.assertIn("provider/model", note)
            # an empty side is no provider/model either
            ok, note, _ = supervise.launch_new(
                self.home, "opencode", self.proj, "hi", model="acme/",
                binary="opencode")
            self.assertFalse(ok)
            self.assertIn("provider/model", note)
        lt.assert_not_called()

    def test_fresh_launch_error(self):
        self.assertEqual(
            supervise.fresh_launch_error("claude", "opus", "high"), "")
        self.assertEqual(
            supervise.fresh_launch_error("cursor", "gpt-5", None), "")
        self.assertIn(
            "/effort", supervise.fresh_launch_error("cursor", None, "high"))
        self.assertIn(
            "capabilities.model",
            supervise.fresh_launch_error("gemini", "x", None))
        # harness.json wins over the fallback either way.
        reg = {"gemini": {"capabilities": {"model": {"flag": "-m"}}},
               "claude": {"capabilities": {"model": {"supported": False}}}}
        self.assertEqual(
            supervise.fresh_launch_error("gemini", "x", None, reg), "")
        self.assertIn("capabilities.model",
                      supervise.fresh_launch_error("claude", "x", None, reg))

    def test_a_failed_control_send_fails_the_launch(self):
        # The pick asked for could not be applied: the launch record
        # must say failed, not stay pending for a prompt that never
        # went out. The pane stays up for a manual attach.
        with patch.object(supervise.shutil, "which",
                          return_value="/x/bin"), \
             patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "send_text", return_value=False):
            ok, note, name = supervise.launch_new(
                self.home, "claude", self.proj, "hi", effort="high", binary="claude")
        self.assertFalse(ok)
        self.assertEqual(name, "qv-n")
        self.assertIn("could not be delivered", note)

    def test_a_control_swallowed_enter_is_pressed_again(self):
        # Checked delivery, the same race _deliver handles for prompts:
        # /effort stuck in the input box gets Enter pressed for it before
        # the prompt ever goes out.
        rule = "─" * 20
        unsent = f"Claude Code\n{rule}\n❯ /effort high\n{rule}\n  auto"
        sent = f"Claude Code\neffort: high\n{rule}\n❯ \n{rule}\n  auto"
        panes = iter(["❯", "❯", unsent, sent])
        with patch.object(supervise.shutil, "which",
                          return_value="/x/bin"), \
             patch.object(supervise, "launch_tmux", return_value="qv-n"), \
             patch.object(supervise, "prime_pane", return_value=True), \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "capture_pane",
                          side_effect=lambda *_a: next(panes)), \
             patch.object(supervise, "send_key") as sk, \
             patch.object(supervise.time, "sleep"), \
             patch.object(supervise, "send_text", return_value=True):
            ok, _note, _ = supervise.launch_new(
                self.home, "claude", self.proj, "hi", effort="high", binary="claude")
        self.assertTrue(ok)
        self.assertEqual(sk.call_args_list[0][0], ("qv-n", "Enter"))

    def test_fork_records_and_applies_tuning(self):
        src = _session(sid="abc", path=self.proj)
        with patch.object(supervise.shutil, "which",
                          return_value="/x/bin"), \
             patch.object(supervise, "launch_tmux", return_value="qv-f"), \
             patch.object(supervise, "tmux_name", return_value="qv-f"), \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "capture_pane",
                          return_value="pane moved"), \
             patch.object(supervise.time, "sleep"), \
             patch.object(supervise, "prime_pane", return_value=True) as pp, \
             patch.object(supervise, "send_text", return_value=True) as st:
            ok, _n, _ = supervise.launch_fork(
                self.home, src, ["claude"], "go", model="opus")
        self.assertTrue(ok)
        self.assertEqual(st.call_args_list[0][0][1], "/model opus")
        pp.assert_called_once_with("qv-f", "go")
        marker = json.loads(
            (supervise._pending_dir(self.home) / "qv-f.json").read_text())
        self.assertEqual(marker["model"], "opus")

    def test_adopt_pending_carries_the_pick_into_meta(self):
        folder = supervise._pending_dir(self.home)
        folder.mkdir(parents=True)
        (folder / "qv-f.json").write_text(json.dumps({
            "tmux": "qv-f", "tool_name": "claude", "cwd": self.proj,
            "launched_at": time.time(), "launch_id": "l1",
            "model": "opus", "effort": "high"}))
        with patch.object(supervise, "_pending_match",
                          return_value="claude:new"):
            self.assertEqual(
                supervise.adopt_pending(self.home, []), ["claude:new"])
        meta = supervise.meta_for(self.home, "claude:new")
        self.assertEqual((meta["model"], meta["effort"]), ("opus", "high"))

    def _attach(self, session, mode, **kw):
        key = f"{session.tool_name}:{session.session_id}"
        with patch.object(supervise, "resume_flags",
                          return_value=["--resume", "abc"]), \
             patch.object(supervise, "resolve_mode",
                          return_value=mode), \
             patch.object(supervise, "launch_tmux",
                          return_value="qv-a"), \
             patch.object(supervise, "boot_pane",
                          return_value="❯"), \
             patch.object(supervise, "capture_pane",
                          return_value="pane moved"), \
             patch.object(supervise.time, "sleep"), \
             patch.object(supervise, "tmux_alive",
                          return_value=True), \
             patch.object(supervise, "_local_writer_pids",
                          return_value=[]), \
             patch.object(supervise, "_stop_local_writers"), \
             patch.object(supervise, "send_text",
                          return_value=True) as st, \
             patch.object(supervise.shutil, "which",
                          return_value="/x/bin"):
            out = supervise.attach(
                self.home, key, session,
                last_append_at=None, status="done", mode=mode, **kw)
        return out, st, key

    def _tmux_patches(self, boot):
        import contextlib
        stack = contextlib.ExitStack()
        for target, kw in (
            ("resume_flags", {"return_value": ["--resume", "abc"]}),
            ("resolve_mode", {"return_value": "tmux"}),
            ("launch_tmux", {"return_value": "qv-a"}),
            ("boot_pane", {"side_effect": boot}),
            ("tmux_alive", {"return_value": True}),
            ("_local_writer_pids", {"return_value": []}),
            ("_stop_local_writers", {}),
        ):
            stack.enter_context(patch.object(supervise, target, **kw))
        stack.enter_context(patch.object(supervise.shutil, "which",
                                         return_value="/x/bin"))
        return stack, stack.enter_context(patch.object(supervise, "kill"))

    def test_a_booting_attach_holds_only_its_own_key(self):
        # The pane's boot (up to ATTACH_BOOT_S) ran under the registry
        # lock: every other attach, detach, keep and the catalog's
        # registry read waited for it. Now another key's write goes
        # through, while a detach of this key waits for the attach.
        import threading
        booting, release = threading.Event(), threading.Event()

        def boot(name, timeout):
            booting.set()
            release.wait(5)
            return "❯"

        src = _session(sid="abc", path=self.proj)
        key = "claude:abc"
        stack, _kill = self._tmux_patches(boot)
        with stack:
            out = []
            t = threading.Thread(target=lambda: out.append(supervise.attach(
                self.home, key, src, last_append_at=None, status="done",
                mode="tmux")))
            t.start()
            self.assertTrue(booting.wait(5))
            other = threading.Thread(target=supervise.mark, args=(
                self.home, "claude:other", {"mode": "headless"}))
            other.start()
            other.join(2)
            self.assertFalse(other.is_alive())
            detached = threading.Event()
            d = threading.Thread(target=lambda: (
                supervise.detach(self.home, key), detached.set()))
            d.start()
            self.assertFalse(detached.wait(0.3))
            release.set()
            t.join(5)
            d.join(5)
        self.assertTrue(out[0][0], out)
        self.assertTrue(detached.is_set())
        # The detach acted on the finished attach, not before it.
        self.assertIsNone(supervise.meta_for(self.home, key))
        self.assertIsNotNone(supervise.meta_for(self.home, "claude:other"))

    def test_attach_elsewhere_during_boot_wins(self):
        # Another process attached the key while this pane booted: its
        # entry stands and this pane is closed.
        key = "claude:abc"

        def boot(name, timeout):
            supervise.mark(self.home, key, {"mode": "tmux", "tmux": "qv-b"})
            return "❯"

        stack, kill = self._tmux_patches(boot)
        with stack:
            ok, note = supervise.attach(
                self.home, key, _session(sid="abc", path=self.proj),
                last_append_at=None, status="done", mode="tmux")
        self.assertTrue(ok)
        self.assertEqual(note, "already attached")
        kill.assert_called_once_with("qv-a")
        self.assertEqual(supervise.meta_for(self.home, key)["tmux"], "qv-b")

    def test_attach_elsewhere_during_boot_with_another_model_is_refused(self):
        # The winning entry runs a different model than this attach
        # asked for: report that, not success.
        key = "claude:abc"

        def boot(name, timeout):
            supervise.mark(self.home, key,
                           {"mode": "headless", "model": "sonnet"})
            return "❯"

        stack, kill = self._tmux_patches(boot)
        with stack, patch.object(supervise, "_send_launch_controls",
                                 return_value=True):
            ok, note = supervise.attach(
                self.home, key, _session(sid="abc", path=self.proj),
                last_append_at=None, status="done", mode="tmux",
                model="opus")
        self.assertFalse(ok)
        self.assertIn("/model", note)
        kill.assert_called_once_with("qv-a")
        self.assertEqual(supervise.meta_for(self.home, key)["model"], "sonnet")

    def test_attach_tmux_types_the_pick_and_records_it(self):
        (ok, note), st, key = self._attach(
            _session(sid="abc", path=self.proj), "tmux", model="opus")
        self.assertTrue(ok, note)
        self.assertEqual(st.call_args_list[0][0][1], "/model opus")
        meta = supervise.meta_for(self.home, key)
        self.assertEqual(meta["model"], "opus")

    def test_attach_headless_records_model_and_refuses_effort(self):
        src = _session(sid="abc", path=self.proj)
        (ok, note), _st, key = self._attach(src, "headless", model="opus")
        self.assertTrue(ok, note)
        self.assertEqual(supervise.meta_for(self.home, key)["model"], "opus")
        supervise.unmark(self.home, key)
        (ok, note), _st, _ = self._attach(src, "headless", effort="high")
        self.assertFalse(ok)
        self.assertIn("effort", note)

    def test_attach_serve_needs_provider_slash_model(self):
        src = _session(tool="opencode", sid="abc", path=self.proj)
        (ok, note), _st, key = self._attach(src, "serve", model="opus")
        self.assertFalse(ok)
        self.assertIn("provider/model", note)
        self.assertIsNone(supervise.meta_for(self.home, key))
        (ok, note), _st, key = self._attach(
            src, "serve", model="acme/kimi-k3", effort="high")
        self.assertTrue(ok, note)
        meta = supervise.meta_for(self.home, key)
        self.assertEqual((meta["model"], meta["effort"]),
                         ("acme/kimi-k3", "high"))

    def test_attach_appserver_records_the_pick(self):
        src = _session(tool="codex", sid="abc", path=self.proj)
        (ok, note), _st, key = self._attach(
            src, "appserver", model="gpt-5", effort="high")
        self.assertTrue(ok, note)
        meta = supervise.meta_for(self.home, key)
        self.assertEqual((meta["model"], meta["effort"]),
                         ("gpt-5", "high"))


class TestStopLocalWriters(unittest.TestCase):
    """Attach SIGTERMs the original resumed writer when identifiable."""

    def _run_with(self, pgrep_out, ps_map):
        """ps_map: pid-string -> command line (or None for no process)."""
        calls = []

        def fake_run(argv, **_kw):
            cp = MagicMock()
            if argv[0] == "pgrep":
                cp.stdout = pgrep_out
            elif argv[0] == "ps":
                cp.stdout = ps_map.get(argv[2], "") or ""
            return cp

        with patch.object(supervise.subprocess, "run", side_effect=fake_run), \
             patch.object(supervise.os, "kill") as kill:
            supervise._stop_local_writers(_session(sid="abc-123"))
            calls.extend(c[0] for c in kill.call_args_list)
        return calls

    def test_kills_matching_writer_only(self):
        calls = self._run_with(
            "1001\n1002\n1003\n",
            {
                "1001": "claude --resume abc-123",
                "1002": "vim notes-about-abc-123",   # wrong binary
            },
        )
        # 1003's ps lookup returns "" → skipped by the binary check
        self.assertEqual(calls, [(1001, supervise.signal.SIGTERM)])

    def test_curl_with_session_url_not_killed(self):
        # `curl .../claude:abc-123/control` matches the pgrep -f sweep
        # but argv[0] is curl, not the harness — it must survive.
        calls = self._run_with(
            "2001\n2002\n",
            {
                "2001": "curl -X POST http://hub/v1/sessions/claude:abc-123/control",
                "2002": "claude --resume abc-123",
            },
        )
        self.assertEqual(calls, [(2002, supervise.signal.SIGTERM)])

    def test_codex_writer_matched_by_thread_uuid(self):
        # codex resume takes the thread uuid lifted out of the rollout
        # stem — the stored session_id never appears on the cmdline.
        sid = ("rollout-2026-01-02T03-04-05-"
               "deadbeef-1234-1234-1234-123456789abc")
        sess = _session(tool="codex", sid=sid)
        calls = []

        def fake_run(argv, **_kw):
            cp = MagicMock()
            if argv[0] == "pgrep":
                cp.stdout = "3001\n3002\n"
            else:
                cp.stdout = {
                    "3001": "codex resume "
                            "deadbeef-1234-1234-1234-123456789abc",
                    "3002": f"curl http://x/{sid}",
                }[argv[2]]
            return cp

        with patch.object(supervise.subprocess, "run",
                          side_effect=fake_run), \
             patch.object(supervise.os, "kill") as kill:
            supervise._stop_local_writers(sess)
            calls.extend(c[0] for c in kill.call_args_list)
        self.assertEqual(calls, [(3001, supervise.signal.SIGTERM)])

    def test_writer_with_id_only_in_env_not_killed(self):
        # The id as a substring of an unrelated token ("log-abc-123")
        # must not qualify — only whole argv tokens count.
        calls = self._run_with(
            "4001\n",
            {"4001": "claude --resume log-abc-123-other"},
        )
        self.assertEqual(calls, [])

    def test_skips_self_pid(self):
        calls = self._run_with(
            f"{supervise.os.getpid()}\n",
            {str(supervise.os.getpid()): "claude --resume abc-123"},
        )
        self.assertEqual(calls, [])

    def test_pgrep_failure_tolerated(self):
        with patch.object(
            supervise.subprocess, "run", side_effect=OSError("no pgrep")
        ), patch.object(supervise.os, "kill") as kill:
            supervise._stop_local_writers(_session(sid="abc-123"))
        kill.assert_not_called()

    def test_empty_sid_returns_early(self):
        with patch.object(
            supervise.subprocess, "run", side_effect=AssertionError("pgrep ran")
        ):
            supervise._stop_local_writers(_session(sid=""))


class TestAttachMtime(unittest.TestCase):
    """The fresh-mtime recheck: cached status lies, the file does not."""

    def setUp(self):
        self.home = temp_dir(self)
        self.key = "claude:abc-123"
        self.session = _session(sid="abc-123", path="/tmp/proj")
        # Neither pgrep/ps over this machine's processes nor tmux on its
        # default server; tests that need either patch it themselves.
        for p in (patch.object(supervise, "_local_writer_pids",
                               return_value=[]),
                  patch.object(supervise, "tmux_alive", return_value=False)):
            p.start()
            self.addCleanup(p.stop)

    def test_fresh_transcript_refused(self):
        fresh = temp_dir(self) / "live.jsonl"
        fresh.write_text("{}\n")  # mtime = now
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=fresh), \
             patch.object(supervise, "_stop_local_writers"):
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done",
            )
        self.assertFalse(ok)
        self.assertIn("idle", note)

    def test_stale_transcript_passes(self):
        old = temp_dir(self) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch("shutil.which", return_value="/x/claude"), \
             patch.object(supervise, "_stop_local_writers"):
            ok, _ = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="headless",
            )
        self.assertTrue(ok)

    def test_tmux_handoff_failure_leaves_writer_alive(self):
        # When the tmux replacement can't be stood up the original
        # writer must stay: killing it first would leave the user with
        # a dead session and no supervision.
        old = temp_dir(self) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "launch_tmux",
                          return_value=None) as launch, \
             patch.object(supervise, "_stop_local_writers") as stop:
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="tmux",
            )
        self.assertFalse(ok)
        self.assertIn("tmux", note)
        launch.assert_called_once()
        stop.assert_not_called()
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_second_attach_is_noop_under_lock(self):
        # Two attaches racing the same session: the loser sees the
        # winner's registry row instead of relaunching over it.
        supervise.mark(self.home, self.key,
                       {"mode": "tmux", "tmux": "qv-winner"})
        with patch.object(supervise, "launch_tmux") as launch, \
             patch.object(supervise, "_stop_local_writers") as stop:
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done",
            )
        self.assertTrue(ok)
        self.assertIn("already", note)
        launch.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(
            supervise.meta_for(self.home, self.key)["tmux"], "qv-winner"
        )

    def test_tmux_handoff_replaces_then_marks(self):
        # Happy path: new pane stands up, original writer is stopped,
        # registry claims the session — in that order.
        old = temp_dir(self) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        order = []
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "launch_tmux",
                          side_effect=lambda *a, **k: order.append(
                              "launch") or "qv-new"), \
             patch.object(supervise, "boot_pane",
                          side_effect=lambda *a, **k: order.append("boot")
                          or "❯"), \
             patch.object(supervise, "tmux_alive", return_value=True), \
             patch.object(supervise, "_stop_local_writers",
                          side_effect=lambda *a, **k: order.append("stop")):
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="tmux",
            )
        self.assertTrue(ok)
        self.assertEqual(order, ["launch", "boot", "stop"])
        meta = supervise.meta_for(self.home, self.key)
        self.assertEqual(meta["tmux"], "qv-new")
        self.assertEqual(meta["mode"], "tmux")

    def test_tmux_handoff_fails_when_boot_kills_the_pane(self):
        # Claude quitting at its trust gate (or any boot screen) must
        # fail the attach and leave the original writer running.
        old = temp_dir(self) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "launch_tmux", return_value="qv-new"), \
             patch.object(supervise, "boot_pane", return_value="❯") as boot, \
             patch.object(supervise, "tmux_alive", return_value=False), \
             patch.object(supervise, "_stop_local_writers") as stop:
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="tmux",
            )
        self.assertFalse(ok)
        self.assertIn("exited", note)
        boot.assert_called_once()
        stop.assert_not_called()
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_tmux_handoff_fails_when_boot_times_out(self):
        # A pane that never settles is not ready for the phone's write:
        # drop it and leave the original writer running.
        old = temp_dir(self) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "launch_tmux", return_value="qv-new"), \
             patch.object(supervise, "boot_pane", return_value=None), \
             patch.object(supervise, "kill") as kill, \
             patch.object(supervise, "_stop_local_writers") as stop:
            ok, note = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="tmux",
            )
        self.assertFalse(ok)
        self.assertIn("did not finish", note)
        kill.assert_called_once_with("qv-new")
        stop.assert_not_called()
        self.assertIsNone(supervise.meta_for(self.home, self.key))

    def test_tmux_handoff_spares_the_replacement(self):
        # The replacement runs the same `claude --resume <id>` argv, so
        # only pids listed before launch may be stopped.
        old = temp_dir(self) / "old.jsonl"
        old.write_text("{}\n")
        import os
        stale = time.time() - 3600
        os.utime(old, (stale, stale))
        live = [111]
        with patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=old), \
             patch.object(supervise, "_local_writer_pids",
                          side_effect=lambda _s: list(live)), \
             patch.object(supervise, "launch_tmux",
                          side_effect=lambda *a, **k: live.append(222)
                          or "qv-new"), \
             patch.object(supervise, "boot_pane", return_value="❯"), \
             patch.object(supervise, "tmux_alive", return_value=True), \
             patch.object(supervise.os, "kill") as kill:
            ok, _ = supervise.attach(
                self.home, self.key, self.session,
                last_append_at=None, status="done", mode="tmux",
            )
        self.assertTrue(ok)
        kill.assert_called_once_with(111, supervise.signal.SIGTERM)


class TestAttachModeGates(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.session = Session(
            timestamp=0, agent="Claude Code", path="/tmp/p",
            session_id="abc", tool_name="claude")

    def test_appserver_rejects_non_codex(self):
        ok, note = supervise.attach(
            self.home, "claude:abc", self.session,
            last_append_at=None, status="done", mode="appserver")
        self.assertFalse(ok)
        self.assertIn("codex", note)

    def test_unknown_mode(self):
        ok, note = supervise.attach(
            self.home, "claude:abc", self.session,
            last_append_at=None, status="done", mode="bogus")
        self.assertFalse(ok)
        self.assertIn("unknown attach mode", note)

    def test_serve_needs_opencode_binary(self):
        session = Session(
            timestamp=0, agent="opencode", path="/tmp/p",
            session_id="o1", tool_name="opencode")
        with patch("shutil.which", return_value=None):
            ok, note = supervise.attach(
                self.home, "opencode:o1", session,
                last_append_at=None, status="done", mode="serve")
        self.assertFalse(ok)
        self.assertIn("not installed", note)

    def test_fingerprint_hashes_non_marker_lines(self):
        # The `or _APPROVAL_ITEM` clause fires on non-marker lines;
        # non-qualifying chatter must not rotate the fingerprint.
        p1 = "chatter\n\nDo you want to proceed?\n❯ 1. Yes\n❯ 2. No"
        p2 = "different chatter\n\n\nDo you want to proceed?\n❯ 1. Yes\n❯ 2. No"
        self.assertEqual(
            supervise.approval_fingerprint(p1),
            supervise.approval_fingerprint(p2))
        p3 = "chatter\nDo you want to proceed?\n❯ 1. Yes\n❯ 2. Never"
        self.assertNotEqual(
            supervise.approval_fingerprint(p1),
            supervise.approval_fingerprint(p3))


class TestResolveMode(unittest.TestCase):
    def test_claude_auto_prefers_tmux(self):
        with patch.object(supervise, "tmux_bin", return_value="/x/tmux"):
            self.assertEqual(
                supervise.resolve_mode(_session(), "auto"), "tmux"
            )

    def test_claude_auto_headless_without_tmux(self):
        with patch.object(supervise, "tmux_bin", return_value=None), \
             patch("shutil.which", return_value="/x/claude"):
            self.assertEqual(
                supervise.resolve_mode(_session(), "auto"), "headless"
            )

    def test_claude_auto_falls_back_to_tmux(self):
        # Neither binary: still tmux, so attach's launch can 422 plainly.
        with patch.object(supervise, "tmux_bin", return_value=None), \
             patch("shutil.which", return_value=None):
            self.assertEqual(
                supervise.resolve_mode(_session(), "auto"), "tmux"
            )

    def test_opencode_auto_unchanged(self):
        session = _session(tool="opencode", sid="o1")
        with patch("shutil.which", return_value="/x/opencode"):
            self.assertEqual(
                supervise.resolve_mode(session, "auto"), "serve"
            )
        with patch("shutil.which", return_value=None):
            self.assertEqual(
                supervise.resolve_mode(session, "auto"), "tmux"
            )

    def test_codex_auto_unchanged(self):
        session = _session(tool="codex", sid="c1")
        with patch("shutil.which", return_value="/x/codex"):
            self.assertEqual(
                supervise.resolve_mode(session, "auto"), "appserver"
            )
        with patch("shutil.which", return_value=None):
            self.assertEqual(
                supervise.resolve_mode(session, "auto"), "tmux"
            )

    def test_headless_attach_checks_claude_binary(self):
        # attach can't register a headless channel whose binary is absent.
        home = temp_dir(self)
        session = _session(sid="abc")
        with patch("shutil.which", return_value=None), \
             patch("swe.remote.tail.resolve_jsonl_path",
                   return_value=None):
            ok, note = supervise.attach(
                home, "claude:abc", session,
                last_append_at=None, status="done", mode="headless")
        self.assertFalse(ok)
        self.assertEqual(note, "claude is not installed")
        self.assertIsNone(supervise.meta_for(home, "claude:abc"))


if __name__ == "__main__":
    unittest.main()
