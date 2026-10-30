"""GitHub notifications in the remote inbox: the shared row shaping,
the catalog merge and unread-transition diff, dismiss and mark-read
wiring, and the ntfy reason filter."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from quiver.github import cli as ghcli
from quiver.github import gh
from quiver.remote.catalog import Catalog
from quiver.remote import notify
from quiver.remote.hub import GLOBAL_KEY, HubState
from quiver.github import shared as G
from quiver.webapp.pages import inbox as gh_inbox

from tests.test_remote_hub import HubBase, _session
from tests.test_webapp_repositories import FakeGh


def _note(nid="9", reason="review_requested", typ="PullRequest",
          title="Review this", repo="o/a",
          updated="2026-10-23T18:29:12Z", unread=True):
    """One notification as ``_fetch_inbox`` shapes it."""
    return {"id": nid, "repo": repo, "title": title, "type": typ,
            "reason": reason, "unread": unread, "updated_at": updated,
            "url": f"https://github.com/{repo}/pull/9"}


def _gh_row(**kw):
    """One ``github`` inbox row as ``unread_rows`` returns it."""
    row = ghcli.inbox_row(_note(**kw))
    assert row is not None
    return row


def _raw(nid, unread=True, reason="review_requested",
         typ="PullRequest"):
    """One notification as GitHub's API returns it."""
    return {"id": nid, "unread": unread, "reason": reason,
            "updated_at": "2026-10-23T18:29:12Z",
            "subject": {"title": "T", "type": typ, "url": None},
            "repository": {"full_name": "o/a",
                           "html_url": "https://github.com/o/a"}}


class TestInboxRow(unittest.TestCase):
    """ghcli.inbox_row: the /v1/inbox ``github`` item shape."""

    def test_maps_subjects_and_reasons(self):
        cases = [
            ("PullRequest", "review_requested", "pr", "review_requested"),
            ("PullRequest", "mention", "pr", "mention"),
            ("PullRequest", "team_mention", "pr", "mention"),
            ("Issue", "assign", "issue", "assignee"),
            ("CheckSuite", "ci_activity", "ci", "ci_fail"),
            ("CheckRun", "ci_activity", "ci", "ci_fail"),
            ("Release", "subscribed", "release", "comment"),
            ("Issue", "comment", "issue", "comment"),
            ("Issue", "author", "issue", "comment"),
            ("Discussion", "subscribed", "other", "comment"),
        ]
        for typ, reason, subject_type, want in cases:
            with self.subTest(typ=typ, reason=reason):
                row = ghcli.inbox_row(_note(reason=reason, typ=typ))
                self.assertEqual(
                    (row["subject_type"], row["reason"]),
                    (subject_type, want))

    def test_row_shape(self):
        row = ghcli.inbox_row(_note(nid="77"))
        self.assertEqual(row["kind"], "github")
        self.assertEqual(row["key"], "gh:77")
        self.assertEqual(row["gh_thread_id"], "77")
        self.assertEqual(row["harness"], "github")
        self.assertEqual(row["agent"], "GitHub")
        self.assertEqual(row["status"], "github")
        self.assertEqual(row["repo"], "o/a")
        self.assertEqual(row["title"], "Review this")
        self.assertEqual(row["url"], "https://github.com/o/a/pull/9")
        self.assertEqual(row["updated_at"], "2026-10-23T18:29:12Z")
        self.assertTrue(row["unread"])
        # ``last_active_at`` is the epoch the dismissal store compares.
        self.assertAlmostEqual(
            row["last_active_at"], ghcli._parse_iso(row["updated_at"]))

    def test_unknown_reason_passes_through(self):
        row = ghcli.inbox_row(_note(reason="security_alert"))
        self.assertEqual(row["reason"], "security_alert")

    def test_no_id_yields_no_row(self):
        self.assertIsNone(ghcli.inbox_row(_note(nid="")))
        self.assertIsNone(ghcli.inbox_row(_note(nid=None)))

    def test_push_reasons(self):
        self.assertEqual(
            set(ghcli.PUSH_REASONS),
            {"review_requested", "assignee", "mention", "ci_fail"})


class _GithubCatalog(unittest.TestCase):
    """A Catalog whose GitHub feed returns scripted rows."""

    def _cat(self, github=(), sessions=(), statuses=()):
        wanted = list(statuses) + ["active"] * len(sessions)
        cat = Catalog(
            Path(tempfile.mkdtemp()), machine_id="mbp",
            fetch_sessions=lambda: list(sessions),
            status_of=lambda ss: wanted[:len(ss)],
            fetch_github=lambda: [dict(r) for r in github])
        return cat


class TestMerge(_GithubCatalog):
    """catalog.inbox(): github rows after the session group."""

    def test_github_rows_join_after_sessions_newest_first(self):
        now = 1_800_000_000.0
        cat = self._cat(
            github=[
                _gh_row(nid="9", updated="2026-10-23T18:29:12Z"),
                _gh_row(nid="8", updated="2026-10-24T10:00:00Z"),
            ],
            sessions=[_session(sid="f1", ts=now - 60)],
            statuses=["followup"])
        cat.refresh()
        cat.poll_github()
        items = cat.inbox(now=now)
        self.assertEqual(
            [i["key"] for i in items],
            ["claude:f1", "gh:8", "gh:9"])
        row = items[1]
        self.assertEqual(row["kind"], "github")
        self.assertEqual(row["machine_id"], "mbp")
        self.assertEqual(row["reason"], "review_requested")

    def test_refresh_does_not_reach_for_github(self):
        # The session path never waits on gh: the catalog loop polls
        # GitHub separately, after its session work.
        cat = self._cat()
        calls = []
        cat.fetch_github = lambda: calls.append(1) or []
        cat.refresh()
        self.assertEqual(calls, [])
        cat.poll_github()
        self.assertEqual(calls, [1])

    def test_unread_github_never_ages_out(self):
        from quiver.remote.catalog import INBOX_MAX_AGE_S
        now = 1_800_000_000.0
        old = "2026-09-01T00:00:00Z"  # way past the 3-day cutoff
        cat = self._cat(github=[_gh_row(nid="9", updated=old)])
        cat.poll_github()
        keys = [i["key"] for i in cat.inbox(now=now)]
        self.assertEqual(keys, ["gh:9"])
        # And INBOX_MAX_AGE_S is genuinely past for this stamp.
        self.assertLess(ghcli._parse_iso(old), now - INBOX_MAX_AGE_S)

    def test_no_feed_no_github_rows(self):
        cat = Catalog(Path(tempfile.mkdtemp()), machine_id="mbp",
                      fetch_sessions=list, status_of=lambda ss: [])
        cat.refresh()
        cat.poll_github()
        self.assertEqual(cat.inbox(), [])

    def test_dismissal_hides_until_the_thread_updates(self):
        cat = self._cat(github=[_gh_row(nid="9")])
        cat.poll_github()
        last = ghcli._parse_iso("2026-10-23T18:29:12Z")
        self.assertEqual(
            cat.inbox(dismissed={"gh:9": last}), [])
        self.assertEqual(
            cat.inbox(dismissed={"gh:9": last + 1}), [])
        # GitHub bumps updated_at on new activity: the row returns.
        self.assertEqual(
            [i["key"] for i in
             cat.inbox(dismissed={"gh:9": last - 1})], ["gh:9"])

    def test_a_failed_poll_keeps_the_last_good_rows(self):
        feed = [[_gh_row(nid="9")]]
        cat = self._cat()
        cat.fetch_github = lambda: [dict(r) for r in feed[0]]
        cat.poll_github()
        self.assertEqual([i["key"] for i in cat.inbox()], ["gh:9"])

        def boom():
            raise OSError("gh exploded")
        cat.fetch_github = boom
        cat.poll_github()
        self.assertEqual([i["key"] for i in cat.inbox()], ["gh:9"])
        cat.fetch_github = lambda: None
        cat.poll_github()
        self.assertEqual([i["key"] for i in cat.inbox()], ["gh:9"])
        feed[0] = []
        cat.fetch_github = lambda: list(feed[0])
        cat.poll_github()
        self.assertEqual(cat.inbox(), [])


class TestAlertDiff(_GithubCatalog):
    """The dedupe map: alerts fire once per (thread, updated_at)."""

    def _alerts(self, cat):
        seen = []
        cat.on_github_alert = seen.append
        return seen

    def test_first_poll_is_a_silent_baseline(self):
        cat = self._cat(github=[_gh_row(nid="9")])
        alerts = self._alerts(cat)
        cat.poll_github()
        self.assertEqual(alerts, [])

    def test_new_unread_alerts_once_per_updated_at(self):
        feed = [[]]
        cat = self._cat()
        cat.fetch_github = lambda: [dict(r) for r in feed[0]]
        alerts = self._alerts(cat)
        cat.poll_github()  # baseline: nothing to alert on
        feed[0] = [_gh_row(nid="9", updated="2026-10-23T18:29:12Z")]
        cat.poll_github()
        self.assertEqual([r["gh_thread_id"] for r in alerts], ["9"])
        cat.poll_github()  # same stamp: no repeat
        self.assertEqual(len(alerts), 1)
        feed[0] = [_gh_row(nid="9", updated="2026-10-24T00:00:00Z")]
        cat.poll_github()
        self.assertEqual(len(alerts), 2)

    def test_read_then_new_activity_alerts_again(self):
        feed = [[]]
        cat = self._cat()
        cat.fetch_github = lambda: [dict(r) for r in feed[0]]
        alerts = self._alerts(cat)
        cat.poll_github()
        feed[0] = [_gh_row(nid="9", updated="2026-10-23T18:29:12Z")]
        cat.poll_github()
        feed[0] = []  # marked read: the thread leaves the listing
        cat.poll_github()
        feed[0] = [_gh_row(nid="9", updated="2026-10-24T00:00:00Z")]
        cat.poll_github()
        self.assertEqual(
            [r["updated_at"] for r in alerts],
            ["2026-10-23T18:29:12Z", "2026-10-24T00:00:00Z"])

    def test_a_failed_poll_keeps_the_dedupe_map(self):
        feed = [[_gh_row(nid="9")]]
        cat = self._cat()
        cat.fetch_github = lambda: [dict(r) for r in feed[0]]
        alerts = self._alerts(cat)
        cat.poll_github()
        cat.fetch_github = lambda: None
        cat.poll_github()
        cat.fetch_github = lambda: [dict(r) for r in feed[0]]
        cat.poll_github()
        self.assertEqual(alerts, [])

    def test_a_broken_alert_does_not_stall_the_poll(self):
        cat = self._cat()
        cat.fetch_github = lambda: [_gh_row(nid="9")]
        calls = []

        def boom(row):
            calls.append(row)
            raise RuntimeError("notifier is on fire")
        cat.on_github_alert = boom
        cat.poll_github()  # baseline
        cat.fetch_github = lambda: [
            _gh_row(nid="9", updated="2026-10-24T00:00:00Z")]
        cat.poll_github()
        self.assertEqual(len(calls), 1)


class TestUnreadRows(unittest.TestCase):
    """webapp.pages.inbox.unread_rows: the shared listing's unread
    slice, shaped for the catalog."""

    def setUp(self):
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)
        self.home = Path(tempfile.mkdtemp())

    def test_unread_rows_shape_and_skip_read(self):
        FakeGh(self, notes=[_raw("1"), _raw("2", unread=False),
                            _raw("3", reason="comment")])
        rows = gh_inbox.unread_rows(self.home)
        self.assertEqual([r["gh_thread_id"] for r in rows], ["1", "3"])
        self.assertEqual(rows[0]["kind"], "github")
        self.assertEqual(rows[1]["reason"], "comment")

    def test_polls_fold_into_the_shared_cache(self):
        # The catalog polls on its 10 s cadence; the TtlCache turns that
        # into one ``gh api notifications`` per listing TTL.
        self.gh = FakeGh(self, notes=[_raw("1")])
        gh_inbox.unread_rows(self.home)
        gh_inbox.unread_rows(self.home)
        listings = [c for c in self.gh.calls()
                    if c[:1] == ["api"] and "--method" not in c]
        self.assertEqual(len(listings), 1)

    def test_the_page_and_the_hub_share_the_probe(self):
        self.gh = FakeGh(self, notes=[_raw("1")])
        ctx = SimpleNamespace(home=self.home, machine={"name": "m"})
        gh_inbox.unread_rows(self.home)
        gh_inbox.get(ctx, {})  # the page's all-rows listing is its own key
        auths = [c for c in self.gh.calls() if c[:2] == ["auth", "status"]]
        self.assertEqual(len(auths), 1)

    def test_a_broken_env_answers_none_before_any_read(self):
        fake = FakeGh(self, notes=[_raw("1")], signed_out=True)
        self.assertIsNone(gh_inbox.unread_rows(self.home))
        self.assertEqual(
            [c for c in fake.calls() if c[:2] == ["api", "notifications"]],
            [])

    def test_a_failed_listing_answers_none(self):
        failed = {"status": "upstream-error", "notifications": [],
                  "error": "502"}
        ok_env = gh.GhEnv(installed=True, authenticated=True)
        with patch("quiver.github.cli._fetch_inbox", return_value=failed), \
                patch.object(gh, "probe_env", return_value=ok_env):
            self.assertIsNone(gh_inbox.unread_rows(self.home))
        # And a never-authed gh stops at the probe, before the listing.
        with patch.object(
                gh, "probe_env",
                return_value=gh.GhEnv(installed=True, authenticated=False)), \
                patch("quiver.github.cli._fetch_inbox") as fetch:
            self.assertIsNone(gh_inbox.unread_rows(self.home))
        fetch.assert_not_called()


class TestMarkReadUnion(unittest.TestCase):
    """A row the hub's unread feed shows must clear even when the
    page's all-rows listing crowded it out of ``LIMIT``."""

    def setUp(self):
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)
        self.ctx = SimpleNamespace(home=Path(tempfile.mkdtemp()),
                                   machine={"name": "hub-box"})

    def test_an_id_only_the_unread_listing_has_still_marks(self):
        def fake_fetch(limit, fetch_all=False):
            # all=true holds newer read rows; thread 9 lives only on
            # the unread listing.
            notes = [] if fetch_all else [_raw("9")]
            return {"status": "ok", "notifications": notes}
        fake = FakeGh(self, notes=[_raw("9")])
        ok_env = gh.GhEnv(installed=True, authenticated=True)
        with patch("quiver.github.cli._fetch_inbox",
                   side_effect=fake_fetch), \
                patch.object(gh, "probe_env", return_value=ok_env):
            body = gh_inbox.post(self.ctx, "read", {"ids": ["9"]})
        self.assertEqual(body["marked"], ["9"])
        self.assertEqual(
            fake.writes(),
            [["api", "--method", "PATCH", "notifications/threads/9"]])


class TestHubRoute(HubBase):
    """The merged feed over HTTP plus dismiss and mark-read wiring."""

    def setUp(self):
        super().setUp()
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)

    def _wire(self):
        self.srv.state.catalog.fetch_github = (
            lambda: gh_inbox.unread_rows(self.srv.state.home))
        self.srv.state.catalog.poll_github()

    def test_inbox_carries_the_github_row(self):
        FakeGh(self, notes=[_raw("1")])
        self._wire()
        status, data = self.srv.request(
            "GET", "/v1/inbox", headers=self.auth())
        self.assertEqual(status, 200)
        items = json.loads(data)["items"]
        gh_items = [i for i in items if i.get("kind") == "github"]
        self.assertEqual(len(gh_items), 1)
        row = gh_items[0]
        self.assertEqual(row["key"], "gh:1")
        self.assertEqual(row["reason"], "review_requested")
        self.assertEqual(row["subject_type"], "pr")

    def test_dismiss_hides_github_row_per_device(self):
        FakeGh(self, notes=[_raw("1")])
        self._wire()
        phone, tablet = self.srv.pair(), self.srv.pair()
        hdr = lambda c: {"Authorization": f"Bearer {c['credential']}"}
        last = ghcli._parse_iso("2026-10-23T18:29:12Z")
        status, data = self.srv.request(
            "POST", "/v1/inbox/dismiss",
            body={"key": "gh:1", "last_active_at": last},
            headers=hdr(phone))
        self.assertEqual(status, 200, data)
        keys = lambda c: [i["key"] for i in json.loads(
            self.srv.request("GET", "/v1/inbox", headers=hdr(c))[1]
        )["items"]]
        self.assertNotIn("gh:1", keys(phone))
        self.assertIn("gh:1", keys(tablet))

    def test_mark_read_drops_the_row(self):
        fake = FakeGh(self, notes=[_raw("1")])
        self._wire()
        auth = self.auth()
        status, data = self.srv.request(
            "POST", "/v1/app/inbox/read", body={"ids": ["1"]},
            headers={**auth,
                     "Origin": f"http://127.0.0.1:{self.srv.port}"})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["marked"], ["1"])
        self.assertEqual(
            fake.writes(),
            [["api", "--method", "PATCH", "notifications/threads/1"]])
        self.srv.state.catalog.poll_github()
        items = json.loads(self.srv.request(
            "GET", "/v1/inbox", headers=auth)[1])["items"]
        self.assertNotIn("gh:1", [i["key"] for i in items])


class TestGithubAlert(unittest.TestCase):
    """HubState._github_alert: the reason gate and the notify event."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.state = HubState(self.home)

    def _events(self):
        return self.state.journal.read_since(GLOBAL_KEY)[0]

    def test_push_reasons_emit_a_github_notify_event(self):
        for reason in ghcli.PUSH_REASONS:
            self.state._github_alert(_gh_row(reason=reason))
        events = self._events()
        self.assertEqual(len(events), len(ghcli.PUSH_REASONS))
        ev = events[0]
        self.assertEqual(ev["type"], "notify")
        self.assertEqual(ev["session"], "")
        self.assertEqual(ev["machine"], self.state.machine_id)
        data = ev["data"]
        self.assertEqual(data["kind"], "github")
        self.assertEqual(data["repo"], "o/a")
        self.assertEqual(data["title"], "Review this")
        self.assertEqual(data["url"], "https://github.com/o/a/pull/9")
        self.assertEqual(data["thread"], "9")

    def test_comments_and_unknown_reasons_stay_quiet(self):
        for reason in ("comment", "security_alert", ""):
            self.state._github_alert(_gh_row(reason=reason))
        self.assertEqual(self._events(), [])


class TestNotifyGithub(unittest.TestCase):
    """notify.py's github branch: wording, click target, the gate."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.state = HubState(self.home)
        self.cfg = {"enabled": True, "url": "https://ntfy.test",
                    "topic": "qw"}
        self.n = notify.Notifier(self.state, "http://h:8477",
                                 subscribe_key=GLOBAL_KEY)
        self.posts = []
        self.n._post = lambda cfg, payload: self.posts.append(payload)

    def _ev(self, **data):
        return {"type": "notify", "session": "",
                "data": {"kind": "github", **data}}

    def test_reason_wording_click_and_action(self):
        self.n._handle(self._ev(
            reason="review_requested", repo="o/a",
            title="Add the flag", url="https://github.com/o/a/pull/9",
            thread="9"), self.cfg)
        self.assertEqual(len(self.posts), 1)
        post = self.posts[0]
        self.assertEqual(post["title"], "o/a: review requested")
        self.assertEqual(post["message"], "Add the flag")
        self.assertEqual(post["priority"], 4)
        self.assertEqual(post["tags"], ["bell"])
        self.assertEqual(post["click"], "https://github.com/o/a/pull/9")
        self.assertEqual(
            post["actions"],
            [{"action": "view", "label": "Open",
              "url": "https://github.com/o/a/pull/9"}])

    def test_ci_fail_siren(self):
        self.n._handle(self._ev(
            reason="ci_fail", repo="o/a", title="CI",
            url="https://github.com/o/a"), self.cfg)
        post = self.posts[0]
        self.assertEqual(post["title"], "o/a: CI failed")
        self.assertEqual(post["tags"], ["rotating_light"])

    def test_comment_never_posts(self):
        self.n._handle(self._ev(
            reason="comment", repo="o/a", title="t",
            url="https://github.com/o/a"), self.cfg)
        self.assertEqual(self.posts, [])

    def test_no_repo_no_url(self):
        self.n._handle(self._ev(reason="mention", title="", url=""),
                       self.cfg)
        post = self.posts[0]
        self.assertEqual(post["title"], "GitHub: mentioned you")
        self.assertNotIn("click", post)
        self.assertNotIn("actions", post)

    def test_title_is_redacted(self):
        self.n._handle(self._ev(
            reason="mention", repo="o/a",
            title='uses "Authorization: Bearer sk-abcdefghijklmnop"',
            url="https://github.com/o/a/pull/9"), self.cfg)
        self.assertNotIn("sk-abcdefghijklmnop",
                         self.posts[0]["message"])


if __name__ == "__main__":
    unittest.main()
