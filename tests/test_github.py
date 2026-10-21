"""Tests for swe gh: repo parsing, the pinned store, shaping, dispatch."""

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from quiver.github import cli as ghcli
from quiver.github import gh, repos
from quiver import paths


class _Proc:
    """Minimal subprocess.CompletedProcess stand-in."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _gh_run(handlers):
    """A subprocess.run replacement dispatching on argv fragments.

    ``handlers`` maps a substring of the joined argv to a _Proc.
    """
    def run(cmd, **kw):
        joined = " ".join(str(a) for a in cmd)
        for needle, proc in handlers.items():
            if needle in joined:
                return proc
        return _Proc(returncode=1, stderr=f"unstubbed: {joined}")
    return run


def _env_ok():
    return gh.GhEnv(installed=True, authenticated=True, login="octocat")


class TestNormalize(unittest.TestCase):
    def test_plain_name(self):
        self.assertEqual(repos.normalize_repo_input("o/r"), "o/r")
        self.assertEqual(repos.normalize_repo_input("a-b_c.d/e"), "a-b_c.d/e")

    def test_urls(self):
        self.assertEqual(
            repos.normalize_repo_input("https://github.com/cli/cli"),
            "cli/cli")
        self.assertEqual(
            repos.normalize_repo_input("https://github.com/cli/cli/pull/12"),
            "cli/cli")
        self.assertEqual(
            repos.normalize_repo_input("github.com/o/r/issues/5"),
            "o/r")

    def test_ssh_remote(self):
        self.assertEqual(
            repos.normalize_repo_input("git@github.com:o/r.git"), "o/r")

    def test_junk(self):
        self.assertIsNone(repos.normalize_repo_input(""))
        self.assertIsNone(repos.normalize_repo_input("not-a-repo"))
        self.assertIsNone(repos.normalize_repo_input("https://gitlab.com/o/r"))
        self.assertIsNone(repos.normalize_repo_input("https://github.com/o"))

    def test_remote_forms(self):
        self.assertEqual(
            repos.repo_from_remote("git@github.com:o/r.git"), "o/r")
        self.assertEqual(
            repos.repo_from_remote("https://github.com/o/r.git"), "o/r")
        self.assertEqual(
            repos.repo_from_remote("https://github.com/o/r/"), "o/r")
        self.assertIsNone(repos.repo_from_remote("ssh://git@x/o/r"))
        self.assertIsNone(repos.repo_from_remote("/local/path"))


class TestPinnedStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "github.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        repos.add_pinned("o/r", self.store)
        repos.add_pinned("a/b", self.store)
        names = [r["name"] for r in repos.load_pinned(self.store)]
        self.assertEqual(names, ["o/r", "a/b"])

    def test_add_dedupes_case(self):
        repos.add_pinned("O/R", self.store)
        repos.add_pinned("o/r", self.store)
        self.assertEqual(len(repos.load_pinned(self.store)), 1)

    def test_remove(self):
        repos.add_pinned("o/r", self.store)
        kept, removed = repos.remove_pinned("o/r", self.store)
        self.assertTrue(removed)
        self.assertEqual(kept, [])
        _, removed = repos.remove_pinned("o/r", self.store)
        self.assertFalse(removed)

    def test_missing_and_corrupt(self):
        self.assertEqual(repos.load_pinned(self.store), [])
        self.store.write_text("{not json")
        self.assertEqual(repos.load_pinned(self.store), [])
        self.store.write_text('{"repos": "nope"}')
        self.assertEqual(repos.load_pinned(self.store), [])

    def test_bad_shapes_degrade(self):
        self.store.write_text("[]")
        self.assertEqual(repos.load_pinned(self.store), [])
        self.store.write_text(json.dumps({"repos": [
            "nope", {"name": 42}, {"name": "o/r", "added_at": "bad"},
        ]}))
        self.assertEqual(repos.load_pinned(self.store),
                         [{"name": "o/r", "added_at": 0}])


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "github.json"
        patcher = patch.object(paths, "GITHUB_FILE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_repo_arg_wins(self):
        self.assertEqual(repos.resolve("me/proj"), ["me/proj"])

    def test_cwd_remote(self):
        run = _gh_run({"remote get-url origin": _Proc(
            stdout="git@github.com:o/r.git\n")})
        with patch("subprocess.run", run):
            self.assertEqual(repos.resolve(None, Path("/tmp/x")), ["o/r"])

    def test_falls_back_to_pinned(self):
        repos.add_pinned("o/r", self.store)
        repos.add_pinned("a/b", self.store)
        run = _gh_run({})  # every git call fails -> not a repo
        with patch("subprocess.run", run):
            got = repos.resolve(None, Path("/tmp/nowhere"))
        self.assertEqual(got, ["o/r", "a/b"])

    def test_upstream_when_origin_is_not_github(self):
        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "get-url origin" in joined:
                return _Proc(stdout="https://gitlab.com/o/r.git\n")
            if "get-url upstream" in joined:
                return _Proc(stdout="git@github.com:o/r.git\n")
            return _Proc(returncode=1)
        with patch("subprocess.run", run):
            self.assertEqual(repos.resolve(None, Path("/tmp/x")), ["o/r"])


class TestGhWrapper(unittest.TestCase):
    def test_ok(self):
        run = _gh_run({"x": _Proc(stdout="hi")})
        with patch("subprocess.run", run):
            out = gh.run_gh(["x"])
        self.assertEqual((out.status, out.data), ("ok", "hi"))

    def test_not_found(self):
        run = _gh_run({"x": _Proc(returncode=1, stderr="HTTP 404: Not Found")})
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_gh(["x"]).status, "not-found")

    def test_upstream_error(self):
        run = _gh_run({"x": _Proc(returncode=1, stderr="boom")})
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_gh(["x"]).status, "upstream-error")

    def test_missing_binary(self):
        def run(cmd, **kw):
            raise FileNotFoundError("gh")
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_gh(["x"]).status, "unavailable")

    def test_timeout(self):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired("gh", 20)
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_gh(["x"]).status, "upstream-error")

    def test_json_parse(self):
        run = _gh_run({"x": _Proc(stdout='{"a": 1}')})
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_json(["x"]).data, {"a": 1})
        run = _gh_run({"x": _Proc(stdout="not json")})
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_json(["x"]).status, "upstream-error")

    def test_probe_env(self):
        calls = []

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            calls.append(joined)
            if "auth status" in joined:
                return _Proc(
                    stderr="✓ Logged in to github.com account octocat")
            return _Proc(returncode=1)
        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", run):
            env = gh.probe_env()
        self.assertTrue(env.installed)
        self.assertTrue(env.authenticated)
        # The login parses out of `auth status` — no second `api user` call.
        self.assertEqual(env.login, "octocat")
        self.assertFalse(any("api user" in call for call in calls))

    def test_probe_env_timeout_is_not_auth_required(self):
        def run(cmd, **kw):
            raise subprocess.TimeoutExpired("gh", 20)
        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", run):
            env = gh.probe_env()
        self.assertEqual(env.status, "upstream-error")
        self.assertIn("timed out", env.error)

    def test_dns_failure_is_upstream_not_not_found(self):
        run = _gh_run({"x": _Proc(returncode=1,
                                  stderr="Could not resolve host")})
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_gh(["x"]).status, "upstream-error")

    def test_graphql_missing_repo_is_not_found(self):
        run = _gh_run({"x": _Proc(
            returncode=1,
            stderr="Could not resolve to a Repository with the name 'o/r'")})
        with patch("subprocess.run", run):
            self.assertEqual(gh.run_gh(["x"]).status, "not-found")


class TestShaping(unittest.TestCase):
    def test_rollup(self):
        self.assertEqual(ghcli.rollup_checks([]), "none")
        self.assertEqual(ghcli.rollup_checks(None), "none")
        self.assertEqual(ghcli.rollup_checks([
            {"__typename": "CheckRun", "status": "COMPLETED",
             "conclusion": "SUCCESS"},
            {"__typename": "StatusContext", "state": "SUCCESS"},
        ]), "passing")
        self.assertEqual(ghcli.rollup_checks([
            {"__typename": "CheckRun", "status": "IN_PROGRESS"},
            {"__typename": "CheckRun", "conclusion": "FAILURE"},
        ]), "failing")  # a finished failure outranks a running check
        self.assertEqual(ghcli.rollup_checks([
            {"__typename": "CheckRun", "status": "IN_PROGRESS"},
        ]), "running")
        self.assertEqual(ghcli.rollup_checks([
            {"__typename": "CheckRun", "conclusion": "SUCCESS"},
            {"__typename": "CheckRun", "status": "QUEUED"},
        ]), "running")
        self.assertEqual(ghcli.rollup_checks([
            {"__typename": "CheckRun", "conclusion": "STALE"},
        ]), "unknown")

    def test_pr_row(self):
        row = ghcli._pr_row({
            "number": 7, "title": "Fix", "state": "OPEN", "isDraft": True,
            "author": {"login": "k"}, "headRefName": "feat",
            "baseRefName": "main", "labels": [{"name": "bug"}],
            "mergeable": "CONFLICTING", "reviewDecision": "CHANGES_REQUESTED",
            "statusCheckRollup": [{"status": "COMPLETED",
                                   "conclusion": "FAILURE"}],
            "updatedAt": "2026-10-20T00:00:00Z", "url": "u",
        })
        self.assertEqual(row["state"], "open")
        self.assertTrue(row["draft"])
        self.assertEqual(row["checks"], "failing")
        self.assertEqual(row["mergeable"], "conflicting")
        self.assertEqual(row["labels"], ["bug"])
        self.assertEqual(ghcli._pr_glyph(row), ("○", "dim"))
        self.assertEqual(ghcli._review_cell(row), ("conflict", "red"))

    def test_pr_glyphs(self):
        self.assertEqual(ghcli._pr_glyph({"state": "merged"}),
                         ("●", "neon_pink"))
        self.assertEqual(ghcli._pr_glyph({"state": "closed"}), ("●", "red"))
        self.assertEqual(ghcli._pr_glyph({"state": "open"}), ("●", "green"))

    def test_notification_url(self):
        row = ghcli._notification_row({
            "id": "1", "unread": True, "reason": "review_requested",
            "subject": {"title": "T", "type": "PullRequest",
                        "url": "https://api.github.com/repos/o/r/pulls/3"},
            "repository": {"full_name": "o/r",
                           "html_url": "https://github.com/o/r"},
            "updated_at": "2026-10-20T00:00:00Z",
        })
        self.assertEqual(row["url"], "https://github.com/o/r/pull/3")
        self.assertEqual(row["repo"], "o/r")
        self.assertTrue(row["unread"])

    def test_notification_url_fallback(self):
        row = ghcli._notification_row({
            "subject": {"title": "T", "type": "CheckSuite", "url": None},
            "repository": {"full_name": "o/r",
                           "html_url": "https://github.com/o/r"},
        })
        self.assertEqual(row["url"], "https://github.com/o/r")


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / "config" / "github.json"
        patcher = patch.object(paths, "GITHUB_FILE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run_cli(self, args, run=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        env = env or _env_ok()
        with patch("quiver.github.gh.probe_env", return_value=env), \
             patch("subprocess.run", run or _gh_run({})), \
             patch("quiver.github.repos.repo_at", return_value=None), \
             redirect_stdout(out), redirect_stderr(err):
            rc = ghcli.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def test_overview_lists_pins(self):
        repos.add_pinned("o/r", self.store)
        rc, out, _ = self._run_cli([])
        self.assertEqual(rc, 0)
        self.assertIn("o/r", out)

    def test_gh_missing(self):
        env = gh.GhEnv(installed=False)
        rc, out, _ = self._run_cli(["pr"], env=env)
        self.assertEqual(rc, 1)
        self.assertIn("brew install gh", out)

    def test_gh_unauthenticated(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, out, _ = self._run_cli(["inbox"], env=env)
        self.assertEqual(rc, 1)
        self.assertIn("gh auth login", out)

    def test_no_repo_message(self):
        rc, out, _ = self._run_cli(["pr"])
        self.assertEqual(rc, 1)
        self.assertIn("swe gh repo add", out)

    def test_bad_repo_arg(self):
        rc, out, _ = self._run_cli(["pr", "--repo=???"])
        self.assertEqual(rc, 1)
        self.assertIn("Not a GitHub repo", out)

    def test_repo_add_remove(self):
        run = _gh_run({
            "repo view": _Proc(stdout='{"nameWithOwner": "cli/cli"}'),
        })
        rc, out, _ = self._run_cli(["repo", "add", "cli/cli"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("cli/cli", out)
        self.assertEqual(repos.load_pinned(self.store)[0]["name"], "cli/cli")

        rc, out, _ = self._run_cli(["repo", "remove", "cli/cli"], run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(repos.load_pinned(self.store), [])

    def test_repo_add_unreachable(self):
        run = _gh_run({
            "repo view": _Proc(returncode=1, stderr="Could not resolve"),
        })
        rc, _, _ = self._run_cli(["repo", "add", "o/r"], run=run)
        self.assertEqual(rc, 1)
        self.assertEqual(repos.load_pinned(self.store), [])

    def test_pr_list_json_envelope(self):
        run = _gh_run({
            "pr list": _Proc(stdout=json.dumps([{
                "number": 12, "title": "Add thing", "state": "OPEN",
                "isDraft": False, "author": {"login": "dev"},
                "headRefName": "feat/x", "baseRefName": "main",
                "labels": [], "mergeable": "MERGEABLE",
                "reviewDecision": "APPROVED",
                "statusCheckRollup": [
                    {"status": "COMPLETED", "conclusion": "SUCCESS"}],
                "updatedAt": "2026-10-20T00:00:00Z",
                "url": "https://github.com/o/r/pull/12",
            }])),
        })
        rc, out, _ = self._run_cli(
            ["pr", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["data"]["github"]["status"], "ok")
        pr = doc["data"]["results"][0]["pull_requests"][0]
        self.assertEqual(pr["number"], 12)
        self.assertEqual(pr["checks"], "passing")
        self.assertEqual(pr["review_decision"], "approved")

    def test_pr_list_table(self):
        run = _gh_run({
            "pr list": _Proc(stdout=json.dumps([{
                "number": 12, "title": "Add thing", "state": "MERGED",
                "author": {"login": "dev"}, "headRefName": "feat/x",
                "statusCheckRollup": [],
                "updatedAt": "2026-10-20T00:00:00Z",
            }])),
        })
        rc, out, _ = self._run_cli(["pr", "--repo=o/r", "--state=all"],
                                   run=run)
        self.assertEqual(rc, 0)
        self.assertIn("#12", out)
        self.assertIn("Add thing", out)
        self.assertIn("o/r", out)

    def test_pr_upstream_error_entry(self):
        run = _gh_run({
            "pr list": _Proc(returncode=1, stderr="rate limit exceeded"),
        })
        rc, out, _ = self._run_cli(["pr", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        entry = json.loads(out)["data"]["results"][0]
        self.assertEqual(entry["status"], "upstream-error")
        self.assertIn("rate limit", entry["error"])

    def test_pr_status_json(self):
        run = _gh_run({
            "pr status": _Proc(stdout=json.dumps({
                "currentBranch": {"number": 3, "title": "wip",
                                  "state": "OPEN", "headRefName": "wip"},
                "createdBy": [{"number": 1, "title": "mine",
                               "state": "OPEN"}],
                "needsReview": [{"number": 2, "title": "theirs",
                                 "state": "OPEN"}],
            })),
        })
        rc, out, _ = self._run_cli(
            ["pr", "status", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        entry = json.loads(out)["data"]["results"][0]
        self.assertEqual(entry["current_branch"]["number"], 3)
        self.assertEqual(len(entry["authored"]), 1)
        self.assertEqual(len(entry["review_requested"]), 1)

    def test_issue_list_json(self):
        run = _gh_run({
            "issue list": _Proc(stdout=json.dumps([{
                "number": 5, "title": "Bug", "state": "OPEN",
                "author": {"login": "u"}, "labels": [{"name": "bug"}],
                "assignees": [{"login": "k"}], "comments": {"totalCount": 2},
                "updatedAt": "2026-10-20T00:00:00Z", "url": "u",
            }])),
        })
        rc, out, _ = self._run_cli(["issue", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        issue = json.loads(out)["data"]["results"][0]["issues"][0]
        self.assertEqual(issue["number"], 5)
        self.assertEqual(issue["labels"], ["bug"])
        self.assertEqual(issue["assignees"], ["k"])
        self.assertEqual(issue["comments"], 2)

    def test_bad_issue_state(self):
        rc, out, _ = self._run_cli(["issue", "--repo=o/r", "--state=merged"])
        self.assertEqual(rc, 1)
        self.assertIn("--state", out)

    def test_run_list_json(self):
        run = _gh_run({
            "run list": _Proc(stdout=json.dumps([{
                "databaseId": 99, "displayTitle": "CI", "workflowName": "ci",
                "headBranch": "main", "event": "push", "status": "completed",
                "conclusion": "failure", "createdAt": "2026-10-20T00:00:00Z",
                "updatedAt": "2026-10-20T01:00:00Z", "url": "u",
            }])),
        })
        rc, out, _ = self._run_cli(["actions", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        run_row = json.loads(out)["data"]["results"][0]["runs"][0]
        self.assertEqual(run_row["conclusion"], "failure")

    def test_pages_no_site(self):
        run = _gh_run({
            "repos/o/r/pages": _Proc(returncode=1, stderr="HTTP 404 Not Found"),
        })
        rc, out, _ = self._run_cli(["pages", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        entry = json.loads(out)["data"]["results"][0]
        self.assertIsNone(entry["pages"])

    def test_pages_site(self):
        def run(cmd, **kw):
            joined = " ".join(cmd)
            if joined.endswith("pages/builds/latest"):
                return _Proc(stdout=json.dumps(
                    {"status": "built", "commit": "abc123def",
                     "updated_at": "2026-10-20T00:00:00Z"}))
            if joined.endswith("repos/o/r/pages"):
                return _Proc(stdout=json.dumps(
                    {"html_url": "https://o.github.io/r", "status": "built",
                     "cname": None}))
            return _Proc(returncode=1, stderr="unstubbed")
        rc, out, _ = self._run_cli(["pages", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        pages = json.loads(out)["data"]["results"][0]["pages"]
        self.assertEqual(pages["url"], "https://o.github.io/r")
        self.assertEqual(pages["latest_build"]["commit"], "abc123d")

    def test_inbox(self):
        run = _gh_run({
            "api notifications": _Proc(stdout=json.dumps([{
                "id": "1", "unread": True, "reason": "mention",
                "subject": {"title": "Ping", "type": "Issue",
                            "url": "https://api.github.com/repos/o/r/issues/4"},
                "repository": {"full_name": "o/r"},
                "updated_at": "2026-10-20T00:00:00Z",
            }])),
        })
        rc, out, _ = self._run_cli(["inbox", "--json"], run=run)
        self.assertEqual(rc, 0)
        note = json.loads(out)["data"]["notifications"][0]
        self.assertEqual(note["url"], "https://github.com/o/r/issues/4")

    def test_inbox_all_asks_for_read_rows(self):
        seen = {}

        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "api notifications" in joined:
                seen["url"] = joined
            return _Proc(stdout="[]")
        rc, out, _ = self._run_cli(["inbox", "--all", "--json"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("notifications?all=true", seen["url"])
        # Without --all the endpoint stays unread-only.
        seen.clear()
        self._run_cli(["inbox", "--json"], run=run)
        self.assertNotIn("all=true", seen["url"])

    def test_unknown_subcommand(self):
        rc, out, _ = self._run_cli(["bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("Unknown", out)

    def test_limit_clamped(self):
        seen = {}

        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "--limit" in joined:
                seen["argv"] = joined
            return _Proc(stdout="[]")
        self._run_cli(["pr", "--repo=o/r", "--limit=9999"], run=run)
        self.assertIn("--limit 200", seen["argv"])

    def test_issue_table_render(self):
        run = _gh_run({
            "issue list": _Proc(stdout=json.dumps([{
                "number": 5, "title": "Buggy", "state": "OPEN",
                "author": {"login": "u"}, "labels": [{"name": "bug"}],
                "comments": {"totalCount": 2},
                "updatedAt": "2026-10-20T00:00:00Z",
            }])),
        })
        rc, out, _ = self._run_cli(["issues", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("#5", out)
        self.assertIn("Buggy", out)

    def test_run_table_render(self):
        run = _gh_run({
            "run list": _Proc(stdout=json.dumps([{
                "databaseId": 99, "displayTitle": "Nightly",
                "workflowName": "ci", "headBranch": "main", "event": "push",
                "status": "completed", "conclusion": "failure",
                "updatedAt": "2026-10-20T00:00:00Z",
            }])),
        })
        rc, out, _ = self._run_cli(["ci", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("Nightly", out)
        self.assertIn("ci", out)

    def test_empty_and_error_render(self):
        run = _gh_run({
            "pr list --repo o/r": _Proc(stdout="[]"),
            "pr list --repo bad/r": _Proc(returncode=1,
                                        stderr="HTTP 404 Not Found"),
        })
        repos.add_pinned("o/r", self.store)
        repos.add_pinned("bad/r", self.store)
        rc, out, _ = self._run_cli(["pr"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("No pull requests", out)
        self.assertIn("bad/r", out)
        self.assertIn("404", out)

    def test_pr_status_render(self):
        run = _gh_run({
            "pr status": _Proc(stdout=json.dumps({
                "currentBranch": None,
                "createdBy": [{"number": 1, "title": "mine", "state": "OPEN",
                               "headRefName": "b"}],
                "needsReview": [],
            })),
        })
        rc, out, _ = self._run_cli(["pr", "status", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("Authored by you", out)
        self.assertIn("#1", out)

    def test_releases_render(self):
        run = _gh_run({
            "release list": _Proc(stdout=json.dumps([{
                "tagName": "v1.0.0", "name": "one", "isLatest": True,
                "isDraft": False, "isPrerelease": False,
                "publishedAt": "2026-10-20T00:00:00Z",
            }])),
        })
        rc, out, _ = self._run_cli(["releases", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("v1.0.0", out)
        self.assertIn("latest", out)

    def test_tags_render(self):
        run = _gh_run({
            "repos/o/r/tags": _Proc(stdout=json.dumps([
                {"name": "v1", "commit": {"sha": "abc123def456"}},
            ])),
        })
        rc, out, _ = self._run_cli(["tags", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("v1", out)
        self.assertIn("abc123d", out)

    def test_branches_render(self):
        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "repos/o/r/branches" in joined:
                return _Proc(stdout=json.dumps([
                    {"name": "main", "protected": True,
                     "commit": {"sha": "abc123def"}},
                    {"name": "dev", "protected": False,
                     "commit": {"sha": "def456abc"}},
                ]))
            if "repos/o/r" in joined:
                return _Proc(stdout="main\n")
            return _Proc(returncode=1, stderr="unstubbed")
        rc, out, _ = self._run_cli(["branches", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("* main", out)
        self.assertIn("dev", out)
        self.assertIn("protected", out)

    def test_branches_json_default(self):
        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "repos/o/r/branches" in joined:
                return _Proc(stdout=json.dumps(
                    [{"name": "main", "commit": {"sha": "abc"}}]))
            if "repos/o/r" in joined:
                return _Proc(stdout="main\n")
            return _Proc(returncode=1)
        rc, out, _ = self._run_cli(["branches", "--repo=o/r", "--json"],
                                   run=run)
        entry = json.loads(out)["data"]["results"][0]
        self.assertEqual(entry["default_branch"], "main")
        self.assertTrue(entry["branches"][0]["default"])

    def test_pages_render_no_site_and_site(self):
        repos.add_pinned("o/r", self.store)
        repos.add_pinned("x/y", self.store)

        def run(cmd, **kw):
            joined = " ".join(cmd)
            if joined.endswith("repos/o/r/pages"):
                return _Proc(returncode=1, stderr="HTTP 404 Not Found")
            if joined.endswith("pages/builds/latest"):
                return _Proc(stdout=json.dumps(
                    {"status": "errored", "error": {"message": "jekyll"},
                     "commit": "abc123", "updated_at": "t"}))
            if joined.endswith("repos/x/y/pages"):
                return _Proc(stdout=json.dumps(
                    {"html_url": "https://x.github.io/y",
                     "cname": "x.example.com", "status": "built"}))
            return _Proc(returncode=1, stderr="unstubbed")
        rc, out, _ = self._run_cli(["pages"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("no Pages site", out)
        self.assertIn("https://x.github.io/y", out)
        self.assertIn("x.example.com", out)
        self.assertIn("jekyll", out)

    def test_inbox_render(self):
        run = _gh_run({
            "api notifications": _Proc(stdout=json.dumps([{
                "id": "1", "unread": True, "reason": "review_requested",
                "subject": {"title": "Look at this", "type": "PullRequest",
                            "url": "https://api.github.com/repos/o/r/pulls/3"},
                "repository": {"full_name": "o/r"},
                "updated_at": "2026-10-20T00:00:00Z",
            }])),
        })
        rc, out, _ = self._run_cli(["inbox"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("Look at this", out)
        self.assertIn("review requested", out)

    def test_inbox_upstream_error(self):
        run = _gh_run({
            "api notifications": _Proc(returncode=1, stderr="boom"),
        })
        rc, out, _ = self._run_cli(["inbox", "--json"], run=run)
        self.assertEqual(rc, 0)
        payload = json.loads(out)["data"]
        self.assertEqual(payload["status"], "upstream-error")
        self.assertEqual(payload["notifications"], [])

    def test_inbox_flattens_pages(self):
        page1 = [{"id": str(i), "subject": {"title": f"n{i}"},
                  "repository": {}, "unread": False} for i in range(100)]

        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "&page=2" in joined:
                return _Proc(stdout=json.dumps([
                    {"id": "100", "subject": {"title": "extra"},
                     "repository": {}, "unread": False}]))
            if "&page=1" in joined:
                return _Proc(stdout=json.dumps(page1))
            return _Proc(returncode=1, stderr="unstubbed")
        rc, out, _ = self._run_cli(["inbox", "--limit=150", "--json"], run=run)
        notes = json.loads(out)["data"]["notifications"]
        self.assertEqual(len(notes), 101)  # 100 + 1; no page=3 stubbed

    def test_pages_build_fetch_failure_is_kept(self):
        def run(cmd, **kw):
            joined = " ".join(cmd)
            if joined.endswith("pages/builds/latest"):
                return _Proc(returncode=1, stderr="rate limited")
            if joined.endswith("repos/o/r/pages"):
                return _Proc(stdout=json.dumps(
                    {"html_url": "https://o.github.io/r"}))
            return _Proc(returncode=1, stderr="unstubbed")
        rc, out, _ = self._run_cli(["pages", "--repo=o/r", "--json"], run=run)
        build = json.loads(out)["data"]["results"][0]["pages"]["latest_build"]
        self.assertEqual(build["status"], "fetch-failed")
        self.assertIn("rate limited", build["error"])

    def test_pr_status_failure_keeps_fields(self):
        run = _gh_run({
            "pr status": _Proc(returncode=1, stderr="HTTP 404 Not Found"),
        })
        rc, out, _ = self._run_cli(
            ["pr", "status", "--repo=o/r", "--json"], run=run)
        entry = json.loads(out)["data"]["results"][0]
        self.assertEqual(entry["status"], "not-found")
        self.assertIsNone(entry["current_branch"])
        self.assertEqual(entry["authored"], [])
        self.assertEqual(entry["review_requested"], [])

    def test_repo_search_honors_limit(self):
        seen = {}

        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            return _Proc(stdout="[]")
        rc, out, _ = self._run_cli(
            ["repo", "search", "q", "--limit=7"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("--limit 7", seen["argv"])

    def test_json_errors_leave_stdout_clean(self):
        run = _gh_run({
            "repo view": _Proc(returncode=1, stderr="Could not resolve to a "
                               "Repository with the name 'o/r'"),
        })
        rc, out, err = self._run_cli(["repo", "add", "o/r", "--json"], run=run)
        self.assertEqual(rc, 1)
        self.assertNotIn("Cannot reach", out)
        self.assertIn("Cannot reach", err)
        rc, out, err = self._run_cli(["pr", "--repo=o/r", "--state=nah",
                                      "--json"], run=run)
        self.assertEqual(rc, 1)
        self.assertEqual(out.strip(), "")
        self.assertIn("--state", err)

    def test_inbox_env_blocked(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, out, _ = self._run_cli(["inbox", "--json"], env=env)
        self.assertEqual(rc, 1)
        payload = json.loads(out)["data"]
        self.assertEqual(payload["status"], "auth-required")

    def test_env_after_clean_sweep_skips_probe(self):
        """A successful fetch is itself the auth proof — no probe runs."""
        with patch("quiver.github.gh.probe_env") as probe:
            env = ghcli._env_after([{"status": "ok"}, {"status": "ok"}])
        self.assertEqual(env.status, "ok")
        probe.assert_not_called()

    def test_env_after_failure_probes(self):
        blocked = gh.GhEnv(installed=True, authenticated=False)
        with patch("quiver.github.gh.probe_env", return_value=blocked) as probe:
            env = ghcli._env_after([{"status": "upstream-error"}])
        probe.assert_called_once()
        self.assertEqual(env.status, "auth-required")

    def test_board_json(self):
        run = _gh_run({
            "pr list": _Proc(stdout="[]"),
            "issue list": _Proc(stdout="[]"),
            "run list": _Proc(stdout="[]"),
            "pages/builds": _Proc(returncode=1, stderr="404 Not Found"),
            "repos/o/r/pages": _Proc(returncode=1, stderr="404 Not Found"),
            "release list": _Proc(stdout="[]"),
            "tags?per_page": _Proc(stdout="[]"),
            "default_branch": _Proc(stdout="main\n"),
            "branches?per_page": _Proc(stdout="[]"),
        })
        rc, out, _ = self._run_cli(["board", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        board = data["boards"][0]
        self.assertEqual(board["repo"], "o/r")
        self.assertEqual(
            set(board["surfaces"]),
            {"pull_requests", "issues", "runs", "pages",
             "releases", "tags", "branches"})
        for entry in board["surfaces"].values():
            self.assertEqual(entry["status"], "ok")

    def test_board_render(self):
        run = _gh_run({
            "pr list": _Proc(stdout=json.dumps([{"number": 7}])),
            "issue list": _Proc(stdout="[]"),
            "run list": _Proc(stdout="[]"),
            "pages": _Proc(returncode=1, stderr="404 Not Found"),
            "release list": _Proc(stdout="[]"),
            "tags?per_page": _Proc(stdout="[]"),
            "default_branch": _Proc(stdout="main\n"),
            "branches?per_page": _Proc(stdout="[]"),
        })
        rc, out, _ = self._run_cli(["board", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("o/r", out)
        self.assertIn("PRs", out)

    def test_board_render_mixed_surfaces(self):
        """Failed entries print their status; a Pages site prints site."""
        run = _gh_run({
            "pr list": _Proc(returncode=1, stderr="boom"),
            "issue list": _Proc(stdout="[]"),
            "run list": _Proc(stdout="[]"),
            "repos/o/r/pages": _Proc(stdout=json.dumps(
                {"html_url": "https://o.github.io/r"})),
            "release list": _Proc(stdout="[]"),
            "tags?per_page": _Proc(stdout="[]"),
            "default_branch": _Proc(stdout="main\n"),
            "branches?per_page": _Proc(stdout="[]"),
        })
        rc, out, _ = self._run_cli(["board", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("upstream-error", out)
        self.assertIn("site", out)

    def test_board_rejects_positional_and_bad_flag(self):
        rc, out, _ = self._run_cli(["board", "extra", "--repo=o/r"])
        self.assertEqual(rc, 1)
        rc, out, _ = self._run_cli(["board", "--bogus"])
        self.assertEqual(rc, 1)
        rc, out, _ = self._run_cli(["board", "--repo"])
        self.assertEqual(rc, 1)

    def test_board_no_repos(self):
        rc, out, _ = self._run_cli(["board", "--json"])
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(out)["data"]["boards"], [])
        rc, _, _ = self._run_cli(["board"])
        self.assertEqual(rc, 1)

    def test_board_web_opens_pulls(self):
        opened = []
        with patch("quiver.github.cli.webbrowser.open", opened.append):
            rc, _, _ = self._run_cli(["board", "--repo=o/r", "--web"])
        self.assertEqual(rc, 0)
        self.assertEqual(opened, ["https://github.com/o/r/pulls"])

    def test_json_no_repos_is_typed_env(self):
        rc, out, _ = self._run_cli(["pr", "--json"])
        self.assertEqual(rc, 1)
        doc = json.loads(out)
        self.assertEqual(doc["data"]["github"]["status"], "ok")
        self.assertEqual(doc["data"]["results"], [])

    def test_json_blocked_env_fails_after_fetch(self):
        """A failed fetch probes to classify; a blocked env is typed."""
        run = _gh_run({"pr list": _Proc(returncode=1, stderr="boom")})
        blocked = gh.GhEnv(installed=True, authenticated=False)
        rc, out, err = self._run_cli(
            ["pr", "--repo=o/r", "--json"], run=run, env=blocked)
        self.assertEqual(rc, 1)
        doc = json.loads(out)
        self.assertEqual(doc["data"]["github"]["status"], "auth-required")
        self.assertEqual(doc["data"]["results"], [])
        self.assertIn("auth", err)

    def test_json_web_still_opens(self):
        opened = []
        run = _gh_run({"pr list": _Proc(stdout="[]")})
        with patch("quiver.github.cli.webbrowser.open", opened.append):
            rc, out, _ = self._run_cli(
                ["pr", "--repo=o/r", "--web", "--json"], run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(opened, ["https://github.com/o/r/pulls"])

    def test_repo_search_render(self):
        run = _gh_run({
            "search repos": _Proc(stdout=json.dumps([
                {"fullName": "a/b", "description": "desc",
                 "stargazersCount": 42, "updatedAt": "t"},
            ])),
        })
        rc, out, _ = self._run_cli(["repo", "search", "quiver"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("a/b", out)
        self.assertIn("desc", out)
        self.assertIn("swe gh repo add", out)

    def test_repo_search_json(self):
        run = _gh_run({
            "search repos": _Proc(stdout=json.dumps(
                [{"fullName": "a/b"}])),
        })
        rc, out, _ = self._run_cli(["repo", "search", "q", "--json"], run=run)
        self.assertEqual(json.loads(out)["data"]["results"][0]["name"], "a/b")

    def test_repo_list_and_unknown(self):
        repos.add_pinned("o/r", self.store)
        rc, out, _ = self._run_cli(["repo"])
        self.assertEqual(rc, 0)
        self.assertIn("o/r", out)
        rc, out, _ = self._run_cli(["repo", "bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("Unknown", out)
        rc, out, _ = self._run_cli(["repo", "add"])
        self.assertEqual(rc, 1)
        self.assertIn("Usage", out)
        rc, out, _ = self._run_cli(["repo", "remove", "o/r"])
        self.assertEqual(rc, 0)

    def test_unexpected_positional(self):
        rc, out, _ = self._run_cli(["pr", "list", "extra", "--repo=o/r"])
        self.assertEqual(rc, 1)
        self.assertIn("Unexpected args", out)

    def test_web_opens_url(self):
        opened = []
        run = _gh_run({"pr list": _Proc(stdout="[]")})
        with patch("quiver.github.cli.webbrowser.open",
                   opened.append):
            rc, out, _ = self._run_cli(["pr", "--repo=o/r", "--web"], run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(opened, ["https://github.com/o/r/pulls"])

    def test_overview_json(self):
        repos.add_pinned("o/r", self.store)
        rc, out, _ = self._run_cli(["--json"])
        self.assertEqual(rc, 0)
        doc = json.loads(out)
        self.assertEqual(doc["data"]["github"]["status"], "ok")
        self.assertEqual(doc["data"]["repos"][0]["name"], "o/r")

    def test_help_routes(self):
        rc, out, _ = self._run_cli(["help"])
        self.assertEqual(rc, 0)
        self.assertIn("swe gh pr", out)


if __name__ == "__main__":
    unittest.main()
