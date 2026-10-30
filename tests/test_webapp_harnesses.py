"""The Harnesses page's data: every registered harness, archived included."""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from quiver.harness.discover import HarnessFinding
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
        self.assertEqual(self.srv.request("GET", "/manage.js")[0], 200)


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
        for action in ("toggle", "remove", "delete", "STAR", ""):
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

    def test_the_page_reads_only_the_discover_view(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/harness", headers=self.auth())[0], 404)
        self.assertEqual(self.srv.request("GET", "/v1/app/harness?view=discover")[0], 401)
        with patch("quiver.harness.discover.discover_harnesses", return_value=[FOUND[0]]):
            status, data = self.srv.request("GET", "/v1/app/harness?view=discover", headers=self.auth())
        self.assertEqual(status, 200, data)
        self.assertEqual([f["name"] for f in json.loads(data)["findings"]], ["droid"])

    def test_edit_and_add_through_the_hub(self):
        auth = {**self.auth(), "Origin": self.origin()}
        status, data = self.srv.request("POST", "/v1/app/harness/edit", headers=auth,
                                        body={"name": "codex", "fields": {"notes": "fast"}})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.disk()["codex"]["notes"], "fast")
        status, data = self.srv.request("POST", "/v1/app/harness/edit", headers=auth,
                                        body={"name": "codex", "fields": {"aliases": "cc"}})
        self.assertEqual(status, 409, data)
        self.assertIn("alias_taken", data.decode())
        status, data = self.srv.request("POST", "/v1/app/harness/add", headers=auth,
                                        body={"name": "mycli", "command": "mycli"})
        self.assertEqual(status, 200, data)
        self.assertIn("mycli", self.disk())

    def test_a_cross_site_edit_is_refused(self):
        before = self.file.read_text()
        for action, body in (("edit", {"name": "codex", "fields": {"notes": "x"}}),
                             ("add", {"name": "mycli", "command": "mycli"}),
                             ("register", {"names": ["droid"]})):
            status, _ = self.srv.request("POST", f"/v1/app/harness/{action}", body=body,
                                         headers={**self.auth(), "Origin": "https://evil.example"})
            self.assertEqual(status, 403, action)
        self.assertEqual(self.file.read_text(), before)

    def test_page_assets_carry_the_writes(self):
        _, js = self.srv.request("GET", "/manage.js")
        self.assertIn(b"/v1/app/harness/", js)
        for action in (b'"edit"', b'"register"', b'"add"', b"view=discover"):
            self.assertIn(action, js)
        _, css = self.srv.request("GET", "/pages.css")
        self.assertIn(b".harness-archive", css)
        self.assertIn(b".harness-sheet", css)



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestHeaderActions(unittest.TestCase):
    """manage.js's Harnesses header: Detect and Add harness only where
    paintHarnesses binds them, never in the loading or error header."""

    def test_actions_only_when_painted(self):
        manage_js = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/manage.js"
        prelude = ("const esc = (s) => String(s); const icon = () => '';\n"
                   "const pageHeader = (t, s, actions = '') => actions;\n")
        body = "process.stdout.write(JSON.stringify([harnessesHeader(), harnessesHeader(true)]));\n"
        done = subprocess.run(["node", "-e", prelude + manage_js.read_text(encoding="utf-8") + "\n" + body],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        loading, painted = json.loads(done.stdout)
        self.assertNotIn("data-sheet", loading)
        self.assertIn('id="page-refresh"', loading)
        self.assertIn('data-sheet="detect"', painted)
        self.assertIn('data-sheet="add"', painted)


# ------------------------------------------------------------- the sheets

FOUND = [
    HarnessFinding("droid", "droid", "/opt/bin/droid", "high", "catalog", "new",
                   aliases=("dr", "cc"), tags=("agentic",)),
    HarnessFinding("maybe", "maybe", "/opt/bin/maybe", "low", "path_scan", "new"),
    HarnessFinding("claude", "claude", "/opt/bin/claude", "high", "catalog", "registered"),
]


class TestEdit(_TempRegistry, unittest.TestCase):
    def edit(self, name, **fields):
        return harness_page.post(None, "edit", {"name": name, "fields": fields})

    def refused(self, body) -> PageError:
        before = self.file.read_text()
        with self.assertRaises(PageError) as caught:
            harness_page.post(None, "edit", body)
        self.assertEqual(self.file.read_text(), before, "a refusal must not write")
        return caught.exception

    def test_saves_the_changed_fields_as_swe_edit_does(self):
        reply = self.edit("codex", display_name="  Codex CLI ", notes="Fast.\nNo MCP.",
                          aliases="cx, cx,  codex-cli", tags="agentic, byok", version="0.9")
        self.assertEqual(reply["name"], "codex")
        self.assertEqual(sorted(reply["changes"]), ["aliases", "display_name", "notes", "tags", "version"])
        entry = self.disk()["codex"]
        self.assertEqual(entry["display_name"], "Codex CLI")
        self.assertEqual(entry["notes"], "Fast.\nNo MCP.")
        self.assertEqual(entry["aliases"], ["cx", "codex-cli"])
        self.assertEqual(entry["tags"], ["agentic", "byok"])
        self.assertEqual(entry["version"], "0.9")
        # The reply's row is the list's row for the harness.
        self.assertEqual(reply["harness"], next(r for r in harnesses.rows() if r["name"] == "codex"))

    def test_an_empty_value_clears_the_field(self):
        self.edit("codex", version="1.0", notes="x")
        reply = self.edit("codex", version="", notes="")
        self.assertEqual(sorted(reply["changes"]), ["notes", "version"])
        self.assertNotIn("notes", self.disk()["codex"])
        self.assertIsNone(reply["harness"]["version"])

    def test_the_same_values_write_nothing(self):
        before = self.file.read_text()
        reply = self.edit("claude", command="claude", aliases="cc")
        self.assertEqual(reply["changes"], [])
        self.assertEqual(self.file.read_text(), before)

    def test_an_alias_resolves_the_harness(self):
        self.assertEqual(self.edit("cc", notes="mine")["name"], "claude")
        self.assertEqual(self.disk()["claude"]["notes"], "mine")

    def test_an_alias_another_harness_answers_to_is_refused(self):
        for alias in ("cc", "claude"):
            error = self.refused({"name": "codex", "fields": {"aliases": f"cx, {alias}"}})
            self.assertEqual((error.status, error.code), (409, "alias_taken"))
            self.assertIn("already used by 'claude'", error.message)

    def test_a_command_is_required(self):
        error = self.refused({"name": "codex", "fields": {"command": "  "}})
        self.assertEqual((error.status, error.code), (422, "command_required"))

    def test_only_the_editors_fields(self):
        for fields in ({}, None, ["notes"], {"primary_color": "#fff"}, {"state": "archived"},
                       {"notes": 3}, {"command": "a\nb"}, {"aliases": "a\nb"},
                       {"notes": "x" * 10001}):
            error = self.refused({"name": "codex", "fields": fields})
            self.assertEqual(error.status, 422, fields)

    def test_the_name_must_be_registered(self):
        error = self.refused({"name": "nope", "fields": {"notes": "x"}})
        self.assertEqual((error.status, error.code), (404, "unknown_harness"))
        error = self.refused({"name": "", "fields": {"notes": "x"}})
        self.assertEqual((error.status, error.code), (400, "bad_name"))


class TestDetect(_TempRegistry, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for p in (patch("quiver.harness.discover.discover_harnesses", return_value=list(FOUND)),
                  patch("quiver.harness.discover.live_version", return_value="1.2.3")):
            p.start()
            self.addCleanup(p.stop)

    def refused(self, names) -> PageError:
        before = self.file.read_text()
        with self.assertRaises(PageError) as caught:
            harness_page.post(None, "register", {"names": names})
        self.assertEqual(self.file.read_text(), before, "a refusal must not write")
        return caught.exception

    def test_the_scan_writes_nothing(self):
        before = self.file.read_text()
        with patch("pathlib.Path.home", return_value=Path("/opt")):
            reply = harness_page.get(None, {"view": "discover"})
        self.assertEqual([f["name"] for f in reply["findings"]], ["droid", "maybe", "claude"])
        self.assertEqual(reply["findings"][0]["short_path"], "~/bin/droid")
        self.assertEqual(reply["findings"][0]["status"], "new")
        self.assertEqual(self.file.read_text(), before)
        with self.assertRaises(PageError) as caught:
            harness_page.get(None, {})
        self.assertEqual(caught.exception.status, 404)

    def test_register_takes_exactly_the_ticked_names(self):
        reply = harness_page.post(None, "register", {"names": ["maybe"]})
        self.assertEqual(reply["applied"]["added"], ["maybe"])
        disk = self.disk()
        self.assertIn("maybe", disk)
        self.assertNotIn("droid", disk)
        self.assertEqual(disk["maybe"]["version"], "1.2.3")
        self.assertIn("maybe", [r["name"] for r in reply["harnesses"]])

    def test_an_alias_another_harness_owns_is_skipped_with_why(self):
        reply = harness_page.post(None, "register", {"names": ["droid"]})
        self.assertEqual(reply["applied"]["added"], ["droid"])
        self.assertEqual(reply["applied"]["skipped"], [{"name": "droid", "reason": "alias 'cc' belongs to 'claude'"}])
        self.assertEqual(self.disk()["droid"]["aliases"], ["dr"])

    def test_only_new_matches(self):
        error = self.refused(["claude", "droid"])
        self.assertEqual((error.status, error.code), (404, "not_new"))
        self.assertIn("claude", error.message)
        error = self.refused(["rm -rf"])
        self.assertEqual(error.code, "not_new")

    def test_the_names_must_be_a_list_of_names(self):
        self.assertEqual(self.refused([]).code, "nothing_ticked")
        for names in (None, "droid", [1], [""], ["x" * 500]):
            self.assertEqual(self.refused(names).code, "bad_names", names)


class TestAdd(_TempRegistry, unittest.TestCase):
    def add(self, **body):
        return harness_page.post(None, "add", body)

    def refused(self, **body) -> PageError:
        before = self.file.read_text()
        with self.assertRaises(PageError) as caught:
            self.add(**body)
        self.assertEqual(self.file.read_text(), before, "a refusal must not write")
        return caught.exception

    def test_adds_with_swe_adds_defaults(self):
        reply = self.add(name="mycli", command=" mycli ", description="", aliases=[], tags=[])
        self.assertEqual((reply["name"], reply["command"], reply["action"]), ("mycli", "mycli", "added"))
        self.assertEqual(reply["tags"], ["agentic", "coding"])
        self.assertEqual(reply["harness"]["name"], "mycli")
        entry = self.disk()["mycli"]
        self.assertEqual((entry["command"], entry["description"], entry["aliases"]), ("mycli", "", []))
        self.assertTrue(entry["added"])

    def test_fields_as_the_form_sends_them(self):
        self.add(name="my.cli-2", command="/opt/my", description="Mine", aliases=["m2", " "], tags="a, b,")
        entry = self.disk()["my.cli-2"]
        self.assertEqual((entry["description"], entry["aliases"], entry["tags"]), ("Mine", ["m2"], ["a", "b"]))

    def test_never_overwrites(self):
        for name in ("codex", "cc"):
            error = self.refused(name=name, command="x")
            self.assertEqual((error.status, error.code), (409, "already_registered"), name)

    def test_an_alias_another_harness_answers_to_is_refused(self):
        error = self.refused(name="mycli", command="mycli", aliases=["cc"])
        self.assertEqual((error.status, error.code), (409, "alias_taken"))

    def test_bad_input(self):
        for body, code in (({"name": "-x", "command": "x"}, "bad_name"),
                           ({"name": "my cli", "command": "x"}, "bad_name"),
                           ({"command": "x"}, "bad_name"),
                           ({"name": "mycli", "command": " "}, "command_required"),
                           ({"name": "mycli"}, "command_required"),
                           ({"name": "mycli", "command": "--help"}, "bad_command"),
                           ({"name": "mycli", "command": "a\nb"}, "bad_command"),
                           ({"name": "mycli", "command": "x", "aliases": [3]}, "bad_field"),
                           ({"name": "mycli", "command": "x", "description": 3}, "bad_field")):
            error = self.refused(**body)
            self.assertEqual((error.status, error.code), (422, code), body)


if __name__ == "__main__":
    unittest.main()
