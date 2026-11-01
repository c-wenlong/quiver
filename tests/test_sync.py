"""`swe sync`: commit, merge in and push the ~/.swe hub repo (#525).

Every test works on throwaway repositories: a bare "remote" and clones of
it as ``<home>/.swe`` under temp homes, so nothing reaches a real remote.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from swe.sync import repo
from swe.sync.commands import cmd_sync
from tests._tempdir import temp_dir

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV}, check=True)
    return done.stdout.strip()


@unittest.skipIf(shutil.which("git") is None, "git is not installed")
class SyncTestBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(temp_dir(self))
        patcher = mock.patch.dict(os.environ, GIT_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.relinks = []
        self.remote = self.root / "remote.git"
        seed = self.root / "seed"
        seed.mkdir()
        git(seed, "init", "-q", "-b", "main")
        (seed / "AGENTS.md").write_text("# shared\n")
        (seed / "skills" / "one").mkdir(parents=True)
        (seed / "skills" / "one" / "SKILL.md").write_text("one\n")
        (seed / ".gitignore").write_text("cache/\nsecrets/\ndata/remote/\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(self.root, "clone", "-q", "--bare", str(seed), str(self.remote))

    def machine(self, name: str, branch: str | None = None) -> Path:
        home = self.root / name
        home.mkdir()
        git(home, "clone", "-q", str(self.remote), ".swe")
        if branch:
            git(home / ".swe", "checkout", "-q", "-b", branch)
        return home

    def relink(self, home, changed, base=None):
        self.relinks.append((home, sorted(changed)))
        return {"changed": len(changed), "linked": 0, "marketplaces": [],
                "mcp": None, "notes": []}

    def sync(self, home, **kw):
        return repo.sync(home, relink=self.relink, **kw)

    def remote_file(self, branch: str, path: str) -> str:
        return git(self.remote, "show", f"{branch}:{path}")

    def snapshot(self, home: Path) -> tuple:
        hub = home / ".swe"
        return (git(hub, "rev-parse", "HEAD"), git(hub, "write-tree"),
                git(hub, "status", "--porcelain=v1", "--untracked-files=all"),
                (hub / "AGENTS.md").read_text())


class SingleBranchTest(SyncTestBase):
    def test_a_skill_edited_on_one_machine_reaches_the_other(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "skills" / "two").mkdir()
        (a / ".swe" / "skills" / "two" / "SKILL.md").write_text("two\n")
        doc = self.sync(a)
        self.assertEqual(doc["result"], "synced")
        self.assertEqual(doc["mode"], "single")
        self.assertTrue(doc["pushed"])
        self.assertIn("skills 1", doc["commit"]["message"])
        self.assertEqual(self.remote_file("main", "skills/two/SKILL.md"), "two")

        doc = self.sync(b)
        self.assertEqual(doc["result"], "synced")
        self.assertEqual(doc["merges"], [{"ref": "origin/main", "commits": 1}])
        self.assertTrue((b / ".swe" / "skills" / "two" / "SKILL.md").exists())
        self.assertEqual(self.relinks, [(b, ["skills/two/SKILL.md"])])

    def test_both_sides_changed_different_files_merge_and_push(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "a.txt").write_text("a\n")
        self.sync(a)
        (b / ".swe" / "b.txt").write_text("b\n")
        doc = self.sync(b)
        self.assertEqual(doc["result"], "synced")
        self.assertEqual(self.remote_file("main", "a.txt"), "a")
        self.assertEqual(self.remote_file("main", "b.txt"), "b")
        # A merge, never a rewrite: A's commit is still in main's history.
        a_head = git(a / ".swe", "rev-parse", "HEAD")
        git(self.remote, "merge-base", "--is-ancestor", a_head, "main")

    def test_nothing_to_do_is_up_to_date(self):
        a = self.machine("a")
        before = self.snapshot(a)
        self.assertEqual(self.sync(a)["result"], "up-to-date")
        self.assertEqual(self.snapshot(a), before)

    def test_a_new_branch_is_pushed_with_upstream(self):
        a = self.machine("a", branch="laptop")
        (a / ".swe" / "AGENTS.md").write_text("# laptop\n")
        doc = self.sync(a)
        self.assertEqual(doc["result"], "synced")
        self.assertEqual(self.remote_file("laptop", "AGENTS.md"), "# laptop")
        self.assertEqual(self.remote_file("main", "AGENTS.md"), "# shared")
        self.assertEqual(git(a / ".swe", "rev-parse", "--abbrev-ref", "@{u}"), "origin/laptop")


class DeviceBranchTest(SyncTestBase):
    def test_merges_main_and_pushes_only_the_device_branch(self):
        a = self.machine("a", branch="device/laptop")
        main_writer = self.machine("w")
        (main_writer / ".swe" / "skills" / "shared").mkdir()
        (main_writer / ".swe" / "skills" / "shared" / "SKILL.md").write_text("s\n")
        git(main_writer / ".swe", "add", "-A")
        git(main_writer / ".swe", "commit", "-q", "-m", "for every device")
        git(main_writer / ".swe", "push", "-q", "origin", "main")
        main_before = git(self.remote, "rev-parse", "main")

        (a / ".swe" / "local.txt").write_text("mine\n")
        doc = self.sync(a)
        self.assertEqual(doc["mode"], "device")
        self.assertEqual(doc["machine"], "laptop")
        self.assertEqual(doc["base"], "origin/main")
        self.assertEqual(doc["result"], "synced")
        self.assertEqual([m["ref"] for m in doc["merges"]], ["origin/main"])
        self.assertTrue((a / ".swe" / "skills" / "shared" / "SKILL.md").exists())
        self.assertEqual(self.remote_file("device/laptop", "local.txt"), "mine")
        self.assertEqual(self.remote_file("device/laptop", "skills/shared/SKILL.md"), "s")
        self.assertEqual(git(self.remote, "rev-parse", "main"), main_before)
        self.assertEqual(self.relinks[-1][1], ["skills/shared/SKILL.md"])

    def test_device_branch_pushed_from_another_clone_is_merged_first(self):
        a = self.machine("a", branch="device/laptop")
        (a / ".swe" / "one.txt").write_text("1\n")
        self.sync(a)
        other = self.root / "other"
        other.mkdir()
        git(other, "clone", "-q", "-b", "device/laptop", str(self.remote), ".swe")
        (other / ".swe" / "two.txt").write_text("2\n")
        self.sync(other)
        (a / ".swe" / "three.txt").write_text("3\n")
        doc = self.sync(a)
        self.assertEqual([m["ref"] for m in doc["merges"]], ["origin/device/laptop"])
        self.assertTrue((a / ".swe" / "two.txt").exists())
        self.assertEqual(self.remote_file("device/laptop", "three.txt"), "3")


class DefaultBranchTest(SyncTestBase):
    def test_a_renamed_default_branch_is_still_merged(self):
        # The clone has no origin/HEAD (as after git init + remote add),
        # and the remote's default was renamed to trunk since.
        a = self.machine("a", branch="device/desk")
        git(a / ".swe", "remote", "set-head", "origin", "-d")
        git(self.remote, "branch", "-m", "main", "trunk")
        git(self.remote, "symbolic-ref", "HEAD", "refs/heads/trunk")
        writer = self.root / "w"
        writer.mkdir()
        git(writer, "clone", "-q", str(self.remote), ".swe")
        (writer / ".swe" / "shared.txt").write_text("s\n")
        self.sync(writer)
        doc = self.sync(a)
        self.assertEqual(doc["result"], "synced")
        self.assertEqual(doc["base"], "origin/trunk")
        self.assertEqual([m["ref"] for m in doc["merges"]], ["origin/trunk"])
        self.assertTrue((a / ".swe" / "shared.txt").exists())

    def test_unrelated_histories_are_refused_in_plain_words(self):
        home = self.root / "inited"
        hub = home / ".swe"
        hub.mkdir(parents=True)
        git(hub, "init", "-q", "-b", "main")
        git(hub, "remote", "add", "origin", str(self.remote))
        (hub / "local.txt").write_text("x\n")
        with self.assertRaisesRegex(repo.SyncError, "share no history"):
            self.sync(home)
        self.assertEqual(git(hub, "status", "--porcelain"), "?? local.txt")


class PushTest(SyncTestBase):
    def test_a_push_that_times_out_is_push_failed_and_still_relinks(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "x.txt").write_text("x\n")
        self.sync(a)
        real = repo._git

        def hanging(root, *args, **kw):
            if args[:1] == ("push",):
                raise repo.SyncError("git push took longer than 180s")
            return real(root, *args, **kw)

        with mock.patch.object(repo, "_git", hanging):
            doc = self.sync(b)
        self.assertEqual(doc["result"], "push-failed")
        self.assertIn("longer than", doc["push_error"])
        self.assertEqual(self.relinks, [(b, ["x.txt"])])


class ConflictTest(SyncTestBase):
    def test_conflict_lists_files_and_leaves_the_repo_as_it_was(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "AGENTS.md").write_text("# from a\n")
        self.sync(a)
        (b / ".swe" / "AGENTS.md").write_text("# from b\n")
        (b / ".swe" / "new.txt").write_text("untracked\n")
        git(b / ".swe", "add", "new.txt")  # a staged file stays staged
        before = self.snapshot(b)
        remote_before = git(self.remote, "rev-parse", "main")
        doc = self.sync(b)
        self.assertEqual(doc["result"], "conflict")
        self.assertEqual(doc["conflicts"], ["AGENTS.md"])
        self.assertFalse(doc["pushed"])
        self.assertEqual(self.snapshot(b), before)
        self.assertEqual(git(self.remote, "rev-parse", "main"), remote_before)
        self.assertFalse((b / ".swe" / ".git" / "MERGE_HEAD").exists())
        self.assertEqual(self.relinks, [])

    def test_a_merge_that_fails_late_is_rolled_back(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "x.txt").write_text("x\n")
        self.sync(a)
        (b / ".swe" / "y.txt").write_text("y\n")
        before = self.snapshot(b)
        real = repo._git

        def failing(root, *args, **kw):
            if args and args[0] == "merge" and "--abort" not in args:
                raise repo.SyncError("git merge failed: boom")
            return real(root, *args, **kw)

        with mock.patch.object(repo, "_git", failing):
            with self.assertRaisesRegex(repo.SyncError, "boom"):
                self.sync(b)
        self.assertEqual(self.snapshot(b), before)

    def test_second_merge_failing_takes_the_first_ones_files_back(self):
        a = self.machine("a", branch="device/laptop")
        (a / ".swe" / "one.txt").write_text("1\n")
        self.sync(a)
        other = self.root / "other"
        other.mkdir()
        git(other, "clone", "-q", "-b", "device/laptop", str(self.remote), ".swe")
        (other / ".swe" / "from-other.txt").write_text("2\n")
        self.sync(other)
        writer = self.machine("w")
        (writer / ".swe" / "from-main.txt").write_text("m\n")
        self.sync(writer)
        (a / ".swe" / "mine.txt").write_text("mine\n")
        before = self.snapshot(a)
        real = repo._git

        def failing(root, *args, **kw):
            if args[:1] == ("merge",) and args[-1] == "origin/main":
                raise repo.SyncError("git merge failed: boom")
            return real(root, *args, **kw)

        with mock.patch.object(repo, "_git", failing):
            with self.assertRaisesRegex(repo.SyncError, "boom"):
                self.sync(a)
        self.assertEqual(self.snapshot(a), before)
        self.assertFalse((a / ".swe" / "from-other.txt").exists())
        self.assertEqual((a / ".swe" / "mine.txt").read_text(), "mine\n")

    def test_a_second_sync_is_refused_while_one_runs(self):
        import fcntl
        a = self.machine("a")
        with open(a / ".swe" / ".git" / "swe-sync.lock", "a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            with self.assertRaisesRegex(repo.SyncError, "another swe sync is running"):
                self.sync(a)
        self.assertEqual(self.sync(a)["result"], "up-to-date")

    def test_conflict_document_keeps_its_shape(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "AGENTS.md").write_text("a\n")
        self.sync(a)
        (b / ".swe" / "AGENTS.md").write_text("b\n")
        doc = self.sync(b)
        self.assertIs(doc["would_push"], False)

    def test_refuses_mid_merge(self):
        a = self.machine("a")
        (a / ".swe" / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
        with self.assertRaisesRegex(repo.SyncError, "middle of a merge"):
            self.sync(a)


class NeverSyncedTest(SyncTestBase):
    def test_old_gitignore_still_keeps_secrets_cache_and_data_out(self):
        a = self.machine("a")
        hub = a / ".swe"
        (hub / ".gitignore").write_text("# an old hub ignored nothing\n")
        for rel in ("secrets/providers/.api_keys", "secrets/mcp/.api_keys",
                    "cache/x.json", "data/remote/devices.json",
                    "data/web-ledger/ledger.sqlite", "mcp/servers/s/README"):
            (hub / rel).parent.mkdir(parents=True, exist_ok=True)
            (hub / rel).write_text("SECRET=1\n")
        (hub / "skills" / "one" / "SKILL.md").write_text("edited\n")
        doc = self.sync(a)
        self.assertEqual(doc["result"], "synced")
        tracked = git(self.remote, "ls-tree", "-r", "--name-only", "main").splitlines()
        self.assertIn("skills/one/SKILL.md", tracked)
        for path in tracked:
            self.assertFalse(path.startswith(("secrets/", "cache/", "data/", "mcp/servers/")),
                             path)
        self.assertIn("secrets/providers/.api_keys", doc["held_back"])
        committed = [f["path"] for f in doc["commit"]["files"]]
        self.assertEqual(sorted(committed), [".gitignore", "skills/one/SKILL.md"])

    def test_a_file_name_that_looks_like_pathspec_magic_syncs(self):
        a = self.machine("a")
        (a / ".swe" / ":notes.md").write_text("n\n")
        (a / ".swe" / "*.md").write_text("star\n")
        doc = self.sync(a)
        self.assertEqual(doc["result"], "synced")
        self.assertEqual(sorted(f["path"] for f in doc["commit"]["files"]),
                         ["*.md", ":notes.md"])

    def test_ignored_secrets_folder_does_not_break_staging(self):
        # git fails a whole `add` whose pathspec names an ignored folder;
        # sync stages path by path so this hub still syncs.
        a = self.machine("a")
        hub = a / ".swe"
        for rel in ("secrets/providers/.api_keys", "cache/x.json"):
            (hub / rel).parent.mkdir(parents=True, exist_ok=True)
            (hub / rel).write_text("SECRET=1\n")
        (hub / "AGENTS.md").write_text("# edited\n")
        self.assertEqual(repo.status(a)["problem"], None)
        doc = self.sync(a)
        self.assertEqual(doc["result"], "synced")
        self.assertEqual(doc["held_back"], [])
        self.assertEqual([f["path"] for f in doc["commit"]["files"]], ["AGENTS.md"])

    def test_never_synced_list_tracks_the_gitignore_body(self):
        names = repo.never_synced()
        for name in ("secrets", "cache", "data/ledger", "data/remote", "mcp/servers"):
            self.assertIn(name, names)


class DryRunTest(SyncTestBase):
    def test_dry_run_reports_and_changes_nothing(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "x.txt").write_text("x\n")
        self.sync(a)
        (b / ".swe" / "y.txt").write_text("y\n")
        before = self.snapshot(b)
        remote_before = git(self.remote, "rev-parse", "main")
        doc = self.sync(b, dry_run=True)
        self.assertEqual(doc["result"], "would-sync")
        self.assertTrue(doc["would_push"])
        self.assertEqual([f["path"] for f in doc["commit"]["files"]], ["y.txt"])
        self.assertEqual(doc["merges"], [{"ref": "origin/main", "commits": 1}])
        self.assertEqual(self.snapshot(b), before)
        self.assertEqual(git(self.remote, "rev-parse", "main"), remote_before)
        self.assertEqual(self.relinks, [])

    def test_dry_run_reports_a_conflict_too(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "AGENTS.md").write_text("a\n")
        self.sync(a)
        (b / ".swe" / "AGENTS.md").write_text("b\n")
        doc = self.sync(b, dry_run=True)
        self.assertEqual(doc["result"], "conflict")
        self.assertEqual(doc["conflicts"], ["AGENTS.md"])


class StatusTest(SyncTestBase):
    def test_status_counts_and_groups(self):
        a, b = self.machine("a"), self.machine("b", branch="device/desk")
        git(b / ".swe", "push", "-q", "-u", "origin", "device/desk")
        (a / ".swe" / "x.txt").write_text("x\n")
        self.sync(a)
        (b / ".swe" / "skills" / "one" / "SKILL.md").write_text("changed\n")
        (b / ".swe" / "mcp.json").write_text("{}\n")
        doc = repo.status(b, do_fetch=True)
        self.assertTrue(doc["ready"])
        self.assertEqual(doc["mode"], "device")
        self.assertEqual(doc["branch"], "device/desk")
        self.assertEqual(doc["upstream"], "origin/device/desk")
        self.assertEqual(doc["base"], "origin/main")
        self.assertEqual((doc["ahead"], doc["behind"], doc["base_behind"]), (0, 0, 1))
        self.assertEqual(doc["groups"], {"skills": 1, "mcp.json": 1})
        self.assertTrue(doc["fetched"])
        # Status reads through a scratch index: nothing got staged.
        self.assertEqual(git(b / ".swe", "diff", "--cached", "--name-only"), "")

    def test_status_without_a_repo_says_why(self):
        home = self.root / "bare-home"
        (home / ".swe").mkdir(parents=True)
        doc = repo.status(home)
        self.assertFalse(doc["ready"])
        self.assertIn("not a git repository", doc["problem"])

    def test_group_of(self):
        self.assertEqual(repo.group_of("skills/a/SKILL.md"), "skills")
        self.assertEqual(repo.group_of("AGENTS_MD/index.txt"), "AGENTS.md")
        self.assertEqual(repo.group_of("mcp.json"), "mcp.json")
        self.assertEqual(repo.group_of("config/x.json"), "config")


class RelinkTest(SyncTestBase):
    def test_default_relink_links_registers_and_syncs_mcp(self):
        a, b = self.machine("a"), self.machine("b")
        spec = lambda cmd: {"command": cmd}  # noqa: E731
        (a / ".swe" / "mcp.json").write_text(json.dumps({"mcpServers": {
            "keep": spec("k"), "edit": spec("v1"), "gone": spec("g")}}))
        self.sync(a)
        self.sync(b)
        (a / ".swe" / "mcp.json").write_text(json.dumps({"mcpServers": {
            "keep": spec("k"), "edit": spec("v2"), "new": spec("n")}}))
        self.sync(a)
        calls = []

        def fake_mcp(args, report):
            calls.append(args)
            report["selected"] = ["edit", "new"]
            return 0

        with mock.patch("swe.init.hub.register_marketplaces",
                        return_value=[("claude", "mine", None)]), \
                mock.patch("swe.mcp.cli._sync", fake_mcp):
            doc = repo.sync(b)
        relinked = doc["relinked"]
        self.assertEqual(relinked["changed"], 1)
        self.assertEqual(relinked["marketplaces"],
                         [{"harness": "claude", "marketplace": "mine", "error": None}])
        # Only what the merge changed, forced so an edited server updates.
        self.assertEqual(calls, [["swe", "--all", "--no-interactive", "--force",
                                  "--only=edit,new"]])
        self.assertEqual(relinked["mcp"]["selected"], ["edit", "new"])
        self.assertEqual(len(relinked["notes"]), 1)
        self.assertIn("gone", relinked["notes"][0])

    def test_a_failed_mcp_copy_is_a_note_with_its_reason(self):
        def failing(args, report):
            print("\x1b[31mMissing secret GITHUB_TOKEN; set it with swe mcp secret set\x1b[0m")
            return 1

        with mock.patch("swe.init.commands.run_init", side_effect=RuntimeError("skip")), \
                mock.patch("swe.init.hub.register_marketplaces", return_value=[]), \
                mock.patch("swe.mcp.cli._sync", failing), \
                mock.patch.object(repo, "mcp_server_changes", return_value=(["github"], [])):
            out = repo.default_relink(self.root, ["mcp.json"])
        self.assertEqual(out["mcp"]["code"], 1)
        note = out["notes"][-1]
        self.assertIn("MCP servers not copied", note)
        self.assertIn("Missing secret GITHUB_TOKEN", note)
        self.assertNotIn("\x1b", note)

    def test_relink_problems_are_notes(self):
        with mock.patch("swe.init.commands.run_init", side_effect=RuntimeError("bad")), \
                mock.patch("swe.init.hub.register_marketplaces", side_effect=OSError("x")):
            out = repo.default_relink(self.root, ["skills/a/SKILL.md"])
        self.assertEqual(len(out["notes"]), 2)
        self.assertIsNone(out["mcp"])


class CommandTest(SyncTestBase):
    def run_cmd(self, args, home):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(repo, "default_relink", self.relink), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmd_sync(args, home=home)
        return code, out.getvalue(), err.getvalue()

    def test_json_envelopes(self):
        a = self.machine("a")
        code, out, _ = self.run_cmd(["status", "--json"], a)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["data"]["mode"], "single")
        (a / ".swe" / "x.txt").write_text("x\n")
        code, out, _ = self.run_cmd(["--dry-run", "--json"], a)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["data"]["result"], "would-sync")

    def test_conflict_exits_one_with_the_files(self):
        a, b = self.machine("a"), self.machine("b")
        (a / ".swe" / "AGENTS.md").write_text("a\n")
        self.run_cmd([], a)
        (b / ".swe" / "AGENTS.md").write_text("b\n")
        code, out, _ = self.run_cmd([], b)
        self.assertEqual(code, 1)
        self.assertIn("AGENTS.md", out)
        self.assertIn("as it was", out)
        self.assertIn("would merge 1 commit(s)", out)
        self.assertNotIn("✓", out)
        code, out, _ = self.run_cmd(["--json"], b)
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["data"]["conflicts"], ["AGENTS.md"])

    def test_text_views_and_errors(self):
        a = self.machine("a")
        (a / ".swe" / "x.txt").write_text("x\n")
        code, out, _ = self.run_cmd(["status"], a)
        self.assertEqual(code, 0)
        self.assertIn("single branch main", out)
        code, out, _ = self.run_cmd([], a)
        self.assertEqual(code, 0)
        self.assertIn("pushed main", out)
        code, out, _ = self.run_cmd(["--bogus"], a)
        self.assertEqual(code, 1)
        home = self.root / "empty"
        home.mkdir()
        code, out, err = self.run_cmd(["--json"], home)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("swe init", err)


if __name__ == "__main__":
    unittest.main()
