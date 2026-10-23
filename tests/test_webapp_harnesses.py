"""The Harnesses page's data: every registered harness, archived included."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.webapp import harnesses
from quiver.webapp.pages import PageError
from quiver.webapp.pages import harness as harness_page

from tests.test_remote_hub import HubBase

REGISTRY = {
    "claude": {"command": "claude", "aliases": ["cc"], "tags": ["agentic"]},
    "codex": {"command": "codex"},
    "ollama": {"command": "ollama"},
}
ARCHIVE = {"ollama": {"reason": "not a harness", "archived_at": "2026-10-06T10:34:07",
                      "usage": "unknown"}}


class _Registry:
    def setUp(self):
        self._patches = [
            patch("quiver.harness.registry.load_registry_readonly", return_value=dict(REGISTRY)),
            patch("quiver.harness.stars.load_stars", return_value=["claude"]),
            patch("quiver.harness.archive.load_archive", return_value=dict(ARCHIVE)),
            patch("quiver.sessions.usage.session_counts", return_value={"claude": 12, "codex": 3}),
            patch("quiver.harness.tools.is_installed", side_effect=lambda cmd: cmd != "codex"),
        ]
        for p in self._patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self._patches):
            p.stop()


class TestRows(_Registry, unittest.TestCase):
    def test_every_harness_with_state_counts_and_no_limits(self):
        rows = {r["name"]: r for r in harnesses.rows()}
        self.assertEqual(list(rows), ["claude", "codex", "ollama"])
        self.assertEqual(rows["claude"]["state"], "starred")
        self.assertEqual(rows["claude"]["aliases"], ["cc"])
        self.assertEqual(rows["claude"]["sessions_100d"], 12)
        self.assertFalse(rows["codex"]["installed"])
        self.assertEqual(rows["ollama"]["state"], "archived")
        self.assertEqual(rows["ollama"]["archived"]["reason"], "not a harness")
        self.assertTrue(all(r["rate_limit"] is None for r in rows.values()))


class TestRoute(_Registry, HubBase):
    def test_needs_a_device_and_returns_the_rows(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/harnesses")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/harnesses", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual(len(body["harnesses"]), 3)
        self.assertEqual(body["window_days"], 100)
        self.assertEqual(body["machine"], self.srv.state.machine["name"])

    def test_an_unreadable_registry_is_a_503(self):
        with patch("quiver.webapp.harnesses.rows", side_effect=ValueError("bad json")):
            status, data = self.srv.request("GET", "/v1/app/harnesses", headers=self.auth())
        self.assertEqual(status, 503)
        self.assertIn("bad json", data.decode())

    def test_page_script_is_served(self):
        self.assertEqual(self.srv.request("GET", "/app/manage.js")[0], 200)


# ----------------------------------------------------------------- writes

ON_DISK = {
    "claude": {"command": "claude", "aliases": ["cc"], "state": "starred", "pin": 1},
    "codex": {"command": "codex"},
    "gemini": {"command": "gemini"},
    "ollama": {"command": "ollama", "state": "archived",
               "archived": {"reason": "not a harness", "archived_at": "2026-10-06T10:34:07",
                            "usage": "unknown"}},
}


class _TempRegistry:
    """A real harness.json in a temp dir: the writes go through the
    registry for real, and the tests read the file back."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        config = Path(self._tmp.name) / ".quiver" / "config"
        config.mkdir(parents=True)
        self.file = config / "harness.json"
        self.file.write_text(json.dumps(ON_DISK, indent=2))
        self._patches = [
            patch("quiver.harness.registry.CONFIG_DIR", config),
            patch("quiver.harness.registry.HARNESS_FILE", self.file),
            patch("quiver.harness.registry.TOOLS_FILE", config / "tools.json"),
            patch("quiver.sessions.usage.session_counts", return_value={"claude": 12, "codex": 3}),
            patch("quiver.harness.tools.is_installed", return_value=True),
        ]
        for p in self._patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def disk(self) -> dict:
        return json.loads(self.file.read_text())


class TestWrites(_TempRegistry, unittest.TestCase):
    def post(self, action, **body):
        return harness_page.post(None, action, body)

    def refused(self, action, **body) -> PageError:
        before = self.file.read_text()
        with self.assertRaises(PageError) as caught:
            self.post(action, **body)
        self.assertEqual(self.file.read_text(), before, "a refusal must not write")
        return caught.exception

    def test_star_pins_it_and_returns_the_row(self):
        reply = self.post("star", name="codex")
        self.assertEqual(reply["name"], "codex")
        self.assertEqual(reply["harness"]["state"], "starred")
        self.assertEqual(reply["harness"]["sessions_100d"], 3)
        self.assertEqual(set(reply["harness"]), set(harnesses.rows()[0]))
        disk = self.disk()
        self.assertEqual((disk["codex"]["state"], disk["codex"]["pin"]), ("starred", 1))
        self.assertEqual(disk["claude"]["pin"], 2)

    def test_star_on_a_starred_harness_changes_nothing(self):
        self.post("star", name="codex")
        before = self.file.read_text()
        self.assertEqual(self.post("star", name="claude")["harness"]["state"], "starred")
        # Not a toggle, and not a reorder: a double click can't undo it.
        self.assertEqual(self.file.read_text(), before)

    def test_star_resolves_an_alias(self):
        self.post("unstar", name="claude")
        reply = self.post("star", name="cc")
        self.assertEqual(reply["name"], "claude")
        self.assertEqual(self.disk()["claude"]["state"], "starred")

    def test_star_refuses_an_archived_harness(self):
        error = self.refused("star", name="ollama")
        self.assertEqual((error.status, error.code), (409, "archived"))
        self.assertEqual(self.disk()["ollama"]["archived"]["reason"], "not a harness")

    def test_unstar(self):
        reply = self.post("unstar", name="claude")
        self.assertEqual(reply["harness"]["state"], "active")
        self.assertNotIn("state", self.disk()["claude"])
        self.assertNotIn("pin", self.disk()["claude"])

    def test_unstar_on_an_unstarred_harness_changes_nothing(self):
        before = self.file.read_text()
        self.assertEqual(self.post("unstar", name="codex")["harness"]["state"], "active")
        self.assertEqual(self.file.read_text(), before)

    def test_archive_records_a_trimmed_one_line_reason(self):
        reply = self.post("archive", name="claude", reason="  too slow,\n  and   no MCP  ")
        row = reply["harness"]
        self.assertEqual(row["state"], "archived")
        self.assertEqual(row["archived"]["reason"], "too slow, and no MCP")
        entry = self.disk()["claude"]
        self.assertEqual(entry["state"], "archived")
        self.assertNotIn("pin", entry, "archived and starred are exclusive")
        self.assertEqual(entry["archived"]["reason"], "too slow, and no MCP")
        self.assertTrue(entry["archived"]["archived_at"])
        self.assertEqual(entry["archived"]["usage"], "used")  # 12 sessions, from the count
        self.assertEqual(entry["command"], "claude", "the rest of the row is kept")

    def test_archive_needs_a_reason(self):
        for body in ({}, {"reason": ""}, {"reason": "  \n\t "}):
            error = self.refused("archive", name="codex", **body)
            self.assertEqual((error.status, error.code), (400, "reason_required"))
        error = self.refused("archive", name="codex", reason=["no"])
        self.assertEqual((error.status, error.code), (400, "bad_reason"))

    def test_archive_caps_the_reason(self):
        error = self.refused("archive", name="codex", reason="x" * (harness_page.REASON_MAX + 1))
        self.assertEqual((error.status, error.code), (400, "reason_too_long"))
        ok = self.post("archive", name="codex", reason="x" * harness_page.REASON_MAX)
        self.assertEqual(len(ok["harness"]["archived"]["reason"]), harness_page.REASON_MAX)

    def test_restore(self):
        reply = self.post("restore", name="ollama")
        self.assertEqual(reply["harness"]["state"], "active")
        self.assertIsNone(reply["harness"]["archived"])
        self.assertEqual(self.disk()["ollama"], {"command": "ollama"})

    def test_restore_never_archives(self):
        # swe hs archive <name> with no reason restores an archived harness
        # and archives any other: restore here must only ever restore.
        before = self.file.read_text()
        self.assertEqual(self.post("restore", name="codex")["harness"]["state"], "active")
        self.assertEqual(self.file.read_text(), before)

    def test_restore_of_a_row_that_was_only_an_archive_record(self):
        disk = self.disk()
        disk["gone"] = {"state": "archived", "archived": {"reason": "uninstalled"}}
        self.file.write_text(json.dumps(disk))
        reply = self.post("restore", name="gone")
        self.assertEqual(reply, {"name": "gone", "harness": None})
        self.assertNotIn("gone", self.disk())

    def test_the_name_must_be_registered(self):
        error = self.refused("star", name="nope")
        self.assertEqual((error.status, error.code), (404, "unknown_harness"))
        for name in (None, "", "  ", 3, ["claude"], "x" * 500):
            error = self.refused("star", name=name)
            self.assertEqual((error.status, error.code), (400, "bad_name"), name)

    def test_unknown_actions_are_refused(self):
        for action in ("toggle", "remove", "edit", "STAR", ""):
            error = self.refused(action, name="claude")
            self.assertEqual((error.status, error.code), (404, "unknown_action"))

    def test_concurrent_writes_all_land(self):
        disk = {f"h{i}": {"command": f"h{i}"} for i in range(12)}
        self.file.write_text(json.dumps(disk))
        threads = [threading.Thread(target=self.post, args=("star",), kwargs={"name": name})
                   for name in disk]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(row.get("state") == "starred" for row in self.disk().values()))


class TestWriteRoute(_TempRegistry, HubBase):
    def origin(self):
        return f"http://127.0.0.1:{self.srv.port}"

    def test_needs_a_device(self):
        before = self.file.read_text()
        status, _ = self.srv.request("POST", "/v1/app/harness/star", body={"name": "codex"})
        self.assertEqual(status, 401)
        self.assertEqual(self.file.read_text(), before)

    def test_star_and_archive_through_the_hub(self):
        auth = self.auth()
        status, data = self.srv.request("POST", "/v1/app/harness/star", body={"name": "codex"},
                                        headers={**auth, "Origin": self.origin()})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["harness"]["state"], "starred")
        status, data = self.srv.request("POST", "/v1/app/harness/archive",
                                        body={"name": "gemini", "reason": "no MCP"}, headers=auth)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.disk()["gemini"]["archived"]["reason"], "no MCP")
        # The read route sees the change.
        _, data = self.srv.request("GET", "/v1/app/harnesses", headers=auth)
        rows = {r["name"]: r for r in json.loads(data)["harnesses"]}
        self.assertEqual(rows["codex"]["state"], "starred")
        self.assertEqual(rows["gemini"]["state"], "archived")

    def test_a_cross_site_origin_is_refused(self):
        before = self.file.read_text()
        status, data = self.srv.request("POST", "/v1/app/harness/star", body={"name": "codex"},
                                        headers={**self.auth(), "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("bad_origin", data.decode())
        self.assertEqual(self.file.read_text(), before)

    def test_refusals_carry_their_status(self):
        auth = self.auth()
        for action, body, want in (
            ("archive", {"name": "codex", "reason": " "}, 400),
            ("star", {"name": "nope"}, 404),
            ("star", {"name": "ollama"}, 409),
            ("toggle", {"name": "codex"}, 404),
        ):
            status, data = self.srv.request("POST", f"/v1/app/harness/{action}", body=body, headers=auth)
            self.assertEqual(status, want, (action, data))
            self.assertIn("message", json.loads(data)["error"])

    def test_an_unreadable_body_is_a_400(self):
        status, _ = self.srv.request("POST", "/v1/app/harness/star", body=["codex"], headers=self.auth())
        self.assertEqual(status, 400)

    def test_a_corrupt_registry_is_a_503_not_a_wipe(self):
        self.file.write_text('{"claude": "oops"}')
        status, _ = self.srv.request("POST", "/v1/app/harness/star", body={"name": "claude"},
                                     headers=self.auth())
        self.assertEqual(status, 503)
        self.assertEqual(self.file.read_text(), '{"claude": "oops"}')

    def test_the_page_has_no_get(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/harness", headers=self.auth())[0], 404)

    def test_page_assets_carry_the_writes(self):
        _, js = self.srv.request("GET", "/app/manage.js")
        self.assertIn(b"/v1/app/harness/", js)
        _, css = self.srv.request("GET", "/app/pages.css")
        self.assertIn(b".harness-archive", css)


if __name__ == "__main__":
    unittest.main()
