"""The web chat's Queued tray (#970): pages/chat.js's port of the
iPhone's tray, under node with a small DOM stub.

A Queue-mode send made while the agent works waits in a tray above the
composer; each row can be edited, removed or sent now through
``POST /v1/sessions/{key}/queue``, and the tray is read again from
``GET .../queue`` when the chat opens."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CHAT_JS = Path(__file__).resolve().parent.parent / "src" / "swe" / "webapp" / "static" / "pages" / "chat.js"

# Elements with the parts chat.js touches: classes, dataset, children,
# and the few selectors the tray and the send paths use.
PRELUDE = r"""
const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
  .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const safeHref = () => '';
globalThis.window = { addEventListener() {} };
globalThis.location = { hash: '' };
globalThis.CSS = { escape: (s) => s };
Object.defineProperty(globalThis, "crypto", { value: { randomUUID: (() => { let n = 0; return () => `c${++n}`; })() } });
function el(tag) {
  const e = { tagName: tag, children: [], dataset: {}, style: {}, parent: null, value: "", disabled: false,
    placeholder: "", textContent: "", _cls: new Set(), _html: "", _sub: {}, scrollTop: 0, scrollHeight: 0, clientHeight: 0,
    focus() {}, setSelectionRange() {} };
  e.classList = { add: (...c) => c.forEach((x) => e._cls.add(x)), remove: (...c) => c.forEach((x) => e._cls.delete(x)),
    contains: (c) => e._cls.has(c),
    toggle: (c, on) => { on = on === undefined ? !e._cls.has(c) : !!on; if (on) e._cls.add(c); else e._cls.delete(c); return on; } };
  Object.defineProperty(e, "className", { get: () => [...e._cls].join(" "),
    set: (v) => { e._cls = new Set(String(v).split(/\s+/).filter(Boolean)); } });
  Object.defineProperty(e, "innerHTML", { get: () => e._html, set: (v) => {
    e._html = v; e._sub = {};
    // Like the DOM, replacing the markup drops the element children.
    for (const c of e.children) c.parent = null;
    e.children = [];
  } });
  Object.defineProperty(e, "lastElementChild", { get: () => e.children[e.children.length - 1] || null });
  e.appendChild = (c) => { c.parent = e; e.children.push(c); return c; };
  e.remove = () => { if (e.parent) e.parent.children = e.parent.children.filter((x) => x !== e); e.parent = null; };
  const matches = (x, sel) => {
    const m = sel.match(/^((?:\.[\w-]+)*)(?:\[([\w-]+)(?:="([^"]*)")?\])?(?::not\(\.([\w-]+)\))?$/);
    if (!m) throw new Error("selector " + sel);
    const cls = m[1].split(".").filter(Boolean);
    if (!cls.every((c) => x._cls.has(c))) return false;
    if (m[2]) {
      const key = m[2].replace(/^data-/, "").replace(/-(\w)/g, (_, c) => c.toUpperCase());
      if (!(key in x.dataset)) return false;
      if (m[3] !== undefined && x.dataset[key] !== m[3]) return false;
    }
    return !(m[4] && x._cls.has(m[4]));
  };
  e.querySelectorAll = (sel) => e.children.filter((x) => matches(x, sel));
  e.querySelector = (sel) => {
    // A child drawn through innerHTML, by its class.
    const c = sel.match(/^\.([\w-]+)$/);
    if (c && e._html.includes(`class="${c[1]}"`)) return e._sub[c[1]] || (e._sub[c[1]] = el("div"));
    return e.querySelectorAll(sel)[0] || null;
  };
  return e;
}
const ids = {};
for (const id of ["chat-log", "chat-tray", "chat-editbar", "chat-text", "chat-send", "chat-working", "chat-mode",
  "chat-stop", "chat-banner"]) ids[id] = el("div");
ids["chat-mode"].value = "queue";
globalThis.document = { addEventListener() {}, createElement: (t) => el(t), getElementById: (id) => ids[id] || null,
  activeElement: null };
const posts = [];
let answer = () => ({ status: 202, data: { waiting: true } });
globalThis.apiPost = async (path, body) => { posts.push({ path, body }); return answer(path, body); };
let queueList = { status: 200, data: { sends: [] } };
globalThis.api = async (path) => path.endsWith("/queue") ? queueList : { status: 404, data: null };
globalThis.renderGate = () => {};
const tick = async () => { for (let i = 0; i < 4; i++) await new Promise((r) => setImmediate(r)); };
"""


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestQueuedTray(unittest.TestCase):
    def run_js(self, body):
        source = CHAT_JS.read_text(encoding="utf-8")
        script = (PRELUDE + source + "\nconst T = SessionChat._test;\n"
                  + "T.chat.key = 'claude:s1'; T.chat.gen = 1; T.chat.rowKnown = true;\n"
                  + "T.chat.row = { status: 'active', ownership: 'attached' };\n"
                  + "const tray = () => T.chat.tray.queued.map((q) => [q.cid, q.text, q.phase, q.note]);\n"
                  + "const bubbles = () => ids['chat-log'].children.map((b) => [b.dataset.clientId, b.className,"
                  + " (b._sub['send-note'] || {}).textContent]);\n"
                  + "(async () => {" + body + "})()"
                  + ".then((r) => process.stdout.write(JSON.stringify(r)))"
                  + ".catch((e) => { console.error(e); process.exit(1); });\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_only_queue_mode_mid_turn_on_the_hubs_own_sessions_joins_the_tray(self):
        out = self.run_js("""
            const active = { status: "active" };
            return [T.sendsToTray("queue", active), T.sendsToTray("steer", active),
                    T.sendsToTray("queue", { status: "done" }), T.sendsToTray("queue", { status: "active", remote: true }),
                    T.sendsToTray("queue", null)];""")
        self.assertEqual(out, [True, False, False, False, False])

    def test_merge_keeps_the_hubs_order_and_text(self):
        out = self.run_js("""
            const local = [
              { cid: "a", text: "old a", phase: "sendingNow", note: "" },
              { cid: "p", text: "posting", phase: "posting", note: "" },
              { cid: "gone", text: "left the hub", phase: "waiting", note: "" },
              { cid: "shown", text: "in the log", phase: "waiting", note: "" }];
            const sends = [{ client_id: "b", text: "from the phone" }, { client_id: "a", text: "edited a" },
              { client_id: "p", text: "posting" }, { client_id: "shown", text: "x" },
              { client_id: "echoed", text: "already out" }];
            const m = T.mergeQueue(local, [], sends, new Set(["shown"]), ["already out"]);
            return { queued: m.queued.map((q) => [q.cid, q.text, q.phase]), departed: m.departed.map((q) => q.cid), gone: m.gone };""")
        self.assertEqual(out["queued"], [["b", "from the phone", "waiting"], ["a", "edited a", "sendingNow"],
                                         ["p", "posting", "waiting"]])
        self.assertEqual(out["departed"], ["gone"])
        self.assertEqual(out["gone"], ["echoed"])

    def test_refusal_notes_match_the_iphone(self):
        out = self.run_js("""
            const e = (code, message = "") => ({ error: { code, message } });
            return [T.queueNote(409, e("not_waiting", "that message was already sent")),
                    T.queueNote(409, e("not_waiting", "that message was removed")),
                    T.queueNote(409, e("not_attached")), T.queueNote(409, e("queue_unsupported")),
                    T.queueNote(429, e("backlog_full")), T.queueNote(404, e("unknown_send")),
                    T.queueNote(0, null), T.queueNote(500, e("boom", "it broke"))];""")
        self.assertEqual(out, ["Already sent", "Already removed", "Attach to send now",
                               "Not available on this machine", "Too many waiting. Try again shortly",
                               "The hub lost this message", "Can't reach the hub", "it broke"])

    def test_a_queued_send_waits_in_the_tray_and_leaves_on_its_result(self):
        out = self.run_js("""
            const out = {};
            await T.sendText("run the tests", "queue");
            out.post = posts[0];
            out.tray = tray();
            out.log = bubbles().length;
            out.html = ids["chat-tray"].innerHTML;
            out.trayShown = !ids["chat-tray"].classList.contains("hidden");
            // It went out on idle: into the transcript, settled.
            T.commandResult({ op: "message", client_id: "c1", ok: true });
            out.after = tray();
            out.logAfter = bubbles();
            out.trayHidden = ids["chat-tray"].classList.contains("hidden");
            return out;""")
        self.assertEqual(out["post"], {"path": "/v1/sessions/claude%3As1/message",
                                       "body": {"text": "run the tests", "mode": "queue", "client_id": "c1"}})
        self.assertEqual(out["tray"], [["c1", "run the tests", "waiting", ""]])
        self.assertEqual(out["log"], 0)
        self.assertIn("1 message queued", out["html"])
        for label in ("Edit", "Remove", "Send now", "Remove all"):
            self.assertIn(label, out["html"])
        self.assertTrue(out["trayShown"])
        self.assertEqual(out["after"], [])
        # Settled: no longer pending, its "Sending…" note gone.
        self.assertEqual(out["logAfter"], [["c1", "msg user arrive", ""]])
        self.assertTrue(out["trayHidden"])

    def test_the_hubs_answer_decides_where_a_send_shows(self):
        out = self.run_js("""
            const out = {};
            // The page thought the agent worked; the hub sent it at once.
            answer = () => ({ status: 202, data: { waiting: false } });
            await T.sendText("one", "queue");
            out.idle = [tray(), bubbles()];
            // The page thought it idle; the hub held it.
            T.chat.row.status = "done";
            answer = () => ({ status: 202, data: { waiting: true } });
            await T.sendText("two", "queue");
            out.held = [tray(), bubbles().length];
            // Steer never waits in the tray.
            T.chat.row.status = "active";
            answer = () => ({ status: 202, data: { waiting: false } });
            await T.sendText("three", "steer");
            out.steer = [tray().length, bubbles().length];
            // A refused POST from the tray becomes a failed bubble.
            answer = () => ({ status: 409, data: { error: { code: "backlog_full", message: "too many messages waiting" } } });
            await T.sendText("four", "queue");
            out.failed = [tray().length, bubbles()[2]];
            return out;""")
        self.assertEqual(out["idle"], [[], [["c1", "msg user pending arrive", "Sent"]]])
        self.assertEqual(out["held"], [[["c2", "two", "waiting", ""]], 1])
        self.assertEqual(out["steer"], [1, 2])
        self.assertEqual(out["failed"], [1, ["c4", "msg user arrive failed", "too many messages waiting"]])

    def test_remove_and_send_now(self):
        out = self.run_js("""
            const out = {};
            for (const t of ["a", "b", "c"]) await T.sendText(t, "queue");
            answer = () => ({ status: 200, data: { ok: true } });
            await T.removeQueued("c1");
            out.removed = [posts[3].body, tray().map((q) => q[0])];
            answer = () => ({ status: 409, data: { error: { code: "not_waiting", message: "that message was already sent" } } });
            await T.removeQueued("c2");
            out.late = tray();
            // A row that already left clears without asking.
            const asked = posts.length;
            await T.removeQueued("c2");
            out.cleared = [tray().map((q) => q[0]), posts.length - asked];
            answer = () => ({ status: 409, data: { error: { code: "not_attached", message: "attach first" } } });
            await T.sendNow("c3");
            out.refused = [posts[posts.length - 1].body, tray()];
            answer = () => ({ status: 200, data: { ok: true } });
            await T.sendNow("c3");
            out.sending = tray();
            out.html = ids["chat-tray"].innerHTML.includes("Sending now");
            // Removed elsewhere: gone without a failure line.
            T.commandResult({ op: "message", client_id: "c3", ok: false, cancelled: true, note: "removed before sending" });
            out.end = [tray(), bubbles()];
            return out;""")
        self.assertEqual(out["removed"], [{"client_id": "c1", "op": "cancel"}, ["c2", "c3"]])
        self.assertEqual(out["late"], [["c2", "b", "left", "Already sent"], ["c3", "c", "waiting", ""]])
        self.assertEqual(out["cleared"], [["c3"], 0])
        self.assertEqual(out["refused"], [{"client_id": "c3", "op": "steer"}, [["c3", "c", "waiting", "Attach to send now"]]])
        self.assertEqual(out["sending"], [["c3", "c", "sendingNow", ""]])
        self.assertTrue(out["html"])
        self.assertEqual(out["end"], [[], []])

    def test_remove_all_asks_first(self):
        out = self.run_js("""
            globalThis.setTimeout = () => 7; globalThis.clearTimeout = () => {};
            for (const t of ["a", "b"]) await T.sendText(t, "queue");
            answer = () => ({ status: 200, data: { ok: true } });
            T.removeAllQueued();
            const armed = [ids["chat-tray"].innerHTML.includes("Remove all 2?"), posts.length];
            T.removeAllQueued();
            await tick();
            return { armed, ops: posts.slice(2).map((p) => [p.body.client_id, p.body.op]), left: tray() };""")
        self.assertEqual(out["armed"], [True, 2])
        self.assertEqual(out["ops"], [["c1", "cancel"], ["c2", "cancel"]])
        self.assertEqual(out["left"], [])

    def test_edit_saves_in_place_or_requeues_on_an_older_hub(self):
        out = self.run_js("""
            const out = {};
            const input = ids["chat-text"];
            await T.sendText("first draft", "queue");
            input.value = "half typed";
            T.beginEdit("c1");
            out.editing = [input.value, ids["chat-send"].textContent, input.placeholder,
                           ids["chat-editbar"].innerHTML.includes("Editing a queued message")];
            input.value = "  better text ";
            answer = () => ({ status: 200, data: { ok: true, client_id: "c1", op: "edit", text: "better text" } });
            await T.saveEdit();
            out.saved = [posts[1].body, tray(), input.value, ids["chat-send"].textContent];
            // An older hub refuses the op: remove, then queue it again.
            T.beginEdit("c1");
            input.value = "newest";
            answer = (path, body) => body.op === "edit" ? { status: 422, data: { error: { code: "bad_command" } } }
              : body.op === "cancel" ? { status: 200, data: { ok: true } } : { status: 202, data: { waiting: true } };
            await T.saveEdit();
            await tick();
            out.requeued = [posts.slice(2).map((p) => p.body.op || p.body.mode), tray(), T.chat.tray.note, input.value];
            // Too late: the text stays in the box to send as a new message.
            T.beginEdit("c2");
            input.value = "too late";
            answer = () => ({ status: 409, data: { error: { code: "not_waiting", message: "that message was already sent" } } });
            await T.saveEdit();
            out.late = [tray(), input.value, T.chat.tray.editingId, T.chat.tray.editNote];
            return out;""")
        self.assertEqual(out["editing"], ["first draft", "Save", "Edit the queued message", True])
        self.assertEqual(out["saved"], [{"client_id": "c1", "op": "edit", "text": "better text"},
                                        [["c1", "better text", "waiting", ""]], "half typed", "Send"])
        self.assertEqual(out["requeued"][0], ["edit", "cancel", "queue"])
        self.assertEqual(out["requeued"][1], [["c2", "newest", "waiting", ""]])
        self.assertEqual(out["requeued"][2], "Edited. It moved to the end of the queue")
        self.assertEqual(out["requeued"][3], "half typed")
        self.assertEqual(out["late"], [[["c2", "newest", "left", "Already sent"]], "too late", None,
                                       "That message already went out"])

    def test_an_echo_takes_a_row_out_and_the_hubs_list_fills_the_tray(self):
        out = self.run_js("""
            const out = {};
            await T.sendText("deploy it", "queue");
            T.echoLeavesTray("@/tmp/a.png deploy it");
            out.echoed = tray();
            // Opening the chat reads what the hub holds, another device's included.
            queueList = { status: 200, data: { sends: [{ client_id: "c1", text: "deploy it" },
              { client_id: "x9", text: "from the phone" }] } };
            await T.loadQueue(1);
            out.loaded = tray();
            // An answer for a chat that's gone is dropped.
            T.chat.gen = 2;
            queueList = { status: 200, data: { sends: [{ client_id: "late", text: "late" }] } };
            await T.loadQueue(1);
            out.stale = tray().map((q) => q[0]);
            return out;""")
        self.assertEqual(out["echoed"], [])
        # c1 already went out (its echo): the list can't bring it back.
        self.assertEqual(out["loaded"], [["x9", "from the phone", "waiting", ""]])
        self.assertEqual(out["stale"], ["x9"])

    def test_a_send_that_goes_out_while_the_list_loads_stays_gone(self):
        out = self.run_js("""
            let release;
            globalThis.api = () => new Promise((r) => { release = r; });
            T.echoLeavesTray("before any list");
            const before = T.chat.tray.unmatched.length;
            const loading = T.loadQueue(1);
            // The phone's send went out after the hub listed it.
            T.echoLeavesTray("from the phone");
            release({ status: 200, data: { sends: [{ client_id: "x9", text: "from the phone" },
              { client_id: "x10", text: "still waiting" }] } });
            await loading;
            return { before, tray: tray().map((q) => q[0]), settled: T.chat.tray.settled.has("x9"),
                     unmatched: T.chat.tray.unmatched.length };""")
        self.assertEqual(out, {"before": 0, "tray": ["x10"], "settled": True, "unmatched": 0})

    def test_a_queue_read_during_an_action_keeps_its_answer(self):
        out = self.run_js("""
            const out = {};
            await T.sendText("a", "queue");
            await T.sendText("b", "queue");
            let release;
            answer = () => new Promise((r) => { release = r; });
            const steer = T.sendNow("c1");
            // A read lands while Send now is out: new row objects.
            queueList = { status: 200, data: { sends: [{ client_id: "c1", text: "a" }, { client_id: "c2", text: "b" }] } };
            await T.loadQueue(1);
            release({ status: 409, data: { error: { code: "backlog_full", message: "full" } } });
            await steer;
            out.steer = tray()[0];
            // The same for a saved edit.
            T.beginEdit("c2");
            ids["chat-text"].value = "b2";
            const save = T.saveEdit();
            await T.loadQueue(1);
            release({ status: 200, data: { ok: true } });
            await save;
            out.edit = tray()[1];
            return out;""")
        self.assertEqual(out["steer"], ["c1", "a", "waiting", "Too many waiting. Try again shortly"])
        self.assertEqual(out["edit"], ["c2", "b2", "waiting", ""])

    def test_a_send_staged_after_a_read_began_survives_its_answer(self):
        out = self.run_js("""
            let releaseList;
            globalThis.api = () => new Promise((r) => { releaseList = r; });
            const reading = T.loadQueue(1);
            await T.sendText("new one", "queue");
            // The read was answered before the hub took the send.
            releaseList({ status: 200, data: { sends: [] } });
            await reading;
            return { tray: tray(), departed: T.chat.tray.departed.length };""")
        self.assertEqual(out, {"tray": [["c1", "new one", "waiting", ""]], "departed": 0})

    def test_requeue_keeps_the_draft_when_the_cancel_result_lands_first(self):
        out = self.run_js("""
            const input = ids["chat-text"];
            await T.sendText("queued", "queue");
            input.value = "my draft";
            T.beginEdit("c1");
            input.value = "edited";
            answer = (path, body) => {
              if (body.op === "edit") return { status: 422, data: { error: { code: "bad_command" } } };
              if (body.op === "cancel") {
                // The hub emits the cancelled result before answering.
                T.commandResult({ op: "message", client_id: "c1", ok: false, cancelled: true });
                return { status: 200, data: { ok: true } };
              }
              return { status: 202, data: { waiting: true } };
            };
            await T.saveEdit();
            await tick();
            return { input: input.value, tray: tray(), editing: T.chat.tray.editingId, note: T.chat.tray.editNote };""")
        self.assertEqual(out, {"input": "my draft", "tray": [["c2", "edited", "waiting", ""]],
                               "editing": None, "note": ""})

    def test_an_edit_saved_after_a_read_began_keeps_its_text(self):
        out = self.run_js("""
            await T.sendText("old", "queue");
            let releaseList;
            globalThis.api = () => new Promise((r) => { releaseList = r; });
            const reading = T.loadQueue(1);
            T.beginEdit("c1");
            ids["chat-text"].value = "new";
            answer = () => ({ status: 200, data: { ok: true } });
            await T.saveEdit();
            releaseList({ status: 200, data: { sends: [{ client_id: "c1", text: "old" }] } });
            await reading;
            const kept = tray()[0][1];
            // A read that starts after the save takes the hub's text again.
            globalThis.api = async () => ({ status: 200, data: { sends: [{ client_id: "c1", text: "from elsewhere" }] } });
            await T.loadQueue(1);
            return { kept, later: tray()[0][1] };""")
        self.assertEqual(out, {"kept": "new", "later": "from elsewhere"})

    def test_the_requeue_fallback_cannot_be_cancelled_under_it(self):
        out = self.run_js("""
            const input = ids["chat-text"];
            await T.sendText("queued", "queue");
            input.value = "my draft";
            T.beginEdit("c1");
            input.value = "edited";
            let releaseCancel;
            answer = (path, body) => body.op === "edit" ? { status: 422, data: { error: { code: "bad_command" } } }
              : body.op === "cancel" ? new Promise((r) => { releaseCancel = r; }) : { status: 202, data: { waiting: true } };
            const saving = T.saveEdit();
            await tick();
            // Escape while the fallback's cancel is out does nothing.
            T.cancelEdit();
            const during = [T.chat.tray.editingId, T.chat.tray.savingEdit, input.value];
            releaseCancel({ status: 200, data: { ok: true } });
            await saving; await tick();
            return { during, input: input.value, tray: tray().map((q) => q[1]), saving: T.chat.tray.savingEdit };""")
        self.assertEqual(out, {"during": ["c1", True, "edited"], "input": "my draft",
                               "tray": ["edited"], "saving": False})

    def test_an_echo_takes_the_row_sent_now_before_an_identical_one(self):
        out = self.run_js("""
            await T.sendText("same", "queue");
            await T.sendText("same", "queue");
            answer = () => ({ status: 200, data: { ok: true } });
            await T.sendNow("c2");
            T.echoLeavesTray("same");
            return tray();""")
        self.assertEqual(out, [["c1", "same", "waiting", ""]])


    def test_a_resync_keeps_the_sends_the_transcript_does_not_show(self):
        """A truncated journal redraws the log from the transcript: a send
        still out or failed keeps its bubble (and a later result still
        lands on it), while one the redraw already shows is settled."""
        out = self.run_js("""
            const now = Math.floor(Date.now() / 1000);
            answer = () => ({ status: 202, data: { waiting: false } });
            await T.sendText("run it", "steer");
            answer = () => ({ status: 500, data: { error: { message: "boom" } } });
            await T.sendText("bad", "steer");
            answer = () => ({ status: 202, data: { waiting: false } });
            await T.sendText("echoed", "steer");
            await T.sendText("again", "steer");
            // Sent twice within seconds: the first already landed, so the
            // transcript's one echo is its, not the second's.
            await T.sendText("twice", "steer");
            T.appendMessage({ kind: "user", text: "twice", ts: now }, true);
            await T.sendText("twice", "steer");
            const user = (seq, text, ts) => ({ type: "message.appended", seq, data: { kind: "user", text, ts } });
            globalThis.api = async (path) => path.includes("/messages")
              ? { status: 200, data: { messages: [user(1, "again", now - 1000), user(2, "echoed", now),
                  user(3, "twice", now)] } }
              : { status: 404, data: null };
            T.chat.seq = 500;
            T.handleFrame(T.chat.key, "data: " + JSON.stringify({ type: "journal.truncated", seq: 3 }));
            await tick();
            const after = bubbles().map((b) => [b[0] || null, b[1]]);
            T.commandResult({ op: "message", client_id: "c1", ok: false, note: "nope" });
            const c1 = ids["chat-log"].children.find((b) => b.dataset.clientId === "c1");
            return { after, settled: c1 ? [c1.className, c1._sub["send-note"].textContent] : null };""")
        self.assertEqual([b[0] for b in out["after"]], [None, None, None, "c1", "c2", "c4", "c6"])
        self.assertIn("pending", out["after"][3][1])
        self.assertIn("failed", out["after"][4][1])
        self.assertIn("pending", out["after"][5][1])
        self.assertIn("pending", out["after"][6][1])
        self.assertEqual(out["settled"][1], "nope")
        self.assertIn("failed", out["settled"][0])

    def test_a_refused_credential_closes_the_chat_before_the_gate(self):
        """Every 401 closes the chat first (stream, reveal timers,
        chat-open), as the stream's own 401 does, so nothing runs on
        behind the pair card."""
        out = self.run_js("""
            const body = el("body"); body.classList.add("chat-open");
            document.body = body;
            let gates = 0;
            globalThis.renderGate = () => { gates++; };
            const out = {};
            const reopen = () => { T.chat.key = "claude:s1"; T.chat.row = { status: "active", ownership: "attached" };
              body.classList.add("chat-open"); };
            await T.sendText("a", "queue");
            await T.sendText("b", "queue");
            answer = () => ({ status: 401, data: null });
            const gen = T.chat.gen;
            await T.sendNow("c1");
            out.sendNow = [T.chat.key, T.chat.gen > gen, body.classList.contains("chat-open"), gates];
            reopen();
            answer = () => ({ status: 202, data: { waiting: true } });
            await T.sendText("c", "queue");
            answer = () => ({ status: 401, data: null });
            await T.removeQueued(T.chat.tray.queued[0].cid);
            out.removeQueued = [T.chat.key, gates];
            globalThis.api = async () => ({ status: 401, data: null });
            reopen(); T.chat.rail.loading = false;
            await T.loadRail(T.chat.gen, true);
            out.loadRail = [T.chat.key, body.classList.contains("chat-open"), gates];
            return out;""")
        self.assertEqual(out, {"sendNow": [None, True, False, 1], "removeQueued": [None, 2],
                               "loadRail": [None, False, 3]})

    def test_stay_attached_reports_a_failure_and_ignores_a_stale_answer(self):
        out = self.run_js("""
            const out = {};
            let release;
            answer = () => new Promise((r) => { release = r; });
            T.chat.detaching = true;
            // A late answer from the chat open before: neither a success
            // nor a refusal touches the chat open now.
            for (const late of [{ status: 200, data: { ok: true } },
                                { status: 500, data: { error: { code: "boom", message: "it broke" } } }]) {
              const stale = T.cancelDetach();
              await tick();
              T.chat.gen++;
              release(late);
              await stale;
            }
            out.stale = [ids["chat-log"].children.length, T.chat.detaching];
            answer = () => ({ status: 409, data: { error: { code: "busy", message: "the session is busy" } } });
            await T.cancelDetach();
            out.failed = ids["chat-log"].children.map((b) => b.className);
            out.text = ids["chat-log"].children.map((b) => b.innerHTML.includes("Stay attached failed: the session is busy"));
            answer = () => ({ status: 200, data: { ok: true } });
            await T.cancelDetach();
            out.ok = T.chat.detaching;
            return out;""")
        self.assertEqual(out["stale"], [0, True])
        self.assertEqual(len(out["failed"]), 1)
        self.assertIn("error", out["failed"][0])
        self.assertEqual(out["text"], [True])
        self.assertFalse(out["ok"])

if __name__ == "__main__":
    unittest.main()
