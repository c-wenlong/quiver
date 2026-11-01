"""The Inbox page's data: swe gh inbox --all, cached, and its route;
and its write, marking read, against a fake ``gh`` on PATH."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from swe.github import gh
from swe.webapp.pages import PageError
from swe.github import shared as G
from swe.webapp.pages import inbox

from tests.test_remote_hub import HubBase
from tests.test_webapp_repositories import FakeGh

NOTE = {"id": "1", "repo": "o/a", "title": "CI failed", "type": "CheckSuite",
        "reason": "ci_activity", "unread": True, "updated_at": "2026-10-23T18:29:12Z",
        "url": "https://github.com/o/a"}


class TestInbox(unittest.TestCase):
    def setUp(self):
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)
        self.ctx = SimpleNamespace(home=None, machine={"name": "hub-box"})

    def test_rows_read_included_without_a_probe(self):
        reply = {"status": "ok", "notifications": [NOTE]}
        with patch("swe.github.cli._fetch_inbox", return_value=reply) as fetch, \
                patch.object(gh, "probe_env") as probe:
            body = inbox.get(self.ctx, {})
            inbox.get(self.ctx, {})
        fetch.assert_called_once_with(50, fetch_all=True)
        probe.assert_not_called()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["notifications"], [NOTE])
        self.assertEqual(body["machine"], "hub-box")
        self.assertIn("fetched_at", body)

    def test_a_failure_is_classified_by_the_probe(self):
        failed = {"status": "upstream-error", "notifications": [], "error": "502"}
        with patch("swe.github.cli._fetch_inbox", return_value=failed), \
                patch.object(gh, "probe_env", return_value=gh.GhEnv(installed=True)):
            body = inbox.get(self.ctx, {})
        self.assertEqual((body["status"], body["github"]["status"]), ("auth-required", "auth-required"))
        self.assertEqual(body["notifications"], [])
        G.CACHE.clear()
        ok_env = gh.GhEnv(installed=True, authenticated=True)
        with patch("swe.github.cli._fetch_inbox", return_value=failed), \
                patch.object(gh, "probe_env", return_value=ok_env):
            body = inbox.get(self.ctx, {})
        # gh works, GitHub failed: the page says "GitHub returned …".
        self.assertEqual((body["status"], body["error"]), ("upstream-error", "502"))


class TestRoute(HubBase):
    def test_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/inbox")[0], 401)
        with patch.object(G, "CACHE", G.TtlCache()), \
                patch("swe.github.cli._fetch_inbox",
                      return_value={"status": "ok", "notifications": [NOTE]}):
            status, data = self.srv.request("GET", "/v1/app/inbox", headers=self.auth())
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["notifications"][0]["id"], "1")



def _raw(nid, unread=True, title="CI failed"):
    """One notification as GitHub's API returns it."""
    return {"id": nid, "unread": unread, "reason": "ci_activity",
            "updated_at": "2026-10-23T18:29:12Z",
            "subject": {"title": title, "type": "CheckSuite", "url": None},
            "repository": {"full_name": "o/a", "html_url": "https://github.com/o/a"}}


class TestMarkRead(unittest.TestCase):
    def setUp(self):
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)
        self.ctx = SimpleNamespace(home=Path(tempfile.mkdtemp()), machine={"name": "hub-box"})
        self.gh = FakeGh(self, notes=[_raw("1"), _raw("2"), _raw("3", unread=False)])
        inbox.get(self.ctx, {})  # what the page lists

    def refused(self, status, code, body):
        with self.assertRaises(PageError) as caught:
            inbox.post(self.ctx, "read", body)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code),
                         caught.exception.message)
        self.assertEqual(self.gh.writes(), [])
        return caught.exception

    def test_one_row_is_marked_and_the_answer_carries_the_rows(self):
        body = inbox.post(self.ctx, "read", {"ids": ["1"]})
        self.assertEqual(self.gh.writes(), [["api", "--method", "PATCH", "notifications/threads/1"]])
        self.assertEqual((body["all"], body["marked"], body["failed"]), (False, ["1"], []))
        unread = {n["id"]: n["unread"] for n in body["notifications"]}
        self.assertEqual(unread, {"1": False, "2": True, "3": False})
        self.assertEqual(body["machine"], "hub-box")
        # The cached listing was dropped: the next read asks GitHub again.
        listings = lambda: [c for c in self.gh.calls() if c[:1] == ["api"] and "--method" not in c]
        before = len(listings())
        again = inbox.get(self.ctx, {})
        self.assertEqual(len(listings()), before + 1)
        self.assertFalse(next(n for n in again["notifications"] if n["id"] == "1")["unread"])

    def test_all_marks_everything_with_one_put(self):
        body = inbox.post(self.ctx, "read", {"all": True})
        writes = self.gh.writes()
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][:4], ["api", "--method", "PUT", "notifications"])
        self.assertRegex(writes[0][5], r"^last_read_at=\d{4}-\d\d-\d\dT")
        self.assertEqual(body["marked"], ["*"])
        self.assertFalse(any(n["unread"] for n in body["notifications"]))

    def test_a_partial_failure_names_the_ids_github_refused(self):
        self.gh.update(fail_ids=["2"])
        body = inbox.post(self.ctx, "read", {"ids": ["1", "2", "1"]})
        self.assertEqual(body["marked"], ["1"])
        self.assertEqual([f["id"] for f in body["failed"]], ["2"])
        self.assertIn("500", body["failed"][0]["error"])
        unread = {n["id"]: n["unread"] for n in body["notifications"]}
        self.assertEqual((unread["1"], unread["2"]), (False, True))
        self.assertEqual(len(self.gh.writes()), 2)  # the repeated id is sent once

    def test_several_rows_at_once_all_stay_read(self):
        body = inbox.post(self.ctx, "read", {"ids": ["1", "2"]})
        self.assertEqual(sorted(body["marked"]), ["1", "2"])
        again = inbox.get(self.ctx, {})
        self.assertEqual([n["unread"] for n in again["notifications"]], [False, False, False])

    def test_reads_run_off_the_write_lock_and_writes_under_it(self):
        # The probe and the two listings are reads; holding WRITE_LOCK
        # over them stalled every other GitHub write on the hub. The
        # marks and the cache drop still need it.
        seen = {}

        def spy(owner, name):
            real = getattr(owner, name)

            def call(*a, **k):
                seen.setdefault(name, []).append(G.WRITE_LOCK.locked())
                return real(*a, **k)
            return patch.object(owner, name, side_effect=call)

        with spy(G, "probe"), spy(inbox, "_listing"), spy(inbox, "_unread_listing"), \
                spy(inbox.ghcli, "mark_threads_read"), spy(inbox.ghcli, "mark_all_read"), \
                spy(inbox, "_drop"):
            inbox.post(self.ctx, "read", {"ids": ["1"]})
            inbox.post(self.ctx, "read", {"all": True})
        for read in ("probe", "_listing", "_unread_listing"):
            self.assertEqual(set(seen[read]), {False}, read)
        for write in ("mark_threads_read", "mark_all_read", "_drop"):
            self.assertEqual(set(seen[write]), {True}, write)

    def test_ids_must_be_ones_the_inbox_lists(self):
        err = self.refused(404, "unknown_notification", {"ids": ["1", "999"]})
        self.assertIn("999", err.message)

    def test_bad_shapes(self):
        for body, code in (({}, "nothing_to_mark"), ({"ids": []}, "nothing_to_mark"),
                           ({"ids": "1"}, "bad_ids"), ({"ids": ["1; rm -rf"]}, "bad_ids"),
                           ({"ids": ["../threads"]}, "bad_ids"), ({"ids": [True]}, "bad_ids"),
                           ({"ids": [{"id": 1}]}, "bad_ids"), ({"all": "yes"}, "bad_all"),
                           ({"ids": ["1"], "all": True}, "ids_and_all"),
                           ({"ids": ["9" * 5000]}, "bad_ids"),
                           ({"ids": [str(i) for i in range(51)]}, "too_many_ids")):
            with self.subTest(body=body):
                self.refused(422, code, body)

    def test_unknown_action_signed_out_and_a_failed_mark_all(self):
        with self.assertRaises(PageError) as caught:
            inbox.post(self.ctx, "unread", {"ids": ["1"]})
        self.assertEqual(caught.exception.status, 404)
        self.gh.update(signed_out=True)
        err = self.refused(409, "gh_auth_required", {"ids": ["1"]})
        self.assertIn("gh auth login", err.message)
        self.gh.update(signed_out=False, api_error="HTTP 502: Bad Gateway")
        G.CACHE.clear()
        with self.assertRaises(PageError) as caught:
            inbox.post(self.ctx, "read", {"all": True})
        self.assertEqual((caught.exception.status, caught.exception.code), (502, "mark_failed"))
        self.assertIn("502", caught.exception.message)


class TestReadRoute(HubBase):
    def setUp(self):
        super().setUp()
        p = patch.object(G, "CACHE", G.TtlCache())
        p.start()
        self.addCleanup(p.stop)

    def test_needs_a_device_and_a_same_site_origin(self):
        fake = FakeGh(self, notes=[_raw("1")])
        body = {"ids": ["1"]}
        self.assertEqual(self.srv.request("POST", "/v1/app/inbox/read", body=body)[0], 401)
        auth = self.auth()
        status, data = self.srv.request("POST", "/v1/app/inbox/read", body=body,
                                        headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertEqual(fake.writes(), [])
        status, data = self.srv.request(
            "POST", "/v1/app/inbox/read", body=body,
            headers={**auth, "Origin": f"http://127.0.0.1:{self.srv.port}"})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["marked"], ["1"])
        self.assertEqual(fake.writes(), [["api", "--method", "PATCH", "notifications/threads/1"]])
        status, _ = self.srv.request("POST", "/v1/app/inbox/read", body={"ids": ["7"]}, headers=auth)
        self.assertEqual(status, 404)



NEEDS_FLOW = r"""
document.querySelector = (sel) => panel.querySelector(sel);
// ``bound(attr)``: the controls carrying ``data-<attr>`` the last paint bound.
const lastQ = {};
const qsa = document.querySelectorAll;
document.querySelectorAll = (sel) => (lastQ[sel] = qsa(sel));
const bound = (attr) => lastQ[`#panel [data-${attr}]`];
const out = {};
const html = () => document.getElementById("panel").innerHTML;
const heads = () => [...html().matchAll(/class="gh-need-section[^"]*">([^<]*)</g)].map((m) => m[1]);
const rowKeys = () => [...html().matchAll(/href="#\/sessions\?chat=([^"]*)"/g)].map((m) => decodeURIComponent(unesc(m[1])));
const clears = () => [...html().matchAll(/data-need-clear="([^"]*)"/g)].map((m) => unesc(m[1]));
const dismissed = () => fetchCalls.filter((c) => c.path === "/v1/inbox/dismiss").map((c) => c.body);
const ITEMS = [
  { key: "m:w1", harness: "claude", title: "Ask", status: "followup", last_active_at: 100 },
  { key: "m:a1", harness: "codex", title: "Run rm?", status: "approval", last_active_at: 90, request_id: "r1" },
  { key: "gh:7", kind: "github", status: "github", title: "PR", last_active_at: 300 },
  { key: "m:d1", harness: "claude", title: "Shipped", status: "done", last_active_at: 50 },
  { key: "m:e1", harness: "claude", title: "Broke", status: "error", last_active_at: 80 },
  { key: "m:i1", harness: "claude", title: "Stopped", status: "interrupted", last_active_at: 70 },
  { key: "m:x1", harness: "claude", title: "Odd", status: "mystery", last_active_at: 60 },
];
let items = ITEMS;
apiAnswers.push(["/v1/app/inbox", { status: 200, data: { github: { status: "ok" }, status: "ok", notifications: [] } }]);
apiAnswers.push(["/v1/inbox", { get status() { return 200; }, get data() { return { items }; } }]);
fetchRoutes.push(["/v1/inbox/dismiss", { status: 200, body: { ok: true } }]);
(async () => {
  location.hash = "#/inbox";
  PAGES.inbox("inbox");
  await tick();
  out.heads = heads();
  out.rows = rowKeys();
  out.clears = clears();
  out.review = (html().match(/gh-need-review/g) || []).length;
  out.githubHeading = /<h2 class="gh-inbox-heading">GitHub<\/h2>/.test(html());
  // One row's Clear: gone at once, one dismiss with its stamp.
  bound("need-clear")
    .find((b) => b.dataset.needClear === "m:e1").onclick({ preventDefault() {} });
  await tick();
  out.afterRow = rowKeys();
  out.rowPost = dismissed();
  // A poll whose answer predates the dismiss still lists it: the ledger hides it.
  PAGES.inbox("inbox");
  await tick();
  out.stillHidden = !rowKeys().includes("m:e1");
  // An approval on a cleared session shows at once, before its stamp moves.
  items = ITEMS.map((i) => (i.key === "m:e1" ? { ...i, status: "approval", request_id: "r2" } : i));
  PAGES.inbox("inbox");
  await tick();
  out.approvalShows = rowKeys().includes("m:e1");
  items = ITEMS;
  PAGES.inbox("inbox");
  await tick();
  out.hiddenAgain = !rowKeys().includes("m:e1");
  // Two reads answering out of order: the older one, still listing the
  // cleared row at its old stamp, must not land after the newer one.
  const realApi = api;
  const held = [];
  globalThis.api = (path) => (path.startsWith("/v1/inbox")
    ? new Promise((resolve) => held.push(resolve)) : realApi(path));
  PAGES.inbox("inbox");
  PAGES.inbox("inbox");
  await tick();
  globalThis.api = realApi;
  // The newer read has the session waiting again (new activity, so the
  // clear retires); the older still has it in Errors.
  held[1]({ status: 200, data: { items: ITEMS.map((i) => (i.key === "m:e1" ? { ...i, status: "followup", last_active_at: 81 } : i)) } });
  await tick();
  held[0]({ status: 200, data: { items: ITEMS } });
  await tick();
  out.newestWins = rowKeys().indexOf("m:e1") === 2 && heads().length === 3;
  // New activity past the stamp brings it back.
  items = ITEMS.map((i) => (i.key === "m:e1" ? { ...i, last_active_at: 81 } : i));
  PAGES.inbox("inbox");
  await tick();
  out.back = rowKeys().includes("m:e1");
  // Waiting on you's Clear sweeps the follow-up and the odd one, never the approval.
  fetchCalls.length = 0;
  bound("need-section")
    .find((b) => b.dataset.needSection === "waiting").onclick();
  await tick();
  out.sectionPost = dismissed().map((b) => b.key);
  out.afterSection = rowKeys();
  // A refused clear says so and the next read restores the row.
  fetchCalls.length = 0;
  fetchRoutes.unshift(["/v1/inbox/dismiss", { status: 500, body: { error: { message: "disk full" } } }]);
  bound("need-clear")
    .find((b) => b.dataset.needClear === "m:d1").onclick({ preventDefault() {} });
  await tick();
  out.failText = /Couldn&#39;t clear &quot;Shipped&quot;: disk full/.test(html());
  out.restored = rowKeys().includes("m:d1");
  // Nothing left: the empty card.
  items = [];
  PAGES.inbox("inbox");
  await tick();
  out.empty = html().includes("Nothing needs you.");
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestNeedsYou(unittest.TestCase):
    """inbox.js's Needs you sections under node: the iOS Inbox's groups
    from GET /v1/inbox?done=1, a row opening its chat, and Clear posting
    POST /v1/inbox/dismiss per row or per section."""

    def test_sections_rows_and_clear(self):
        from tests.test_webapp_launch_sheets import PAGES, STUB
        script = (STUB + (PAGES / "github.js").read_text(encoding="utf-8") + "\n"
                  + (PAGES / "inbox.js").read_text(encoding="utf-8") + "\n" + NEEDS_FLOW)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run.js"
            path.write_text(script)
            done = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = json.loads(done.stdout)
        # iOS order; the GitHub row stays in the list below, not here;
        # an unknown status lands in Waiting on you.
        self.assertEqual(out["heads"], ["Waiting on you", "Errors", "Finished today"])
        self.assertEqual(out["rows"], ["m:w1", "m:a1", "m:x1", "m:e1", "m:i1", "m:d1"])
        self.assertTrue(out["githubHeading"])
        # The approval has Review and no Clear.
        self.assertEqual(out["review"], 1)
        self.assertNotIn("m:a1", out["clears"])
        self.assertEqual(len(out["clears"]), 5)
        self.assertNotIn("m:e1", out["afterRow"])
        self.assertEqual(out["rowPost"], [{"key": "m:e1", "last_active_at": 80}])
        self.assertTrue(out["stillHidden"])
        self.assertTrue(out["approvalShows"])
        self.assertTrue(out["hiddenAgain"])
        self.assertTrue(out["newestWins"])
        self.assertTrue(out["back"])
        self.assertEqual(sorted(out["sectionPost"]), ["m:w1", "m:x1"])
        self.assertIn("m:a1", out["afterSection"])
        self.assertNotIn("m:w1", out["afterSection"])
        self.assertTrue(out["failText"])
        self.assertTrue(out["restored"])
        self.assertTrue(out["empty"])


if __name__ == "__main__":
    unittest.main()
