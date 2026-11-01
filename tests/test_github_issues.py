"""swe gh issue view|comment|worktree, the resolve prompt, and
manage.issue_worktree over a real local git "remote" (no network)."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from swe import paths
from swe.github import cli as ghcli
from swe.github import gh, manage
from swe.github import issues as gh_issues

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octocat")

RAW_ISSUE = {
    "number": 5,
    "title": "Fix the thing",
    "state": "OPEN",
    "author": {"login": "alice"},
    "labels": [{"name": "bug"}, {"name": "ui"}],
    "assignees": [{"login": "bob"}],
    "url": "https://github.com/o/r/issues/5",
    "body": "It breaks.\n\nSteps: run it.",
    "createdAt": "2026-10-20T10:00:00Z",
    "updatedAt": "2026-10-21T10:00:00Z",
    "comments": [
        {"author": {"login": "carol"}, "body": "Same here.\nOn 1.1.",
         "createdAt": "2026-10-21T09:00:00Z",
         "url": "https://github.com/o/r/issues/5#issuecomment-1"},
    ],
}


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _git(*args, cwd=None):
    """Run git for a fixture; raises on failure so a broken fixture is loud."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, env=env, check=True)
    return done.stdout.strip()


# ------------------------------------------------------------------ worktree

@unittest.skipUnless(shutil.which("git"), "git is not installed")
class TestIssueWorktree(unittest.TestCase):
    """A local bare repo stands in for GitHub; ``gh repo clone`` is
    swapped for a plain ``git clone --bare`` of it."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.home = root / "home"
        self.work = root / "work"
        self.origin = root / "origin.git"
        _git("init", "-q", "-b", "main", str(self.work))
        (self.work / "README").write_text("one\n")
        _git("add", "README", cwd=self.work)
        _git("commit", "-q", "-m", "one", cwd=self.work)
        _git("clone", "-q", "--bare", str(self.work), str(self.origin))
        _git("remote", "add", "origin", str(self.origin), cwd=self.work)
        self.clones = 0

        def run_gh(args, timeout=0, stdin=None):
            self.assertEqual(args[:2], ["repo", "clone"])
            self.clones += 1
            return manage.run_git(["clone", "-q", "--bare",
                                   str(self.origin), args[3]])

        patcher = patch("swe.github.gh.run_gh", side_effect=run_gh)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make(self, number=7):
        notes = []
        result = manage.issue_worktree("o/r", number, self.home,
                                       progress=notes.append)
        return result, notes

    def push_commit(self, text):
        (self.work / "README").write_text(text)
        _git("commit", "-q", "-am", text.strip(), cwd=self.work)
        _git("push", "-q", "origin", "main", cwd=self.work)
        return _git("rev-parse", "HEAD", cwd=self.work)

    def test_creates_a_named_branch_off_the_default_tip(self):
        result, notes = self.make()
        self.assertEqual(result.status, "ok", result.error)
        data = result.data
        expected = str(paths.worktrees_dir_for(self.home) / "o__r-issue7")
        self.assertEqual(data, {"path": expected, "branch": "quiver/issue-7",
                                "base": "origin/main", "created": True})
        self.assertEqual(notes, ["Cloning mirror…",
                                 "Creating worktree quiver/issue-7…"])
        self.assertEqual(
            _git("rev-parse", "--abbrev-ref", "HEAD", cwd=data["path"]),
            "quiver/issue-7")
        self.assertEqual(_git("rev-parse", "HEAD", cwd=data["path"]),
                         _git("rev-parse", "HEAD", cwd=self.work))
        self.assertEqual(
            manage.issue_worktree_path("o/r", 7, self.home), data["path"])
        # No upstream: the agent's first push sets it.
        with self.assertRaises(subprocess.CalledProcessError):
            _git("rev-parse", "--abbrev-ref", "@{upstream}",
                 cwd=data["path"])

    def test_asking_again_resumes_the_same_checkout(self):
        first, _ = self.make()
        again, notes = self.make()
        self.assertEqual(again.status, "ok", again.error)
        self.assertFalse(again.data["created"])
        self.assertEqual(again.data["path"], first.data["path"])
        self.assertEqual(notes, [])  # no clone, no new worktree
        self.assertEqual(self.clones, 1)

    def test_a_new_issue_starts_from_the_fetched_tip(self):
        self.make(7)
        tip = self.push_commit("two\n")
        result, notes = self.make(8)
        self.assertEqual(result.status, "ok", result.error)
        self.assertEqual(notes, ["Creating worktree quiver/issue-8…"])
        self.assertEqual(_git("rev-parse", "HEAD", cwd=result.data["path"]),
                         tip)

    def test_branch_kept_when_its_folder_was_deleted(self):
        first, _ = self.make()
        path = first.data["path"]
        (Path(path) / "fix.txt").write_text("fix\n")
        _git("add", "fix.txt", cwd=path)
        _git("commit", "-q", "-m", "wip", cwd=path)
        wip = _git("rev-parse", "HEAD", cwd=path)
        shutil.rmtree(path)
        again, _ = self.make()
        self.assertEqual(again.status, "ok", again.error)
        self.assertTrue(again.data["created"])
        self.assertEqual(_git("rev-parse", "HEAD", cwd=path), wip)
        self.assertEqual(
            _git("rev-parse", "--abbrev-ref", "HEAD", cwd=path),
            "quiver/issue-7")

    def test_a_switched_checkout_is_refused_not_reused(self):
        first, _ = self.make()
        path = first.data["path"]
        _git("switch", "-q", "-c", "elsewhere", cwd=path)
        again, _ = self.make()
        self.assertNotEqual(again.status, "ok")
        self.assertIn("on elsewhere, not quiver/issue-7", again.error)
        _git("switch", "-q", "--detach", cwd=path)
        again, _ = self.make()
        self.assertIn("detached HEAD", again.error)
        _git("switch", "-q", "quiver/issue-7", cwd=path)
        again, _ = self.make()
        self.assertEqual(again.status, "ok", again.error)

    def test_the_fetch_leaves_fetch_head_alone(self):
        # pr_worktree checks out FETCH_HEAD in the same mirror.
        self.make()
        head = manage.mirror_path("o/r", self.home) / "FETCH_HEAD"
        head.write_text("sentinel\n")
        self.push_commit("two\n")
        result, _ = self.make(8)
        self.assertEqual(result.status, "ok", result.error)
        self.assertEqual(head.read_text(), "sentinel\n")

    def test_fetch_failure_names_the_step(self):
        self.make()
        mirror = manage.mirror_path("o/r", self.home)
        _git("-C", str(mirror), "remote", "set-url", "origin",
             str(self.origin) + "-gone")
        result, _ = self.make(9)
        self.assertNotEqual(result.status, "ok")
        self.assertTrue(result.error.startswith("Fetch failed:"),
                        result.error)
        self.assertFalse(
            Path(manage.issue_worktree_path("o/r", 9, self.home)).exists())

    def test_two_first_resolves_share_one_clone(self):
        # Two issues of a new repo at once: one clone, both checkouts.
        import threading
        import time

        real = gh.run_gh

        def slow_clone(*args, **kwargs):
            time.sleep(0.3)
            return real(*args, **kwargs)

        results = {}
        with patch("swe.github.gh.run_gh", side_effect=slow_clone):
            threads = [threading.Thread(
                target=lambda n=n: results.update(
                    {n: manage.issue_worktree("o/r", n, self.home)}))
                for n in (7, 8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(self.clones, 1)
        for n in (7, 8):
            self.assertEqual(results[n].status, "ok", results[n].error)

    def test_clone_failure_names_the_step(self):
        with patch("swe.github.gh.run_gh",
                   return_value=gh.GhResult("upstream-error",
                                            error="denied")):
            result, notes = self.make()
        self.assertEqual(result.error, "Clone failed: denied")
        self.assertEqual(notes, ["Cloning mirror…"])


# ------------------------------------------------------------------ prompt

def _issue(**over):
    issue = gh_issues.shape(RAW_ISSUE, "o/r", Path("/h"))
    issue.update(over)
    return issue


class TestBuildPrompt(unittest.TestCase):
    def test_exact_shape_without_comments_or_context(self):
        text = gh_issues.build_prompt("o/r", _issue(comments=[]),
                                      "/w/o__r-issue5")
        self.assertEqual(text, (
            "Resolve o/r#5 — Fix the thing\n"
            "https://github.com/o/r/issues/5\n"
            "\n"
            "It breaks.\n\nSteps: run it.\n"
            "\n"
            "You are on branch quiver/issue-5 in a worktree of o/r\n"
            "at /w/o__r-issue5. Implement the change, run the tests, "
            "commit, push the\n"
            "branch, and open a PR that closes this issue "
            '("Closes #5").'))

    def test_comments_and_context_blocks(self):
        text = gh_issues.build_prompt("o/r", _issue(), "/w",
                                      context="Use the new API.",
                                      context_author="octocat")
        self.assertIn("Comments (1):\n- carol, 2026-10-21: Same here.\nOn 1.1.",
                      text)
        self.assertIn("Context for this run, from octocat:\nUse the new API.",
                      text)
        self.assertLess(text.index("Comments (1)"),
                        text.index("Context for this run"))
        self.assertLess(text.index("Context for this run"),
                        text.index("You are on branch"))
        anon = gh_issues.build_prompt("o/r", _issue(), "/w", context="x")
        self.assertIn("Context for this run, from the user:\nx", anon)
        blank = gh_issues.build_prompt("o/r", _issue(), "/w", context="  ")
        self.assertNotIn("Context for this run", blank)

    def test_empty_body(self):
        text = gh_issues.build_prompt("o/r", _issue(body="  "), "/w")
        self.assertIn("\n\n(no description)\n\n", text)

    def test_oldest_comments_go_first_past_the_limit(self):
        comments = [{"author": f"u{i}", "body": f"c{i} " + "x" * 2000,
                     "created_at": "2026-10-01T00:00:00Z", "url": ""}
                    for i in range(40)]
        text = gh_issues.build_prompt("o/r", _issue(comments=comments), "/w")
        self.assertLessEqual(len(text), gh_issues.PROMPT_LIMIT)
        self.assertIn("Comments (40):\n(", text)
        self.assertRegex(text, r"\((\d+) earlier comments omitted\)")
        self.assertIn("- u39, ", text)
        self.assertNotIn("- u0, ", text)
        self.assertTrue(text.endswith('("Closes #5").'))

    def test_huge_body_and_comment_are_cut(self):
        text = gh_issues.build_prompt(
            "o/r", _issue(body="b" * 50_000, comments=[
                {"author": "a", "body": "c" * 20_000,
                 "created_at": "2026-10-01T00:00:00Z", "url": ""}]), "/w")
        self.assertLessEqual(len(text), gh_issues.PROMPT_LIMIT)
        self.assertIn("more characters cut; read the rest on GitHub", text)
        self.assertLess(text.count("b"), gh_issues.BODY_LIMIT + 200)
        self.assertLess(text.count("c"), gh_issues.COMMENT_LIMIT + 200)

    def test_issue_number(self):
        for ref, want in (("5", 5), ("#5", 5), ("0", None), ("abc", None),
                          ("https://github.com/o/r/issues/12", 12),
                          ("https://github.com/o/r/pull/12", None)):
            self.assertEqual(gh_issues.issue_number(ref), want, ref)


# ------------------------------------------------------------------ CLI

class TestIssueCli(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in (
                ("GITHUB_FILE", self.root / "config" / "github.json"),
                ("WORKTREES_DIR", self.root / "worktrees"),
                ("GITHUB_MIRRORS", self.root / "mirrors")):
            patcher = patch.object(paths, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.calls = []

    def _run(self, args, replies=None, env=OK_ENV):
        replies = replies or {}

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            self.calls.append((list(cmd), kw.get("input")))
            for needle, proc in replies.items():
                if needle in joined:
                    return proc
            return _Proc(returncode=1, stderr=f"unstubbed: {joined}")

        out, err = io.StringIO(), io.StringIO()
        with patch("swe.github.gh.probe_env", return_value=env), \
             patch("subprocess.run", run), \
             patch("swe.github.repos.repo_at", return_value=None), \
             redirect_stdout(out), redirect_stderr(err):
            rc = ghcli.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def _data(self, out):
        return json.loads(out)["data"]

    def test_view_json_shape(self):
        rc, out, _ = self._run(
            ["issue", "view", "5", "--repo=o/r", "--json"],
            {"issue view": _Proc(stdout=json.dumps(RAW_ISSUE))})
        self.assertEqual(rc, 0)
        data = self._data(out)
        self.assertEqual(data["github"]["status"], "ok")
        self.assertEqual((data["repo"], data["status"]), ("o/r", "ok"))
        issue = data["issue"]
        self.assertEqual(set(issue), {
            "number", "title", "state", "author", "labels", "assignees",
            "url", "body", "created_at", "updated_at", "comments", "branch",
            "worktree", "suggested_prompt"})
        self.assertEqual(issue["state"], "open")
        self.assertEqual(issue["labels"], ["bug", "ui"])
        self.assertEqual(issue["assignees"], ["bob"])
        self.assertEqual(issue["comments"], [{
            "author": "carol", "body": "Same here.\nOn 1.1.",
            "created_at": "2026-10-21T09:00:00Z",
            "url": "https://github.com/o/r/issues/5#issuecomment-1"}])
        self.assertEqual(issue["branch"], "quiver/issue-5")
        self.assertEqual(issue["worktree"],
                         str(self.root / "worktrees" / "o__r-issue5"))
        self.assertTrue(issue["suggested_prompt"].startswith(
            "Resolve o/r#5 — Fix the thing\n"))
        self.assertIn(issue["worktree"], issue["suggested_prompt"])
        argv = self.calls[0][0]
        self.assertIn("comments", argv[argv.index("--json") + 1])

    def test_view_text_and_url_ref(self):
        rc, out, _ = self._run(
            ["issue", "view", "https://github.com/o/r/issues/5"],
            {"issue view": _Proc(stdout=json.dumps(RAW_ISSUE))})
        self.assertEqual(rc, 0)
        self.assertIn("Fix the thing", out)
        self.assertIn("Comments (1)", out)
        self.assertIn("On 1.1.", out)
        self.assertIn("--repo o/r", " ".join(self.calls[0][0]))

    def test_view_not_found(self):
        rc, out, _ = self._run(
            ["issue", "view", "99", "--repo=o/r", "--json"],
            {"issue view": _Proc(returncode=1,
                                 stderr="Could not resolve to an Issue")})
        self.assertEqual(rc, 1)
        data = self._data(out)
        self.assertEqual(data["status"], "not-found")
        self.assertIsNone(data["issue"])

    def test_gh_missing(self):
        env = gh.GhEnv(installed=False)
        for sub in ("view", "worktree"):
            rc, out, _ = self._run(["issue", sub, "5", "--repo=o/r",
                                    "--json"], env=env)
            self.assertEqual(rc, 1)
            data = self._data(out)
            self.assertEqual(data["github"]["status"], "unavailable")
            self.assertIn("brew install gh", data["error"])
        rc, out, _ = self._run(["issue", "comment", "5", "--repo=o/r",
                                "--body=hi", "--json"], env=env)
        self.assertEqual(rc, 1)
        self.assertIsNone(self._data(out)["comment"])
        self.assertEqual(self.calls, [])

    def test_comment_sends_the_body_on_stdin(self):
        body = "--x=y and --json\nsecond line = fine"
        rc, out, _ = self._run(
            ["issue", "comment", "5", "--repo=o/r", f"--body={body}",
             "--json"],
            {"issue comment": _Proc(
                stdout="https://github.com/o/r/issues/5#issuecomment-9\n")})
        self.assertEqual(rc, 0)
        argv, stdin = self.calls[0]
        self.assertEqual(argv, ["gh", "issue", "comment", "5", "--repo",
                                "o/r", "--body-file", "-"])
        self.assertEqual(stdin, body)
        data = self._data(out)
        self.assertEqual((data["repo"], data["status"], data["issue"]),
                         ("o/r", "ok", 5))
        comment = data["comment"]
        self.assertEqual(comment["author"], "octocat")
        self.assertEqual(comment["body"], body)
        self.assertEqual(comment["url"],
                         "https://github.com/o/r/issues/5#issuecomment-9")
        self.assertRegex(comment["created_at"],
                         r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_comment_reads_the_body_from_stdin_with_body_file(self):
        # The app passes --body-file=- so the text never sits on argv,
        # where any process on the machine can read it.
        body = "secret plan --json\nline two"
        with patch("sys.stdin", io.StringIO(body)):
            rc, out, _ = self._run(
                ["issue", "comment", "5", "--repo=o/r", "--body-file=-",
                 "--json"],
                {"issue comment": _Proc(
                    stdout="https://github.com/o/r/issues/5#issuecomment-9\n")})
        self.assertEqual(rc, 0)
        argv, stdin = self.calls[0]
        self.assertNotIn(body, " ".join(argv))
        self.assertEqual(stdin, body)
        self.assertEqual(self._data(out)["comment"]["body"], body)

    def test_comment_body_file_path_and_conflicts(self):
        path = self.root / "note.md"
        path.write_text("from a file", encoding="utf-8")
        rc, out, _ = self._run(
            ["issue", "comment", "5", "--repo=o/r", f"--body-file={path}",
             "--json"],
            {"issue comment": _Proc(stdout="u\n")})
        self.assertEqual(rc, 0)
        self.assertEqual(self.calls[0][1], "from a file")
        self.calls.clear()
        for args in ([f"--body-file={path}", "--body=x"],
                     [f"--body-file={self.root / 'missing.md'}"]):
            rc, out, _ = self._run(
                ["issue", "comment", "5", "--repo=o/r", "--json", *args])
            self.assertEqual(rc, 1)
            self.assertIsNone(self._data(out)["comment"])
        self.assertEqual(self.calls, [])
        rc, _out, _err = self._run(
            ["issue", "view", "5", "--repo=o/r", "--body-file=-"])
        self.assertEqual(rc, 1)

    def test_comment_unreadable_stdin_is_an_error_reply(self):
        class Broken(io.StringIO):
            def read(self, *args):
                raise OSError("Is a directory")
        with patch("sys.stdin", Broken()):
            rc, out, _ = self._run(
                ["issue", "comment", "5", "--repo=o/r", "--body-file=-",
                 "--json"])
        self.assertEqual(rc, 1)
        data = self._data(out)
        self.assertIsNone(data["comment"])
        self.assertIn("Can't read stdin", data["error"])
        self.assertEqual(self.calls, [])

    def test_comment_empty_body_is_an_error(self):
        for args in (["--body=   "], []):
            rc, out, _ = self._run(
                ["issue", "comment", "5", "--repo=o/r", "--json", *args])
            self.assertEqual(rc, 1)
            data = self._data(out)
            self.assertIn("empty", data["error"])
            self.assertIsNone(data["comment"])
        rc, _out, err = self._run(
            ["issue", "comment", "5", "--repo=o/r", "--body"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])

    def test_comment_failure(self):
        rc, out, _ = self._run(
            ["issue", "comment", "5", "--repo=o/r", "--body=hi", "--json"],
            {"issue comment": _Proc(returncode=1, stderr="locked")})
        self.assertEqual(rc, 1)
        data = self._data(out)
        self.assertEqual(data["status"], "upstream-error")
        self.assertIn("locked", data["error"])

    def test_body_only_goes_with_comment(self):
        rc, _out, _err = self._run(
            ["issue", "view", "5", "--repo=o/r", "--body=x"])
        self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])

    def test_worktree_json(self):
        made = gh.GhResult("ok", data={
            "path": "/w/o__r-issue5", "branch": "quiver/issue-5",
            "base": "origin/main", "created": True})
        with patch("swe.github.manage.issue_worktree",
                   return_value=made) as wt:
            rc, out, _ = self._run(
                ["issue", "worktree", "5", "--repo=o/r", "--json"])
        self.assertEqual(rc, 0)
        wt.assert_called_once_with("o/r", 5)
        data = self._data(out)
        self.assertEqual(
            {k: data[k] for k in ("repo", "issue", "path", "branch",
                                  "base", "created")},
            {"repo": "o/r", "issue": 5, "path": "/w/o__r-issue5",
             "branch": "quiver/issue-5", "base": "origin/main",
             "created": True})
        self.assertNotIn("error", data)

    def test_worktree_failure(self):
        with patch("swe.github.manage.issue_worktree",
                   return_value=gh.GhResult("upstream-error",
                                            error="Fetch failed: x")):
            rc, out, _ = self._run(
                ["issue", "worktree", "5", "--repo=o/r", "--json"])
            self.assertEqual(rc, 1)
            data = self._data(out)
            self.assertIsNone(data["path"])
            self.assertFalse(data["created"])
            self.assertEqual(data["error"], "Fetch failed: x")
            rc, out, _ = self._run(["issue", "worktree", "5", "--repo=o/r"])
            self.assertIn("Fetch failed", out)

    def test_bad_ref_and_usage(self):
        rc, out, _ = self._run(["issue", "view", "abc", "--repo=o/r"])
        self.assertEqual(rc, 1)
        self.assertIn("/issues/<N>", out)
        rc, out, _ = self._run(["issue", "view", "--repo=o/r"])
        self.assertEqual(rc, 1)
        self.assertIn("Usage: swe gh issue view", out)

    def test_list_still_works(self):
        rows = [{"number": 1, "title": "t", "state": "OPEN",
                 "author": {"login": "a"}, "labels": [], "assignees": [],
                 "comments": [], "updatedAt": None, "url": "u"}]
        for args in (["issue"], ["issue", "list"], ["issues"]):
            rc, out, _ = self._run(
                [*args, "--repo=o/r", "--json"],
                {"issue list": _Proc(stdout=json.dumps(rows))})
            self.assertEqual(rc, 0, args)
            result = self._data(out)["results"][0]
            self.assertEqual(result["issues"][0]["number"], 1)
        rc, _out, _ = self._run(["issue", "bogus", "--repo=o/r"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
