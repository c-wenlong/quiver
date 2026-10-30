"""The Sessions page: per-session usage, projects, accidental sessions,
and /v1/app/sessions on the hub."""

import http.client
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from quiver.ledger.models import LedgerRecord, UsageEvent
from quiver.remote import protocol
from quiver.ledger.pricing import PricingCatalog, rates_from_entry
from quiver.ledger.sessions import by_session
from quiver.ledger.store import LedgerStore
from quiver.webapp import sessions
from quiver.webapp.api import WebApp

from tests.test_remote_hub import HubBase, _AttachedServer
from tests.test_remote_owner_device import _OWNER_IP, OwnerBase

STATIC = Path(__file__).resolve().parent.parent / "src" / "quiver" / "webapp" / "static"

CATALOG = PricingCatalog({"m": rates_from_entry(
    {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06})})


def _ev(session, ts, inp, out=0, model="m", cwd="/w", harness="claude"):
    return UsageEvent(harness=harness, account="/h", session=session, cwd=cwd,
                      model=model, ts=ts, input=inp, output=out)


class TestBySession(unittest.TestCase):
    def setUp(self):
        self.store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(self.store.close)

    def add(self, key, ev):
        self.store.upsert(LedgerRecord(key=key, event=ev))

    def test_totals_models_span_and_folder(self):
        self.add("a", _ev("s1", 100, 1000, 10, cwd="/repo"))
        self.add("b", _ev("s1", 400, 500, 0, model="other", cwd="/repo"))
        self.add("c", _ev("s1", 200, 1, 0, cwd="/x/side-session-notes/local_1"))
        self.add("d", _ev("s1", 250, 1, 0, cwd="/x/side-session-notes/local_1"))
        self.add("e", _ev("s2", 50, 7))
        u = by_session(self.store, CATALOG)[("claude", "s1")]
        self.assertEqual((u["tokens"], u["events"], u["first_ts"], u["last_ts"]), (1512, 4, 100, 400))
        self.assertAlmostEqual(u["cost_usd"], 1002 * 1e-06 + 10 * 2e-06)
        self.assertEqual(u["unpriced_tokens"], 500)
        self.assertEqual([m["model"] for m in u["models"]], ["m", "other"])
        # A real folder beats the notes folder, even with fewer events.
        self.assertEqual(u["cwd"], "/repo")


class TestProjects(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_repository_and_its_subfolders(self):
        repo = self.root / "quiver"
        (repo / ".git").mkdir(parents=True)
        (repo / "src" / "deep").mkdir(parents=True)
        self.assertEqual(sessions.project_of(str(repo / "src" / "deep")), "quiver")

    def test_worktree_names_its_main_repository(self):
        (self.root / "main-repo" / ".git" / "worktrees" / "wt").mkdir(parents=True)
        wt = self.root / "elsewhere" / "wt"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {self.root}/main-repo/.git/worktrees/wt\n")
        self.assertEqual(sessions.project_of(str(wt)), "main-repo")

    def test_gone_paths_fall_back_to_worktree_shapes(self):
        self.assertEqual(sessions.project_of("/gone/app/.claude/worktrees/x"), "app")
        self.assertEqual(sessions.project_of("/home/u/.codex/worktrees/ab12/site"), "site")
        self.assertEqual(sessions.project_of("/gone/plain"), "plain")
        self.assertIsNone(sessions.project_of(None))

    def test_hash_like(self):
        self.assertTrue(sessions.is_hash_like("01a03a60389b"))
        self.assertTrue(sessions.is_hash_like("080c2c1b-bdc1-4f92-96f9-c83b228871c7"))
        self.assertFalse(sessions.is_hash_like("setup-server"))
        self.assertFalse(sessions.is_hash_like("abc"))


class TestTrivial(unittest.TestCase):
    def u(self, **kw):
        base = {"tokens": 500, "events": 1, "first_ts": 0, "last_ts": 10,
                "output": 20, "reasoning": 0}
        base.update(kw)
        return base

    def test_rules(self):
        self.assertTrue(sessions.is_trivial("claude", self.u(), True))
        self.assertFalse(sessions.is_trivial("claude", self.u(output=500), True))
        self.assertFalse(sessions.is_trivial("claude", self.u(last_ts=120), True))
        self.assertFalse(sessions.is_trivial("claude", self.u(events=3), True))
        self.assertTrue(sessions.is_trivial("claude", self.u(tokens=0), True))
        self.assertFalse(sessions.is_trivial("cursor", self.u(), True))
        self.assertFalse(sessions.is_trivial("gemini", self.u(tokens=0), True))
        # No usage at all: only for a harness this ledger reads, after a scan.
        self.assertTrue(sessions.is_trivial("codex", None, True))
        self.assertFalse(sessions.is_trivial("codex", None, False))
        self.assertTrue(sessions.is_trivial("pi", None, True))
        self.assertTrue(sessions.is_trivial("droid", None, True))
        # Cursor's feed is not a local scan: an uncovered session is not
        # judged empty.
        self.assertFalse(sessions.is_trivial("cursor", None, True))
        # And nor is a row whose own source the scan never read, even
        # under a harness the ledger covers elsewhere.
        self.assertFalse(sessions.is_trivial("cline", None, True, covered=False))

    def test_location(self):
        self.assertEqual(sessions.location("cursor", "bc-123"), "Cloud")
        self.assertEqual(sessions.location("grok-bot", "x"), "Cloud")
        self.assertEqual(sessions.location("claude", "x"), "Local")


class TestBuild(unittest.TestCase):
    def test_join_is_local_only_and_sorted(self):
        rows = [
            {"key": "claude:s1", "id": "s1", "harness": "claude", "machine_id": "hub",
             "cwd": "/gone/one", "last_active_at": 10, "title": "a"},
            {"key": "claude:s1@mac", "id": "s1", "harness": "claude", "machine_id": "mac",
             "remote": True, "cwd": "/Users/me/x", "last_active_at": 20, "title": "b"},
        ]
        usage = {("claude", "s1"): {"tokens": 9, "events": 5, "first_ts": 0, "last_ts": 300,
                                    "output": 500, "reasoning": 0, "models": [], "cwd": "/w"}}
        out = sessions.build(rows, usage, "hub", {"mac": {"name": "MacBook"}}, scanned=True)
        self.assertEqual([r["key"] for r in out], ["claude:s1@mac", "claude:s1"])
        remote, here = out
        self.assertIsNone(remote["usage"])
        self.assertTrue(remote["remote"])
        self.assertEqual(remote["machine"], "MacBook")
        self.assertFalse(remote["trivial"])
        self.assertEqual(here["usage"]["tokens"], 9)
        self.assertNotIn("cwd", here["usage"])
        self.assertEqual((here["duration_s"], here["project"], here["project_key"]), (300, "one", "one"))


    def test_rows_carry_remote_control_state(self):
        rows = [
            {"key": "claude:a", "id": "a", "harness": "claude", "machine_id": "hub",
             "ownership": "attached", "attachable": True, "last_active_at": 2},
            {"key": "gemini:b", "id": "b", "harness": "gemini", "machine_id": "hub",
             "last_active_at": 1},
        ]
        attached, bare = sessions.build(rows, {}, "hub", {}, scanned=True)
        self.assertEqual((attached["ownership"], attached["attachable"]), ("attached", True))
        # A row the catalog didn't mark is observed: never offer a composer
        # the hub would refuse.
        self.assertEqual((bare["ownership"], bare["attachable"]), ("observed", False))


    def test_rows_carry_the_hubs_model_and_blank_when_unknown(self):
        rows = [
            {"key": "claude:a", "id": "a", "harness": "claude", "machine_id": "hub",
             "model": "claude-opus-4-5-20251101", "last_active_at": 2},
            {"key": "gemini:b", "id": "b", "harness": "gemini", "machine_id": "hub",
             "last_active_at": 1},
        ]
        known, unknown = sessions.build(rows, {}, "hub", {}, scanned=True)
        # Verbatim: the page drops the provider path and date stamp.
        self.assertEqual(known["model"], "claude-opus-4-5-20251101")
        self.assertEqual(unknown["model"], "")


class TestCodexResume(unittest.TestCase):
    def test_a_resumed_thread_carries_every_rollout(self):
        store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(store.close)
        thread = "01a03a60-389b-73d0-a8a3-8cde9d36da15"
        first = f"rollout-2026-10-01T10-00-00-{thread}"
        resumed = f"{first}_01a10f10-4631-7193-b444-1fb7ed99dc4f"
        # Response 1 in the original rollout, repeated by the resume under
        # the same response id; response 2 only in the resumed rollout.
        store.upsert(LedgerRecord(key="codex:resp:1", event=_ev(first, 100, 1000, harness="codex")))
        store.upsert(LedgerRecord(key="codex:resp:1", event=_ev(resumed, 900, 1000, harness="codex")))
        store.upsert(LedgerRecord(key="codex:resp:2", event=_ev(resumed, 1000, 300, harness="codex")))
        usage = by_session(store, CATALOG)
        rows = [{"key": "codex:x", "id": resumed, "harness": "codex", "machine_id": "hub",
                 "cwd": "/w", "last_active_at": 1000, "title": "t"}]
        [row] = sessions.build(rows, usage, "hub", {}, scanned=True)
        self.assertEqual(row["usage"]["tokens"], 1300)
        self.assertEqual((row["usage"]["first_ts"], row["duration_s"]), (100, 900))


class TestLedgerCoverage(unittest.TestCase):
    """``ledger_covers``: the catalog lists more than the ledger scans
    for cline (the legacy taskHistory index) and droid (a transcript
    whose settings file is missing); those rows are unread, not
    accidental. ``parse_pi`` and the ledger read the same tree."""

    def _build(self, home, harness, sid, **extra):
        row = {"key": f"{harness}:{sid}", "id": sid, "harness": harness,
               "machine_id": "hub", "cwd": "/w", "last_active_at": 1, **extra}
        [built] = sessions.build([row], {}, "hub", {}, scanned=True, home=home)
        return built

    def test_cline_taskhistory_row_stays_visible(self):
        home = Path(tempfile.mkdtemp())
        state = home / ".cline" / "data" / "state"
        state.mkdir(parents=True)
        (state / "taskHistory.json").write_text(json.dumps([
            {"id": "legacy-1", "task": "old"}]))
        self.assertFalse(self._build(home, "cline", "legacy-1")["trivial"])

    def test_a_cline_manifest_row_is_judged(self):
        home = Path(tempfile.mkdtemp())
        d = home / ".cline" / "data" / "sessions" / "s2"
        d.mkdir(parents=True)
        (d / "s2.json").write_text("{}")
        self.assertTrue(self._build(home, "cline", "s2")["trivial"])

    def test_a_cline_row_under_an_account_home_uses_that_home(self):
        home = Path(tempfile.mkdtemp())
        profile = home / ".quiver" / "accounts" / "cline" / "p1" / "home"
        d = profile / "data" / "sessions" / "s3"
        d.mkdir(parents=True)
        (d / "s3.json").write_text("{}")
        self.assertTrue(
            self._build(home, "cline", "s3", account_home=str(profile))["trivial"])

    def test_a_droid_row_needs_its_settings_file(self):
        home = Path(tempfile.mkdtemp())
        proj = home / ".factory" / "sessions" / "proj"
        proj.mkdir(parents=True)
        (proj / "abc.jsonl").write_text("{}\n")
        self.assertFalse(self._build(home, "droid", "abc")["trivial"])
        (proj / "abc.settings.json").write_text("{}")
        self.assertTrue(self._build(home, "droid", "abc")["trivial"])

    def test_droid_folders_are_listed_once_per_build(self):
        # A glob per Droid row walked every project folder each time.
        home = Path(tempfile.mkdtemp())
        proj = home / ".factory" / "sessions" / "proj"
        proj.mkdir(parents=True)
        for sid in ("a", "b", "c"):
            (proj / f"{sid}.settings.json").write_text("{}")
        rows = [{"key": f"droid:{sid}", "id": sid, "harness": "droid",
                 "machine_id": "hub", "cwd": "/w", "last_active_at": 1}
                for sid in ("a", "b", "c", "gone")]
        real = Path.glob
        with patch.object(Path, "glob", autospec=True,
                          side_effect=lambda self, pat: real(self, pat)) as walk:
            built = sessions.build(rows, {}, "hub", {}, scanned=True, home=home)
        self.assertEqual(walk.call_count, 1)
        self.assertEqual({r["id"]: r["trivial"] for r in built},
                         {"a": True, "b": True, "c": True, "gone": False})

    def test_pi_rows_are_covered_by_definition(self):
        home = Path(tempfile.mkdtemp())
        self.assertTrue(self._build(home, "pi", "anything")["trivial"])


class TestCarefulJudgement(unittest.TestCase):
    def test_satellite_projects_ignore_this_disk(self):
        repo = Path(tempfile.mkdtemp()) / "hubrepo"
        (repo / ".git").mkdir(parents=True)
        (repo / "wt").mkdir()
        self.assertEqual(sessions.project_of(str(repo / "wt")), "hubrepo")
        self.assertEqual(sessions.project_of(str(repo / "wt"), local=False), "wt")

    def test_unscanned_sessions_stay_visible(self):
        rows = [{"key": "claude:s9", "id": "s9", "harness": "claude", "machine_id": "hub",
                 "cwd": "/w", "last_active_at": 1, "title": "t"}]
        [row] = sessions.build(rows, {}, "hub", {}, scanned=False)
        self.assertFalse(row["trivial"])


class TestSessionsRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.srv.state.webapp = WebApp(self.srv.home, download_prices=False)
        proj = self.srv.home / ".claude" / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(json.dumps({
            "timestamp": "2026-10-03T10:00:00Z", "sessionId": "s1", "requestId": "r",
            "message": {"id": "m", "model": "claude-opus-5",
                        "usage": {"input_tokens": 1000, "output_tokens": 10}},
        }) + "\n")

    def test_rows_carry_usage(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/sessions")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/sessions", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        [row] = body["sessions"]
        self.assertEqual(row["key"], "claude:s1")
        self.assertEqual(row["usage"]["tokens"], 1010)
        self.assertEqual(row["usage"]["models"], [{"model": "claude-opus-5", "tokens": 1010}])
        self.assertEqual(row["project"], "proj")
        self.assertIn("claude", body["ledger_harnesses"])
        # A terminal owns it; claude can resume, so the chat offers Attach.
        self.assertEqual((row["ownership"], row["attachable"]), ("observed", True))

    def test_rows_carry_the_models_of_the_newest_reply(self):
        _, data = self.srv.request("GET", "/v1/app/sessions", headers=self.auth())
        [row] = json.loads(data)["sessions"]
        self.assertEqual(row["model"], "")  # the hub doesn't know it
        # The hub's own catalog row says it (session_model.py): the page
        # reuses that, it has no second transcript reader.
        self.srv.state.catalog._model_of = lambda ss: ["claude-opus-5-20251101"] * len(ss)
        self.srv.state.catalog.refresh()
        _, data = self.srv.request("GET", "/v1/app/sessions", headers=self.auth())
        [row] = json.loads(data)["sessions"]
        self.assertEqual(row["model"], "claude-opus-5-20251101")

    @unittest.skipIf(__import__("os").geteuid() == 0, "root reads anything")
    def test_an_unreadable_source_keeps_its_sessions_visible(self):
        f = self.srv.home / ".claude" / "projects" / "p" / "s1.jsonl"
        f.chmod(0)
        self.addCleanup(f.chmod, 0o644)
        _, data = self.srv.request("GET", "/v1/app/sessions", headers=self.auth())
        body = json.loads(data)
        self.assertFalse(body["sessions"][0]["trivial"])
        self.assertEqual(len(body["notes"]), 1)

    def test_page_script_is_served(self):
        self.assertEqual(self.srv.request("GET", "/sessions.js")[0], 200)
        status, body = self.srv.request("GET", "/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b"globalThis.SessionChat", body)
        status, body = self.srv.request("GET", "/pages.css")
        self.assertIn(b".chat-composer", body)


class TestChatContract(unittest.TestCase):
    """The hub answers the chat (pages/chat.js) relies on, in the order it
    makes the calls: backfill, catalog row, stream from the backfill's
    last seq, then the writes, against a session attached to a stub."""

    def setUp(self):
        self.srv = _AttachedServer()
        self.addCleanup(self.srv.stop)
        self.hdr = {"Authorization": f"Bearer {self.srv.pair()['credential']}"}
        self.key = "claude:s1"
        self.srv.state.status_of = lambda sessions: ["done"]

    def emit(self, etype, data):
        return self.srv.state.emit(self.key, protocol.event(etype, self.key, "hub", data))

    def stream(self, last):
        """Every event the stream sends up to journal.caught-up."""
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        try:
            conn.request("GET", f"/v1/sessions/claude%3As1/events?last={last}", headers=self.hdr)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            events = []
            for _ in range(200):
                line = resp.readline().decode()
                if not line:
                    break
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
                    if events[-1]["type"] == "journal.caught-up":
                        break
            return events
        finally:
            conn.close()

    def test_open_reads_row_then_backfill_then_replays_what_came_after(self):
        self.emit("message.appended", protocol.message("user", "fix it"))
        self.emit("message.appended", protocol.message("assistant", "on it"))
        self.emit("approval.requested", {"request_id": "r1", "prompt": "Run tests?"})
        status, data = self.srv.request(
            "GET", "/v1/sessions/claude%3As1/messages?limit=500", headers=self.hdr)
        messages = json.loads(data)["messages"]
        status, data = self.srv.request("GET", "/v1/sessions", headers=self.hdr)
        row = next(r for r in json.loads(data)["sessions"] if r["key"] == self.key)
        self.assertEqual((row["ownership"], row["attachable"]), ("attached", True))
        self.assertEqual([m["data"]["kind"] for m in messages], ["user", "assistant"])
        # The stream opens after the last message shown, so the approval
        # asked since then reaches the page.
        events = self.stream(messages[-1]["seq"])
        self.assertEqual([e["type"] for e in events], ["approval.requested", "journal.caught-up"])
        self.assertEqual(events[0]["data"]["request_id"], "r1")

    def test_a_waiting_approval_older_than_the_last_message_is_found(self):
        """Codex's app-server can journal the ask before its tool call: the
        Inbox names the waiting request_id, and a replay from 0 has its
        prompt, before the backfill's last message."""
        self.emit("message.appended", protocol.message("user", "edit it"))
        asked = self.emit("approval.requested", {"request_id": "r9", "prompt": "Edit a.py?"})
        last = self.emit("message.appended", protocol.message("tool_use", "", tool={
            "name": "apply_patch", "call_id": "c", "args_summary": "", "args_full": "\"x\""}))
        self.assertLess(asked["seq"], last["seq"])
        _, data = self.srv.request("GET", "/v1/inbox", headers=self.hdr)
        [item] = [i for i in json.loads(data)["items"] if i["key"] == self.key]
        self.assertEqual((item["status"], item["request_id"]), ("approval", "r9"))
        replay = self.stream(0)
        ask = next(e for e in replay if e["type"] == "approval.requested")
        self.assertEqual((ask["seq"], ask["data"]["prompt"]), (asked["seq"], "Edit a.py?"))

    def test_send_settles_by_client_id(self):
        from quiver.remote import inject
        fake = MagicMock()
        fake.send.return_value = inject.InjectResult(True, "sent", degraded=True)
        q = self.srv.state.bus.subscribe(self.key)
        with patch.object(inject, "injector_for", return_value=fake):
            status, data = self.srv.request(
                "POST", "/v1/sessions/claude%3As1/message",
                body={"text": "hi", "mode": "steer", "client_id": "c-1"}, headers=self.hdr)
            self.assertEqual(status, 202, data)
            ev = q.get(timeout=5)
        self.assertEqual(json.loads(data)["client_id"], "c-1")
        self.assertEqual(ev["type"], "command.result")
        self.assertEqual((ev["data"]["client_id"], ev["data"]["ok"], ev["data"]["degraded"]),
                         ("c-1", True, True))

    def test_approval_and_stop(self):
        from quiver.remote import inject
        fake = MagicMock()
        fake.approval.return_value = inject.InjectResult(True, "approved")
        fake.control.return_value = inject.InjectResult(True, "interrupted")
        with patch.object(inject, "injector_for", return_value=fake):
            status, _ = self.srv.request(
                "POST", "/v1/sessions/claude%3As1/approval",
                body={"request_id": "r1", "decision": "always"}, headers=self.hdr)
            self.assertEqual(status, 200)
            status, _ = self.srv.request(
                "POST", "/v1/sessions/claude%3As1/control", body={"op": "interrupt"}, headers=self.hdr)
            self.assertEqual(status, 200)
        resolved = [e for e in self.srv.state.journal.read_since(self.key, 0)[0]
                    if e["type"] == "approval.resolved"]
        self.assertEqual(resolved[0]["data"], {"request_id": "r1", "decision": "always"})
        fake.control.assert_called_once()

    def test_every_route_wants_the_device_credential(self):
        for method, action, body in (("GET", "messages", None), ("GET", "events?last=0", None),
                                     ("POST", "message", {"text": "x"}), ("POST", "approval", {}),
                                     ("POST", "control", {"op": "interrupt"})):
            status, _ = self.srv.request(method, f"/v1/sessions/claude%3As1/{action}", body=body)
            self.assertEqual(status, 401, action)


class TestChatObserved(HubBase):
    """An observed session: every write but Attach is 409 not_attached,
    which the chat answers by locking its composer."""

    def test_writes_refused_until_attached(self):
        auth = self.auth()
        for action, body in (("message", {"text": "hi", "client_id": "c"}),
                             ("approval", {"request_id": "r", "decision": "allow"}),
                             ("control", {"op": "interrupt"})):
            status, data = self.srv.request("POST", f"/v1/sessions/claude%3As1/{action}",
                                            body=body, headers=auth)
            self.assertEqual(status, 409, action)
            self.assertEqual(json.loads(data)["error"]["code"], "not_attached")

    def test_attach_refuses_a_session_mid_turn(self):
        # The test catalog reads every session as active.
        status, data = self.srv.request("POST", "/v1/sessions/claude%3As1/control",
                                        body={"op": "attach"}, headers=self.auth())
        self.assertEqual(status, 409, data)
        self.assertEqual(json.loads(data)["error"]["code"], "not_idle")


class TestChatOwnerDevice(OwnerBase):
    """The hub owner's own tailnet device has no bearer: the chat's
    requests carry app.js's X-Quiver-Pair header and get in on that."""

    def test_reads_stream_and_writes_without_pairing(self):
        headers = {"X-Forwarded-For": _OWNER_IP, "X-Forwarded-Proto": "https", "X-Quiver-Pair": "1"}
        self.assertEqual(self.get("/v1/sessions/claude%3As1/messages?limit=500")[0], 200)
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=5)
        try:
            conn.request("GET", "/v1/sessions/claude%3As1/events?last=0", headers=headers)
            self.assertEqual(conn.getresponse().status, 200)
        finally:
            conn.close()
        # In, and refused only by the session's ownership.
        status, data = self.srv.request("POST", "/v1/sessions/claude%3As1/message",
                                        {"text": "hi"}, headers=headers)
        self.assertEqual((status, json.loads(data)["error"]["code"]), (409, "not_attached"))
        headers.pop("X-Quiver-Pair")
        self.assertEqual(self.srv.request("GET", "/v1/sessions/claude%3As1/messages",
                                          headers=headers)[0], 401)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestChatScript(unittest.TestCase):
    """pages/chat.js's pure helpers, run under node with the globals it
    borrows from app.js stubbed."""

    def run_js(self, body):
        prelude = (
            "const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')"
            ".replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&#39;');\n"
            "const safeHref = (u) => /^(https?:\\/\\/|mailto:)/i.test(String(u || '').trim()) ? esc(String(u).trim()) : '';\n"
            "globalThis.window = { addEventListener() {} };\n"
            "const registered = {};\n"
            "globalThis.document = { addEventListener(t, f) { registered[t] = f; } };\n"
            "globalThis.location = { hash: '' };\n")
        source = (STATIC / "pages" / "chat.js").read_text(encoding="utf-8")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(prelude + source + "\nconst T = SessionChat._test;\n"
                    + "(async () => {" + body + "})()"
                    + ".then(r => process.stdout.write(JSON.stringify(r)));\n")
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_reconnects_back_off_to_half_a_minute(self):
        out = self.run_js("return [0, 1, 2, 3, 4, 5, 9, 60].map(T.retryDelay);")
        self.assertEqual(out, [2000, 4000, 8000, 16000, 30000, 30000, 30000, 30000])

    def test_a_hidden_tab_waits_and_showing_it_reconnects(self):
        """A dropped stream retries on a timer that does nothing while the
        tab is hidden; showing the tab reconnects at once, and failures in
        a row wait longer."""
        out = self.run_js(r"""
const timers = [], listeners = {};
let fetches = 0;
globalThis.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
globalThis.clearTimeout = () => {};
globalThis.fetch = () => { fetches++; return Promise.reject(new Error("down")); };
globalThis.authHeaders = () => ({});
document.getElementById = () => null;
const settle = () => new Promise((r) => setImmediate(r));
// The visibility handler chat.js registered at load.
const vis = registered.visibilitychange;
Object.assign(T.chat, { key: "k", opened: true, gen: 1 });
T.openStream(1, 0); await settle();
document.hidden = true; document.visibilityState = "hidden";
timers[0].fn(); await settle();                       // hidden: no request
const hiddenFetches = fetches;
document.hidden = false; document.visibilityState = "visible";
vis(); await settle();                                // shown: reconnects now
timers[1].fn(); await settle();                       // visible: the retry runs
return { fetches, hiddenFetches, delays: timers.map((t) => t.ms) };
""")
        self.assertEqual(out, {"fetches": 3, "hiddenFetches": 1, "delays": [2000, 2000, 4000]})

    RESYNC = r"""
let answer; const asked = [];
globalThis.api = (path) => {
  asked.push(path);
  if (path === "/v1/sessions") return Promise.resolve({ status: 200, data: { sessions: [] } });
  return new Promise((r) => { answer = r; });
};
const card = { kind: "open approval card" }, appended = [];
const log = { innerHTML: "old history", querySelectorAll: () => [card], appendChild: (el) => appended.push(el) };
document.getElementById = (id) => (id === "chat-log" ? log : null);
globalThis.setTimeout = () => 1; globalThis.clearTimeout = () => {};
const frame = (ev) => "data: " + JSON.stringify(ev);
Object.assign(T.chat, { key: "k", gen: 1, seq: 500 });
T.handleFrame("k", frame({ type: "journal.truncated", seq: 3 }));
const during = { seq: T.chat.seq, asked: asked.slice(), log: log.innerHTML };
T.handleFrame("k", frame({ type: "journal.caught-up", seq: 3 }));
T.handleFrame("k", frame({ type: "session.status", seq: 1, data: {} }));
T.handleFrame("k", frame({ type: "message.appended", seq: 2, data: {} }));
T.handleFrame("k", frame({ type: "approval.requested", seq: 1, data: { request_id: "r" } }));
T.handleFrame("k", frame({ type: "message.appended", seq: 4, data: {} }));
const heldWhileReading = T.chat.held.length;
T.chat.revealTimer = 1;  // keep what is queued in chat.pending, unrevealed
answer(ANSWER);
await new Promise((r) => setImmediate(r));
return { during, heldWhileReading, held: T.chat.held, seq: T.chat.seq, log: log.innerHTML,
         kept: appended.length, asked, queued: T.chat.pending.map((e) => e.type + ":" + e.seq) };
"""

    def test_a_truncated_journal_takes_its_lower_seq_and_refetches(self):
        """A hub whose journal was wiped sends journal.truncated with its
        head below this view's cursor: the view adopts it, reads
        /messages again, holds what streams meanwhile, then applies all
        but the messages the redraw shows; an unanswered approval card
        survives the redraw and the session row is read again."""
        out = self.run_js(self.RESYNC.replace("ANSWER", "{ status: 200, data: { messages: [] } }"))
        self.assertEqual(out["during"], {"seq": 3, "asked": ["/v1/sessions/k/messages?limit=500"], "log": "old history"})
        self.assertEqual(out["heldWhileReading"], 4)
        self.assertIsNone(out["held"])
        self.assertEqual(out["seq"], 3)
        self.assertEqual((out["log"], out["kept"]), ("", 1))
        self.assertEqual(out["queued"], ["session.status:1", "approval.requested:1", "message.appended:4"])
        self.assertEqual(out["asked"][-1], "/v1/sessions")

    def test_a_failed_refetch_keeps_the_history(self):
        out = self.run_js(self.RESYNC.replace("ANSWER", "{ status: 0, data: null }"))
        self.assertEqual((out["log"], out["kept"], out["seq"]), ("old history", 0, 3))
        self.assertEqual(out["queued"], ["session.status:1", "approval.requested:1", "message.appended:4"])

    def test_an_approval_prompt_is_plain_text(self):
        """The prompt comes from the agent's pane: links, images, headings
        and HTML in it show as typed, in monospace with spacing kept."""
        out = self.run_js(r"""
const div = { dataset: {}, innerHTML: "", classList: { toggle() {} },
  querySelector: () => ({ setAttribute() {} }), querySelectorAll: () => [] };
T.approvalCard(div, { request_id: "r", options: [],
  text: "# Run it?\n  [click](javascript:alert(1)) ![x](http://evil.test/p.png) <b>hi</b>" });
return div.innerHTML;
""")
        prompt = out[out.index('<div class="text approval-prompt">'):]
        prompt = prompt[:prompt.index("</div>")]
        self.assertIn("# Run it?\n  [click](javascript:alert(1)) ![x](http://evil.test/p.png) &lt;b&gt;hi&lt;/b&gt;", prompt)
        for rendered in ("<a ", "<img", "<h", "<b>", "<strong"):
            self.assertNotIn(rendered, prompt)

    def test_bare_urls_keep_their_underscores(self):
        """A bare URL goes back after emphasis runs, so `__init__` in a
        path stays in the href and the text, never <strong>."""
        url = "https://github.com/a/b/blob/main/pkg/__init__.py"
        out = self.run_js("return T.md(" + json.dumps(f"see {url} and **bold**") + ");")
        self.assertEqual(out, f'<p>see <a href="{url}" target="_blank" rel="noopener">{url}</a>'
                              " and <strong>bold</strong></p>")

    def test_writes_go_through_app_js_apipost(self):
        """chat.js has a send() of its own (the composer's): its requests
        must reach app.js's apiPost, not call that handler again."""
        out = self.run_js(
            "const calls = []; globalThis.apiPost = async (path, body) => { calls.push([path, body]); return { status: 200, data: {} }; };"
            " const r = await T.post('/v1/sessions/k/send', { text: 'hi' }); return { calls, status: r.status };")
        self.assertEqual(out, {"calls": [["/v1/sessions/k/send", {"text": "hi"}]], "status": 200})

    def test_markdown_escapes_then_restores_code_and_bold(self):
        out = self.run_js(
            "return [T.md('<img src=x onerror=alert(1)> **bold** `a<b`'),"
            " T.md('before\\n```python\\nx = 1\\n```\\nafter'),"
            " T.md('```notalang\\nx = 1\\n```')];")
        self.assertEqual(out[0], "<p>&lt;img src=x onerror=alert(1)&gt; <strong>bold</strong> <code>a&lt;b</code></p>")
        # A known fence language tokenises the code; unknown tags stay plain.
        self.assertEqual(out[1], '<p>before</p><pre class="code"><code>x = <span class="tk-n">1</span></code></pre><p>after</p>')
        self.assertEqual(out[2], '<pre class="code"><code>x = 1</code></pre>')

    def test_code_keeps_its_markers(self):
        out = self.run_js(
            "return [T.md('```js\\nconst s = \"**keep**\"; // `tick`\\n```'),"
            " T.md('`**raw**` and **bold**')];")
        # In a fence the markers are string and comment text, not emphasis.
        self.assertEqual(out[0], '<pre class="code"><code><span class="tk-k">const</span> s = <span class="tk-s">&quot;**keep**&quot;</span>; <span class="tk-c">// `tick`</span></code></pre>')
        self.assertEqual(out[1], "<p><code>**raw**</code> and <strong>bold</strong></p>")

    def test_syntax_tokens_and_tool_args(self):
        """hl() colours shell and file code, readableArgs unwraps a
        command, and toolOutLang only claims a language for read-ish
        calls — the same rules ToolDisplay.swift pins."""
        out = self.run_js("""
          return {
            bash: T.hl('sudo apt-get install -y jq # done', 'bash'),
            numbered: T.hl('  1\u2192import os\\n  2\u2192x = 1\\n  3\u2192print(x)', 'python'),
            cmd: T.readableArgs({ name: 'Bash', args_full: '{"command":"ls -la"}' }),
            argv: T.readableArgs({ name: 'Bash', args_full: '{"command":["bash","-lc","echo hi"]}' }),
            file: T.readableArgs({ name: 'Write', args_full: '{"file_path":"/a/x.py","content":"print(1)"}' }),
            read: T.toolOutLang({ name: 'Read', args_full: '{"file_path":"/a/x.css"}' }),
            edit: T.toolOutLang({ name: 'Edit', args_full: '{"file_path":"/a/x.css","old_string":"a","new_string":"b"}' }),
            jsonish: T.looksJson('{"a":1}'), prose: T.looksJson('file updated'),
            diff: T.toolDiff({ name: 'Edit', args_full: JSON.stringify({ file_path: 'a.py', old_string: 'x = 1', new_string: 'x = 2' }) }) };""")
        self.assertIn('<span class="tk-b">sudo</span>', out["bash"])
        self.assertIn('<span class="tk-m">-y</span>', out["bash"])
        self.assertIn('<span class="tk-c"># done</span>', out["bash"])
        self.assertIn('<span class="tk-m">  1\u2192</span>', out["numbered"])
        self.assertIn('<span class="tk-k">import</span>', out["numbered"])
        self.assertEqual(out["cmd"], {"text": "ls -la", "lang": "bash"})
        # An argv command keeps its argument boundaries under quoting.
        self.assertEqual(out["argv"], {"text": "bash -lc 'echo hi'", "lang": "bash"})
        self.assertEqual(out["file"], {"text": "print(1)", "lang": "python"})
        self.assertEqual(out["read"], "css")
        self.assertEqual(out["edit"], "")
        self.assertTrue(out["jsonish"])
        self.assertFalse(out["prose"])
        # A diff row keeps its add/del class, with the file's own tokens inside.
        self.assertIn('<div class="diff-line diff-add">+ x = <span class="tk-n">2</span></div>', out["diff"])

    def test_syntax_edge_cases_review_round(self):
        """The cases a review pass flagged: SQL folds case, xml maps to
        html, numbered output peels line numbers out of multiline
        tokens, and a diff row resumes an open comment."""
        out = self.run_js("""
          return {
            sql: T.hl('SELECT name FROM t WHERE x = TRUE', 'sql'),
            xml: T.hl('<a b="1">t</a>', 'xml'),
            numberedTpl: T.hl('  1\u2192s = `\\n  2\u2192mid\\n  3\u2192end`\\n  4\u2192x = 1', 'javascript'),
            cssSpaces: T.hl('a {\\n' + ' '.repeat(3000) + 'color: red;\\n}', 'css'),
            diff: T.toolDiff({ name: 'Edit', args_full: JSON.stringify({
              file_path: 'a.js',
              old_string: '/* a\\nmid comment\\nb */\\nx',
              new_string: '/* a\\nmid comment\\nb */\\ny' }) }) };""")
        self.assertIn('<span class="tk-k">SELECT</span>', out["sql"])
        self.assertIn('<span class="tk-k">FROM</span>', out["sql"])
        self.assertIn('<span class="tk-k">&lt;a</span>', out["xml"])
        # Each line-number prefix is a meta token even inside a string.
        self.assertEqual(out["numberedTpl"].count('<span class="tk-m">'), 4)
        self.assertIn('<span class="tk-s">mid</span>', out["numberedTpl"])
        # The long whitespace run is not rescanned per space (returns at all).
        self.assertIn('<span class="tk-b">color</span>', out["cssSpaces"])
        # Rows after the comment opener stay comments until it closes.
        self.assertIn('diff-ctx"><span class="tk-c"> mid comment</span>', out["diff"])
        self.assertIn('diff-ctx"><span class="tk-c"> b */</span>', out["diff"])
        self.assertIn('diff-del">- x', out["diff"])

    def test_inline_links_escapes_and_labels(self):
        """The phone hands inline text to AttributedString(markdown:), so
        <url> autolinks, & in a query, emphasis inside a link label and
        \\-escaped markers all behave the CommonMark way."""
        out = self.run_js(r"""
          return {
            angle: T.md('See <https://example.com/report> for details.'),
            query: T.md('Open https://x.test/?a=1&b=2 now'),
            label: T.md('[**Report**](https://example.com) and [a_b](x)'),
            escaped: T.md('\\_literal\\_ and \\*not bold\\* and C:\\\\d'),
          };""")
        self.assertEqual(
            out["angle"],
            '<p>See &lt;<a href="https://example.com/report" target="_blank" rel="noopener">'
            "https://example.com/report</a>&gt; for details.</p>")
        self.assertIn('href="https://x.test/?a=1&amp;b=2"', out["query"])
        self.assertEqual(
            out["label"],
            '<p><a href="https://example.com" target="_blank" rel="noopener">'
            "<strong>Report</strong></a> and <code>a_b</code></p>")
        self.assertEqual(
            out["escaped"], "<p>_literal_ and *not bold* and C:\\d</p>")

    def test_tables_render_as_tables(self):
        """The phone's Markdown.parse cases: header, separator aligns,
        escaped \\| cells, and what is not a table."""
        out = self.run_js(r"""
          return {
            html: T.md('| Resource | Action |\n|---|---|\n'
              + '| Cloud Scheduler `toolbox-v2-db-sync` | sync |\n'
              + '| Remove the `*-dsn` secrets \\| after revoking. | revoke |'),
            aligns: T.separatorAlignments('| :--- | :---: | ---: |'),
            rule: T.separatorAlignments('---'),
            notSep: T.separatorAlignments('| a | b |'),
            cells: T.cells('| `C:\\Users\\alice` | \\d+ \\| x |'),
          };""")
        self.assertIn("<th>Resource</th><th>Action</th>", out["html"])
        self.assertIn("<td>Cloud Scheduler <code>toolbox-v2-db-sync</code></td>", out["html"])
        self.assertIn("<td>Remove the <code>*-dsn</code> secrets | after revoking.</td>", out["html"])
        self.assertEqual(out["aligns"], ["l", "c", "r"])
        self.assertIsNone(out["rule"], "a lone rule is not a table separator")
        self.assertIsNone(out["notSep"])
        self.assertEqual(out["cells"], ["`C:\\Users\\alice`", "\\d+ | x"])

    def test_table_is_bounded(self):
        """A malformed or huge table can't build millions of cells: 24
        columns and 200 rows, with a note for the rest."""
        out = self.run_js(r"""
          const wide = '| ' + Array.from({length: 30}, (_, i) => 'h' + i).join(' | ') + ' |\n'
            + '|' + '---|'.repeat(30) + '\n| ' + Array.from({length: 30}, (_, i) => 'c' + i).join(' | ') + ' |';
          const deep = '| a |\n|---|\n' + '| 1 |\n'.repeat(205);
          return [T.md(wide), T.md(deep)];""")
        self.assertIn("6 more columns not shown", out[0])
        self.assertIn("5 more rows not shown", out[1])
        self.assertNotIn("c24", out[0])

    def test_headings_lists_quotes_rules_and_code(self):
        """Markdown.parse's structural test, ported from the phone suite."""
        out = self.run_js(
            "return T.mdBlocks('## Plan\\n1. First\\n2. Second\\n  continued\\n"
            "- nested parent\\n  - child\\n> quoted\\n---\\n"
            "```swift\\nlet x = 1\\n```\\n#not a heading');")
        self.assertEqual(out, [
            {"t": "h", "level": 2, "text": "Plan"},
            {"t": "li", "indent": 0, "marker": "1.", "text": "First"},
            {"t": "li", "indent": 0, "marker": "2.", "text": "Second\ncontinued"},
            {"t": "li", "indent": 0, "marker": "•", "text": "nested parent"},
            {"t": "li", "indent": 1, "marker": "•", "text": "child"},
            {"t": "quote", "text": "quoted"},
            {"t": "hr"},
            {"t": "code", "lang": "swift", "code": "let x = 1"},
            {"t": "p", "text": "#not a heading"},
        ])

    def test_heading_text_keeps_its_hashes(self):
        out = self.run_js(
            "return [T.heading('## C#'), T.heading('## #123'), T.heading('## Title ##'),"
            " T.heading('#'), T.heading('###')];")
        self.assertEqual(out, [[2, "C#"], [2, "#123"], [2, "Title"], None, None])

    def test_blocks_indented_under_a_list_item(self):
        """A fence or table indented under a list item parses as its own
        block, and a fence's lines lose the fence's indent."""
        out = self.run_js(
            "return T.mdBlocks('1. Run\\n   ```\\n   make test\\n   ```\\n"
            "2. Then\\n   | a | b |\\n   |---|---|\\n   | 1 | 2 |');")
        self.assertEqual(len(out), 4)
        self.assertEqual(out[0], {"t": "li", "indent": 0, "marker": "1.", "text": "Run"})
        self.assertEqual(out[1], {"t": "code", "lang": "", "code": "make test"})
        self.assertEqual(out[2]["t"], "li")
        self.assertEqual(out[3]["t"], "table")
        self.assertEqual(out[3]["rows"], [["1", "2"]])

    def test_agent_coded_text_folds_to_notes(self):
        """<json-render> views and lone images become note chips; inline
        images read as *Image: alt*; unclosed fences still render."""
        out = self.run_js(r"""
          return {
            rendered: T.md('<json-render>{"root":"d","elements":{}}</json-render>'),
            trailing: T.mdBlocks('<json-render>{}</json-render> Saved successfully.'),
            underItem: T.mdBlocks('- Result\n  <json-render>{}</json-render>\n- Chart\n  ![c](c.png)'),
            img: T.md('Done.\n\n![Backfill outcomes](/Users/k/Downloads/outcomes.png)'),
            inlineImg: T.inlineImagesAsText('see ![chart](a.png) here'),
            codeImg: T.inlineImagesAsText('write `![chart](a.png)` or ![b](b.png)'),
            escImg: T.inlineImagesAsText('literal \\![x](y.png)'),
            two: T.imageLine('![first](a.png)![second](b.png)'),
            tasks: T.md('- [ ] **Privacy (your call).**\n- [x] Shipped'),
            fence: T.mdBlocks('```\nhalf'),
          };""")
        self.assertIn("Rendered view", out["rendered"])
        self.assertEqual(out["trailing"], [
            {"t": "rendered", "body": "<json-render>{}</json-render>"},
            {"t": "p", "text": "Saved successfully."}])
        self.assertEqual(out["underItem"], [
            {"t": "li", "indent": 0, "marker": "•", "text": "Result"},
            {"t": "rendered", "body": "<json-render>{}</json-render>"},
            {"t": "li", "indent": 0, "marker": "•", "text": "Chart"},
            {"t": "img", "alt": "c", "src": "c.png"}])
        self.assertIn("Backfill outcomes", out["img"])
        self.assertIn("outcomes.png · on the machine", out["img"])
        self.assertEqual(out["inlineImg"], "see *Image: chart* here")
        self.assertEqual(out["codeImg"], "write `![chart](a.png)` or *Image: b*")
        self.assertEqual(out["escImg"], "literal \\![x](y.png)")
        self.assertIsNone(out["two"])
        self.assertIn('<input type="checkbox" disabled>', out["tasks"])
        self.assertIn('<input type="checkbox" disabled checked>', out["tasks"])
        self.assertEqual(out["fence"], [{"t": "code", "lang": "", "code": "half"}])

    def test_local_file_links_show_as_file_names(self):
        """A link to the agent's own files opens nothing on the web
        either: its label shows as a file name (the phone's code-intent
        rule), while web links stay links."""
        out = self.run_js(
            "return T.md('Read [AGENTS.md](/Users/k/.quiver/AGENTS.md), "
            "[ai.py](finance/ai.py) and [PR](https://github.com/x/y/pull/1). "
            "Plus https://example.com/a plain.');")
        self.assertIn("<code>AGENTS.md</code>", out)
        self.assertIn("<code>ai.py</code>", out)
        self.assertIn('<a href="https://github.com/x/y/pull/1" target="_blank" rel="noopener">PR</a>', out)
        self.assertIn('<a href="https://example.com/a" target="_blank" rel="noopener">https://example.com/a</a>', out)

    def test_codex_patches_from_the_decoder_render_as_diffs(self):
        """args_full exactly as tail.py journals a codex apply_patch: a
        custom tool whose input is the raw patch text."""
        from quiver.remote import tail
        patch_text = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-old\n+new\n*** End Patch"
        [msg] = tail._decode_codex({"type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "apply_patch", "call_id": "c1", "input": patch_text}})
        out = self.run_js(
            "return [T.toolDiff(" + json.dumps(msg["tool"]) + "),"
            " T.toolDiff({ name: 'apply_patch', args_full: JSON.stringify({ input: " + json.dumps(patch_text) + " }) })];")
        for html in out:
            self.assertIn('<div class="diff-file">src/a.py</div>', html)
            self.assertIn('<div class="diff-line diff-del">-old</div><div class="diff-line diff-add">+new</div>', html)
            self.assertIn('<div class="diff-line diff-meta">*** End Patch</div>', html)

    def test_edit_tools_render_as_diffs(self):
        out = self.run_js("""
          const edit = T.toolDiff({ name: "Edit", args_full: JSON.stringify(
            { file_path: "a.py", old_string: "x\\ny\\nz", new_string: "x\\nY\\nz" }) });
          const write = T.toolDiff({ name: "Write", args_full: JSON.stringify({ file_path: "<b>.txt", content: "hi" }) });
          const patch = T.toolDiff({ name: "apply_patch", args_full: JSON.stringify({ patch: "@@ h\\n-a\\n+b" }) });
          return { edit, write, patch,
            bash: T.toolDiff({ name: "Bash", args_full: "{}" }),
            junk: T.toolDiff({ name: "Edit", args_full: "not json" }),
            lines: T.diffLines("a\\nb\\nc", "a\\nB\\nc") };""")
        self.assertIn('<div class="diff-line diff-del">- y</div><div class="diff-line diff-add">+ Y</div>', out["edit"])
        self.assertIn('<div class="diff-file">a.py</div>', out["edit"])
        self.assertIn("&lt;b&gt;.txt", out["write"])
        self.assertIn('<div class="diff-line diff-meta">@@ h</div>', out["patch"])
        self.assertIsNone(out["bash"])
        self.assertIsNone(out["junk"])
        self.assertEqual(out["lines"], {"ctxTop": ["a"], "del": ["b"], "add": ["B"], "ctxBot": ["c"]})

    def test_page_preview_targets(self):
        """Which tool calls get Preview and the route path each asks for:
        the cases PagePreviewTests.swift pins for the phone."""
        out = self.run_js(r"""
          const call = (name, args) => ({ name, args_full: typeof args === "string" ? args : JSON.stringify(args) });
          const files = (c) => T.pageFiles(c);
          const targets = (c, cwd) => T.pageTargets(T.pageFiles(c), cwd);
          const patch = ["*** Begin Patch", "*** Add File: site/index.html", "+<h1>hi</h1>",
            "*** Update File: src/app.py", "@@", "-a", "+b", "*** Update File: site/old.htm",
            "*** Move to: site/new.htm", "*** Delete File: site/gone.html",
            "*** Add File: site/index.html", "*** End Patch"].join("\n");
          return {
            rel: [T.relativePath("/Users/k/proj/out/mock.html", "/Users/k/proj"),
                  T.relativePath("/Users/k/proj/mock.html", "/Users/k/proj/"),
                  T.relativePath("/Users/k//proj/./out/mock.html", "/Users/k/proj"),
                  T.relativePath("./out/a.html", "/w"), T.relativePath("a.svg", null),
                  T.relativePath("/private/tmp/w/a.html", "/tmp/w"),
                  T.relativePath("/var/folders/x/a.html", "/private/var/folders/x")],
            refused: [T.relativePath("/Users/k/other/mock.html", "/Users/k/proj"),
                      T.relativePath("/Users/k/project2/mock.html", "/Users/k/proj"),
                      T.relativePath("/Users/k/proj", "/Users/k/proj"),
                      T.relativePath("/Users/k/proj/../secret.html", "/Users/k/proj"),
                      T.relativePath("/Users/k/proj/a.html", null),
                      T.relativePath("/Users/k/proj/a.html", "proj"),
                      T.relativePath("../a.html", "/w"), T.relativePath("out/../../a.html", "/w"),
                      T.relativePath("~/proj/a.html", "/Users/k"), T.relativePath("", "/w"),
                      T.relativePath("a\0.html", "/w")],
            isPage: ["out/Mock.HTML", "a.htm", "/x/logo.svg", "a.html.bak", ".html", "html", "notes.md"].map(T.isPage),
            write: targets(call("Write", { file_path: "/w/out/mock.html", content: "<h1>hi</h1>" }), "/w"),
            edit: targets(call("Edit", { file_path: "/w/logo.SVG", old_string: "a", new_string: "b" }), "/w"),
            multi: targets(call("MultiEdit", { file_path: "/w/a.htm", edits: [] }), "/w"),
            others: [files(call("write", { filePath: "/w/a.html", content: "x" })),
                     files(call("write_file", { file_path: "/w/a.html", content: "x" })),
                     files(call("str_replace_based_edit_tool", { command: "create", path: "/w/a.html", file_text: "x" }))],
            none: [files(call("str_replace_based_edit_tool", { command: "view", path: "/w/a.html" })),
                   files(call("Write", { file_path: "/w/notes.md", content: "see a.html" })),
                   files(call("Read", { file_path: "/w/a.html" })),
                   files(call("Bash", { command: "open a.html" })),
                   files({ name: "Write", args_summary: "Write · a.html" }),
                   files(call("Write", '{"file_path":"/w/a.html"'))],
            patch: targets(call("apply_patch", JSON.stringify(patch)), "/w"),
            wrapped: targets(call("apply_patch", { input: patch }), "/w").length,
            outside: targets(call("Write", { file_path: "/tmp/elsewhere/a.html", content: "x" }), "/w"),
            url: T.previewUrl("claude:s1@mac", "out/a+b c&d.html"),
          };""")
        self.assertEqual(out["rel"], ["out/mock.html", "mock.html", "out/mock.html", "out/a.html",
                                      "a.svg", "a.html", "a.html"])
        self.assertEqual(out["refused"], [None] * 11)
        self.assertEqual(out["isPage"], [True, True, True, False, False, False, False])
        self.assertEqual(out["write"], ["out/mock.html"])
        self.assertEqual(out["edit"], ["logo.SVG"])
        self.assertEqual(out["multi"], ["a.htm"])
        self.assertEqual(out["others"], [["/w/a.html"]] * 3)
        self.assertEqual(out["none"], [[]] * 6)
        # The moved file previews where it landed; the repeated add once.
        self.assertEqual(out["patch"], ["site/index.html", "site/new.htm"])
        self.assertEqual(out["wrapped"], 2)
        self.assertEqual(out["outside"], [])
        # "+" goes as %2B: the hub's parse_qs reads a raw "+" as a space.
        self.assertEqual(out["url"], "/v1/sessions/claude%3As1%40mac/preview?path=out%2Fa%2Bb%20c%26d.html")

    def test_page_preview_is_locked(self):
        """The frame runs no script and loads nothing: an empty sandbox
        and the hub's CSP as the document's first element, ahead of any
        tag that could load, wherever the page's own text starts; the
        page's frame sits in a wrapper of our own whose policy stops it
        navigating itself."""
        out = self.run_js(r"""
          const enc = (s) => new TextEncoder().encode(s);
          const doc = (kind, s) => T.pageDocument(kind, enc(s));
          const page = '<!doctype html><a target="_self" href="https://x.test/">go</a>';
          const svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><image href="http://x.test/a.png"/></svg>';
          return {
            csp: T.PREVIEW_CSP,
            attrs: T.previewFrameAttrs("<p>x</p>", "Preview of a.html"),
            doctype: doc("html", '<!DOCTYPE html>\n<html><head><link rel="stylesheet" href="http://x.test/a.css"></head></html>'),
            bare: doc("html", '<img src="http://x.test/a.png"><script>alert(1)</script>'),
            bom: doc("html", '﻿<!doctype html><p>x</p>'),
            lead: doc("html", '  \n<!doctype html><p>x</p>'),
            comment: doc("html", '<!--><img src="http://x.test/a.png">--><!doctype html>'),
            nbsp: doc("html", ' <!doctype html><img src="http://x.test/a.png">'),
            quoted: doc("html", '<!doctype html PUBLIC "x>y"><img src="https://x.test/a.png">'),
            svg: doc("svg", svg),
            svgB64: Buffer.from(svg).toString("base64"),
            wrapper: T.previewDocument("html", enc(page)),
            inner: esc(T.pageDocument("html", enc(page))),
            kinds: [T.previewKind("text/html; charset=utf-8", "a.svg"), T.previewKind("image/svg+xml", "a.html"),
                    T.previewKind(null, "a.SVG"), T.previewKind("", "a.htm")],
          };""")
        meta = f'<meta http-equiv="Content-Security-Policy" content="{out["csp"]}">'
        self.assertEqual(out["csp"], "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                     "font-src data:; media-src data:; base-uri 'none'; form-action 'none'")
        # No token at all: no allow-scripts, allow-same-origin, allow-forms,
        # allow-popups or allow-top-navigation. Sandbox comes before srcdoc.
        self.assertEqual(out["attrs"], {"sandbox": "", "referrerpolicy": "no-referrer",
                                        "title": "Preview of a.html", "srcdoc": "<p>x</p>"})
        self.assertEqual(list(out["attrs"])[0], "sandbox")
        # Straight after a doctype (standards mode kept), else first of all.
        self.assertTrue(out["doctype"].startswith("<!DOCTYPE html>" + meta + '<base target="_blank">\n<html>'))
        self.assertTrue(out["bare"].startswith(meta + '<base target="_blank"><img '))
        self.assertTrue(out["bom"].startswith("<!doctype html>" + meta))
        self.assertTrue(out["lead"].startswith("  \n<!doctype html>" + meta))
        # `<!-->` is a whole comment to the parser, and NBSP is not HTML
        # whitespace: either would put the policy after the <img>.
        self.assertTrue(out["comment"].startswith(meta))
        self.assertTrue(out["nbsp"].startswith(meta))
        # A doctype ends at its first ">", quotes or not (the tokenizer's
        # abrupt-doctype-public-identifier rule): the policy is a real
        # element after it, ahead of the <img>.
        self.assertTrue(out["quoted"].startswith('<!doctype html PUBLIC "x>' + meta + '<base target="_blank">y"><img'))
        # The wrapper is ours alone: its policy first, then one frame with
        # the same empty sandbox holding the page, escaped as srcdoc.
        self.assertTrue(out["wrapper"].startswith('<!doctype html><html><head><meta charset="utf-8">' + meta))
        self.assertIn('<iframe sandbox="" referrerpolicy="no-referrer" title="Page" srcdoc="'
                      + out["inner"] + '"></iframe></body></html>', out["wrapper"])
        self.assertEqual(out["wrapper"].count("<iframe"), 1)
        self.assertNotIn("x.test", out["wrapper"].replace(out["inner"], ""))
        # An SVG is an <img> of a data: URL: its script and link never run.
        self.assertIn(meta, out["svg"])
        self.assertIn(f'<img alt="" src="data:image/svg+xml;base64,{out["svgB64"]}">', out["svg"])
        self.assertNotIn("<script", out["svg"])
        self.assertNotIn("x.test", out["svg"])
        self.assertEqual(out["kinds"], ["html", "svg", "svg", "html"])

    def test_preview_messages(self):
        out = self.run_js("""
          const m = (status, code, message) => T.previewMessage(status, code ? { error: { code, message } } : null);
          return [m(404, "not_found", "unknown route"), m(404, "not_found", "no such file"),
                  m(413, "too_large", ""), m(422, "preview_unavailable", ""), m(0), m(500, "boom", "it broke"),
                  m(415)];""")
        self.assertEqual(out, [
            "Update the hub to preview pages here.", "That file isn't on disk any more.",
            "This file is too big to preview (the hub's limit is 2 MB).",
            "Preview works only for sessions on the hub's own machine for now.",
            "Couldn't reach the hub. Check the connection, then try again.", "it broke",
            "Only .html, .htm and .svg files preview."])

    def test_link_cards(self):
        """Web links in a reply get cards: http(s) only, distinct, at
        most three, none from code; the card opens a new tab with no
        opener or referrer."""
        out = self.run_js(r"""
          const urls = (t) => T.linkUrls(t).map((u) => u.href);
          return {
            mixed: urls("See [the page](https://www.claude.ai/code/artifact/abc?x=1&y=2) and "
              + "https://example.com, again https://example.com, mailto:a@b.c, `https://code.test/x`\n"
              + "```\nhttps://fence.test\n```\n[file](src/a.py)"),
            many: urls("https://a.test/1 https://b.test/2 https://c.test/3 https://d.test/4"),
            none: urls("no links here"),
            host: [T.linkHost(new URL("https://www.claude.ai/x")), T.linkHost(new URL("http://example.com"))],
            path: [T.linkPath(new URL("https://claude.ai/code/x?y=1")), T.linkPath(new URL("https://claude.ai/"))],
            html: T.linkCardsHtml('Go to https://example.com/a?q="x"'),
            empty: T.linkCardsHtml("plain"),
          };""")
        self.assertEqual(out["mixed"], ["https://www.claude.ai/code/artifact/abc?x=1&y=2", "https://example.com/"])
        self.assertEqual(out["many"], ["https://a.test/1", "https://b.test/2", "https://c.test/3"])
        self.assertEqual(out["none"], [])
        self.assertEqual(out["host"], ["claude.ai", "example.com"])
        self.assertEqual(out["path"], ["/code/x?y=1", ""])
        self.assertIn('target="_blank" rel="noopener noreferrer"', out["html"])
        self.assertIn('<span class="link-card-host">example.com</span>', out["html"])
        self.assertNotIn('"x"', out["html"])
        self.assertEqual(out["empty"], "")

    def test_frames_pacing_and_routes(self):
        out = self.run_js("""
          return {
            frame: T.parseFrame('id: 4\\nevent: x\\ndata: {"type":"a",\\ndata: "seq":4}'),
            beat: T.parseFrame(': ka'), bad: T.parseFrame('data: {nope'),
            delays: [T.revealDelay(0), T.revealDelay(1), T.revealDelay(20), T.revealDelay(25)],
            keys: [SessionChat.keyFromHash('#/sessions?chat=claude%3As1%40mac'),
                   SessionChat.keyFromHash('#/sessions'), SessionChat.keyFromHash('#/overview?chat=x')],
          };""")
        self.assertEqual(out["frame"], {"type": "a", "seq": 4})
        self.assertIsNone(out["beat"])
        self.assertIsNone(out["bad"])
        self.assertEqual(out["delays"], [None, 180, 60, None])
        self.assertEqual(out["keys"], ["claude:s1@mac", None, None])

    def test_slash_command_menu_helpers(self):
        """The / menu's token, filtering and insertion, as the remote
        client's prompt menu has them."""
        out = self.run_js("""
          const rows = [
            { name: "clear", description: "Clear history", source: "builtin" },
            { name: "compact", source: "builtin", argument_hint: "[focus]" },
            { name: "review-pr", description: "Review a PR", source: "project" },
          ];
          return {
            tok: [T.slashToken("/co", 3), T.slashToken("/co", 1), T.slashToken("a/co", 3),
                  T.slashToken("/clear now", 6), T.slashToken("/clear n", 8), T.slashToken("", 0)],
            starts: T.filterCommands(rows, "c").map((c) => c.name),
            all: T.filterCommands(rows, "").length,
            holds: T.filterCommands(rows, "ear").map((c) => c.name),
            ins: T.insertCommand("/co", { start: 0, end: 3, query: "co" }, rows[1]),
            reuse: T.insertCommand("/co now", { start: 0, end: 3, query: "co" }, rows[0]),
            custom: T.insertCommand("/$", { start: 0, end: 2, query: "$" }, { name: "x", insert: "$x" }),
            insert: T.commandInsert({ name: "model", insert: "$fast " }),
            labels: [T.sourceLabel("builtin"), T.sourceLabel("plugin"), T.sourceLabel(""), T.sourceLabel("odd")],
            keys: [T.menuKey({ key: "Escape" }, 2, false), T.menuKey({ key: "ArrowDown" }, 2, true),
                   T.menuKey({ key: "ArrowUp" }, 2, true), T.menuKey({ key: "Enter" }, 2, false),
                   T.menuKey({ key: "Tab" }, 2, true), T.menuKey({ key: "Enter" }, 0, true),
                   T.menuKey({ key: "Enter" }, 0, false),
                   T.menuKey({ key: "Tab" }, 0, false), T.menuKey({ key: "ArrowDown" }, 0, true),
                   T.menuKey({ key: "Enter", shiftKey: true }, 2, true),
                   T.menuKey({ key: "Enter", isComposing: true }, 2, false), T.menuKey({ key: "x" }, 2, false)],
          };""")
        # A /token lives only at the start, inside the first word; the
        # caret on the word's end still counts, one space in ends it.
        self.assertEqual(out["tok"], [
            {"start": 0, "end": 3, "query": "co"},
            {"start": 0, "end": 3, "query": ""},
            None,
            {"start": 0, "end": 6, "query": "clear"},
            None,
            None,
        ])
        self.assertEqual(out["starts"], ["clear", "compact"])
        self.assertEqual(out["all"], 3)
        self.assertEqual(out["holds"], ["clear"])
        # A pick inserts "/name " with the caret past its space; a space
        # already after the word is reused instead of doubled.
        self.assertEqual(out["ins"], {"text": "/compact ", "caret": 9})
        self.assertEqual(out["reuse"], {"text": "/clear now", "caret": 7})
        # The hub's own insert text wins (a Codex skill is $name).
        self.assertEqual(out["custom"], {"text": "$x ", "caret": 3})
        self.assertEqual(out["insert"], "$fast ")
        self.assertEqual(out["labels"], ["Built-in", "Plugin", "", "Odd"])
        # Escape closes, arrows move only with rows, Enter picks, waits
        # only while the first fetch is out, then sends on an answered
        # empty list. Tab moves focus without rows, and Shift-Enter falls
        # through to the box's newline, menu open or not.
        self.assertEqual(out["keys"], ["dismiss", "next", "prev", "pick", "pick",
                                       "wait", None, None, None, None, None, None])

    def test_commands_warm_and_kept_lists(self):
        """The / menu warms on chat open and keeps lists per session: a
        reopen seeds at once, a failed refresh keeps the last good list,
        and an older in-flight answer can't overwrite a newer one."""
        out = self.run_js("""
          document.getElementById = () => null;
          const out = {};
          // First open warms the list and keeps it for the session.
          T.chat.key = "s1";
          globalThis.api = async () => ({ status: 200, data: { commands: [{ name: "compact" }] } });
          await T.warmCommands();
          out.first = T.cmdLists.s1.response.commands.map(c => c.name);
          T.resetMenus(); T.chat.key = "s2";
          globalThis.api = async () => ({ status: 200, data: { commands: [{ name: "review" }] } });
          await T.warmCommands();
          // Reopen s1: the kept list shows at once; the refresh fails
          // and the kept list survives beside its error.
          T.resetMenus(); T.chat.key = "s1";
          globalThis.api = async () => ({ status: 500, data: null });
          const warming = T.warmCommands();
          out.seeded = T.cmds.response.commands.map(c => c.name);
          await warming;
          out.kept = T.cmdLists.s1.response.commands.map(c => c.name);
          out.error = T.cmdLists.s1.error;
          out.liveRows = T.cmds.response.commands.map(c => c.name);
          // A request a reopen left behind can't replace the newer one.
          T.resetMenus(); T.chat.key = "s9";
          let release;
          globalThis.api = () => new Promise(r => {
            release = () => r({ status: 200, data: { commands: [{ name: "old" }] } });
          });
          const slow = T.loadCommands();
          T.resetMenus(); T.chat.key = "s9";
          globalThis.api = async () => ({ status: 200, data: { commands: [{ name: "new" }] } });
          await T.loadCommands();
          release(); await slow;
          out.newest = T.cmdLists.s9.response.commands.map(c => c.name);
          return out;
        """)
        self.assertEqual(out["first"], ["compact"])
        self.assertEqual(out["seeded"], ["compact"])
        self.assertEqual(out["kept"], ["compact"])
        self.assertNotEqual(out["error"], "")
        self.assertEqual(out["liveRows"], ["compact"])
        self.assertEqual(out["newest"], ["new"])

    def test_mention_token_and_insert_match_the_iphone(self):
        """The @ menu's token and insertion rules, case for case with
        PromptCompletion.swift's Mention."""
        out = self.run_js("""
          const tok = (t, c) => T.mentionToken(t, c === undefined ? t.length : c);
          return {
            tok: [tok("@src"), tok("look at @sr", 11), tok("mail a@b.com"), tok("@"),
                  tok("x @src/main.py y", 6), tok("@a b"), tok("hi"),
                  tok('@"My Doc'), tok('see @"My Documents/no'), tok('@"a b" c', 4),
                  tok('x@"a b'), tok('@"a\\nb')],
            file: T.insertMention("see @ma", { start: 4, end: 7 }, "src/main.py", false),
            reuse: T.insertMention("@ma now", { start: 0, end: 3 }, "main.py", false),
            dir: T.insertMention("@sr", { start: 0, end: 3 }, "src/", true),
            quoted: T.insertMention('@My', { start: 0, end: 3 }, "My Documents/a b.txt", false),
            qdir: T.insertMention('@My', { start: 0, end: 3 }, "My Documents", true),
            split: [T.splitPath("src/app/main.py"), T.splitPath("README.md"), T.splitPath("src/")],
          };""")
        self.assertEqual(out["tok"], [
            {"start": 0, "end": 4, "query": "src"},
            {"start": 8, "end": 11, "query": "sr"},
            None,  # an @ inside a word (an email) never starts one
            {"start": 0, "end": 1, "query": ""},
            # The caret mid-word: the token runs to the word's end.
            {"start": 2, "end": 14, "query": "src"},
            None,  # a space ends it
            None,
            {"start": 0, "end": 8, "query": "My Doc"},
            {"start": 4, "end": 21, "query": "My Documents/no"},
            {"start": 0, "end": 6, "query": "a "},  # runs to its closing quote
            None,  # @" inside a word
            None,  # a newline ends a quoted token
        ])
        # A file gets a trailing space with the caret after it; an existing
        # space is reused; a folder keeps the menu open with its slash.
        self.assertEqual(out["file"], {"text": "see @src/main.py ", "caret": 17})
        self.assertEqual(out["reuse"], {"text": "@main.py now", "caret": 9})
        self.assertEqual(out["dir"], {"text": "@src/", "caret": 5})
        self.assertEqual(out["quoted"], {"text": '@"My Documents/a b.txt" ', "caret": 24})
        self.assertEqual(out["qdir"], {"text": '@"My Documents/', "caret": 15})
        self.assertEqual(out["split"], [{"name": "main.py", "parent": "src/app"},
                                        {"name": "README.md", "parent": ""},
                                        {"name": "src", "parent": ""}])

    def test_mention_search_debounces_encodes_and_renders(self):
        """Typing @ asks the hub's files route once per query after the
        debounce, encodes the query, renders rows in the / menu's box and
        words the hub's refusals."""
        out = self.run_js("""
          const els = {
            "chat-text": { value: "", selectionStart: 0, selectionEnd: 0, disabled: false,
              setAttribute() {}, removeAttribute() {}, focus() {}, setSelectionRange(a, b) {
                this.selectionStart = a; this.selectionEnd = b; },
              style: {} },
            "chat-menu": { innerHTML: "", classList: { toggle() {} }, setAttribute(k, v) { this[k] = v; },
              querySelector() { return null; } },
          };
          document.getElementById = (id) => els[id] || null;
          Object.defineProperty(document, "activeElement", { get: () => els["chat-text"] });
          const input = els["chat-text"];
          const type = (v) => { input.value = v; input.selectionStart = input.selectionEnd = v.length; T.trackMenus(); };
          T.FILES.debounce = 0;
          T.chat.key = "claude:s 1";
          T.chat.row = { machine: "mini" };
          const urls = [];
          globalThis.api = async (u) => { urls.push(u); return { status: 200, data: { entries: [
            { path: "src/a b.py", kind: "file" }, { path: "src", kind: "dir" }] } }; };
          const out = {};
          type("see @s");
          out.searching = els["chat-menu"].innerHTML;
          type("see @s&x");
          await new Promise((r) => setTimeout(r, 20));
          out.urls = urls.slice();
          out.label = els["chat-menu"]["aria-label"];
          out.html = els["chat-menu"].innerHTML;
          out.rows = T.menu.rows.map((r) => r.path);
          // A caret move inside the same word doesn't search again.
          T.trackMenus();
          await new Promise((r) => setTimeout(r, 20));
          out.again = urls.length;
          // The Enter key picks the highlighted file.
          T.menu.active = 0;
          out.key = T.menuKey({ key: "Enter" }, T.menu.rows.length, false);
          // The satellite refusal, worded with the machine.
          globalThis.api = async () => ({ status: 409, data: { error: { code: "files_unavailable" } } });
          type("see @q");
          await new Promise((r) => setTimeout(r, 20));
          out.error = els["chat-menu"].innerHTML;
          out.errors = [T.filesError(404, null, ""), T.filesError(0, null, ""), T.filesError(500, null, "")];
          return out;
        """)
        self.assertIn("Searching files", out["searching"])
        # The first keystroke's search was replaced before it fired.
        self.assertEqual(out["urls"], ["/v1/sessions/claude%3As%201/files?q=s%26x&limit=50"])
        self.assertEqual(out["label"], "Files")
        self.assertIn('aria-label="a b.py, in src"', out["html"])
        self.assertIn('<span class="menu-sub">src</span>', out["html"])
        self.assertEqual(out["rows"], ["src/a b.py", "src"])
        self.assertEqual(out["again"], 1)
        self.assertEqual(out["key"], "pick")
        self.assertIn("File search isn&#39;t available for sessions on mini", out["error"])
        self.assertEqual(out["errors"], ["Update the hub to search files from here",
                                         "Can't reach the hub", "Couldn't search files"])

    def test_mention_search_drops_late_answers(self):
        """A newer query aborts the older request, and an older answer that
        lands after the newer one (or after the menu closed) changes
        nothing."""
        out = self.run_js("""
          const els = {
            "chat-text": { value: "", selectionStart: 0, selectionEnd: 0, disabled: false,
              setAttribute() {}, removeAttribute() {}, style: {} },
            "chat-menu": { innerHTML: "", classList: { toggle() {} }, setAttribute() {},
              querySelector() { return null; } },
          };
          document.getElementById = (id) => els[id] || null;
          Object.defineProperty(document, "activeElement", { get: () => els["chat-text"] });
          const input = els["chat-text"];
          const type = (v) => { input.value = v; input.selectionStart = input.selectionEnd = v.length; T.trackMenus(); };
          const tick = () => new Promise((r) => setTimeout(r, 10));
          T.FILES.debounce = 0;
          T.chat.key = "s1";
          T.chat.row = {};
          // Each request waits for its own release; the signal is kept.
          const reqs = {};
          globalThis.api = (u, opts) => new Promise((r) => {
            const q = new URLSearchParams(u.split("?")[1]).get("q");
            reqs[q] = { signal: opts && opts.signal,
              release: (path) => r({ status: 200, data: { entries: [{ path, kind: "file" }] } }) };
          });
          const out = {};
          type("@a");
          await tick();
          type("@ab");
          await tick();
          out.oldAborted = reqs.a.signal.aborted;
          out.newAborted = reqs.ab.signal.aborted;
          reqs.ab.release("ab.py");
          await tick();
          reqs.a.release("a.py");
          await tick();
          out.rows = T.menu.rows.map((r) => r.path);
          out.answered = T.files.answered;
          // An answer after the menu closed leaves it closed and empty.
          type("@abc");
          await tick();
          type("plain");
          reqs.abc.release("abc.py");
          await tick();
          out.closed = { open: T.menu.open, entries: T.files.entries.length, answered: T.files.answered,
                         aborted: reqs.abc.signal.aborted };
          return out;
        """)
        self.assertTrue(out["oldAborted"])
        self.assertFalse(out["newAborted"])
        self.assertEqual(out["rows"], ["ab.py"])
        self.assertEqual(out["answered"], "ab")
        self.assertEqual(out["closed"], {"open": False, "entries": 0, "answered": None, "aborted": True})


SESSIONS_JS = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/sessions.js"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestTranscriptSearchBox(unittest.TestCase):
    """sessions.js's search box under node: rows match on details in the
    browser, and on what was said through the hub's /v1/search."""

    ROWS = [
        {"key": "k1", "title": "Trim the export queue", "harness": "codex", "last_active_at": 2e9},
        {"key": "k2", "title": "Dark mode", "harness": "claude", "last_active_at": 2e9},
        {"key": "k3", "title": "Unrelated", "harness": "claude", "last_active_at": 2e9},
    ]

    def run_js(self, body, search_answer):
        prelude = (
            "const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;')"
            ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');\n"
            "const asked = [];\n"
            "globalThis.api = async (path) => { asked.push(path); return " + json.dumps(search_answer) + "; };\n"
            "globalThis.document = { getElementById: (id) => id === 'session-search' ? {} : null };\n"
            "globalThis.setTimeout = (f) => { f(); return 0; }; globalThis.clearTimeout = () => {};\n"
            "globalThis.Fmt = { logo: () => '', harnessName: (h) => h, modelName: (m) => m || '', tokens: String, dollars: String };\n")
        script = (prelude + SESSIONS_JS.read_text(encoding="utf-8")
                  + "\nrepaintKeepingFocus = () => {};\n"
                  + "sessionsPage.data = { sessions: " + json.dumps(self.ROWS) + " };\n"
                  + "sessionsPage.window = 'all';\n"
                  + "(async () => { const out = await (async () => {" + body + "})();"
                  + " process.stdout.write(JSON.stringify(out)); })();\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    ANSWER = {"status": 200, "data": {"indexed": True, "results": [
        {"key": "k2", "source": "index", "snippet": "the queue length is 5",
         "marked_snippet": "the \ue000queue\ue001 length is <5>"},
    ]}}

    def type_query(self, query):
        return ("sessionsPage.query = " + json.dumps(query) + "; scheduleTranscriptSearch();"
                " await new Promise((r) => setImmediate(r));")

    def test_a_transcript_hit_keeps_its_row_with_a_marked_snippet(self):
        out = self.run_js(self.type_query("queue") + """
            const kept = filteredSessions().kept.map((r) => r.key);
            return { kept, asked, row: sessionRow(sessionsPage.data.sessions[1]), note: searchNote(filteredSessions().kept) };
        """, self.ANSWER)
        self.assertEqual(out["kept"], ["k1", "k2"])
        self.assertEqual(out["asked"], ["/v1/search?q=queue&limit=200"])
        self.assertIn('<span class="session-snippet">the <mark>queue</mark> length is &lt;5&gt;</span>', out["row"])
        self.assertIn("2 sessions match, 1 of them on what was said.", out["note"])

    def test_a_late_answer_for_an_older_query_never_lands(self):
        # Two searches in flight; the older one answers last.
        out = self.run_js("""
            const pending = [];
            api = (path) => new Promise((resolve) => pending.push({ path, resolve }));
            const hit = (key) => ({ status: 200, data: { indexed: true, results: [
                { key, source: "index", snippet: "s", marked_snippet: "s" }] } });
            sessionsPage.query = "queue"; scheduleTranscriptSearch();
            sessionsPage.query = "queue length"; scheduleTranscriptSearch();
            pending[1].resolve(hit("k2"));
            await new Promise((r) => setImmediate(r));
            pending[0].resolve(hit("k3"));
            await new Promise((r) => setImmediate(r));
            return { asked: pending.map((p) => p.path), query: sessionsPage.search.query,
                     hits: [...sessionsPage.search.hits.keys()] };
        """, self.ANSWER)
        self.assertEqual(out["asked"], ["/v1/search?q=queue&limit=200",
                                        "/v1/search?q=queue%20length&limit=200"])
        self.assertEqual((out["query"], out["hits"]), ("queue length", ["k2"]))

    def test_a_trailing_space_keeps_the_search(self):
        out = self.run_js("""
            let timers = 0;
            setTimeout = (f) => { timers++; return 0; };
            sessionsPage.query = "queue"; scheduleTranscriptSearch();
            sessionsPage.query = "queue "; scheduleTranscriptSearch();
            return { timers, state: sessionsPage.search.state };
        """, self.ANSWER)
        self.assertEqual(out, {"timers": 1, "state": "pending"})

    def test_an_answer_after_leaving_is_asked_again_on_return(self):
        out = self.run_js("""
            document.getElementById = () => null;
            sessionsPage.query = "queue"; scheduleTranscriptSearch();
            await new Promise((r) => setImmediate(r));
            const left = sessionsPage.search.query;
            document.getElementById = (id) => id === "session-search" ? {} : null;
            scheduleTranscriptSearch();
            await new Promise((r) => setImmediate(r));
            return { left, asked: asked.length, hits: [...sessionsPage.search.hits.keys()] };
        """, self.ANSWER)
        self.assertEqual(out, {"left": "", "asked": 2, "hits": ["k2"]})

    def test_one_character_never_asks_the_hub(self):
        out = self.run_js(self.type_query("q") + "return asked;", self.ANSWER)
        self.assertEqual(out, [])

    def test_a_hub_without_an_index_says_how_to_turn_it_on(self):
        out = self.run_js(self.type_query("queue") + "return searchNote(filteredSessions().kept);",
                          {"status": 200, "data": {"indexed": False, "results": []}})
        self.assertIn('href="#/settings?section=experimental"', out)

    def test_a_failed_search_falls_back_to_details(self):
        out = self.run_js(self.type_query("trim") + """
            return { kept: filteredSessions().kept.map((r) => r.key), note: searchNote(filteredSessions().kept) };
        """, {"status": 0, "data": None})
        self.assertEqual(out["kept"], ["k1"])
        self.assertIn("Couldn't reach transcript search", out["note"])


FORMAT_JS = SESSIONS_JS.with_name("format.js")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSessionModelShown(unittest.TestCase):
    """The newest reply's model on the Sessions list rows and the chat
    header (#643), as the phone shows it: no provider path, no date stamp,
    nothing when unknown."""

    def run_js(self, body, with_sessions=True):
        fmt = FORMAT_JS.read_text(encoding="utf-8")
        prelude = (
            "const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;')"
            ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');\n"
            "globalThis.window = { addEventListener() {} };\n"
            "globalThis.document = { addEventListener() {}, getElementById: () => null };\n"
            "globalThis.location = { hash: '' };\n")
        # format.js declares `const Fmt`; run it in its own scope and hand
        # the result to the page scripts as the global they read.
        prelude += ("globalThis.Fmt = new Function(" + json.dumps(fmt)
                    + " + '\\nreturn Fmt;')();\n")
        if with_sessions:
            source = SESSIONS_JS.read_text(encoding="utf-8")
        else:
            source = (STATIC / "pages" / "chat.js").read_text(encoding="utf-8")
        script = (prelude + source + "\n(async () => { const out = await (async () => {"
                  + body + "})(); process.stdout.write(JSON.stringify(out)); })();\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_model_name_drops_provider_and_date_stamp(self):
        out = self.run_js("""
          const raw = ["claude-opus-4-5-20251101", "anthropic/claude-opus-4-5",
            "openrouter/nvidia/nemotron-3:free", "gpt-5.1-codex", "swe-2-max",
            "model-2025110", "-20251101", "  claude-opus-4-5  ", "", "   ", null, undefined, 7];
          return raw.map((m) => Fmt.modelName(m));""")
        self.assertEqual(out, [
            "claude-opus-4-5", "claude-opus-4-5", "nemotron-3:free",
            "gpt-5.1-codex",  # a version is not a date stamp
            "swe-2-max",
            "model-2025110",  # seven digits are kept
            "-20251101",      # never shortened to nothing
            "claude-opus-4-5", "", "", "", "", ""])

    def test_list_row_names_the_model_once(self):
        out = self.run_js("""
          const usage = (...models) => ({ tokens: 1, cost_usd: 0, models: models.map((model) => ({ model, tokens: 1 })) });
          const base = { key: "k", harness: "claude", title: "t", last_active_at: 2e9, location: "Local" };
          const sub = (r) => /<span class="session-sub">([^<]*)<\\/span>/.exec(sessionRow({ ...base, ...r }))[1];
          return {
            known: sub({ model: "claude-opus-4-5-20251101" }),
            unknown: sub({}),
            blank: sub({ model: "" }),
            deduped: sub({ model: "claude-opus-4-5-20251101", usage: usage("claude-opus-4-5-20251101") }),
            others: sub({ model: "anthropic/claude-opus-4-5", usage: usage("claude-sonnet-4-5", "claude-opus-4-5") }),
            ledgerOnly: sub({ usage: usage("claude-sonnet-4-5") }),
          };""")
        # Time and duration follow, so pull out just the model words.
        def models(sub):
            return [p for p in sub.split(" · ") if p.startswith(("claude", "gpt"))]
        self.assertEqual(models(out["known"]), ["claude-opus-4-5"])
        self.assertEqual(models(out["unknown"]), [])
        self.assertEqual(models(out["blank"]), [])
        self.assertEqual(models(out["deduped"]), ["claude-opus-4-5"])
        self.assertEqual(models(out["others"]), ["claude-opus-4-5, claude-sonnet-4-5"])
        self.assertEqual(models(out["ledgerOnly"]), ["claude-sonnet-4-5"])

    def test_chat_header_shows_the_model_after_the_folder(self):
        out = self.run_js("""
          const T = SessionChat._test;
          return {
            local: T.barSub({ harness: "claude", cwd: "/w/app", model: "claude-opus-4-5-20251101" }),
            remote: T.barSub({ harness: "codex", cwd: "/w/app", model: "openai/gpt-5.1-codex", remote: true, machine: "Mac mini" }),
            unknown: T.barSub({ harness: "claude", cwd: "/w/app" }),
            none: T.barSub(null),
          };""", with_sessions=False)
        self.assertEqual(out["local"], "Claude Code · /w/app · claude-opus-4-5")
        self.assertEqual(out["remote"], "Codex · /w/app · gpt-5.1-codex · Mac mini")
        self.assertEqual(out["unknown"], "Claude Code · /w/app")
        self.assertEqual(out["none"], "")

    def test_chat_reads_the_model_again_after_a_reply(self):
        """The row is read once at open and no event carries the model, so
        a finished turn (and a return to the tab) reads it again."""
        out = self.run_js("""
          const T = SessionChat._test;
          const out = {};
          T.chat.key = "s1"; T.chat.gen = 3;
          T.chat.row = { harness: "claude", status: "active" };
          const answer = (status, sessions) => { globalThis.api = async () => ({ status, data: { sessions } }); };
          // A hub that fails, or doesn't know the model, leaves the header.
          answer(503, null);
          await T.refreshModel();
          out.failed = T.chat.row.model || "";
          answer(200, [{ key: "s1" }]);
          await T.refreshModel();
          out.unknown = T.chat.row.model || "";
          // The opening reply names one; only the model is taken.
          answer(200, [{ key: "s1", model: "claude-opus-4-5-20251101", status: "done" }]);
          out.first = await T.refreshModel();
          out.status = T.chat.row.status;
          // A switch elsewhere changes it.
          answer(200, [{ key: "s1", model: "claude-sonnet-4-5" }]);
          out.switched = await T.refreshModel();
          // An answer landing after the chat moved on is dropped.
          let release;
          globalThis.api = () => new Promise((r) => {
            release = () => r({ status: 200, data: { sessions: [{ key: "s1", model: "late" }] } });
          });
          const slow = T.refreshModel();
          T.chat.gen = 4;
          release();
          await slow;
          out.late = T.chat.row.model;
          return out;""", with_sessions=False)
        self.assertEqual(out["failed"], "")
        self.assertEqual(out["unknown"], "")
        self.assertEqual(out["first"], "claude-opus-4-5-20251101")
        self.assertEqual(out["status"], "active")
        self.assertEqual(out["switched"], "claude-sonnet-4-5")
        self.assertEqual(out["late"], "claude-sonnet-4-5")


if __name__ == "__main__":
    unittest.main()
