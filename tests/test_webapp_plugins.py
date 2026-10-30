"""The Plugins page: Health (drift) and Explore (the catalog as the app
places it), each cached for a short while, Health's Fix and Fix all, and
the routes with auth. Every harness CLI call a fix makes goes to a fake
runner that records it and makes the change it would have made."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.webapp.pages import PageContext, PageError
from quiver.webapp.pages import plugins as page

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from test_plugin_catalog import _CatalogHome
    from test_plugin_drift import _codex_copy, _FakeHome, _write
    from test_remote_hub import HubBase
except ImportError:  # `python -m unittest tests.test_webapp_plugins`
    from tests.test_plugin_catalog import _CatalogHome
    from tests.test_plugin_drift import _codex_copy, _FakeHome, _write
    from tests.test_remote_hub import HubBase


def _state(available=False, installed=False, enabled=None, path=None, copies=()):
    return {"available": available, "installed": installed, "enabled": enabled,
            "version": None, "scopes": [], "path": path, "copies": list(copies)}


def _plugin(name, market, states, drift=None):
    return {"name": name, "ref": f"{name}@{market}", "marketplace": market,
            "harnesses": states, "drift": drift, "skills": []}


def _index(catalog):
    plugins = {}
    for m in catalog["marketplaces"]:
        for p in m["plugins"]:
            plugins[p["ref"]] = page._plugin(p, {"marketplaces": {}, "plugins": {}})
    return plugins, page.sources(catalog, plugins)


def _tree(sources):
    return {s["id"]: {m["id"]: [e["ref"] for e in m["plugins"]] for m in s["marketplaces"]}
            for s in sources}


class TestStatus(unittest.TestCase):
    def test_each_state_reads_as_the_app_does(self):
        self.assertEqual(page.status(_state(installed=True, enabled=True)), "enabled")
        self.assertEqual(page.status(_state(installed=True, enabled=False)), "disabled")
        self.assertEqual(page.status(_state(installed=True)), "installed")
        self.assertEqual(page.status(_state(available=True)), "available")
        self.assertEqual(page.status(_state()), "absent")
        # Cursor: installed unknown, a copy on disk.
        self.assertEqual(page.status(_state(installed=None, copies=["/c"])), "cached")
        self.assertEqual(page.status(_state(installed=None, available=True)), "available")
        self.assertEqual(page.status(None), "absent")

    def test_worst_drift_and_where(self):
        self.assertIsNone(page.worst_drift(None))
        self.assertIsNone(page.worst_drift({"claude": {"state": "installed"}}))
        worst = page.worst_drift({"claude": {"state": "stale"}, "codex": {"state": "not-installed"},
                                  "cursor": {"state": "not-installed"}})
        self.assertEqual(worst, {"state": "not-installed", "title": "Not installed",
                                 "harnesses": ["codex", "cursor"]})
        self.assertEqual(page.worst_drift({"codex": {"state": "from-the-future"}})["title"], "Needs a look")


class TestSources(unittest.TestCase):
    def test_sources_come_in_order_and_no_plugin_is_lost(self):
        # PluginCatalogTests.sourcesComeInOrderAndNoPluginIsLost
        absent, listed = _state(), _state(available=True)
        catalog = {"harnesses": ["claude", "codex"], "actions": ["claude", "codex"], "marketplaces": [
            {"name": "zeta", "harnesses": {"gemini": {"source_type": "url"}},
             "plugins": [_plugin("g", "zeta", {"gemini": listed})]},
            {"name": "beta", "harnesses": {"codex": {"source_type": "directory"},
                                           "claude": {"source_type": "github"}},
             "plugins": [_plugin("lost", "beta", {"claude": absent, "codex": absent})]},
            {"name": "alpha", "harnesses": {"claude": {"source_type": "github"}},
             "plugins": [_plugin("a", "alpha", {"claude": listed})]},
            {"name": "old", "harnesses": {},
             "plugins": [_plugin("o", "old", {"codex": _state(installed=True)})]},
            {"name": "hub", "hub": True, "harnesses": {},
             "plugins": [_plugin("h", "hub", {"claude": listed, "codex": absent})]},
        ]}
        plugins, sources = _index(catalog)
        self.assertEqual([s["id"] for s in sources], ["local", "claude", "gemini", "unregistered"])
        self.assertEqual([m["id"] for m in sources[1]["marketplaces"]], ["claude/beta", "claude/alpha"])
        self.assertEqual(_tree(sources)["claude"]["claude/beta"], ["lost@beta"])
        self.assertEqual(sources[2]["note"], "Read only")
        self.assertEqual(sources[1]["note"], "Marketplaces Claude Code registers")
        self.assertEqual(sources[0]["marketplaces"][0]["tag"], "local")
        self.assertEqual(sources[0]["marketplaces"][0]["plugins"][0]["harnesses"], ["claude", "codex"])
        refs = {e["ref"] for s in sources for m in s["marketplaces"] for e in m["plugins"]}
        self.assertEqual(refs, set(plugins))

    def test_a_copy_installed_where_the_marketplace_is_no_longer_registered(self):
        catalog = {"harnesses": ["claude", "codex"], "actions": ["claude", "codex"], "marketplaces": [
            {"name": "split", "harnesses": {"claude": {"source_type": "github"}}, "plugins": [
                _plugin("p", "split", {"claude": _state(available=True),
                                       "codex": _state(installed=True, enabled=True)}),
                _plugin("q", "split", {"claude": _state(),
                                       "codex": _state(installed=True, enabled=False)}),
            ]},
        ]}
        _plugins, sources = _index(catalog)
        self.assertEqual(_tree(sources), {
            "claude": {"claude/split": ["p@split"]},
            "unregistered": {"unregistered/split": ["p@split", "q@split"]},
        })
        self.assertEqual([e["harnesses"] for e in sources[1]["marketplaces"][0]["plugins"]],
                         [["codex"], ["codex"]])


class TestBuilt(_CatalogHome, unittest.TestCase):
    """Against test_plugin_catalog's fake home: a hub marketplace, a github
    one registered in claude and codex, a cursor cache, a gone marketplace."""

    def setUp(self):
        super().setUp()
        page.clear_cache()
        self.addCleanup(page.clear_cache)

    def test_explore_places_every_plugin(self):
        body = page.explore(self.home)
        self.assertEqual(body["harnesses"], ["claude", "codex", "cursor"])
        self.assertEqual(body["total"], len(body["plugins"]))
        ids = [s["id"] for s in body["sources"]]
        self.assertEqual(ids[0], "local")
        self.assertEqual(ids[-1], "unregistered")
        lsp = body["plugins"]["lsp@official"]
        self.assertEqual(lsp["harnesses"]["claude"]["status"], "disabled")
        self.assertEqual(lsp["skills"], [{"name": "hover", "description": "Hover docs"}])
        self.assertEqual(lsp["author"], "Anthropic")
        self.assertEqual(body["plugins"]["slack@cursor-public"]["harnesses"]["cursor"]["status"], "cached")
        placed = {e["ref"] for s in body["sources"] for m in s["marketplaces"] for e in m["plugins"]}
        self.assertEqual(placed, set(body["plugins"]))
        official = [m for s in body["sources"] for m in s["marketplaces"] if m["name"] == "official"]
        self.assertEqual([m["tag"] for m in official], ["github", "git"])

    def test_a_missing_folder_is_written_from_home(self):
        inside = {"harness": "claude", "marketplace": "nus", "detail": "x",
                  "path": str(self.home / ".quiver" / "plugins" / "nus")}
        self.assertEqual(page._error(inside, self.home)["short_path"], "~/.quiver/plugins/nus")
        outside = {**inside, "path": "/srv/plugins/nus"}
        self.assertEqual(page._error(outside, self.home)["short_path"], "/srv/plugins/nus")
        plain = {"harness": "codex", "marketplace": None, "detail": "unreadable"}
        self.assertEqual(page._error(plain, self.home), plain)

    def test_display_names_overlay_names_rows(self):
        cfg = self.home / ".quiver" / "config" / "plugin-display-names.json"
        _write(cfg, json.dumps({"marketplaces": {"official": "Official"},
                                "plugins": {"lsp@official": "Language", "x@y": ""}}))
        body = page.explore(self.home)
        self.assertEqual(body["plugins"]["lsp@official"]["title"], "Language")
        titles = {m["title"] for s in body["sources"] for m in s["marketplaces"] if m["name"] == "official"}
        self.assertEqual(titles, {"Official"})
        cfg.write_text("not json")
        self.assertEqual(page.display_names(self.home), {"marketplaces": {}, "plugins": {}})

    def test_health_titles_each_finding(self):
        # The source moves on, so both installed copies are out of date.
        _write(self.plugin / "skills" / "go" / "SKILL.md", "---\nname: go\n---\nchanged\n")
        report = page.health(self.home)
        self.assertEqual(report["sources"], 1)
        stale = [f for f in report["drift"] if f["kind"] == "stale"]
        self.assertTrue(stale)
        self.assertEqual(stale[0]["kind_title"], "Out of date")
        self.assertFalse(stale[0]["severe"])
        self.assertIn("skills/go/SKILL.md", stale[0]["paths"])

    def test_views_are_cached_until_refresh(self):
        clock = [1000.0]
        with patch.object(page, "explore", side_effect=lambda h: {"n": clock[0]}) as build:
            first = page.cached("explore", self.home, now=lambda: clock[0])
            clock[0] += 30
            self.assertEqual(page.cached("explore", self.home, now=lambda: clock[0]), first)
            self.assertEqual(build.call_count, 1)
            fresh = page.cached("explore", self.home, refresh=True, now=lambda: clock[0])
            self.assertEqual(fresh, ({"n": 1030.0}, 1030.0))
            clock[0] += page.CACHE_TTL_S + 1
            page.cached("explore", self.home, now=lambda: clock[0])
            self.assertEqual(build.call_count, 3)

    def test_get_checks_the_view(self):
        ctx = PageContext(home=self.home, state=type("S", (), {"machine": {"name": "box"}})())
        with self.assertRaises(PageError) as raised:
            page.get(ctx, {"view": "nope"})
        self.assertEqual(raised.exception.status, 400)
        body = page.get(ctx, {})
        self.assertEqual((body["view"], body["machine"], body["sources"]), ("health", "box", 1))
        self.assertIsInstance(body["built_at"], float)


class TestRoute(HubBase):
    def setUp(self):
        super().setUp()
        page.clear_cache()
        self.addCleanup(page.clear_cache)

    def test_needs_a_device_and_answers_both_views(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/plugins")[0], 401)
        with patch.object(page, "health", return_value={"sources": 0, "drift": []}), \
                patch.object(page, "explore", return_value={"sources": [], "plugins": {}, "total": 0}):
            status, data = self.srv.request("GET", "/v1/app/plugins?view=health", headers=self.auth())
            self.assertEqual(status, 200, data)
            self.assertEqual(json.loads(data)["sources"], 0)
            status, data = self.srv.request("GET", "/v1/app/plugins?view=explore&refresh=1",
                                            headers=self.auth())
            self.assertEqual((status, json.loads(data)["view"]), (200, "explore"))
        status, _ = self.srv.request("GET", "/v1/app/plugins?view=bad", headers=self.auth())
        self.assertEqual(status, 400)

    def test_an_unreadable_registry_is_a_503(self):
        with patch.object(page, "health", side_effect=ValueError("bad json")):
            status, data = self.srv.request("GET", "/v1/app/plugins", headers=self.auth())
        self.assertEqual(status, 503)
        self.assertIn("bad json", data.decode())

    def test_page_script_and_styles_are_bundled(self):
        status, body = self.srv.request("GET", "/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b".pl-market", self.srv.request("GET", "/pages.css")[1])


# ---------------------------------------------------------------- Fixes


class _Runner:
    """Stands in for subprocess.run: records each argv and the HOME it ran
    under, and for the commands it knows makes the change the harness CLI
    would, on the fake home. ``fail`` names an argv[0:3] to fail."""

    def __init__(self, fake, fail=None, also=None):
        self.fake, self.fail, self.also = fake, fail, also
        self.calls, self.homes = [], []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        self.homes.append(kw["env"]["HOME"])
        if self.fail and tuple(argv[:3]) == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "Error: marketplace is busy\n")
        f = self.fake
        if argv[:3] == ["claude", "plugin", "install"]:
            f.installed["plugins"]["tool@mk"] = [
                {"scope": "user", "installPath": str(f.claude_copy), "version": "1.0.0"}]
            f._write_claude()
        elif argv[:4] == ["claude", "plugin", "marketplace", "add"]:
            f.known = {"mk": {"source": {"source": "directory", "path": argv[4]},
                              "installLocation": argv[4]}}
            f._write_claude()
        elif argv[:4] == ["codex", "plugin", "marketplace", "add"]:
            f._write_codex(register=True)
        elif argv[:3] == ["codex", "plugin", "add"]:
            _codex_copy(f.plugin, f.codex_copy)
        if self.also:
            self.also()
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")


class _Ctx:
    machine = {"name": "box"}


class TestFix(_FakeHome, unittest.TestCase):
    """post() against a real temp HOME: _FakeHome's marketplace ``mk`` with
    plugin ``tool`` installed in claude and codex, broken per test."""

    def setUp(self):
        super().setUp()
        page.clear_cache()
        self.addCleanup(page.clear_cache)
        self.ctx = PageContext(home=self.home, state=_Ctx())

    def run_with(self, runner, action, body):
        with patch.object(page, "_run", runner):
            return page.post(self.ctx, action, body)

    def refused(self, action, body, runner=None):
        runner = runner or _Runner(self)
        with self.assertRaises(PageError) as raised:
            self.run_with(runner, action, body)
        self.assertEqual(runner.calls, [], "a refusal must run nothing")
        return raised.exception

    def uninstall_claude(self):
        self.installed["plugins"] = {}
        self._write_claude()
        return "claude:not-installed:tool@mk"

    def unregister_codex(self):
        self._write_codex(register=False)
        return f"codex:unregistered:{self.market}"

    def test_fix_runs_the_findings_commands_and_answers_the_fresh_view(self):
        fid = self.uninstall_claude()
        runner = _Runner(self)
        body = self.run_with(runner, "fix", {"id": fid})
        self.assertEqual(runner.calls, [["claude", "plugin", "install", "tool@mk"]])
        # The harness CLI acts on the hub's home, not the caller's shell.
        self.assertEqual(runner.homes, [str(self.home)])
        self.assertEqual(body["action"], "fix")
        self.assertEqual(body["ran"], 1)
        [result] = body["results"]
        self.assertEqual((result["id"], result["status"], result["error"]), (fid, "fixed", None))
        # FixedDrift's "fixed": the finding as swe find plugins --json lists it.
        self.assertEqual(result["fixed"]["fix"], "claude plugin install tool@mk")
        self.assertTrue(result["fixed"]["runnable"])
        health = body["health"]
        self.assertEqual((health["view"], health["machine"], health["drift"]), ("health", "box", []))
        self.assertIsInstance(health["built_at"], float)

    def test_a_fix_drops_both_cached_views(self):
        fid = self.uninstall_claude()
        old = page.cached("health", self.home)
        self.assertEqual([f["id"] for f in old[0]["drift"]], [fid])
        page._cache[("explore", str(self.home))] = ({"old": True}, old[1])
        self.run_with(_Runner(self), "fix", {"id": fid})
        self.assertNotIn(("explore", str(self.home)), page._cache)
        # The next GET reads the fixed state, not the 60 s old one.
        body = page.get(self.ctx, {})
        self.assertEqual(body["drift"], [])

    def test_a_failed_command_is_reported_with_the_fresh_view(self):
        fid = self.uninstall_claude()
        runner = _Runner(self, fail=("claude", "plugin", "install"))
        body = self.run_with(runner, "fix", {"id": fid})
        [result] = body["results"]
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["fixed"])
        self.assertIn("claude plugin install tool@mk failed: Error: marketplace is busy", result["error"])
        self.assertEqual(body["ran"], 0)
        self.assertEqual([f["id"] for f in body["health"]["drift"]], [fid])

    def test_a_harness_cli_missing_from_path_is_a_failed_result(self):
        fid = self.uninstall_claude()

        def missing(argv, **kw):
            raise FileNotFoundError(argv[0])

        body = self.run_with(missing, "fix", {"id": fid})
        self.assertEqual(body["results"][0]["error"], "claude is not on PATH")

    def test_fix_all_runs_in_report_order_one_at_a_time(self):
        claude = self.uninstall_claude()
        codex = self.unregister_codex()
        runner = _Runner(self)
        # The page sends what its confirmation listed; report order wins.
        body = self.run_with(runner, "fix_all", {"ids": [codex, claude]})
        self.assertEqual(runner.calls, [["claude", "plugin", "install", "tool@mk"],
                                        ["codex", "plugin", "marketplace", "add", str(self.market)]])
        self.assertEqual([(r["id"], r["status"]) for r in body["results"]],
                         [(claude, "fixed"), (codex, "fixed")])
        self.assertEqual((body["action"], body["ran"], body["health"]["drift"]), ("fix_all", 2, []))

    def test_fix_all_skips_one_an_earlier_fix_cleared(self):
        claude = self.uninstall_claude()
        codex = self.unregister_codex()
        # Installing in claude happens to register codex too.
        runner = _Runner(self, also=lambda: self._write_codex(register=True))
        body = self.run_with(runner, "fix_all", {"ids": [claude, codex]})
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual([(r["id"], r["status"]) for r in body["results"]],
                         [(claude, "fixed"), (codex, "gone")])
        self.assertEqual(body["ran"], 1)

    def test_fix_all_stops_at_the_first_failure(self):
        claude = self.uninstall_claude()
        codex = self.unregister_codex()
        runner = _Runner(self, fail=("claude", "plugin", "install"))
        body = self.run_with(runner, "fix_all", {"ids": [claude, codex]})
        self.assertEqual(runner.calls, [["claude", "plugin", "install", "tool@mk"]])
        self.assertEqual([(r["id"], r["status"]) for r in body["results"]],
                         [(claude, "failed"), (codex, "not-run")])
        self.assertEqual({f["id"] for f in body["health"]["drift"]}, {claude, codex})

    def test_fix_all_never_runs_a_finding_a_fix_exposed(self):
        # Codex has neither the marketplace nor the copy: registering it
        # reveals "not installed", which waits for its own confirmation.
        shutil.rmtree(self.codex_copy.parent)
        codex = self.unregister_codex()
        runner = _Runner(self)
        body = self.run_with(runner, "fix_all", {"ids": [codex]})
        self.assertEqual(runner.calls, [["codex", "plugin", "marketplace", "add", str(self.market)]])
        self.assertEqual([f["id"] for f in body["health"]["drift"]], ["codex:not-installed:tool@mk"])

    def test_only_ids_health_lists_now_are_accepted(self):
        self.uninstall_claude()
        error = self.refused("fix", {"id": "claude:stale:tool@mk"})
        self.assertEqual((error.status, error.code), (404, "unknown_finding"))
        # No command from the client is ever taken, whatever it looks like.
        error = self.refused("fix", {"id": "claude plugin install tool@mk; rm -rf ~"})
        self.assertEqual(error.status, 404)
        error = self.refused("fix_all", {"ids": ["claude:not-installed:tool@mk", "codex:stale:tool@mk"]})
        self.assertEqual((error.status, error.code), (404, "unknown_finding"))

    def test_a_hand_edit_is_refused(self):
        self.codex_enabled = "false"
        self._write_codex()
        error = self.refused("fix", {"id": "codex:disabled-here:tool@mk"})
        self.assertEqual((error.status, error.code), (422, "not_runnable"))
        self.assertIn("enabled = true", error.message)

    def test_bad_bodies_are_refused(self):
        fid = self.uninstall_claude()
        for action, body in (
            ("fix", {}), ("fix", {"id": 3}), ("fix", {"id": ""}), ("fix", {"id": "x" * 5000}),
            ("fix", {"ids": [fid]}),
            ("fix_all", {}), ("fix_all", {"ids": []}), ("fix_all", {"ids": fid}),
            ("fix_all", {"ids": [fid, fid]}), ("fix_all", {"ids": [fid, None]}),
            ("fix_all", {"ids": [f"{fid}{i}" for i in range(page.IDS_MAX + 1)]}),
        ):
            error = self.refused(action, body)
            self.assertEqual(error.status, 422, (action, body))
        self.assertEqual(self.refused("fix", ["not", "an", "object"]).status, 422)

    def test_unknown_actions_are_refused(self):
        fid = self.uninstall_claude()
        for action in ("install", "enable", "add_marketplace", "FIX", ""):
            error = self.refused(action, {"id": fid})
            self.assertEqual((error.status, error.code), (404, "unknown_action"))

    def test_one_fix_at_a_time(self):
        fid = self.uninstall_claude()
        page._WRITE_LOCK.acquire()
        try:
            error = self.refused("fix", {"id": fid})
        finally:
            page._WRITE_LOCK.release()
        self.assertEqual((error.status, error.code), (409, "busy"))
        # Free again once the other fix is done.
        self.assertEqual(self.run_with(_Runner(self), "fix", {"id": fid})["ran"], 1)


class TestPluginAction(_FakeHome, unittest.TestCase):
    """The detail sheet's per-harness buttons: post("plugin", ...) against
    _FakeHome's ``tool@mk``, installed and enabled in claude and codex."""

    def setUp(self):
        super().setUp()
        page.clear_cache()
        self.addCleanup(page.clear_cache)
        self.ctx = PageContext(home=self.home, state=_Ctx())

    def act(self, verb, harness, runner=None, ref="tool@mk"):
        runner = runner or _Runner(self)
        with patch.object(page, "_run", runner):
            return page.post(self.ctx, "plugin", {"verb": verb, "ref": ref, "harness": harness}), runner

    def refused(self, body):
        runner = _Runner(self)
        with patch.object(page, "_run", runner), self.assertRaises(PageError) as raised:
            page.post(self.ctx, "plugin", body)
        self.assertEqual(runner.calls, [], "a refusal must run nothing")
        return raised.exception

    def test_explore_offers_each_harness_its_actions(self):
        tool = page.explore(self.home)["plugins"]["tool@mk"]
        self.assertEqual(tool["harnesses"]["claude"]["actions"], ["disable", "uninstall"])
        self.assertEqual(tool["harnesses"]["codex"]["actions"], ["disable", "uninstall"])

    def test_install_runs_the_harness_cli_and_answers_the_fresh_view(self):
        self.installed["plugins"] = {}
        self._write_claude()
        page.explore(self.home)
        body, runner = self.act("install", "claude")
        self.assertEqual(runner.calls, [["claude", "plugin", "install", "tool@mk"]])
        self.assertEqual(runner.homes, [str(self.home)])
        self.assertIsNone(body["error"])
        self.assertTrue(body["result"]["changed"])
        self.assertIn(body["explore"]["plugins"]["tool@mk"]["harnesses"]["claude"]["status"],
                      ("enabled", "installed"))

    def test_codex_disable_is_swes_own_config_edit(self):
        body, runner = self.act("disable", "codex")
        self.assertEqual(runner.calls, [])
        self.assertTrue(body["result"]["changed"])
        self.assertTrue(body["result"]["edited"].endswith("config.toml"))
        codex = body["explore"]["plugins"]["tool@mk"]["harnesses"]["codex"]
        self.assertEqual((codex["status"], codex["actions"]), ("disabled", ["enable", "uninstall"]))

    def test_a_failed_command_is_the_error_with_the_fresh_view(self):
        self.installed["plugins"] = {}
        self._write_claude()
        body, _ = self.act("install", "claude", runner=_Runner(self, fail=("claude", "plugin", "install")))
        self.assertIsNone(body["result"])
        self.assertIn("marketplace is busy", body["error"])
        self.assertIn("tool@mk", body["explore"]["plugins"])

    def test_only_what_the_sheet_offers(self):
        for body, want in (({"verb": "install", "ref": "tool@mk", "harness": "claude"}, (409, "not_offered")),
                           ({"verb": "enable", "ref": "tool@mk", "harness": "cursor"}, (409, "not_offered")),
                           ({"verb": "install", "ref": "nope@mk", "harness": "claude"}, (404, "unknown_plugin")),
                           ({"verb": "install", "ref": "tool@mk"}, (422, "bad_action")),
                           ({"verb": ["x"], "ref": "tool@mk", "harness": "claude"}, (422, "bad_action"))):
            error = self.refused(body)
            self.assertEqual((error.status, error.code), want, body)

    def test_one_change_at_a_time(self):
        page._WRITE_LOCK.acquire()
        try:
            error = self.refused({"verb": "disable", "ref": "tool@mk", "harness": "claude"})
        finally:
            page._WRITE_LOCK.release()
        self.assertEqual((error.status, error.code), (409, "busy"))


class TestHarnessActions(unittest.TestCase):
    def test_matches_the_apps_menu(self):
        self.assertEqual(page.harness_actions(_state(installed=True, enabled=True), True), ["disable", "uninstall"])
        self.assertEqual(page.harness_actions(_state(installed=True, enabled=False), True), ["enable", "uninstall"])
        self.assertEqual(page.harness_actions(_state(installed=True), True), ["uninstall"])
        self.assertEqual(page.harness_actions(_state(available=True), True), ["install"])
        self.assertEqual(page.harness_actions(_state(), True), [])
        self.assertEqual(page.harness_actions(_state(installed=None, available=True), True), [])
        self.assertEqual(page.harness_actions(_state(installed=True, enabled=True), False), [])


class _Fixture(_FakeHome, unittest.TestCase):
    def runTest(self):
        pass


class TestFixRoute(HubBase):
    """POST /v1/app/plugins/<action> through the hub, on a _FakeHome laid
    out in the hub's own home."""

    def setUp(self):
        super().setUp()
        page.clear_cache()
        self.addCleanup(page.clear_cache)
        self.fake = _Fixture()
        self.addCleanup(self.fake.doCleanups)
        with patch("tempfile.mkdtemp", return_value=str(self.srv.home)):
            self.fake.setUp()
        self.fake.installed["plugins"] = {}
        self.fake._write_claude()
        self.fid = "claude:not-installed:tool@mk"
        self.runner = _Runner(self.fake)
        patcher = patch.object(page, "_run", self.runner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def origin(self):
        return f"http://127.0.0.1:{self.srv.port}"

    def test_needs_a_device(self):
        status, _ = self.srv.request("POST", "/v1/app/plugins/fix", body={"id": self.fid})
        self.assertEqual(status, 401)
        self.assertEqual(self.runner.calls, [])

    def test_a_cross_site_origin_is_refused(self):
        status, data = self.srv.request("POST", "/v1/app/plugins/fix", body={"id": self.fid},
                                        headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertEqual(self.runner.calls, [])

    def test_fix_through_the_hub(self):
        auth = self.auth()
        _, data = self.srv.request("GET", "/v1/app/plugins", headers=auth)
        self.assertEqual([f["id"] for f in json.loads(data)["drift"]], [self.fid])
        status, data = self.srv.request("POST", "/v1/app/plugins/fix", body={"id": self.fid},
                                        headers={**auth, "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(body["results"][0]["status"], "fixed")
        self.assertEqual(body["health"]["drift"], [])
        self.assertEqual(self.runner.calls, [["claude", "plugin", "install", "tool@mk"]])
        # The read route sees the change at once, cache or not.
        _, data = self.srv.request("GET", "/v1/app/plugins", headers=auth)
        self.assertEqual(json.loads(data)["drift"], [])

    def test_plugin_action_through_the_hub(self):
        status, data = self.srv.request("POST", "/v1/app/plugins/plugin",
                                        body={"verb": "install", "ref": "tool@mk", "harness": "claude"},
                                        headers={**self.auth(), "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.runner.calls, [["claude", "plugin", "install", "tool@mk"]])
        self.assertTrue(json.loads(data)["result"]["changed"])

    def test_fix_all_through_the_hub(self):
        status, data = self.srv.request("POST", "/v1/app/plugins/fix_all", body={"ids": [self.fid]},
                                        headers=self.auth())
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["ran"], 1)

    def test_refusals_carry_their_status(self):
        auth = self.auth()
        for action, body, want in (
            ("fix", {"id": "claude:stale:tool@mk"}, 404),
            ("fix", {"id": 7}, 422),
            ("fix_all", {"ids": []}, 422),
            ("install", {"id": self.fid}, 404),
        ):
            status, data = self.srv.request("POST", f"/v1/app/plugins/{action}", body=body, headers=auth)
            self.assertEqual(status, want, (action, data))
            self.assertIn("message", json.loads(data)["error"])
        self.assertEqual(self.runner.calls, [])


if __name__ == "__main__":
    unittest.main()
