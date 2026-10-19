"""`swe skills new`: skeleton SKILL.md in the hub skills root."""

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


class SkillsNewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name)
        (self.home / ".quiver" / "skills").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_skills(self, *args):
        """In-process: swe skills <args> against the throwaway HOME."""
        from quiver.skills.commands import cmd_skills

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
             mock.patch.object(pathlib.Path, "home",
                               classmethod(lambda cls: self.home)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_skills(list(args))
        return code, out.getvalue(), err.getvalue()

    def skill(self, name):
        return self.home / ".quiver" / "skills" / name / "SKILL.md"

    def test_creates_exact_content(self):
        code, out, err = self.run_skills("new", "my-skill")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            self.skill("my-skill").read_text(),
            "---\n"
            "name: my-skill\n"
            "description: Describe when an agent should use this skill.\n"
            "---\n"
            "\n"
            "# my-skill\n"
            "\n"
            "Write the instructions here.\n")
        self.assertIn("Created my-skill", out)

    def test_bad_names_refuse(self):
        for name in ("My-Skill", "a b", "-lead", "trail-", "a--b",
                     "a" * 65, "UPPER"):
            with self.subTest(name=name):
                code, _, _ = self.run_skills("new", name)
                self.assertEqual(code, 1)
                self.assertFalse(self.skill(name).exists())
        code, _, _ = self.run_skills("new", "a" * 64)
        self.assertEqual(code, 0)

    def test_newline_description_refused(self):
        code, _, _ = self.run_skills("new", "s", "--description=a\nb")
        self.assertEqual(code, 1)

    def test_description_round_trips_quoted(self):
        desc = 'Use when: testing "quotes" # and more'
        code, out, err = self.run_skills("new", "s", f"--description={desc}")
        self.assertEqual(code, 0, out + err)
        from quiver.skills.discovery import parse_skill_md
        name, parsed = parse_skill_md(self.skill("s"))
        self.assertEqual(name, "s")
        self.assertEqual(parsed, desc)

    def test_existing_name_refused_and_untouched(self):
        self.skill("s").parent.mkdir()
        self.skill("s").write_text("custom\n")
        code, _, _ = self.run_skills("new", "s")
        self.assertEqual(code, 1)
        self.assertEqual(self.skill("s").read_text(), "custom\n")

    def test_missing_quiver_dir_refuses(self):
        import shutil
        shutil.rmtree(self.home / ".quiver")
        code, out, err = self.run_skills("new", "s")
        self.assertEqual(code, 1)
        self.assertIn("swe init", out + err)

    def test_missing_skills_dir_is_created(self):
        import shutil
        shutil.rmtree(self.home / ".quiver" / "skills")
        code, out, err = self.run_skills("new", "s")
        self.assertEqual(code, 0, out + err)

    def test_json_shape_and_listing(self):
        code, out, _ = self.run_skills("new", "s", "--json")
        doc = json.loads(out)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["data"]["name"], "s")
        self.assertTrue(doc["data"]["path"].endswith("SKILL.md"))


if __name__ == "__main__":
    unittest.main()
