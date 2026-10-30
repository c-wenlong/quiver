"""The Sessions page's pin, rename and fork (#970): the iPhone's
swipe-left actions on the web list's rows and the chat's header.

``/v1/app/sessions`` rows carry the hub's pin, name and ``can_fork``;
``sessions.js`` sorts pinned rows into a Pinned group, pins at once and
puts a refused pin back, keeps a list fetched mid-pin from undoing it,
renames by the hub's rules and forks like a new-session launch."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from quiver.webapp import sessions

from tests.test_webapp import app_js_function
from tests.test_webapp_launch_sheets import MENU_JS, STUB

STATIC = Path(__file__).resolve().parent.parent / "src" / "quiver" / "webapp" / "static"
SESSIONS_JS = STATIC / "sessions.js"
CHAT_JS = STATIC / "pages" / "chat.js"


class TestRowsCarryPinNameAndFork(unittest.TestCase):
    def test_build_passes_the_catalogs_meta_through(self):
        rows = [
            {"key": "claude:a", "id": "a", "harness": "claude", "machine_id": "hub",
             "title": "Mine", "harness_title": "Fix the bug", "pinned": True,
             "pinned_at": 1700.5, "can_fork": True, "last_active_at": 2},
            {"key": "gemini:b", "id": "b", "harness": "gemini", "machine_id": "hub",
             "title": "Theirs", "last_active_at": 1, "pinned_at": 99},
        ]
        named, bare = sessions.build(rows, {}, "hub", {}, scanned=True)
        self.assertEqual(
            (named["title"], named["harness_title"], named["pinned"],
             named["pinned_at"], named["can_fork"]),
            ("Mine", "Fix the bug", True, 1700.5, True))
        # An old catalog row without the fields reads unpinned and
        # unforkable; a stray pinned_at on an unpinned row is dropped.
        self.assertEqual(
            (bare["harness_title"], bare["pinned"], bare["pinned_at"], bare["can_fork"]),
            ("", False, None, False))


def _run_node(test, script):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(script)
    test.addCleanup(Path(f.name).unlink)
    done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
    test.assertEqual(done.returncode, 0, done.stderr)
    return json.loads(done.stdout)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSessionActionRules(unittest.TestCase):
    """sessions.js's pure parts under node, with app.js's globals stubbed."""

    PRELUDE = (
        "const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;')"
        ".replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\"/g, '&quot;');\n"
        "globalThis.document = { getElementById: () => null };\n"
        "globalThis.location = { hash: '' };\n"
        "globalThis.Fmt = { logo: () => '', harnessName: (h) => h, modelName: (m) => m || '',"
        " tokens: String, dollars: String };\n"
        "globalThis.renderGate = () => { gated++; };\nlet gated = 0;\n"
        "const posts = [];\nlet answer = () => ({ status: 200, data: {} });\n"
        "globalThis.apiPost = async (path, body) => { posts.push({ path, body }); return answer(path, body); };\n")

    def run_js(self, body):
        script = (self.PRELUDE + SESSIONS_JS.read_text(encoding="utf-8")
                  + "\n(async () => { const out = await (async () => {" + body + "})();"
                  + " process.stdout.write(JSON.stringify(out)); })()"
                  + ".catch((e) => { console.error(e); process.exit(1); });\n")
        return _run_node(self, script)

    def test_menu_offers_fork_only_where_the_row_says(self):
        out = self.run_js("""
            return [rowMenuActions({}), rowMenuActions({ pinned: true, can_fork: true }),
                    rowMenuActions({ can_fork: "yes" })];""")
        self.assertEqual(out, [["pin", "rename"], ["unpin", "rename", "fork"], ["pin", "rename"]])

    def test_rename_rules_match_the_hub(self):
        out = self.run_js("""
            const long = "é".repeat(130);
            return {
              clamp: clampTitle("a\\nb\\r\\nc"),
              clampLong: titleLength(clampTitle(long)),
              // Code points, like the hub: a flag is two.
              flag: titleLength("🇸🇬"),
              norm: normalTitle("  fix   the\\tbug  "),
              normCut: normalTitle("x".repeat(119) + " y"),
              empty: canSaveTitle("   ", "Old"),
              same: canSaveTitle(" Old ", "Old"),
              changed: canSaveTitle("New", "Old"),
              custom: [hasCustomTitle({ title: "Mine", harness_title: "Fix" }),
                       hasCustomTitle({ title: "Fix", harness_title: "Fix" }),
                       hasCustomTitle({ title: "Mine" }),
                       hasCustomTitle({ title: "Mine", harness_title: "" })],
            };""")
        self.assertEqual(out["clamp"], "a b  c")
        self.assertEqual(out["clampLong"], 120)
        self.assertEqual(out["flag"], 2)
        self.assertEqual(out["norm"], "fix the bug")
        self.assertEqual(out["normCut"], "x" * 119)
        self.assertEqual((out["empty"], out["same"], out["changed"]), (False, False, True))
        self.assertEqual(out["custom"], [True, False, False, True])

    def test_pinned_rows_lead_in_pin_order_and_ignore_the_window(self):
        out = self.run_js("""
            sessionsPage.window = "7d";
            sessionsPage.data = { sessions: [
              { key: "new", title: "New", harness: "claude", last_active_at: Date.now() / 1000 },
              { key: "old-pin", title: "Old pin", harness: "claude", last_active_at: 1, pinned: true, pinned_at: 50 },
              { key: "first-pin", title: "First pin", harness: "claude", last_active_at: 2, pinned: true, pinned_at: 10, trivial: true },
              { key: "old", title: "Old", harness: "claude", last_active_at: 1 },
            ] };
            const { kept, trivial } = filteredSessions();
            const html = sessionsTable(kept);
            const order = [...html.matchAll(/class="session-row"[^>]*data-key="([^"]+)"/g)].map((m) => m[1]);
            return { kept: kept.map((r) => r.key), trivial, order,
                     pinnedLabel: html.indexOf("Pinned") < html.indexOf('data-key="new"'),
                     more: (html.match(/class="row-more"/g) || []).length };""")
        # The old unpinned row falls outside 7 days; pinned ones stay,
        # a trivial pinned one included.
        self.assertEqual(out["kept"], ["new", "old-pin", "first-pin"])
        self.assertEqual(out["trivial"], 0)
        self.assertEqual(out["order"], ["first-pin", "old-pin", "new"])
        self.assertTrue(out["pinnedLabel"])
        self.assertEqual(out["more"], 3)

    def test_pin_is_at_once_and_a_refusal_puts_it_back(self):
        out = self.run_js("""
            sessionsPage.data = { sessions: [{ key: "k", title: "T", harness_title: "T", pinned: false, pinned_at: null }] };
            const row = () => sessionsPage.data.sessions[0];
            const seen = [];
            let release;
            answer = () => new Promise((r) => { release = r; });
            const pin = setPinned(row(), true);
            seen.push(row().pinned);
            release({ status: 200, data: { title: "T", harness_title: "T", pinned: true, pinned_at: 123 } });
            await pin;
            seen.push([row().pinned, row().pinned_at]);
            answer = () => ({ status: 409, data: { error: { code: "too_many_pins", message: "too many pinned sessions; unpin one first" } } });
            await setPinned(row(), false);
            seen.push([row().pinned, row().pinned_at, sessionsPage.actionNote]);
            return { seen, posts };""")
        self.assertEqual(out["seen"][0], True)
        self.assertEqual(out["seen"][1], [True, 123])
        self.assertEqual(out["seen"][2], [True, 123, "Couldn't unpin it. too many pinned sessions; unpin one first"])
        self.assertEqual(out["posts"], [{"path": "/v1/sessions/k/pin", "body": {"pinned": True}},
                                        {"path": "/v1/sessions/k/pin", "body": {"pinned": False}}])

    def test_a_stale_answer_and_an_early_list_never_undo_a_pin(self):
        out = self.run_js("""
            sessionsPage.data = { sessions: [{ key: "k", title: "T", pinned: false }] };
            const row = () => sessionsPage.data.sessions[0];
            const pending = [];
            answer = () => new Promise((r) => pending.push(r));
            const pin = setPinned(row(), true);
            const unpin = setPinned(row(), false);
            // The pin's reply lands after the unpin was chosen: ignored.
            pending[0]({ status: 200, data: { title: "T", pinned: true, pinned_at: 5 } });
            await pin;
            const afterStale = row().pinned;
            // A list fetched while the unpin is out still says pinned.
            const mark = pinLedger.clock;
            const early = pinOverlay([{ key: "k", pinned: true, pinned_at: 5 }], mark)[0].pinned;
            pending[1]({ status: 200, data: { title: "T", pinned: false, pinned_at: null } });
            await unpin;
            // A list fetched after the answer is the hub's word again.
            const late = pinOverlay([{ key: "k", pinned: true, pinned_at: 9 }], pinLedger.clock)[0].pinned;
            return { afterStale, early, late, left: pinLedger.choices.size };""")
        self.assertEqual(out, {"afterStale": False, "early": False, "late": True, "left": 0})

    def test_overlapping_refusals_go_back_to_the_saved_pin(self):
        out = self.run_js("""
            sessionsPage.data = { sessions: [{ key: "k", title: "T", pinned: false, pinned_at: null }] };
            const row = () => sessionsPage.data.sessions[0];
            const pending = [];
            answer = () => new Promise((r) => pending.push(r));
            const refuse = { status: 0, data: null };
            // Pin, then Unpin, before either answers; both fail.
            const pin = setPinned(row(), true);
            const unpin = setPinned(row(), false);
            pending[0](refuse); await pin;
            pending[1](refuse); await unpin;
            const bothFailed = row().pinned;
            // Pin, then Unpin; the pin lands and the unpin fails.
            const pin2 = setPinned(row(), true);
            const unpin2 = setPinned(row(), false);
            pending[2]({ status: 200, data: { title: "T", pinned: true, pinned_at: 42 } }); await pin2;
            pending[3](refuse); await unpin2;
            return { bothFailed, pinLanded: [row().pinned, row().pinned_at] };""")
        self.assertEqual(out, {"bothFailed": False, "pinLanded": [True, 42]})

    def test_a_meta_answer_reaches_the_open_chat(self):
        out = self.run_js("""
            const applied = [];
            globalThis.SessionChat = { key: "k", applyMeta: (f) => applied.push(f) };
            sessionsPage.data = { sessions: [{ key: "k", title: "Old", harness_title: "Old" }] };
            patchMeta("k", { title: "New", harness_title: "Old", pinned: true, pinned_at: 7 });
            patchMeta("other", { title: "X" });
            // A cleared name falls back to the harness's own.
            patchMeta("k", { title: "", harness_title: "Old", pinned: false, pinned_at: 7 });
            return { applied, row: sessionsPage.data.sessions[0] };""")
        self.assertEqual(out["applied"], [
            {"title": "New", "harness_title": "Old", "pinned": True, "pinned_at": 7},
            {"title": "Old", "harness_title": "Old", "pinned": False, "pinned_at": None}])
        self.assertEqual(out["row"]["title"], "Old")

    def test_follow_launch_settles_like_the_phones_launch_flow(self):
        out = self.run_js("""
            globalThis.setTimeout = (f) => { f(); return 0; };
            const run = async (answers, first = { launch_id: "L1", state: "pending" }) => {
              let i = 0;
              globalThis.api = async (path) => answers[Math.min(i++, answers.length - 1)];
              const l = await followLaunch(first, () => true);
              return [l.state, l.key || null, launchNote(l, false)[1], i];
            };
            return {
              started: await run([{ status: 0 }, { status: 200, data: { state: "started", key: "codex:new" } }]),
              failed: await run([{ status: 200, data: { state: "failed", note: "boom" } }]),
              lost: await run([{ status: 404, data: null }]),
              waiting: await run([{ status: 200, data: { state: "pending" } }]),
              // A started launch without its key yet keeps polling.
              keyless: await run([{ status: 200, data: { state: "started" } }, { status: 200, data: { state: "started", key: "k2" } }]),
              notes: [launchNote({ state: "failed", note: "boom" }, false), launchNote({ state: "waiting" }, true)],
              gone: await followLaunch({ launch_id: "L1", state: "pending" }, () => false),
            };""")
        self.assertEqual(out["started"], ["started", "codex:new", False, 2])
        self.assertEqual(out["failed"][:3], ["failed", None, True])
        self.assertEqual(out["lost"][:3], ["lost", None, True])
        self.assertEqual(out["waiting"], ["waiting", None, False, 90])
        self.assertEqual(out["keyless"][:2], ["started", "k2"])
        self.assertEqual(out["notes"], [
            ["The session didn't start: boom", True],
            ["Queued. It starts when that machine is online, then shows up in Sessions.", False]])
        self.assertIsNone(out["gone"])


# The launch sheets' DOM stub, reading ids off <form>, <span> and <p>
# too, with the input methods the sheets call.
SHEET_STUB = (STUB.replace("(input|button|a|div)", "(input|button|a|div|form|span|p)")
              + "{ const made = makeEl; makeEl = (...a) => Object.assign(made(...a), { select() {},"
              " close() { this.open = false; if (this.onclose) this.onclose(); } }); }\n"
              + app_js_function("bodySheet")
              + "globalThis.apiPost = async (path, body) => { fetchCalls.push({ path, body });"
              " const r = fetchRoutes.find(([p]) => path.startsWith(p));"
              " const a = r ? r[1] : { status: 404, body: { error: { message: 'unstubbed ' + path } } };"
              " return { status: a.status, data: a.body }; };\n")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSessionActionSheets(unittest.TestCase):
    """The rename and fork sheets driven through the stubbed DOM."""

    def run_flow(self, flow):
        script = (SHEET_STUB + SESSIONS_JS.read_text(encoding="utf-8") + "\n"
                  + "const out = {};\n(async () => {\n" + flow
                  + "\nprocess.stdout.write(JSON.stringify(out));\n})()"
                  + ".catch((e) => { console.error(e); process.exit(1); });\n")
        return _run_node(self, script)

    def test_rename_saves_the_normalized_name_and_offers_the_original(self):
        out = self.run_flow(r"""
            sessionsPage.data = { sessions: [{ key: "claude:a", title: "Mine", harness_title: "Fix the bug" }] };
            fetchRoutes.push(["/v1/sessions/claude%3Aa/title", { status: 200,
              body: { ok: true, title: "Better name", harness_title: "Fix the bug", pinned: false, pinned_at: null } }]);
            openRenameSheet(sessionsPage.data.sessions[0]);
            const d = created.get("session-action-sheet");
            const q = (s) => d.querySelector(s);
            out.open = d.open;
            out.placeholder = q("#rename-input").attrs.placeholder;
            out.hasReset = !!q("#rename-reset");
            out.original = d._html.includes("Original: Fix the bug");
            out.saveOffAtFirst = q("#rename-save").disabled;
            q("#rename-input").value = "  Better \n name ";
            q("#rename-input").oninput();
            out.count = q("#rename-count").textContent;
            out.saveOn = !q("#rename-save").disabled;
            q("#rename-form").onsubmit({ preventDefault() {} });
            await tick();
            out.sent = fetchCalls.map((c) => [c.path, c.body]);
            out.title = sessionsPage.data.sessions[0].title;
            out.closed = !d.open;
            // "Use the original name" clears it.
            fetchRoutes[0][1] = { status: 200, body: { ok: true, title: "Fix the bug", harness_title: "Fix the bug", pinned: false } };
            openRenameSheet({ key: "claude:a", title: "Mine", harness_title: "Fix the bug" });
            q("#rename-reset").onclick();
            await tick();
            out.reset = fetchCalls[1].body;
            // A refusal stays in the sheet with the hub's reason.
            fetchRoutes[0][1] = { status: 422, body: { error: { code: "bad_command", message: "title must be a string" } } };
            openRenameSheet({ key: "claude:a", title: "Fix the bug", harness_title: "Fix the bug" });
            out.noResetWhenOriginal = !q("#rename-reset");
            q("#rename-input").value = "Other";
            q("#rename-input").oninput();
            q("#rename-form").onsubmit({ preventDefault() {} });
            await tick();
            out.error = [q("#rename-error").hidden, q("#rename-error").textContent, d.open];
        """)
        self.assertTrue(out["open"])
        self.assertEqual(out["placeholder"], "Fix the bug")
        self.assertTrue(out["hasReset"] and out["original"])
        self.assertTrue(out["saveOffAtFirst"])
        self.assertEqual(out["count"], "16/120")
        self.assertTrue(out["saveOn"])
        self.assertEqual(out["sent"], [["/v1/sessions/claude%3Aa/title", {"title": "Better name"}]])
        self.assertEqual(out["title"], "Better name")
        self.assertTrue(out["closed"])
        self.assertEqual(out["reset"], {"title": ""})
        self.assertTrue(out["noResetWhenOriginal"])
        self.assertEqual(out["error"], [False, "title must be a string", True])

    def test_fork_posts_the_prompt_and_opens_the_new_chat(self):
        out = self.run_flow(r"""
            fetchRoutes.push(["/v1/sessions/codex%3At/fork", { status: 202,
              body: { ok: true, launch_id: "L9", state: "pending" } }]);
            apiAnswers.push(["/v1/launches/L9", { status: 200, data: { state: "started", key: "codex:new" } }]);
            const row = { key: "codex:t", title: "Trim", harness: "codex", cwd: "/w/app", can_fork: true };
            openForkSheet(row);
            const d = created.get("session-action-sheet");
            const q = (s) => d.querySelector(s);
            out.title = d._html.includes("Fork Trim");
            out.folder = d._html.includes("Runs under tmux in app.");
            out.startOff = q("#fork-start").disabled;
            q("#fork-prompt").value = "  try the other approach ";
            q("#fork-prompt").oninput();
            out.startOn = !q("#fork-start").disabled;
            q("#fork-form").onsubmit({ preventDefault() {} });
            await tick();
            out.sent = fetchCalls.map((c) => [c.path, c.body]);
            out.hash = location.hash;
            out.closed = !d.open;
            // A harness that can't fork: the hub's reason, Start again.
            fetchRoutes[0][1] = { status: 409, body: { error: { code: "fork_unsupported", message: "gemini can't fork a session" } } };
            openForkSheet(row);
            q("#fork-prompt").value = "go";
            q("#fork-prompt").oninput();
            q("#fork-form").onsubmit({ preventDefault() {} });
            await tick();
            out.refused = [q("#fork-note").textContent, q("#fork-note").hidden, q("#fork-start").disabled, d.open];
        """)
        self.assertTrue(out["title"] and out["folder"])
        self.assertTrue(out["startOff"])
        self.assertTrue(out["startOn"])
        self.assertEqual(out["sent"], [["/v1/sessions/codex%3At/fork", {"prompt": "try the other approach"}]])
        self.assertEqual(out["hash"], "#/sessions?chat=codex%3Anew")
        self.assertTrue(out["closed"])
        self.assertEqual(out["refused"], ["gemini can't fork a session", False, False, True])

    def test_a_fork_sheet_closed_by_leaving_never_opens_the_chat(self):
        out = self.run_flow(r"""
            fetchRoutes.push(["/v1/sessions/codex%3At/fork", { status: 202,
              body: { ok: true, launch_id: "L9", state: "pending" } }]);
            let release;
            globalThis.api = (path) => new Promise((r) => { release = () => r({ status: 200, data: { state: "started", key: "codex:new" } }); });
            openForkSheet({ key: "codex:t", title: "Trim", harness: "codex", cwd: "/w/app", can_fork: true });
            const d = created.get("session-action-sheet");
            d.querySelector("#fork-prompt").value = "go";
            d.querySelector("#fork-prompt").oninput();
            d.querySelector("#fork-form").onsubmit({ preventDefault() {} });
            await tick();
            // Navigation closes route sheets (app.js closeRouteSheets).
            d.close();
            release();
            await tick();
            out.hash = location.hash;
            out.routeSheet = "routeSheet" in d.dataset || "data-route-sheet" in d.attrs;
        """)
        self.assertEqual(out["hash"], "")
        self.assertTrue(out["routeSheet"])


    def test_a_new_session_sheet_closed_by_leaving_never_opens_the_chat(self):
        """Back while New session waits for its launch: the navigation
        closes the sheet (app.js closeRouteSheets), and the poll's answer
        must not open the new chat from wherever the user went."""
        script = (SHEET_STUB + MENU_JS.read_text(encoding="utf-8") + "\n"
                  + SESSIONS_JS.read_text(encoding="utf-8") + "\n" + r"""
const out = {};
(async () => {
  globalThis.setTimeout = (f) => { f(); return 0; };
  apiAnswers.push(["/v1/machines", { status: 200, data: { machines: [
    { id: "m1", name: "hub", online: true, launchable: ["claude"] }] } }]);
  fetchRoutes.push(["/v1/sessions", { status: 202, body: { launch_id: "L1", state: "pending" } }]);
  sessionsPage.data = { sessions: [] };
  await openNewSession();
  await tick();
  const d = created.get("new-session-sheet");
  const q = (s) => d.querySelector(s);
  q("#ns-folder").value = "/w/app"; q("#ns-folder").oninput(); await tick();
  q("#ns-prompt").value = "go"; q("#ns-prompt").oninput();
  let release;
  globalThis.api = () => new Promise((r) => { release = () => r({ status: 200, data: { state: "started", key: "claude:new" } }); });
  q("#ns-start").onclick();
  await tick();
  out.waiting = [ns.phase, !!release];
  d.close();
  release();
  await tick();
  out.hash = location.hash;
  out.phase = ns.phase;
  out.routeSheet = "routeSheet" in d.dataset || "data-route-sheet" in d.attrs;
  detachClick({ key: "claude:a", title: "A", harness: "claude", status: "active", ownership: "attached" });
  const det = created.get("detach-sheet");
  out.detachRouteSheet = !!det && ("routeSheet" in det.dataset || "data-route-sheet" in det.attrs);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
""")
        out = _run_node(self, script)
        self.assertEqual(out["waiting"], ["waiting", True])
        self.assertEqual(out["hash"], "")
        self.assertEqual(out["phase"], "idle")
        self.assertTrue(out["routeSheet"])
        self.assertTrue(out["detachRouteSheet"])

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestChatHeaderMeta(unittest.TestCase):
    def test_a_meta_event_renames_the_open_chat(self):
        prelude = (
            "const esc = (s) => String(s == null ? '' : s);\n"
            "globalThis.window = { addEventListener() {} };\n"
            "globalThis.document = { addEventListener() {}, getElementById: () => null };\n"
            "globalThis.location = { hash: '' };\n"
            "globalThis.Fmt = { modelName: (m) => m || '', harnessName: (h) => h };\n")
        body = """
            const T = SessionChat._test;
            T.chat.key = "k";
            T.chat.row = { title: "Old", harness_title: "Old", pinned: false };
            SessionChat.applyMeta({ title: "New", harness_title: "Old", pinned: true, pinned_at: 3 });
            const renamed = Object.assign({}, T.chat.row);
            SessionChat.applyMeta({ title: "", harness_title: "Old", pinned: false, pinned_at: 3 });
            return { renamed, cleared: T.chat.row };"""
        script = (prelude + CHAT_JS.read_text(encoding="utf-8")
                  + "\n(async () => { const out = await (async () => {" + body + "})();"
                  + " process.stdout.write(JSON.stringify(out)); })();\n")
        out = _run_node(self, script)
        self.assertEqual(out["renamed"], {"title": "New", "harness_title": "Old", "pinned": True, "pinned_at": 3})
        self.assertEqual(out["cleared"], {"title": "Old", "harness_title": "Old", "pinned": False, "pinned_at": None})


if __name__ == "__main__":
    unittest.main()
