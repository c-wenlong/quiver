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
        (self.home / ".swe" / "skills").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def run_skills(self, *args):
        """In-process: swe skills <args> against the throwaway HOME."""
        from swe.skills.commands import cmd_skills

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
             mock.patch.object(pathlib.Path, "home",
                               classmethod(lambda cls: self.home)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_skills(list(args))
        return code, out.getvalue(), err.getvalue()

    def skill(self, name):
        return self.home / ".swe" / "skills" / name / "SKILL.md"

    def test_creates_exact_content(self):
        code, out, err = self.run_skills("new", "my-skill")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(
            self.skill("my-skill").read_text(),
            "---\n"
            "name: 'my-skill'\n"
            "description: 'Describe when an agent should use this skill.'\n"
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
        from swe.skills.discovery import parse_skill_md
        name, parsed = parse_skill_md(self.skill("s"))
        self.assertEqual(name, "s")
        self.assertEqual(parsed, desc)

    def test_description_with_both_quotes_round_trips_as_yaml(self):
        from swe.skills.discovery import parse_skill_md

        for i, desc in enumerate(["it's \"quoted\"", "- starts with a dash", "ends with '"]):
            name = f"s{i}"
            code, out, err = self.run_skills("new", name, f"--description={desc}")
            self.assertEqual(code, 0, out + err)
            self.assertEqual(parse_skill_md(self.skill(name))[1], desc)
        # Single-quoted YAML: the quote is doubled, nothing else escaped.
        self.assertIn("description: 'it''s \"quoted\"'", self.skill("s0").read_text())

    def test_yaml_lookalikes_stay_text(self):
        from swe.skills.discovery import parse_skill_md

        code, out, err = self.run_skills("new", "123", "--description=true")
        self.assertEqual(code, 0, out + err)
        text = self.skill("123").read_text()
        self.assertIn("name: '123'\n", text)
        self.assertIn("description: 'true'\n", text)
        self.assertEqual(parse_skill_md(self.skill("123")), ("123", "true"))

    def test_carriage_return_refused(self):
        code, _, _ = self.run_skills("new", "s", "--description=a\rb")
        self.assertEqual(code, 1)
        self.assertFalse(self.skill("s").parent.exists())

    def test_failed_write_leaves_no_folder(self):
        from swe.skills import create as create_mod

        with mock.patch.object(create_mod.tempfile, "mkstemp", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                create_mod.create("s", home=self.home)
        self.assertFalse(self.skill("s").parent.exists())

    def test_help_and_usage_errors(self):
        code, out, _ = self.run_skills("new", "--help")
        self.assertEqual(code, 0)
        self.assertIn("swe skills new", out)
        self.assertEqual(self.run_skills("new")[0], 1)
        self.assertEqual(self.run_skills("new", "help")[0], 0)
        self.assertTrue(self.skill("help").exists())
        self.assertEqual(self.run_skills("new", "a", "b")[0], 1)
        self.assertEqual(self.run_skills("new", "a", "--bogus")[0], 1)
        self.assertEqual(self.run_skills("new", "a", "--description")[0], 1)

    def test_discovery_reads_double_quoted_values(self):
        from swe.skills.discovery import parse_skill_md

        md = self.home / "x" / "SKILL.md"
        md.parent.mkdir()
        md.write_text('---\nname: "x"\ndescription: "say \\"hi\\" \\\\ ok"\n---\n')
        self.assertEqual(parse_skill_md(md), ("x", 'say "hi" \\ ok'))

    def test_existing_name_refused_and_untouched(self):
        self.skill("s").parent.mkdir()
        self.skill("s").write_text("custom\n")
        code, _, _ = self.run_skills("new", "s")
        self.assertEqual(code, 1)
        self.assertEqual(self.skill("s").read_text(), "custom\n")

    def test_missing_quiver_dir_refuses(self):
        import shutil
        shutil.rmtree(self.home / ".swe")
        code, out, err = self.run_skills("new", "s")
        self.assertEqual(code, 1)
        self.assertIn("swe init", out + err)

    def test_missing_skills_dir_is_created(self):
        import shutil
        shutil.rmtree(self.home / ".swe" / "skills")
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
