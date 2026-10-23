"""The Plugins page: Health (drift) and Explore (the catalog as the app
places it), each cached for a short while, and the route with auth."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.webapp.pages import PageContext, PageError
from quiver.webapp.pages import plugins as page

try:  # `unittest discover -s tests` puts tests/ itself on sys.path
    from test_plugin_catalog import _CatalogHome
    from test_plugin_drift import _write
    from test_remote_hub import HubBase
except ImportError:  # `python -m unittest tests.test_webapp_plugins`
    from tests.test_plugin_catalog import _CatalogHome
    from tests.test_plugin_drift import _write
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
        status, body = self.srv.request("GET", "/app/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b'registerPage("plugins"', body)
        self.assertIn(b".pl-market", self.srv.request("GET", "/app/pages.css")[1])


if __name__ == "__main__":
    unittest.main()
