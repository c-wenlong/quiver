"""`--json` for the link views: `swe init --check` and `swe find skills`.

Both feed the macOS app's AGENTS.md and Skills pages, which decode stdout
whole, so under --json each command prints one enveloped document and
nothing else, and neither may write anywhere under HOME.

Each test builds a throwaway HOME whose link states are known by
construction (a symlink is linked, a real file is a conflict, a real
directory holding a skill nobody else has is keep) and asserts those literal
states, never a value recomputed the way the code computes it.
"""

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from test_json_output import REGISTRY, SRC, decode, run_swe, write_registry
except ImportError:  # run as `python3 -m unittest tests.test_link_json`
    from tests.test_json_output import (
        REGISTRY, SRC, decode, run_swe, write_registry)


def run_swe_in(home: Path, cwd: Path, *args: str):
    """run_swe from another working directory (`swe find` scans the cwd)."""
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(SRC),
        "NO_COLOR": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "swe.cli", *args],
        capture_output=True, text=True, env=env, timeout=60, cwd=cwd,
    )


def write_skill(skills: Path, name: str) -> Path:
    folder = skills / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    return folder


def build_machine(home: Path) -> Path:
    """claude is fully linked; codex has its own instructions and skills.

    ~/.claude/CLAUDE.md            -> ~/.swe/AGENTS.md   (linked)
    ~/.claude/skills               -> ~/.swe/skills      (linked)
    ~/.codex/AGENTS.md             a real file              (conflict)
    ~/.codex/skills/only-codex     a skill nobody else has  (keep)
    """
    quiver = home / ".swe"
    agents = quiver / "AGENTS.md"
    agents.parent.mkdir(parents=True, exist_ok=True)
    agents.write_text("# shared instructions\n")
    shared = quiver / "skills"
    write_skill(shared, "alpha")
    write_skill(shared, "beta")
    write_registry(home, REGISTRY)

    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").symlink_to(agents)
    (home / ".claude" / "skills").symlink_to(shared)

    (home / ".codex").mkdir()
    (home / ".codex" / "AGENTS.md").write_text("codex's own notes\n")
    write_skill(home / ".codex" / "skills", "only-codex")
    return shared


def snapshot(home: Path) -> dict:
    """Every path under HOME with its type and content, links unfollowed."""
    seen = {}
    for dirpath, dirnames, filenames in os.walk(home):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(home))
            if path.is_symlink():
                seen[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                seen[rel] = ("dir", None)
            else:
                seen[rel] = ("file", path.read_bytes())
    return seen


class InitCheckJsonTest(unittest.TestCase):
    def setUp(self):
        # resolve(): on macOS the temp dir is a symlink, and `swe find`
        # compares the cwd (resolved) against HOME to decide a root's scope.
        self.home = Path(tempfile.mkdtemp()).resolve()
        build_machine(self.home)

    def init_check(self, *extra):
        result = run_swe(self.home, "init", "--check", "--json", *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return decode(result)

    def test_reports_the_shared_file_and_each_harness_link_state(self):
        doc = self.init_check()
        self.assertEqual(doc["schema"], 1)
        data = doc["data"]
        self.assertEqual(
            data["agents_file"], str(self.home / ".swe" / "AGENTS.md"))
        self.assertTrue(data["agents_file_exists"])
        rows = {row["name"]: row for row in data["harnesses"]}
        self.assertEqual(rows["claude"]["instructions"], {
            "path": str(self.home / ".claude" / "CLAUDE.md"),
            "state": "linked",
        })
        self.assertEqual(rows["claude"]["skills"], {
            "path": str(self.home / ".claude" / "skills"),
            "state": "linked",
        })
        self.assertEqual(rows["codex"]["instructions"], {
            "path": str(self.home / ".codex" / "AGENTS.md"),
            "state": "conflict",
        })
        self.assertEqual(rows["codex"]["skills"], {
            "path": str(self.home / ".codex" / "skills"),
            "state": "keep",
        })

    def test_writes_nothing_under_home(self):
        before = snapshot(self.home)
        self.init_check()
        self.assertEqual(snapshot(self.home), before)

    def test_missing_shared_file_is_reported_not_created(self):
        (self.home / ".swe" / "AGENTS.md").unlink()
        before = snapshot(self.home)
        data = self.init_check()["data"]
        self.assertFalse(data["agents_file_exists"])
        self.assertEqual(snapshot(self.home), before)

    def test_json_without_check_refuses_and_writes_nothing(self):
        before = snapshot(self.home)
        result = run_swe(self.home, "init", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("--check", result.stderr)
        self.assertEqual(snapshot(self.home), before)

    def test_harness_without_an_instruction_file_has_null_instructions(self):
        registry = dict(REGISTRY)
        registry["zed"] = {
            "command": "zed", "description": "Editor",
            "capabilities": {"skills": {"supported": True,
                                        "root": "~/.zed/skills"}},
        }
        write_registry(self.home, registry)
        (self.home / ".zed").mkdir()
        (self.home / ".zed" / "skills").symlink_to(
            self.home / ".swe" / "skills")
        rows = {r["name"]: r for r in self.init_check()["data"]["harnesses"]}
        self.assertIsNone(rows["zed"]["instructions"])
        self.assertEqual(rows["zed"]["skills"], {
            "path": str(self.home / ".zed" / "skills"),
            "state": "linked",
        })

    def test_linkignored_path_reads_ignored(self):
        (self.home / ".swe" / ".linkignore").write_text(".codex/skills\n")
        rows = {r["name"]: r for r in self.init_check()["data"]["harnesses"]}
        self.assertEqual(rows["codex"]["skills"]["state"], "ignored")
        self.assertEqual(rows["codex"]["instructions"]["state"], "conflict")

    def test_unreviewed_skills_folder_is_skipped_not_prompted(self):
        write_skill(self.home / ".newthing" / "skills", "mine")
        result = run_swe(self.home, "init", "--check", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {r["name"]: r for r in decode(result)["data"]["harnesses"]}
        self.assertEqual(rows["newthing"]["skills"]["state"], "skipped")

    def test_failures_keep_stdout_empty(self):
        (self.home / ".swe" / ".linkignore").mkdir()
        result = run_swe(self.home, "init", "--check", "--json")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(".linkignore", result.stderr)

        result = run_swe(self.home, "init", "--check", "--json", "--bogus")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("--bogus", result.stderr)


class FindSkillsJsonTest(unittest.TestCase):
    def setUp(self):
        # resolve(): on macOS the temp dir is a symlink, and `swe find`
        # compares the cwd (resolved) against HOME to decide a root's scope.
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.shared = build_machine(self.home)

    def find_skills(self, *extra):
        # -r scans from HOME; without it the scan starts at the cwd.
        result = run_swe(self.home, "find", "skills", "--json", "-r", *extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        doc = decode(result)
        self.assertEqual(doc["schema"], 1)
        return doc["data"]

    def roots(self, *extra):
        return {r["path"]: r for r in self.find_skills(*extra)["roots"]}

    def test_lists_the_shared_skills_and_each_harness_root_with_its_state(self):
        data = self.find_skills()
        self.assertEqual(data["shared_dir"], str(self.shared))
        self.assertEqual(
            sorted(data["skills"], key=lambda s: s["name"]),
            [{"name": "alpha", "path": str(self.shared / "alpha"),
              "scope": "global"},
             {"name": "beta", "path": str(self.shared / "beta"),
              "scope": "global"}])
        roots = {r["path"]: r for r in data["roots"]}
        self.assertEqual(set(roots), {
            str(self.home / ".claude" / "skills"),
            str(self.home / ".codex" / "skills"),
        })
        claude = roots[str(self.home / ".claude" / "skills")]
        self.assertEqual(claude["harness"], "claude")
        self.assertEqual(claude["scope"], "global")
        self.assertEqual(claude["kind"], "symlink")
        self.assertEqual(claude["state"], "linked")
        self.assertEqual(claude["count"], 2)
        codex = roots[str(self.home / ".codex" / "skills")]
        self.assertEqual(codex["harness"], "codex")
        self.assertEqual(codex["scope"], "global")
        self.assertEqual(codex["kind"], "directory")
        self.assertEqual(codex["state"], "keep")
        self.assertEqual(codex["count"], 1)
        self.assertEqual(codex["detail"], "1 of 1 skills exist nowhere else")

    def test_writes_nothing_under_home(self):
        before = snapshot(self.home)
        self.find_skills()
        self.assertEqual(snapshot(self.home), before)

    def test_without_the_root_flag_it_scans_the_working_directory(self):
        result = run_swe_in(
            self.home, self.home / ".codex", "find", "skills", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        paths = [r["path"] for r in decode(result)["data"]["roots"]]
        self.assertEqual(paths, [str(self.home / ".codex" / "skills")])

    def test_scope_filter_separates_project_roots(self):
        write_skill(self.home / "proj" / ".claude" / "skills", "local-one")
        project = str(self.home / "proj" / ".claude" / "skills")
        self.assertNotIn(project, self.roots())
        self.assertEqual(set(self.roots("--scope=local")), {project})
        local = self.roots("--scope=local")[project]
        self.assertEqual(local["scope"], "local")
        self.assertIsNone(local["harness"])
        self.assertIn(project, self.roots("--scope=all"))
        self.assertEqual(len(self.roots("--scope=all")), 3)

    def test_archived_harness_is_hidden_until_asked_for(self):
        registry = copy.deepcopy(REGISTRY)
        registry["codex"]["state"] = "archived"
        write_registry(self.home, registry)
        codex = str(self.home / ".codex" / "skills")
        self.assertNotIn(codex, self.roots())
        self.assertIn(codex, self.roots("--harness=all"))

    def test_a_dot_named_shared_skill_is_listed_like_the_root_counts_it(self):
        write_skill(self.shared, ".review")
        names = [s["name"] for s in self.find_skills()["skills"]]
        self.assertIn(".review", names)

    def test_nested_shared_skills_are_listed_at_their_real_path(self):
        write_skill(self.shared / "team", "review")
        skills = {s["name"]: s["path"] for s in self.find_skills()["skills"]}
        self.assertEqual(skills["review"], str(self.shared / "team" / "review"))
        # A grouping folder holds skills; it is not one itself.
        self.assertNotIn("team", skills)

    def test_no_shared_tree_is_an_empty_list_not_an_error(self):
        empty = Path(tempfile.mkdtemp())
        result = run_swe(empty, "find", "skills", "--json", "-r")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = decode(result)["data"]
        self.assertEqual(data["skills"], [])
        self.assertEqual(data["roots"], [])

    def test_failures_keep_stdout_empty(self):
        for args in (
            ("find", "skills", "--json", "--scope=bogus"),
            ("find", "skills", "--json", "--harness=bogus"),
            ("find", "skills", "--json", "--interactive"),
            ("find", "skills", "--json", "extra"),
            ("find", "mcps", "--json"),
            ("find", "--json"),
        ):
            with self.subTest(args=args):
                result = run_swe(self.home, *args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertNotEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
