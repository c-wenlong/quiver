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
        self.assertFalse(sessions.is_trivial("pi", None, True))

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
        self.assertEqual(self.srv.request("GET", "/app/sessions.js")[0], 200)
        status, body = self.srv.request("GET", "/app/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b"globalThis.SessionChat", body)
        status, body = self.srv.request("GET", "/app/pages.css")
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
            "globalThis.window = { addEventListener() {} };\n"
            "globalThis.document = { addEventListener() {} };\n"
            "globalThis.location = { hash: '' };\n")
        source = (STATIC / "pages" / "chat.js").read_text(encoding="utf-8")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(prelude + source + "\nconst T = SessionChat._test;\n"
                    + "process.stdout.write(JSON.stringify((() => {" + body + "})()));\n")
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_markdown_escapes_then_restores_code_and_bold(self):
        out = self.run_js(
            "return [T.md('<img src=x onerror=alert(1)> **bold** `a<b`'),"
            " T.md('before\\n```python\\nx = 1\\n```\\nafter')];")
        self.assertEqual(out[0], "&lt;img src=x onerror=alert(1)&gt; <strong>bold</strong> <code>a&lt;b</code>")
        self.assertEqual(out[1], 'before<pre class="code"><code>x = 1</code></pre>after')

    def test_code_keeps_its_markers(self):
        out = self.run_js(
            "return [T.md('```js\\nconst s = \"**keep**\"; // `tick`\\n```'),"
            " T.md('`**raw**` and **bold**')];")
        self.assertEqual(out[0], '<pre class="code"><code>const s = &quot;**keep**&quot;; // `tick`</code></pre>')
        self.assertEqual(out[1], "<code>**raw**</code> and <strong>bold</strong>")

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
        """The / menu's token, filtering and insertion, as the hub PWA's
        prompt menu has them."""
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


if __name__ == "__main__":
    unittest.main()


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
            "globalThis.Fmt = { logo: () => '', harnessName: (h) => h, tokens: String, dollars: String };\n")
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
