"""Unread dots on the web app's Sessions rows (#1439): sessions.js's
SessionUnread under node, with app.js's globals stubbed.

The hub flags each row ``unread`` for this browser; the chat posts
``POST /v1/sessions/{key}/read`` when it opens, at most every few seconds
while messages arrive, and once more on close when one is due. A local
mark clears the dot at once until the next list read reconciles it."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "src" / "quiver" / "webapp" / "static"
SESSIONS_JS = STATIC / "sessions.js"
CHAT_JS = STATIC / "pages" / "chat.js"

PRELUDE = (
    "const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;')"
    ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');\n"
    "globalThis.document = { getElementById: () => null };\n"
    "globalThis.location = { hash: '' };\n"
    "globalThis.Fmt = { logo: () => '', harnessName: (h) => h, modelName: (m) => m || '',"
    " tokens: String, dollars: String };\n"
    "const posts = [];\nlet answer = () => ({ status: 200, data: { ok: true, at: 500 } });\n"
    "globalThis.apiPost = async (path, body) => { posts.push({ path, body }); return answer(path, body); };\n"
)

# Timers the test fires by hand, so the throttle is checked without waiting.
TIMERS = (
    "const timers = [];\n"
    "unread.setTimer = (fn, ms) => { timers.push({ fn, ms, live: true }); return timers.length - 1; };\n"
    "unread.clearTimer = (t) => { if (timers[t]) timers[t].live = false; };\n"
    "const fire = async () => { for (const t of timers) if (t.live) { t.live = false; t.fn(); }"
    " await new Promise((r) => setTimeout(r, 0)); };\n"
    "const tick = () => new Promise((r) => setTimeout(r, 0));\n"
)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSessionUnread(unittest.TestCase):
    def run_js(self, body):
        script = (PRELUDE + SESSIONS_JS.read_text(encoding="utf-8") + "\n" + TIMERS
                  + "(async () => { const out = await (async () => {" + body + "})();"
                  + " process.stdout.write(JSON.stringify(out)); })()"
                  + ".catch((e) => { console.error(e); process.exit(1); });\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(script)
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_dot_follows_the_hub_flag_the_open_chat_and_marks(self):
        out = self.run_js("""
            const row = (key, u, at) => ({ key, unread: u, last_active_at: at });
            const plain = [unreadShown(row("a", true, 10)), unreadShown(row("b", false, 10)),
                           unreadShown({ key: "old" }), unreadShown(null)];
            unread.openKey = "a";
            const open = unreadShown(row("a", true, 10));
            unread.openKey = null;
            unread.marks.set("a", 10);
            const marked = unreadShown(row("a", true, 10));
            const moved = unreadShown(row("a", true, 11));
            return { plain, open, marked, moved };""")
        # An older hub sends no `unread`: no dot.
        self.assertEqual(out["plain"], [True, False, False, False])
        self.assertFalse(out["open"])
        self.assertFalse(out["marked"])
        # Activity after the mark lights it again.
        self.assertTrue(out["moved"])

    def test_opening_clears_at_once_and_posts_an_empty_read(self):
        out = self.run_js("""
            const p = SessionUnread.opened("claude:a@b", 100);
            const during = unread.marks.get("claude:a@b");
            await p;
            return { posts, during, after: unread.marks.get("claude:a@b"), open: unread.openKey };""")
        self.assertEqual(out["posts"], [{"path": "/v1/sessions/claude%3Aa%40b/read", "body": {}}])
        self.assertEqual(out["during"], 100)
        # The hub's answered cursor becomes the mark.
        self.assertEqual(out["after"], 500)
        self.assertEqual(out["open"], "claude:a@b")

    def test_a_burst_of_messages_costs_one_post(self):
        out = self.run_js("""
            await SessionUnread.opened("k", 1);
            posts.length = 0;
            for (let i = 0; i < 5; i++) SessionUnread.activity("k");
            SessionUnread.activity("other");  // not the open chat
            const queued = posts.length;
            await fire();
            const afterFire = posts.length;
            SessionUnread.activity("k");
            await fire();
            return { queued, afterFire, total: posts.length, timers: timers.map((t) => t.ms) };""")
        self.assertEqual(out["queued"], 0)
        self.assertEqual(out["afterFire"], 1)
        self.assertEqual(out["total"], 2)
        self.assertEqual(out["timers"], [3000, 3000])

    def test_closing_flushes_a_due_read_and_nothing_more(self):
        out = self.run_js("""
            await SessionUnread.opened("k", 1);
            posts.length = 0;
            SessionUnread.activity("k");
            await SessionUnread.closed("k");
            const flushed = posts.length;
            await fire();  // the cancelled timer must not post again
            SessionUnread.activity("k");  // a late event after close
            await SessionUnread.closed("k");
            return { flushed, total: posts.length, open: unread.openKey };""")
        self.assertEqual(out["flushed"], 1)
        self.assertEqual(out["total"], 1)
        self.assertIsNone(out["open"])

    def test_closing_always_posts_once_so_history_shown_counts(self):
        out = self.run_js("""
            await SessionUnread.opened("k", 1);
            posts.length = 0;
            await SessionUnread.closed("k");
            return posts.length;""")
        self.assertEqual(out, 1)

    def test_a_hidden_tab_posts_nothing(self):
        out = self.run_js("""
            await SessionUnread.opened("k", 1);
            posts.length = 0;
            SessionUnread.activity("k");
            let shown = false;
            unread.visible = () => shown;
            await fire();  // hid before the timer fired
            const whileHidden = posts.length;
            shown = true;
            SessionUnread.activity("k");  // chat.js asks again when the tab shows
            await fire();
            const onShow = posts.length;
            shown = false;
            await SessionUnread.closed("k");
            return { whileHidden, onShow, afterHiddenClose: posts.length };""")
        self.assertEqual(out, {"whileHidden": 0, "onShow": 1, "afterHiddenClose": 1})

    def test_a_chat_from_a_link_marks_its_row_once_known(self):
        out = self.run_js("""
            answer = () => ({ status: 500, data: null });
            await SessionUnread.opened("k", 0);
            SessionUnread.seen("k", 42);
            SessionUnread.seen("other", 99);  // not the open chat
            answer = () => { throw new Error("offline"); };
            await SessionUnread.closed("k");
            return [unread.marks.get("k"), unread.marks.has("other"),
                    unreadShown({ key: "k", unread: true, last_active_at: 42 })];""")
        self.assertEqual(out, [42, False, False])

    def test_closing_keeps_what_it_showed_read_if_the_post_fails(self):
        out = self.run_js("""
            answer = () => { throw new Error("offline"); };
            await SessionUnread.opened("k", 100);
            SessionUnread.activity("k", 130);
            SessionUnread.activity("k", 120);
            await SessionUnread.closed("k");
            unreadReconcile([{ key: "k", unread: true, last_active_at: 130 }]);
            return [unread.marks.get("k"), unreadShown({ key: "k", unread: true, last_active_at: 130 }),
                    unreadShown({ key: "k", unread: true, last_active_at: 131 })];""")
        # The reply shown at 130 stays read; one after it lights the dot.
        self.assertEqual(out, [130, False, True])

    def test_opening_another_chat_closes_the_first(self):
        out = self.run_js("""
            await SessionUnread.opened("a", 1);
            SessionUnread.activity("a");
            await SessionUnread.opened("b", 2);
            await tick();
            return { paths: posts.map((p) => p.path), open: unread.openKey };""")
        self.assertEqual(out["paths"], ["/v1/sessions/a/read", "/v1/sessions/a/read", "/v1/sessions/b/read"])
        self.assertEqual(out["open"], "b")

    def test_a_failed_read_keeps_the_local_clear(self):
        out = self.run_js("""
            answer = () => { throw new Error("offline"); };
            await SessionUnread.opened("k", 7);
            unread.openKey = null;
            const offline = unreadShown({ key: "k", unread: true, last_active_at: 7 });
            answer = () => ({ status: 404, data: { error: {} } });
            await SessionUnread.opened("j", 3);
            return { offline, mark: unread.marks.get("j") };""")
        self.assertFalse(out["offline"])
        self.assertEqual(out["mark"], 3)

    def test_a_list_read_reconciles_the_marks(self):
        out = self.run_js("""
            for (const k of ["read", "gone", "moved", "pending"]) unread.marks.set(k, 10);
            unreadReconcile([
              { key: "read", unread: false, last_active_at: 9 },
              { key: "moved", unread: true, last_active_at: 12 },
              { key: "pending", unread: true, last_active_at: 10 },
            ]);
            return [...unread.marks.keys()];""")
        # Only a mark the hub hasn't caught up with yet stays.
        self.assertEqual(out, ["pending"])

    def test_the_row_draws_the_dot(self):
        out = self.run_js("""
            sessionsPage.query = "";
            globalThis.transcriptHit = () => null;
            const html = (r) => sessionRow(Object.assign({ key: "k", title: "T", harness: "claude",
              last_active_at: 5 }, r));
            return [html({ unread: true }).includes("unread-dot"), html({ unread: false }).includes("unread-dot")];""")
        self.assertEqual(out, [True, False])


class TestChatCallsTheReadHooks(unittest.TestCase):
    """chat.js reaches SessionUnread on open, live messages, a shown tab
    and close; a cheap guard, since the chat's flow tests live elsewhere."""

    def test_hooks_are_wired(self):
        src = CHAT_JS.read_text(encoding="utf-8")
        for call in ('unreadHook("opened", key', 'unreadHook("closed", chat.key)',
                     'unreadHook("seen", chat.key, row.last_active_at)',
                     'unreadHook("activity", chat.key)'):
            self.assertIn(call, src)
        self.assertEqual(src.count('unreadHook("activity"'), 2)


if __name__ == "__main__":
    unittest.main()
