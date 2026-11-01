"""Tests for swe gh: repo parsing, the pinned store, shaping, dispatch."""

import io
import json
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from swe.github import catalog, cli as ghcli
from swe.github import gh, repos
from swe import paths
from tests._tempdir import temp_dir


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

    def test_reorder(self):
        for n in ("o/r", "a/b", "x/y"):
            repos.add_pinned(n, self.store)
        pinned = repos.reorder_pinned(["x/y", "o/r"], self.store)
        self.assertEqual([r["name"] for r in pinned],
                         ["x/y", "o/r", "a/b"])

    def test_reorder_ignores_unknown_and_dupes(self):
        repos.add_pinned("o/r", self.store)
        repos.add_pinned("a/b", self.store)
        pinned = repos.reorder_pinned(
            ["a/b", "no/pe", "A/B"], self.store)
        self.assertEqual([r["name"] for r in pinned],
                         ["a/b", "o/r"])


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Path(self.tmp.name) / "github.json"
        patcher = patch.object(paths, "GITHUB_FILE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_repo_arg_wins(self):
        self.assertEqual(repos.resolve("me/proj"), ["me/proj"])

    def test_repo_arg_comma_list(self):
        self.assertEqual(repos.resolve("a/b,c/d"), ["a/b", "c/d"])

    def test_repo_arg_comma_drops_invalid_parts(self):
        self.assertEqual(repos.resolve("a/b,not a repo"), ["a/b"])
        self.assertEqual(repos.resolve("not a repo"), [])

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


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "catalog.sqlite"

    def _rows(self, owner, names, stars=0):
        return [{"full_name": f"{owner}/{n}", "owner": owner,
                 "description": f"{n} description", "stars": stars,
                 "updated_at": "2026-10-20", "is_private": False}
                for n in names]

    def test_status_none_before_sync(self):
        self.assertIsNone(catalog.status(self.db))

    def test_search_empty_db(self):
        self.assertEqual(catalog.search("x", 5, self.db), [])

    def test_write_then_status(self):
        catalog.write(self._rows("octocat", ["quiver"]), ["octocat"],
                      self.db)
        st = catalog.status(self.db)
        self.assertEqual(st["repos"], 1)
        self.assertEqual(st["owners"], ["octocat"])
        self.assertGreater(st["synced_at"], 0)

    def test_search_ranks_name_over_description(self):
        catalog.write(
            [{"full_name": "b/other", "owner": "b",
              "description": "infra tooling", "stars": 99,
              "updated_at": None, "is_private": False},
             {"full_name": "a/infra-docs", "owner": "a",
              "description": "plain", "stars": 1,
              "updated_at": None, "is_private": False}],
            ["a", "b"], self.db)
        hits = catalog.search("infra", 10, self.db)
        self.assertEqual(hits[0]["name"], "a/infra-docs")

    def test_resync_prunes_only_covered_owners(self):
        catalog.write(self._rows("a", ["keep", "gone"])
                      + self._rows("b", ["theirs"]), ["a", "b"], self.db)
        catalog.write(self._rows("a", ["keep"]), ["a"], self.db)
        names = {r["name"] for r in catalog.search("", 50, self.db)}
        self.assertEqual(names, {"a/keep", "b/theirs"})

    def test_search_treats_wildcard_chars_literally(self):
        catalog.write([{"full_name": "a/quxver", "owner": "a",
                        "description": None, "stars": 0,
                        "updated_at": None, "is_private": False},
                       {"full_name": "a/qu_ver", "owner": "a",
                        "description": None, "stars": 0,
                        "updated_at": None, "is_private": False}],
                      ["a"], self.db)
        hits = catalog.search("qu_ver", 10, self.db)
        self.assertEqual([h["name"] for h in hits], ["a/qu_ver"])


class TestCommands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Path(self.tmp.name) / "config" / "github.json"
        patcher = patch.object(paths, "GITHUB_FILE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.owners_cache = Path(self.tmp.name) / "cache" / "github_owners.json"
        owners_patcher = patch.object(paths, "GITHUB_OWNERS_CACHE",
                                      self.owners_cache)
        owners_patcher.start()
        self.addCleanup(owners_patcher.stop)
        self.catalog_db = (Path(self.tmp.name) / "data" / "github"
                           / "catalog.sqlite")
        catalog_patcher = patch.object(paths, "GITHUB_CATALOG",
                                       self.catalog_db)
        catalog_patcher.start()
        self.addCleanup(catalog_patcher.stop)

    def _run_cli(self, args, run=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        env = env or _env_ok()
        with patch("swe.github.gh.probe_env", return_value=env), \
             patch("subprocess.run", run or _gh_run({})), \
             patch("swe.github.repos.repo_at", return_value=None), \
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

    def test_repo_reorder_json(self):
        for n in ("o/r", "a/b", "x/y"):
            repos.add_pinned(n, self.store)
        rc, out, _ = self._run_cli(
            ["repo", "reorder", "x/y", "a/b", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out)["data"]
        self.assertEqual(payload["pinned"], ["x/y", "a/b", "o/r"])
        self.assertEqual([r["name"] for r in repos.load_pinned(self.store)],
                         ["x/y", "a/b", "o/r"])

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

    def test_pr_empty_stdout_is_an_error_not_an_empty_list(self):
        # gh exiting 0 with nothing on stdout is an eaten reply, not a
        # zero-PR repo — the entry must fail so a transient blip shows
        # as an error instead of "no pull requests".
        run = _gh_run({"pr list": _Proc(stdout="")})
        rc, out, _ = self._run_cli(["pr", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        entry = json.loads(out)["data"]["results"][0]
        self.assertEqual(entry["status"], "upstream-error")
        self.assertEqual(entry["pull_requests"], [])

    def test_pr_non_list_reply_is_an_error(self):
        run = _gh_run({"pr list": _Proc(stdout='{"unexpected": true}')})
        rc, out, _ = self._run_cli(["pr", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        entry = json.loads(out)["data"]["results"][0]
        self.assertEqual(entry["status"], "upstream-error")

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

    def test_issue_list_comments_array(self):
        # `gh issue list` returns the comment objects themselves — the
        # row folds them to the count the app's Int field decodes.
        run = _gh_run({
            "issue list": _Proc(stdout=json.dumps([{
                "number": 5, "title": "Bug", "state": "OPEN",
                "author": {"login": "u"}, "labels": [], "assignees": [],
                "comments": [{"body": "a"}, {"body": "b"}, {"body": "c"}],
                "updatedAt": "2026-10-20T00:00:00Z", "url": "u",
            }])),
        })
        rc, out, _ = self._run_cli(["issue", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        issue = json.loads(out)["data"]["results"][0]["issues"][0]
        self.assertEqual(issue["comments"], 3)

    def test_fan_handles_no_names_and_one(self):
        # The hub's surfaces fan out through the same helper
        # (shared.fan): an empty pin list must not build an empty pool.
        from swe.github import shared

        self.assertIs(shared.fan, ghcli._fan)
        self.assertEqual(ghcli._fan([], lambda n: n), [])
        self.assertEqual(ghcli._fan(["a"], str.upper), ["A"])
        self.assertEqual(ghcli._fan(["a", "b", "c"], str.upper),
                         ["A", "B", "C"])

    def test_comma_repo_fans_out(self):
        run = _gh_run({"issue list": _Proc(stdout="[]")})
        rc, out, _ = self._run_cli(
            ["issue", "--repo=o/r,a/b", "--json"], run=run)
        self.assertEqual(rc, 0)
        got = json.loads(out)["data"]["results"]
        self.assertEqual([r["repo"] for r in got], ["o/r", "a/b"])

    def test_comma_repo_rejects_bad_part(self):
        rc, out, _ = self._run_cli(["issue", "--repo=o/r,not a repo"])
        self.assertEqual(rc, 1)
        self.assertIn("not a repo", out.lower())

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

    def test_a_failed_later_page_marks_the_inbox_partial(self):
        # The first page's rows stay, but the listing must not read as
        # complete when page 2 failed.
        page1 = [{"id": str(i), "subject": {"title": f"n{i}"},
                  "repository": {}, "unread": False} for i in range(100)]

        def run(cmd, **kw):
            joined = " ".join(cmd)
            if "&page=1" in joined:
                return _Proc(stdout=json.dumps(page1))
            return _Proc(returncode=1, stderr="rate limited")
        rc, out, _ = self._run_cli(["inbox", "--limit=150", "--json"], run=run)
        data = json.loads(out)["data"]
        self.assertEqual(len(data["notifications"]), 100)
        self.assertTrue(data["partial"])
        self.assertIn("page 2", data["error"])
        tags = ghcli._repo_result("o/r", "tags", gh.GhResult(
            "ok", data=[{"name": "v1"}], error="page 2 failed: x"), dict)
        self.assertEqual((tags["status"], tags["partial"]), ("ok", True))

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

    def test_repo_search_scopes_to_user_and_orgs(self):
        seen = {}
        run = _gh_run({
            "user/orgs": _Proc(stdout="org-one\norg-two\n"),
            "search repos": _Proc(stdout="[]"),
        })

        def record(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            return run(cmd, **kw)

        rc, out, _ = self._run_cli(["repo", "search", "q"], run=record)
        self.assertEqual(rc, 0)
        self.assertIn("--owner octocat", seen["argv"])
        self.assertIn("--owner org-one", seen["argv"])
        self.assertIn("--owner org-two", seen["argv"])

    def test_repo_search_all_flag_runs_unscoped(self):
        seen = {}
        run = _gh_run({
            "search repos": _Proc(stdout="[]"),
        })

        def record(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            return run(cmd, **kw)

        rc, out, _ = self._run_cli(["repo", "search", "q", "--all"], run=record)
        self.assertEqual(rc, 0)
        self.assertNotIn("--owner", seen["argv"])

    def test_repo_search_org_fetch_failure_still_scopes_login(self):
        """A failed orgs read shouldn't widen to public GitHub — the
        search degrades to just the user's own repos."""
        seen = {}
        org_calls = []
        run = _gh_run({
            "user/orgs": _Proc(returncode=1, stderr="boom"),
            "search repos": _Proc(stdout="[]"),
        })

        def record(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            if "user/orgs" in joined:
                org_calls.append(joined)
            return run(cmd, **kw)

        rc, out, _ = self._run_cli(["repo", "search", "q"], run=record)
        self.assertEqual(rc, 0)
        self.assertIn("--owner octocat", seen["argv"])
        self.assertNotIn("org-one", seen["argv"])
        # ...and the login-only fallback is not cached: a later search
        # retries the orgs read rather than hiding org repos for the TTL.
        self._run_cli(["repo", "search", "q2"], run=record)
        self.assertEqual(len(org_calls), 2)

    def test_repo_search_owner_scope_is_cached(self):
        """Each `swe` run is a new process — the owner scope persists
        on disk so a typing pause doesn't refetch the same orgs."""
        calls = []
        run = _gh_run({
            "user/orgs": _Proc(stdout="org-one\n"),
            "search repos": _Proc(stdout="[]"),
        })

        def record(cmd, **kw):
            calls.append(" ".join(str(a) for a in cmd))
            return run(cmd, **kw)

        self._run_cli(["repo", "search", "q"], run=record)
        self._run_cli(["repo", "search", "q2"], run=record)
        self.assertEqual(sum("user/orgs" in c for c in calls), 1)

    def test_repo_search_owner_cache_expires(self):
        self.owners_cache.parent.mkdir(parents=True, exist_ok=True)
        self.owners_cache.write_text(json.dumps({"logins": {"octocat": {
            "owners": ["octocat", "stale-org"],
            "fetched_at": time.time() - 3600}}}))
        seen = {}
        run = _gh_run({
            "user/orgs": _Proc(stdout="fresh-org\n"),
            "search repos": _Proc(stdout="[]"),
        })

        def record(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            return run(cmd, **kw)

        self._run_cli(["repo", "search", "q"], run=record)
        self.assertIn("--owner fresh-org", seen["argv"])
        self.assertNotIn("stale-org", seen["argv"])

    def test_repo_search_owner_cache_is_per_login(self):
        """A cache entry under another account must not scope this one —
        `gh auth switch` means the login on file can differ."""
        self.owners_cache.parent.mkdir(parents=True, exist_ok=True)
        self.owners_cache.write_text(json.dumps({"logins": {"someone-else": {
            "owners": ["someone-else", "their-org"],
            "fetched_at": time.time()}}}))
        seen = {}
        run = _gh_run({
            "user/orgs": _Proc(stdout="my-org\n"),
            "search repos": _Proc(stdout="[]"),
        })

        def record(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            return run(cmd, **kw)

        self._run_cli(["repo", "search", "q"], run=record)
        self.assertIn("--owner my-org", seen["argv"])
        self.assertNotIn("their-org", seen["argv"])

    def _catalog_rows(self, owner, names):
        return [{"full_name": f"{owner}/{n}", "owner": owner,
                 "description": f"{n} description", "stars": i,
                 "updated_at": "2026-10-20T00:00:00Z", "is_private": False}
                for i, n in enumerate(names)]

    def _sync_run(self, repo_lists, orgs="org-one\n"):
        handlers = {"user/orgs": _Proc(stdout=orgs)}
        for owner, rows in repo_lists.items():
            handlers[f"repo list {owner}"] = _Proc(
                stdout=json.dumps([
                    {"nameWithOwner": f"{owner}/{n}", "description": f"{n}",
                     "stargazerCount": i,
                     "updatedAt": "2026-10-20T00:00:00Z",
                     "isPrivate": False}
                    for i, n in enumerate(rows)]))
        return _gh_run(handlers)

    def test_sync_writes_catalog_and_emits_summary(self):
        run = self._sync_run({"octocat": ["swe"], "org-one": ["sdk"]})
        rc, out, _ = self._run_cli(["sync", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["repos"], 2)
        self.assertEqual(data["owners"], ["octocat", "org-one"])
        self.assertEqual(data["failed"], [])
        status = catalog.status()
        self.assertEqual(status["repos"], 2)
        self.assertGreater(status["synced_at"], 0)

    def test_sync_failed_owner_returns_1_and_keeps_rows(self):
        run = self._sync_run({"octocat": ["swe"], "org-one": ["sdk"]})
        self._run_cli(["sync"], run=run)
        handlers = {
            "user/orgs": _Proc(stdout="org-one\n"),
            "repo list octocat": _Proc(stdout="[]"),
            "repo list org-one": _Proc(returncode=1, stderr="boom"),
        }
        # --json: the payload carries `failed`, so the exit stays 0 and
        # callers can still read which owners did not sync.
        rc, out, _ = self._run_cli(["sync", "--json"],
                                   run=_gh_run(handlers))
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual([f["owner"] for f in data["failed"]], ["org-one"])
        # Text mode reports the partial failure as exit 1.
        rc, _, _ = self._run_cli(["sync"], run=_gh_run(handlers))
        self.assertEqual(rc, 1)
        # The failed owner's rows survive; octocat's pruned rows don't.
        self.assertEqual(
            [r["name"] for r in catalog.search("sdk", 10)],
            ["org-one/sdk"])
        self.assertEqual(catalog.search("quiver", 10), [])

    def test_sync_env_blocked(self):
        env = gh.GhEnv(installed=False)
        rc, out, _ = self._run_cli(["sync"], env=env)
        self.assertEqual(rc, 1)
        self.assertIn("brew install gh", out)

    def test_sync_account_switch_wipes_previous_rows(self):
        run = self._sync_run({"octocat": ["swe"]}, orgs="")
        self._run_cli(["sync"], run=run)
        other = gh.GhEnv(installed=True, authenticated=True,
                         login="someone-else")
        run = self._sync_run({"someone-else": ["theirs"]}, orgs="")
        self._run_cli(["sync"], run=run, env=other)
        self.assertEqual(catalog.search("quiver", 10), [])
        self.assertEqual(
            [r["name"] for r in catalog.search("theirs", 10)],
            ["someone-else/theirs"])
        self.assertEqual(catalog.status()["owners"], ["someone-else"])

    def test_sync_orgs_failure_is_reported(self):
        run = _gh_run({
            "user/orgs": _Proc(returncode=1, stderr="boom"),
            "repo list octocat": _Proc(stdout=json.dumps([
                {"nameWithOwner": "octocat/quiver", "description": None,
                 "stargazerCount": 1, "updatedAt": "2026-10-20T00:00:00Z",
                 "isPrivate": False}])),
        })
        rc, out, _ = self._run_cli(["sync", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["failed"][0]["owner"], "<orgs>")
        self.assertEqual(data["owners"], ["octocat"])

    def test_sync_nonlist_response_counts_as_failure(self):
        self._run_cli(["sync"], run=self._sync_run(
            {"octocat": ["swe"], "org-one": ["sdk"]}))
        handlers = {
            "user/orgs": _Proc(stdout="org-one\n"),
            # gh exits 0 but prints junk — must not prune the old rows.
            "repo list org-one": _Proc(stdout="not json"),
            "repo list octocat": _Proc(stdout="[]"),
        }
        rc, out, _ = self._run_cli(["sync", "--json"],
                                   run=_gh_run(handlers))
        data = json.loads(out)["data"]
        self.assertEqual([f["owner"] for f in data["failed"]],
                         ["org-one"])
        self.assertEqual(
            [r["name"] for r in catalog.search("sdk", 10)],
            ["org-one/sdk"])

    def test_repo_search_reads_catalog_without_gh(self):
        catalog.write(self._catalog_rows("octocat", ["quiver"]),
                      ["octocat"])
        # Every gh call is unstubbed — a catalog hit must not make one.
        rc, out, _ = self._run_cli(
            ["repo", "search", "quiver", "--json"], run=_gh_run({}))
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["source"], "catalog")
        self.assertEqual(data["results"][0]["name"], "octocat/quiver")

    def test_repo_search_catalog_miss_falls_back_live(self):
        catalog.write(self._catalog_rows("octocat", ["quiver"]),
                      ["octocat"])
        seen = {}
        run = _gh_run({
            "user/orgs": _Proc(stdout=""),
            "search repos": _Proc(stdout=json.dumps([
                {"fullName": "octocat/new-repo", "description": "fresh",
                 "stargazersCount": 1, "updatedAt": "2026-10-20"}])),
        })

        def record(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            return run(cmd, **kw)

        rc, out, _ = self._run_cli(["repo", "search", "new", "--json"],
                                   run=record)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["source"], "github")
        self.assertIn("--owner octocat", seen["argv"])

    def test_repo_search_all_bypasses_catalog(self):
        catalog.write(self._catalog_rows("octocat", ["quiver"]),
                      ["octocat"])
        seen = {}
        run = _gh_run({"search repos": _Proc(stdout="[]")})

        def record(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "search repos" in joined:
                seen["argv"] = joined
            return run(cmd, **kw)

        self._run_cli(["repo", "search", "quiver", "--all"], run=record)
        self.assertIn("search repos", seen["argv"])
        self.assertNotIn("--owner", seen["argv"])

    def test_overview_json_reports_catalog(self):
        rc, out, _ = self._run_cli(["--json"])
        data = json.loads(out)["data"]
        self.assertIn("catalog", data)          # never synced: present but null
        self.assertIsNone(data["catalog"])
        catalog.write(self._catalog_rows("octocat", ["quiver"]),
                      ["octocat"])
        rc, out, _ = self._run_cli(["--json"])
        data = json.loads(out)["data"]
        self.assertEqual(data["catalog"]["repos"], 1)
        self.assertEqual(data["catalog"]["owners"], ["octocat"])

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
        with patch("swe.github.gh.probe_env") as probe:
            env = ghcli._env_after([{"status": "ok"}, {"status": "ok"}])
        self.assertEqual(env.status, "ok")
        probe.assert_not_called()

    def test_env_after_failure_probes(self):
        blocked = gh.GhEnv(installed=True, authenticated=False)
        with patch("swe.github.gh.probe_env", return_value=blocked) as probe:
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
        with patch("swe.github.cli.webbrowser.open", opened.append):
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
        with patch("swe.github.cli.webbrowser.open", opened.append):
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
        with patch("swe.github.cli.webbrowser.open",
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

    # -- swe gh pr view ---------------------------------------------

    def _pr_detail_json(self):
        return json.dumps({
            "number": 7, "title": "Ship it", "state": "OPEN",
            "isDraft": False, "author": {"login": "octocat"},
            "headRefName": "feat/x", "baseRefName": "main",
            "body": "the body", "url": "https://github.com/o/r/pull/7",
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN",
            "reviewDecision": "APPROVED",
            "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "build",
                 "workflowName": "CI", "status": "COMPLETED",
                 "conclusion": "SUCCESS",
                 "detailsUrl": "https://x/1"},
                {"__typename": "StatusContext", "context": "lint",
                 "state": "FAILURE", "targetUrl": "https://x/2"},
            ],
            "latestReviews": [
                {"author": {"login": "rev"}, "state": "APPROVED",
                 "submittedAt": "t"},
            ],
            "reviewRequests": [
                {"login": "asked"}, {"slug": "team-a"},
            ],
            "additions": 10, "deletions": 2, "changedFiles": 3,
            "labels": [{"name": "bug"}],
            "createdAt": "c", "updatedAt": "u", "mergedAt": None,
            "autoMergeRequest": {"mergeMethod": "SQUASH",
                                 "enabledAt": "e"},
        })

    def test_pr_view_json(self):
        run = _gh_run({"pr view": _Proc(stdout=self._pr_detail_json())})
        rc, out, _ = self._run_cli(
            ["pr", "view", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["repo"], "o/r")
        self.assertEqual(data["status"], "ok")
        pr = data["pr"]
        self.assertEqual(pr["number"], 7)
        self.assertEqual(pr["body"], "the body")
        self.assertEqual(pr["merge_state"], "clean")
        self.assertEqual(pr["additions"], 10)
        self.assertEqual(pr["changed_files"], 3)
        self.assertEqual(pr["check_runs"][0]["name"], "build")
        self.assertEqual(pr["check_runs"][0]["workflow"], "CI")
        self.assertEqual(pr["check_runs"][1]["name"], "lint")
        self.assertEqual(pr["check_runs"][1]["conclusion"], "failure")
        self.assertEqual(pr["reviews"][0]["author"], "rev")
        self.assertEqual(pr["review_requests"], ["asked", "team-a"])
        self.assertEqual(pr["auto_merge"]["method"], "squash")

    def test_pr_view_render(self):
        run = _gh_run({"pr view": _Proc(stdout=self._pr_detail_json())})
        rc, out, _ = self._run_cli(
            ["pr", "view", "7", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("Ship it", out)
        self.assertIn("build", out)
        self.assertIn("octocat", out)

    def test_pr_view_not_found(self):
        run = _gh_run({
            "pr view": _Proc(returncode=1, stderr="no pull requests found"),
        })
        rc, out, _ = self._run_cli(
            ["pr", "view", "99", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["status"], "upstream-error")
        self.assertIsNone(data["pr"])
        self.assertIn("error", data)

    def test_pr_view_multi_pin_ambiguous(self):
        repos.add_pinned("o/a", self.store)
        repos.add_pinned("o/b", self.store)
        rc, out, _ = self._run_cli(["pr", "view", "7"])
        self.assertEqual(rc, 1)
        self.assertIn("--repo", out)

    def test_pr_view_env_blocked(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, _, _ = self._run_cli(["pr", "view", "7", "--repo=o/r"], env=env)
        self.assertEqual(rc, 1)

    def test_pr_view_env_blocked_emits_envelope(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, out, _ = self._run_cli(
            ["pr", "view", "7", "--repo=o/r", "--json"], env=env)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["github"]["status"], "auth-required")
        self.assertIsNone(data["pr"])
        self.assertIn("error", data)

    def test_pr_view_pending_context_shows_running(self):
        detail = json.loads(self._pr_detail_json())
        detail["statusCheckRollup"] = [
            {"__typename": "StatusContext", "context": "ci/build",
             "state": "PENDING", "targetUrl": "https://x/3"},
        ]
        run = _gh_run({"pr view": _Proc(stdout=json.dumps(detail))})
        rc, out, _ = self._run_cli(
            ["pr", "view", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        chk = json.loads(out)["data"]["pr"]["check_runs"][0]
        self.assertEqual(chk["state"], "running")
        self.assertEqual(chk["status"], "queued")
        self.assertIsNone(chk["conclusion"])

    def test_pr_view_pending_context_renders_glyph(self):
        detail = json.loads(self._pr_detail_json())
        detail["statusCheckRollup"] = [
            {"__typename": "StatusContext", "context": "ci/build",
             "state": "PENDING", "targetUrl": "https://x/3"},
        ]
        run = _gh_run({"pr view": _Proc(stdout=json.dumps(detail))})
        rc, out, _ = self._run_cli(
            ["pr", "view", "7", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("◐", out)

    def test_pr_view_single_pin_resolves(self):
        repos.add_pinned("o/r", self.store)
        run = _gh_run({"pr view": _Proc(stdout=self._pr_detail_json())})
        rc, out, _ = self._run_cli(["pr", "view", "7", "--json"], run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"]["repo"], "o/r")

    # -- swe gh pr merge ---------------------------------------------

    def test_pr_merge_squash_default(self):
        seen = []

        def run(cmd, **kw):
            seen.append(" ".join(str(a) for a in cmd))
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["method"], "squash")
        self.assertTrue(data["merged"])
        self.assertFalse(data["auto"])
        self.assertIn("--squash", seen[0])
        self.assertNotIn("--auto", seen[0])

    def test_pr_merge_method_and_auto(self):
        seen = []

        def run(cmd, **kw):
            seen.append(" ".join(str(a) for a in cmd))
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r", "--method=rebase",
             "--auto", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["method"], "rebase")
        self.assertTrue(data["auto"])
        self.assertFalse(data["merged"])
        self.assertIn("--rebase", seen[0])
        self.assertIn("--auto", seen[0])

    def test_pr_merge_bad_method(self):
        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r", "--method=yolo"])
        self.assertEqual(rc, 1)
        self.assertIn("--method", out)

    def test_pr_merge_failure(self):
        run = _gh_run({
            "pr merge": _Proc(returncode=1, stderr="not mergeable"),
        })
        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertFalse(data["merged"])
        self.assertIn("not mergeable", data["error"])

    # -- swe gh pr worktree -------------------------------------------

    def _patch_paths(self):
        mirrors = Path(self.tmp.name) / "data" / "github" / "mirrors"
        trees = Path(self.tmp.name) / "worktrees"
        patches = [
            patch.object(paths, "GITHUB_MIRRORS", mirrors),
            patch.object(paths, "WORKTREES_DIR", trees),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return mirrors, trees

    def test_pr_worktree_creates_then_reuses(self):
        mirrors, trees = self._patch_paths()
        seen = []

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            seen.append(joined)
            if "worktree list" in joined:
                wt = trees / "o__r-pr7"
                if wt.exists():
                    return _Proc(stdout=f"worktree {wt}\n")
                return _Proc(stdout="worktree /x/mirror\n")
            if "rev-parse" in joined:
                return _Proc(stdout="abc123def456\n")
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["repo"], "o/r")
        self.assertEqual(data["pr"], 7)
        self.assertTrue(data["created"])
        self.assertEqual(data["head"], "abc123def456")
        self.assertIn("refs/pull/7/head", seen[1])
        self.assertIn("worktree list", seen[3])
        self.assertIn("worktree add --detach", seen[4])
        # The clone ran first, bare + blob-filtered.
        self.assertIn("repo clone o/r", seen[0])
        self.assertIn("--bare", seen[0])
        self.assertIn("--filter=blob:none", seen[0])

        # Second call: mirror exists, worktree registered → reuse.
        (mirrors / "o__r.git").mkdir(parents=True)
        (trees / "o__r-pr7").mkdir(parents=True)
        seen.clear()
        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertFalse(data["created"])
        self.assertFalse(any("repo clone" in c for c in seen))
        self.assertFalse(any("worktree add" in c for c in seen))

    def test_pr_worktree_lands_where_the_hub_names_it(self):
        # The hub names a PR's folder before the checkout exists (a
        # pending launch's cwd, the detail page's ``worktree``): the
        # checkout must land exactly there.
        from swe.github import pulls as gh_pulls

        _mirrors, trees = self._patch_paths()

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "worktree list" in joined:
                return _Proc(stdout="worktree /x/mirror\n")
            if "rev-parse" in joined:
                return _Proc(stdout="abc123def456\n")
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=O/r.x", "--json"], run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"]["path"],
                         gh_pulls.worktree_path("O/r.x", "7"))
        self.assertEqual(gh_pulls.worktree_path("O/r.x", 7),
                         str(trees / "O__r.x-pr7"))

    def test_pr_worktree_accepts_url_ref(self):
        self._patch_paths()
        seen = []

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            seen.append(joined)
            if "worktree list" in joined:
                return _Proc(stdout="worktree /x\n")
            if "rev-parse" in joined:
                return _Proc(stdout="abc\n")
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "worktree", "https://github.com/o/r/pull/9",
             "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["data"]["pr"], 9)
        self.assertIn("refs/pull/9/head", seen[1])

    def test_pr_worktree_bad_ref(self):
        rc, out, _ = self._run_cli(
            ["pr", "worktree", "abc", "--repo=o/r"])
        self.assertEqual(rc, 1)
        self.assertIn("pull", out)

    def test_pr_worktree_fetch_failure(self):
        self._patch_paths()

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "refs/pull/" in joined:
                return _Proc(returncode=1,
                             stderr="couldn't find remote ref")
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "worktree", "99", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertIsNone(data["path"])
        self.assertIn("Fetch failed", data["error"])

    def test_pr_worktree_env_blocked(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r", "--json"], env=env)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["github"]["status"], "auth-required")
        self.assertIsNone(data["path"])
        self.assertIn("error", data)
        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r"], env=env)
        self.assertEqual(rc, 1)
        self.assertIn("gh auth login", out)

    def test_pr_worktree_no_repo_resolves_error(self):
        rc, out, _ = self._run_cli(["pr", "worktree", "7", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertIsNone(data["path"])
        self.assertIn("No repo to query", data["error"])

    def test_pr_worktree_clone_failure(self):
        self._patch_paths()

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "repo clone" in joined:
                return _Proc(returncode=1, stderr="denied")
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertIn("Clone failed", data["error"])
        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r"], run=run)
        self.assertEqual(rc, 1)
        self.assertIn("Clone failed", out)

    def test_pr_worktree_add_failure(self):
        self._patch_paths()

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "worktree list" in joined:
                return _Proc(returncode=1, stderr="bad mirror")
            if "worktree add" in joined:
                return _Proc(returncode=1, stderr="locked")
            if "rev-parse" in joined:
                return _Proc(stdout="abc\n")
            return _Proc(stdout="ok")

        # A failing `worktree list` counts as unregistered → tries `add`.
        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertIn("Worktree failed", data["error"])

    def test_pr_worktree_text_success(self):
        mirrors, trees = self._patch_paths()

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "worktree list" in joined:
                return _Proc(stdout="worktree /x\n")
            if "rev-parse" in joined:
                return _Proc(stdout="abc123\n")
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["pr", "worktree", "7", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("checked out", out)
        self.assertIn("o__r-pr7", out)

    def test_run_git_never_raises(self):
        for exc in (FileNotFoundError, subprocess.TimeoutExpired("git", 1),
                    OSError("bad")):
            with patch("subprocess.run", side_effect=exc):
                result = ghcli._run_git(["status"])
            self.assertNotEqual(result.status, "ok", exc)
        with patch("subprocess.run",
                   return_value=_Proc(returncode=1, stderr="nope")):
            result = ghcli._run_git(["status"])
        self.assertEqual(result.status, "upstream-error")
        self.assertEqual(result.error, "nope")

    # -- swe gh run rerun ---------------------------------------------

    def test_run_rerun_json(self):
        seen = []

        def run(cmd, **kw):
            seen.append(" ".join(str(a) for a in cmd))
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertEqual(data["run"], 42)
        self.assertTrue(data["rerun"])
        self.assertFalse(data["failed_only"])
        self.assertIn("run rerun 42", seen[0])

    def test_run_rerun_failed_flag(self):
        seen = []

        def run(cmd, **kw):
            seen.append(" ".join(str(a) for a in cmd))
            return _Proc(stdout="ok")

        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r", "--failed", "--json"],
            run=run)
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["data"]["failed_only"])
        self.assertIn("--failed", seen[0])

    def test_pr_merge_env_blocked_emits_envelope(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r", "--json"], env=env)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["github"]["status"], "auth-required")
        self.assertFalse(data["merged"])
        self.assertIn("error", data)

    def test_run_rerun_bad_id(self):
        rc, out, _ = self._run_cli(["run", "rerun", "abc", "--repo=o/r"])
        self.assertEqual(rc, 1)
        self.assertIn("numeric", out)

    def test_run_rerun_failure(self):
        run = _gh_run({
            "run rerun": _Proc(returncode=1, stderr="not found"),
        })
        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r", "--json"], run=run)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertFalse(data["rerun"])
        self.assertIn("error", data)

    # -- swe gh inbox read ---------------------------------------------

    def test_inbox_read_ids_json(self):
        seen = []

        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            seen.append(joined)
            if "threads/2" in joined:
                return _Proc(returncode=1, stderr="gone")
            return _Proc(stdout="{}")

        rc, out, _ = self._run_cli(
            ["inbox", "read", "1", "2", "--json"], run=run)
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertFalse(data["all"])
        self.assertEqual(data["marked"], ["1"])
        self.assertEqual(data["failed"][0]["id"], "2")
        self.assertIn("notifications/threads/1", seen[0])

    def test_inbox_read_ids_and_all_rejected(self):
        rc, out, _ = self._run_cli(["inbox", "read", "1", "--all"])
        self.assertEqual(rc, 1)
        self.assertIn("not both", out)

    def test_inbox_read_bad_id(self):
        rc, out, _ = self._run_cli(["inbox", "read", "abc"])
        self.assertEqual(rc, 1)
        self.assertIn("Not a notification id", out)

    def test_inbox_read_needs_args(self):
        rc, out, _ = self._run_cli(["inbox", "read"])
        self.assertEqual(rc, 1)
        self.assertIn("Usage", out)

    def test_run_rerun_env_blocked_emits_envelope(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r", "--json"], env=env)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["github"]["status"], "auth-required")
        self.assertFalse(data["rerun"])
        self.assertIn("error", data)

    def test_inbox_read_env_blocked_emits_envelope(self):
        env = gh.GhEnv(installed=True, authenticated=False)
        rc, out, _ = self._run_cli(
            ["inbox", "read", "1", "--json"], env=env)
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertEqual(data["github"]["status"], "auth-required")
        self.assertEqual(data["marked"], [])
        self.assertIn("error", data)

    def test_pr_unknown_subcommand(self):
        rc, out, _ = self._run_cli(["pr", "bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("Unknown: swe gh pr bogus", out)

    def test_pr_extra_positional(self):
        rc, out, _ = self._run_cli(["pr", "status", "extra"])
        self.assertEqual(rc, 1)
        self.assertIn("Unexpected args", out)

    def test_pr_view_and_merge_usage(self):
        for argv in (["pr", "view"], ["pr", "view", "1", "2"],
                     ["pr", "merge"]):
            rc, out, _ = self._run_cli(argv)
            self.assertEqual(rc, 1, argv)
            self.assertIn("Usage", out)

    def test_view_multi_pin_resolves_error(self):
        repos.add_pinned("o/r", self.store)
        repos.add_pinned("a/b", self.store)
        rc, out, _ = self._run_cli(["pr", "view", "7", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertIsNone(data["pr"])
        self.assertIn("one repo", data["error"])
        self.assertEqual(data["github"]["status"], "ok")

    def test_view_no_repo_resolves_error(self):
        rc, out, _ = self._run_cli(["pr", "view", "7", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertIsNone(data["repo"])
        self.assertIn("No repo to query", data["error"])

    def test_merge_failure_surfaces_in_json_and_text(self):
        run = _gh_run({"pr merge": _Proc(returncode=1,
                                         stderr="merge conflict")})
        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r", "--json"], run=run)
        data = json.loads(out)["data"]
        self.assertEqual(rc, 1)
        self.assertFalse(data["merged"])
        self.assertIn("conflict", data["error"])
        self.assertEqual(data["github"]["status"], "ok")
        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r"], run=run)
        self.assertEqual(rc, 1)
        self.assertIn("Merge failed", out)

    def test_merge_auto_and_success_text(self):
        run = _gh_run({"pr merge": _Proc(stdout="")})
        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r", "--auto", "--json"],
            run=run)
        data = json.loads(out)["data"]
        self.assertEqual(rc, 0)
        self.assertTrue(data["auto"])
        self.assertFalse(data["merged"])
        rc, out, _ = self._run_cli(
            ["pr", "merge", "7", "--repo=o/r"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("Merged 7 in o/r", out)

    def test_run_dispatch_errors(self):
        for argv in (["run", "list", "extra"], ["run", "bogus"],
                     ["run", "rerun"]):
            rc, out, _ = self._run_cli(argv)
            self.assertEqual(rc, 1, argv)
            self.assertRegex(out, "Usage|Unexpected args")

    def test_rerun_multi_pin_resolves_error(self):
        repos.add_pinned("o/r", self.store)
        repos.add_pinned("a/b", self.store)
        rc, out, _ = self._run_cli(["run", "rerun", "42", "--json"])
        self.assertEqual(rc, 1)
        data = json.loads(out)["data"]
        self.assertFalse(data["rerun"])
        self.assertIn("one repo", data["error"])

    def test_rerun_failure_and_success(self):
        fail = _gh_run({"run rerun": _Proc(returncode=1,
                                           stderr="not found")})
        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r", "--json"], run=fail)
        data = json.loads(out)["data"]
        self.assertEqual(rc, 1)
        self.assertFalse(data["rerun"])
        self.assertIn("not found", data["error"])
        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r"], run=fail)
        self.assertIn("Re-run failed", out)
        ok = _gh_run({"run rerun": _Proc(stdout="")})
        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r", "--json"], run=ok)
        data = json.loads(out)["data"]
        self.assertEqual(rc, 0)
        self.assertTrue(data["rerun"])
        self.assertEqual(data["run"], 42)
        rc, out, _ = self._run_cli(
            ["run", "rerun", "42", "--repo=o/r", "--failed"], run=ok)
        self.assertEqual(rc, 0)
        self.assertIn("Re-run", out)

    def test_inbox_read_all(self):
        # One test: a second def of this name in the class used to
        # replace the first, so the PUT's shape was never checked.
        seen = []
        inner = _gh_run({"PUT notifications": _Proc(stdout="")})

        def run(cmd, **kw):
            seen.append(" ".join(str(a) for a in cmd))
            return inner(cmd, **kw)

        rc, out, _ = self._run_cli(
            ["inbox", "read", "--all", "--json"], run=run)
        data = json.loads(out)["data"]
        self.assertEqual(rc, 0)
        self.assertTrue(data["all"])
        self.assertEqual(data["marked"], ["*"])
        put = [c for c in seen if "PUT" in c]
        self.assertTrue(put)
        self.assertIn("last_read_at=", put[0])
        rc, out, _ = self._run_cli(["inbox", "read", "--all"], run=run)
        self.assertEqual(rc, 0)
        self.assertIn("All notifications marked read", out)

    def test_inbox_read_all_failure(self):
        run = _gh_run({"PUT notifications": _Proc(returncode=1,
                                                stderr="boom")})
        rc, out, _ = self._run_cli(
            ["inbox", "read", "--all", "--json"], run=run)
        data = json.loads(out)["data"]
        self.assertEqual(rc, 1)
        self.assertEqual(data["marked"], [])
        self.assertIn("boom", data["error"])
        rc, out, _ = self._run_cli(["inbox", "read", "--all"], run=run)
        self.assertIn("Marking read failed", out)

    def test_inbox_read_mixed_results(self):
        def run(cmd, **kw):
            joined = " ".join(str(a) for a in cmd)
            if "threads/1" in joined:
                return _Proc(stdout="")
            return _Proc(returncode=1, stderr="gone")
        rc, out, _ = self._run_cli(
            ["inbox", "read", "1", "2", "--json"], run=run)
        data = json.loads(out)["data"]
        self.assertEqual(data["marked"], ["1"])
        self.assertEqual(data["failed"], [{"id": "2", "error": "gone"}])
        rc, out, _ = self._run_cli(["inbox", "read", "1", "2"], run=run)
        self.assertEqual(rc, 1)
        self.assertIn("1 marked read", out)
        self.assertIn("gone", out)

    def test_inbox_unexpected_args(self):
        rc, out, _ = self._run_cli(["inbox", "bogus"])
        self.assertEqual(rc, 1)
        self.assertIn("Unexpected args", out)

    def test_check_row_workflow_dict_name(self):
        row = ghcli._check_row({"status": "COMPLETED",
                                "conclusion": "SUCCESS",
                                "name": "test",
                                "workflow": {"name": "CI"}})
        self.assertEqual(row["workflow"], "CI")
        self.assertEqual(row["state"], "passing")

    def test_pr_web_flag(self):
        opened = []
        with patch("swe.github.cli._open_web",
                   lambda what, extra: opened.append(what)):
            rc, _, _ = self._run_cli(["inbox", "--web"])
        self.assertEqual(rc, 0)
        self.assertEqual(opened, ["inbox"])


class TestSharedLayer(unittest.TestCase):
    """The ``gh`` cache, write lock and pins live in ``swe.github``."""

    def test_remote_and_github_do_not_import_web_pages(self):
        # swe.remote's issue, pull, task and Linear routes reached
        # into the web app's private page module for the shared cache.
        import ast
        src = Path(__file__).resolve().parent.parent / "src" / "swe"
        offenders = []
        for folder in ("remote", "github"):
            for path in sorted((src / folder).glob("*.py")):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom) and (
                            node.module or "").startswith("swe.webapp.pages"):
                        names = {a.name for a in node.names}
                        if node.module.endswith("_github") or "_github" in names:
                            offenders.append(path.name)
        self.assertEqual(offenders, [])

    def test_pinned_name_errors(self):
        from swe.github import shared
        from swe.webapp.pages import PageError, _github as W
        home = temp_dir(self)
        with patch.object(shared, "pinned",
                          return_value=[{"name": "Acme/Tool"}]):
            self.assertEqual(shared.pinned_name(home, "acme/tool"), "Acme/Tool")
            with self.assertRaises(shared.PinError) as bad:
                shared.pinned_name(home, "not a repo")
            self.assertEqual(bad.exception.status, 400)
            with self.assertRaises(PageError) as missing:
                W.pinned_name(home, "acme/other")
            self.assertEqual((missing.exception.status,
                              missing.exception.code), (404, "not_pinned"))


if __name__ == "__main__":
    unittest.main()
