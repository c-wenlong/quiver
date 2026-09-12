"""~/.quiver/.linkignore: paths swe init must leave alone.

A harness can keep its own skills or instructions by listing its path. The
plan still sees the path, stamps it ``ignored``, and nothing acts on it.
"""

import io
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quiver.init import commands as init_commands
from quiver.init.layout import (
    is_linkignored,
    link_states,
    linkignore_file,
    load_linkignore,
    plan,
)


def _home(tmp: str) -> Path:
    home = Path(tmp)
    (home / ".quiver").mkdir()
    for rel in (".claude", ".codex", ".agents"):
        (home / rel).mkdir(parents=True)
    return home


def _skill(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")


def _ignore(home: Path, body: str) -> None:
    linkignore_file(home).write_text(body)


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class LoadTest(unittest.TestCase):
    def test_missing_file_means_no_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_linkignore(_home(tmp)), [])

    def test_comments_blanks_and_prefixes_are_normalised(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _ignore(home, "# a comment\n\n~/.agents/skills/\n  .config/*/AGENTS.md  \n/.pane\n")
            self.assertEqual(
                load_linkignore(home),
                [".agents/skills", ".config/*/AGENTS.md", ".pane"],
            )


class MatchTest(unittest.TestCase):
    def test_exact_wildcard_and_parent_matches(self):
        home = Path("/h")
        patterns = [".agents/skills", ".config/*/AGENTS.md", ".pane"]
        self.assertTrue(is_linkignored(home / ".agents/skills", home, patterns))
        self.assertTrue(is_linkignored(home / ".config/crush/AGENTS.md", home, patterns))
        self.assertTrue(is_linkignored(home / ".pane/skills", home, patterns))
        self.assertFalse(is_linkignored(home / ".agents/AGENTS.md", home, patterns))
        self.assertFalse(is_linkignored(home / ".claude/skills", home, patterns))
        self.assertFalse(is_linkignored(Path("/elsewhere/.pane"), home, patterns))

    def test_no_patterns_never_matches(self):
        home = Path("/h")
        self.assertFalse(is_linkignored(home / ".agents/skills", home, []))


class PlanTest(unittest.TestCase):
    def test_ignored_skill_root_is_stamped_not_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _skill(home / ".agents/skills", "only-here")
            _ignore(home, ".agents/skills\n")
            _, skills = plan(home)
            by_label = {s.label: s for s in skills}
            self.assertEqual(by_label["agents"].state, "ignored")
            self.assertIn(".linkignore", by_label["agents"].detail)
            self.assertEqual(by_label["claude"].state, "create")

    def test_ignored_instruction_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _ignore(home, ".codex\n")
            instructions, _ = plan(home)
            by_label = {s.label: s for s in instructions}
            self.assertEqual(by_label["codex"].state, "ignored")
            self.assertEqual(by_label["claude"].state, "create")

    def test_link_states_carries_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _ignore(home, ".codex\n")
            states = link_states(home)
            self.assertEqual(states["codex"]["agents"], "ignored")
            self.assertEqual(states["codex"]["skills"], "ignored")


class CmdInitTest(unittest.TestCase):
    def _run(self, home: Path, args):
        with mock.patch.object(Path, "home", staticmethod(lambda: home)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = init_commands.cmd_init(args)
            return code, _plain(buf.getvalue())

    def test_ignored_root_is_never_touched_or_reported_as_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            agents = home / ".agents/skills"
            _skill(agents, "only-here")
            _ignore(home, ".agents/skills\n")

            code, out = self._run(home, [])
            self.assertEqual(code, 0)
            self.assertFalse(agents.is_symlink())
            self.assertTrue((agents / "only-here/SKILL.md").is_file())
            self.assertIn("1 ignored", out)
            self.assertNotIn("exist nowhere else", out)
            self.assertNotIn("Left alone", out)
            self.assertEqual(list((home / ".quiver/backups").iterdir()), [])

    def test_force_still_respects_ignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            agents = home / ".agents/skills"
            _skill(agents, "only-here")
            _ignore(home, ".agents\n")
            self._run(home, ["--force"])
            self.assertFalse(agents.is_symlink())

    def test_full_view_names_the_ignored_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _skill(home / ".agents/skills", "only-here")
            _ignore(home, ".agents/skills\n")
            _, out = self._run(home, ["--full"])
            self.assertRegex(out, r"ignored\s+~/.agents/skills\s+listed in ~/.quiver/.linkignore")

    def test_init_seeds_a_commented_linkignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _, out = self._run(home, [])
            self.assertIn("seed .linkignore", out)
            body = linkignore_file(home).read_text()
            self.assertTrue(body.startswith("#"))
            self.assertEqual(load_linkignore(home), [], "seed must ignore nothing")

    def test_protected_hint_mentions_linkignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = _home(tmp)
            _skill(home / ".agents/skills", "only-here")
            _, out = self._run(home, [])
            self.assertIn("exist nowhere else", out)
            self.assertIn(".linkignore", out)


if __name__ == "__main__":
    unittest.main()
