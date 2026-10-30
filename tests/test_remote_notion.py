"""remote/notion.py: the Notion TaskSource — config and property
mapping, the REST transport (429 and pagination), the status mapping,
the page body as markdown, and its share of ``GET /v1/tasks``.

Nothing real runs: ``_request`` is stubbed with a router over
hand-written replies shaped as Notion's API (version 2022-06-28) serves
them, so no network leaves the test."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from quiver import paths
from quiver.github import gh
from quiver.mcp import secrets as mcp_secrets
from quiver.remote import notion
from quiver.remote import tasks as tasks_mod
from quiver.github import shared as G

from tests.test_remote_hub import _Server, _session

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octocat")

DB = "0123456789abcdef0123456789abcdef"
DB_ID = "01234567-89ab-cdef-0123-456789abcdef"
ME = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PAGE_A = "11111111-2222-3333-4444-555555555555"
PAGE_B = "66666666-7777-8888-9999-000000000000"

# A status-property database: groups as Notion lists them, each holding
# its options' ids.
SCHEMA = {
    "object": "database", "id": DB_ID,
    "title": [{"type": "text", "plain_text": "Roadmap"}],
    "properties": {
        "Name": {"id": "title", "type": "title", "title": {}},
        "Status": {"id": "s1", "type": "status", "status": {
            "options": [{"id": "o1", "name": "Not started"},
                        {"id": "o2", "name": "Building"},
                        {"id": "o3", "name": "Shipped"}],
            "groups": [
                {"id": "g1", "name": "To-do", "option_ids": ["o1"]},
                {"id": "g2", "name": "In progress", "option_ids": ["o2"]},
                {"id": "g3", "name": "Complete", "option_ids": ["o3"]}]}},
        "Stage": {"id": "s2", "type": "select", "select": {}},
        "Done": {"id": "s3", "type": "checkbox", "checkbox": {}},
        "Owner": {"id": "p1", "type": "people", "people": {}},
        "Link": {"id": "u1", "type": "url", "url": {}},
        "Notes": {"id": "n1", "type": "rich_text", "rich_text": {}},
    },
}


def _page(pid=PAGE_A, title="Ship the roadmap", status=("o1", "Not started"),
          stage=None, done=False, link=None, **kw):
    """One query result, its properties keyed by name."""
    page = {
        "object": "page", "id": pid,
        "url": f"https://www.notion.so/Ship-{pid.replace('-', '')}",
        "last_edited_time": "2026-10-01T02:03:00.000Z",
        "archived": False, "in_trash": False,
        "parent": {"type": "database_id", "database_id": DB_ID},
        "properties": {
            "Name": {"id": "title", "type": "title", "title": [
                {"type": "text", "plain_text": title}]},
            "Status": {"id": "s1", "type": "status", "status": (
                {"id": status[0], "name": status[1]} if status else None)},
            "Stage": {"id": "s2", "type": "select", "select": (
                {"id": "x", "name": stage} if stage else None)},
            "Done": {"id": "s3", "type": "checkbox", "checkbox": done},
            "Owner": {"id": "p1", "type": "people", "people": [
                {"object": "user", "id": ME}]},
            "Link": {"id": "u1", "type": "url", "url": link},
        },
    }
    page.update(kw)
    return page


def _config(home, section):
    path = paths.config_file_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"notion": section}), encoding="utf-8")


def _secret(home, value="ntn_test"):
    mcp_secrets.set_secret("NOTION_TOKEN", value, home=home)


def _section(**over):
    section = {"user": ME,
               "databases": [{"id": DB, "properties": {
                   "status": "Status", "owner": "Owner"}}]}
    section.update(over)
    return section


class _Router:
    """``_request`` stand-in: replies by ``(method, path prefix)``; a
    list of replies is served in order (one per call). Calls are kept."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, token, method, path, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        for (m, prefix), reply in self.routes.items():
            if m == method and path.startswith(prefix):
                if isinstance(reply, list):
                    reply = reply.pop(0) if len(reply) > 1 else reply[0]
                return reply
        raise AssertionError(f"unexpected {method} {path}")


def ok(data):
    return notion.NotionResult("ok", data=data)


def _routes(pages=None, schema=SCHEMA, **extra):
    routes = {
        ("GET", "/users/me"): ok({"object": "user", "type": "bot",
                                   "name": "Quiver"}),
        ("GET", f"/users/{ME}"): ok({"id": ME, "name": "Kai"}),
        ("GET", f"/databases/{DB_ID}"): ok(schema),
        ("POST", f"/databases/{DB_ID}/query"): ok(
            {"results": pages if pages is not None else [_page()],
             "has_more": False, "next_cursor": None}),
    }
    routes.update(extra)
    return routes


class _Home(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def _run(self, routes, call):
        router = _Router(routes)
        with patch("quiver.remote.notion._request", side_effect=router):
            out = call(notion.NotionTasks(self.home))
        return out, router


class TestIds(unittest.TestCase):
    def test_ids_from_every_shape(self):
        for raw in (DB, DB_ID, DB.upper(),
                    f"https://www.notion.so/acme/Roadmap-{DB}?v="
                    "fedcba9876543210fedcba9876543210",
                    f"https://www.notion.so/{DB}"):
            self.assertEqual(notion.normalize_id(raw), DB_ID, raw)

    def test_a_hex_looking_title_word_is_not_part_of_the_id(self):
        url = f"https://www.notion.so/Feed-{DB}"
        self.assertEqual(notion.normalize_id(url), DB_ID)

    def test_no_id(self):
        for raw in ("", "Roadmap", None, 5, "abc123", "https://[broken"):
            self.assertIsNone(notion.normalize_id(raw))


class TestRefs(unittest.TestCase):
    """A Notion page id is one task however it is spelled."""

    def test_parse_ref_and_task_id_use_the_dashed_id(self):
        bare = {"source": "notion", "kind": "page", "project": "Roadmap",
                "ref": PAGE_A.replace("-", "").upper()}
        self.assertEqual(tasks_mod.parse_ref(bare)["ref"], PAGE_A)
        self.assertEqual(tasks_mod.task_id(bare),
                         f"notion:page:roadmap:{PAGE_A}")
        # A ref that isn't an id passes through as before.
        odd = {**bare, "ref": "not-an-id"}
        self.assertEqual(tasks_mod.parse_ref(odd)["ref"], "not-an-id")

    def test_a_bare_id_link_from_before_is_rekeyed_and_folded(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bare = {"source": "notion", "kind": "page",
                    "project": "Roadmap", "ref": PAGE_A.replace("-", "")}
            dashed = {**bare, "ref": PAGE_A}
            old_key = f"notion:page:roadmap:{PAGE_A.replace('-', '')}"
            new_key = f"notion:page:roadmap:{PAGE_A}"
            path = tasks_mod.tasks_file(home)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"tasks": {
                old_key: {"task": bare, "sessions": ["claude:a"],
                          "launches": [], "created_at": 1,
                          "last_active_at": 5},
                new_key: {"task": dashed, "sessions": ["claude:b"],
                          "launches": ["l1"], "created_at": 3,
                          "last_active_at": 9}}}))
            rows = tasks_mod._load(home)
        self.assertEqual(list(rows), [new_key])
        row = rows[new_key]
        self.assertEqual(row["sessions"], ["claude:a", "claude:b"])
        self.assertEqual(row["launches"], ["l1"])
        self.assertEqual((row["created_at"], row["last_active_at"]),
                         (1, 9))
        self.assertEqual(row["task"]["ref"], PAGE_A)


class TestSettings(unittest.TestCase):
    """The section → ``(user, databases, error)``: a bad entry is a
    clear message, never an exception."""

    def test_defaults_and_shared_properties(self):
        user, dbs, error = notion._settings({
            "user": "kai@example.com",
            "properties": {"status": "Status", "owner": "Owner"},
            "databases": [DB, {"id": DB_ID, "name": "Bugs",
                               "properties": {"status": "Done"}}]})
        self.assertEqual(error, "")
        self.assertEqual(user, "kai@example.com")
        self.assertEqual(dbs[0].properties,
                         {"status": "Status", "owner": "Owner"})
        # A database's own mapping wins over the shared one.
        self.assertEqual(dbs[1].properties["status"], "Done")
        self.assertEqual(dbs[1].name, "Bugs")
        self.assertEqual(dbs[1].error, "")

    def test_section_level_faults_block(self):
        for cfg, words in (
                ({}, "notion.user"),
                ({"user": 5}, "notion.user"),
                ({"user": "kai"}, "neither an email"),
                ({"user": ME}, "notion.databases"),
                ({"user": ME, "databases": []}, "notion.databases"),
                ({"user": ME, "databases": DB}, "notion.databases"),
                ({"user": ME, "databases": [DB], "properties": "x"},
                 "notion.properties")):
            _u, dbs, error = notion._settings(cfg)
            self.assertIn(words, error, cfg)
            self.assertEqual(dbs, [])

    def test_database_faults_stay_on_that_database(self):
        _u, dbs, error = notion._settings({"user": ME, "databases": [
            {"id": DB, "properties": {"owner": "Owner"}},
            {"id": "nope"},
            7,
            {"id": DB, "properties": {"status": 1}},
            {"id": DB, "properties": {"status": "S", "owner": "O"}}]})
        self.assertEqual(error, "")
        self.assertIn("properties.status is required", dbs[0].error)
        self.assertIn("databases[1].id", dbs[1].error)
        self.assertIn("must be an object", dbs[2].error)
        self.assertIn("databases[3].properties", dbs[3].error)
        self.assertEqual(dbs[4].error, "")

    def test_owner_is_required(self):
        _u, dbs, _e = notion._settings({"user": ME, "databases": [
            {"id": DB, "properties": {"status": "Status"}}]})
        self.assertIn("properties.owner is required", dbs[0].error)


class TestMapping(unittest.TestCase):
    """Config property names against the database's schema."""

    def test_title_defaults_to_the_title_property(self):
        mapping, error = notion._mapping(
            SCHEMA, {"status": "Status", "owner": "Owner"}, "Roadmap")
        self.assertEqual(error, "")
        self.assertEqual(mapping["title"], "Name")
        self.assertEqual(mapping["status_type"], "status")
        self.assertEqual(mapping["owner_type"], "people")
        self.assertEqual(mapping["url"], "")
        self.assertEqual(mapping["groups"],
                         {"o1": "open", "o2": "in_progress",
                          "o3": "closed"})

    def test_a_property_missing_from_the_database_names_it(self):
        _m, error = notion._mapping(
            SCHEMA, {"status": "State", "owner": "Owner"}, "Roadmap")
        self.assertIn('"Roadmap" has no property "State" for status',
                      error)
        self.assertIn("Owner", error)  # what it does have

    def test_wrong_types_say_what_they_must_be(self):
        cases = (({"status": "Notes", "owner": "Owner"}, "status, select"),
                 ({"status": "Status", "owner": "Stage"}, "person"),
                 ({"status": "Status", "owner": "Owner", "url": "Done"},
                  "url or text"),
                 ({"status": "Status", "owner": "Owner", "title": "Notes"},
                  "not the title"))
        for props, words in cases:
            _m, error = notion._mapping(SCHEMA, props, "Roadmap")
            self.assertIn(words, error, props)

    def test_empty_schema_never_raises(self):
        _m, error = notion._mapping({}, {"status": "S", "owner": "O"}, "X")
        self.assertIn("(it has: none)", error)

    def test_renamed_groups_fall_back_to_their_place(self):
        column = copy.deepcopy(SCHEMA["properties"]["Status"])
        for g, name in zip(column["status"]["groups"],
                           ("Queue", "Cooking", "Gone")):
            g["name"] = name
        self.assertEqual(notion._groups(column),
                         {"o1": "open", "o2": "in_progress",
                          "o3": "closed"})


class TestStatus(unittest.TestCase):
    """Select, status and checkbox all map to status words and a state."""

    def _map(self, status):
        mapping, error = notion._mapping(
            SCHEMA, {"status": status, "owner": "Owner"}, "Roadmap")
        self.assertEqual(error, "")
        return mapping

    def test_status_reads_its_group(self):
        m = self._map("Status")
        for option, want in ((("o1", "Not started"), "open"),
                             (("o2", "Building"), "in_progress"),
                             (("o3", "Shipped"), "closed")):
            row = notion._row(_page(status=option), m, "Roadmap")
            self.assertEqual((row["state"], row["status_name"]),
                             (want, option[1]))
        row = notion._row(_page(status=None), m, "Roadmap")
        self.assertEqual((row["state"], row["status_name"]), ("open", ""))

    def test_select_reads_its_words(self):
        m = self._map("Stage")
        table = {"Not started": "open", "Backlog": "open",
                 "In progress": "in_progress", "In Review": "in_progress",
                 "Done ✅": "closed", "Won't do": "closed",
                 "Incomplete": "open", "Someday": "open"}
        for stage, want in table.items():
            row = notion._row(_page(stage=stage), m, "Roadmap")
            self.assertEqual(row["state"], want, stage)
            self.assertEqual(row["status_name"], stage)
        row = notion._row(_page(stage=None), m, "Roadmap")
        self.assertEqual((row["state"], row["status_name"]), ("open", ""))

    def test_checkbox_ticked_is_done_in_its_own_name(self):
        m = self._map("Done")
        row = notion._row(_page(done=True), m, "Roadmap")
        self.assertEqual((row["state"], row["status_name"]),
                         ("closed", "Done"))
        row = notion._row(_page(done=False), m, "Roadmap")
        self.assertEqual((row["state"], row["status_name"]), ("open", ""))

    def test_row_is_a_valid_task_ref(self):
        m = self._map("Status")
        row = notion._row(_page(), m, "Roadmap")
        task = row["task"]
        self.assertEqual(task, {
            "source": "notion", "kind": "page", "ref": PAGE_A,
            "title": "Ship the roadmap", "project": "Roadmap",
            "url": f"https://www.notion.so/Ship-{PAGE_A.replace('-', '')}"})
        self.assertEqual(tasks_mod.task_id(task),
                         f"notion:page:roadmap:{PAGE_A}")
        self.assertEqual(tasks_mod.parse_ref(task)["ref"], PAGE_A)
        self.assertEqual(row["updated_at"], "2026-10-01T02:03:00.000Z")

    def test_url_property_wins_when_set(self):
        mapping, _e = notion._mapping(
            SCHEMA, {"status": "Status", "owner": "Owner",
                     "url": "Link"}, "Roadmap")
        row = notion._row(_page(link="https://example.com/spec"),
                          mapping, "Roadmap")
        self.assertEqual(row["task"]["url"], "https://example.com/spec")
        row = notion._row(_page(link=None), mapping, "Roadmap")
        self.assertIn("notion.so", row["task"]["url"])


class TestEnv(_Home):
    def test_no_section_is_disabled(self):
        env = notion.NotionTasks(self.home).probe()
        self.assertEqual(env.status, "disabled")
        self.assertEqual(notion.env_payload(env),
                         {"status": "disabled", "login": None,
                          "error": None})

    def test_missing_secret_is_auth_required_offline(self):
        _config(self.home, _section())
        env, router = self._run({}, lambda s: s.probe())
        self.assertEqual(router.calls, [])
        self.assertEqual(env.status, "auth-required")
        self.assertIn("swe mcp secret set NOTION_TOKEN", env.error)

    def test_a_literal_token_is_refused(self):
        _config(self.home, _section(token_ref="ntn_plaintext"))
        env = notion.NotionTasks(self.home).probe()
        self.assertEqual(env.status, "auth-required")
        self.assertIn("credential-free", env.error)

    def test_ok_with_a_user_id(self):
        _config(self.home, _section())
        _secret(self.home)
        env, _r = self._run(_routes(), lambda s: s.probe())
        self.assertEqual(env.status, "ok")
        self.assertEqual(env.user_id, ME)
        self.assertEqual(env.login, "Kai")

    def test_refused_token(self):
        _config(self.home, _section())
        _secret(self.home)
        env, _r = self._run({("GET", "/users/me"): notion.NotionResult(
            "auth-required", code=401, error="Notion refused the "
            "integration token (HTTP 401)")}, lambda s: s.probe())
        self.assertEqual(env.status, "auth-required")
        self.assertIn("401", env.error)

    def test_bad_section_is_bad_config_after_auth(self):
        _config(self.home, {"user": ME})
        _secret(self.home)
        env, _r = self._run(_routes(), lambda s: s.probe())
        self.assertEqual(env.status, "bad-config")
        self.assertIn("notion.databases", env.error)

    def test_email_resolves_through_the_user_list(self):
        _config(self.home, _section(user="Kai@Example.com"))
        _secret(self.home)
        users = [ok({"results": [
                     {"id": "x", "type": "bot", "bot": {}},
                     {"id": "y", "name": "Ann", "type": "person",
                      "person": {"email": "ann@example.com"}}],
                     "has_more": True, "next_cursor": "c2"}),
                 ok({"results": [
                     {"id": ME, "name": "Kai", "type": "person",
                      "person": {"email": "kai@example.com"}}],
                     "has_more": False, "next_cursor": None})]
        routes = _routes()
        routes[("GET", "/users?")] = users
        env, router = self._run(routes, lambda s: s.probe())
        self.assertEqual(env.status, "ok", env.error)
        self.assertEqual((env.user_id, env.login), (ME, "Kai"))
        self.assertIn("start_cursor=c2", router.calls[-1][1])

    def test_email_without_the_capability_says_how_to_fix(self):
        _config(self.home, _section(user="kai@example.com"))
        _secret(self.home)
        routes = _routes()
        routes[("GET", "/users?")] = ok({"results": [
            {"id": ME, "type": "person", "person": {}}],
            "has_more": False})
        env, _r = self._run(routes, lambda s: s.probe())
        self.assertEqual(env.status, "bad-config")
        self.assertIn("email addresses", env.error)

    def test_unknown_email(self):
        _config(self.home, _section(user="who@example.com"))
        _secret(self.home)
        routes = _routes()
        routes[("GET", "/users?")] = ok({"results": [
            {"id": ME, "type": "person",
             "person": {"email": "kai@example.com"}}], "has_more": False})
        env, _r = self._run(routes, lambda s: s.probe())
        self.assertEqual(env.status, "bad-config")
        self.assertIn("who@example.com", env.error)

    def test_unknown_user_id(self):
        _config(self.home, _section())
        _secret(self.home)
        routes = _routes()
        routes[("GET", f"/users/{ME}")] = notion.NotionResult(
            "upstream-error", code=404, error="HTTP 404")
        env, _r = self._run(routes, lambda s: s.probe())
        self.assertEqual(env.status, "bad-config")
        self.assertIn("no user", env.error)

    def test_user_id_without_user_capability_still_works(self):
        _config(self.home, _section())
        _secret(self.home)
        routes = _routes()
        routes[("GET", f"/users/{ME}")] = notion.NotionResult(
            "upstream-error", code=403, error="HTTP 403")
        env, _r = self._run(routes, lambda s: s.probe())
        self.assertEqual(env.status, "ok")
        self.assertEqual(env.user_id, ME)
        self.assertEqual(env.login, "Quiver")  # the integration's name


class TestList(_Home):
    def setUp(self):
        super().setUp()
        _config(self.home, _section())
        _secret(self.home)

    def test_rows_filtered_to_the_owner_and_open(self):
        pages = [_page(PAGE_A, status=("o2", "Building")),
                 _page(PAGE_B, title="Old", status=("o3", "Shipped")),
                 _page("12345678-1234-1234-1234-123456789012",
                       title="Trashed", in_trash=True)]
        out, router = self._run(_routes(pages), lambda s: s.list())
        self.assertEqual(out["status"], "ok")
        self.assertIn("fetched_at", out)
        self.assertEqual([t["task"]["ref"] for t in out["tasks"]], [PAGE_A])
        self.assertEqual(out["tasks"][0]["state"], "in_progress")
        self.assertEqual(out["tasks"][0]["status_name"], "Building")
        query = [c for c in router.calls if c[0] == "POST"][0]
        self.assertEqual(query[2]["filter"],
                         {"property": "Owner", "people": {"contains": ME}})
        self.assertEqual(query[2]["sorts"][0]["timestamp"],
                         "last_edited_time")

    def test_owner_filter_follows_the_property_type(self):
        schema = copy.deepcopy(SCHEMA)
        schema["properties"]["Owner"] = {"type": "created_by"}
        _out, router = self._run(_routes(schema=schema),
                                 lambda s: s.list())
        query = [c for c in router.calls if c[0] == "POST"][0]
        self.assertEqual(query[2]["filter"],
                         {"property": "Owner",
                          "created_by": {"contains": ME}})

    def test_checkbox_status_drops_done_rows_server_side(self):
        _config(self.home, _section(databases=[{"id": DB, "properties": {
            "status": "Done", "owner": "Owner"}}]))
        _out, router = self._run(_routes(), lambda s: s.list())
        query = [c for c in router.calls if c[0] == "POST"][0]
        self.assertEqual(query[2]["filter"]["and"][1],
                         {"property": "Done", "checkbox": {"equals": False}})

    def test_paginates_and_caps(self):
        def page_of(start):
            return ok({"results": [
                _page(f"{i:08x}-0000-0000-0000-000000000000")
                for i in range(start, start + notion.PAGE_SIZE)],
                "has_more": True, "next_cursor": f"c{start}"})
        routes = _routes()
        routes[("POST", f"/databases/{DB_ID}/query")] = [
            page_of(0), page_of(100)]
        with patch.object(notion, "LIST_LIMIT", 150):
            out, router = self._run(routes, lambda s: s.list())
        posts = [c for c in router.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertNotIn("start_cursor", posts[0][2])
        self.assertEqual(posts[1][2]["start_cursor"], "c0")
        self.assertEqual(len(out["tasks"]), 150)

    def test_page_cap_bounds_a_database_of_done_rows(self):
        done = ok({"results": [_page(status=("o3", "Shipped"))],
                   "has_more": True, "next_cursor": "more"})
        routes = _routes()
        routes[("POST", f"/databases/{DB_ID}/query")] = [done]
        out, router = self._run(routes, lambda s: s.list())
        posts = [c for c in router.calls if c[0] == "POST"]
        self.assertEqual(len(posts), notion.MAX_PAGES)
        self.assertEqual(out["tasks"], [])

    def test_name_defaults_to_the_database_title(self):
        out, _r = self._run(_routes(), lambda s: s.list())
        self.assertEqual(out["tasks"][0]["task"]["project"], "Roadmap")
        _config(self.home, _section(databases=[{
            "id": DB, "name": "Plan",
            "properties": {"status": "Status", "owner": "Owner"}}]))
        G.CACHE.clear()
        out, _r = self._run(_routes(), lambda s: s.list())
        self.assertEqual(out["tasks"][0]["task"]["project"], "Plan")

    def test_one_bad_database_does_not_hide_the_others(self):
        other = "fedcba98-7654-3210-fedc-ba9876543210"
        _config(self.home, _section(databases=[
            {"id": other, "name": "Bugs",
             "properties": {"status": "State", "owner": "Owner"}},
            {"id": DB, "properties": {"status": "Status",
                                      "owner": "Owner"}}]))
        routes = _routes()
        routes[("GET", f"/databases/{other}")] = ok(SCHEMA)
        out, router = self._run(routes, lambda s: s.list())
        self.assertEqual(out["status"], "bad-config")
        self.assertIn('"Bugs" has no property "State"', out["error"])
        self.assertEqual(out["databases"][0]["name"], "Bugs")
        self.assertEqual([t["task"]["ref"] for t in out["tasks"]], [PAGE_A])
        # The broken database never got queried.
        self.assertNotIn(("POST", f"/databases/{other}/query"),
                         [(c[0], c[1]) for c in router.calls])

    def test_a_database_not_shared_says_how_to_share_it(self):
        routes = _routes()
        routes[("GET", f"/databases/{DB_ID}")] = notion.NotionResult(
            "upstream-error", code=404, error="HTTP 404")
        out, _r = self._run(routes, lambda s: s.list())
        self.assertEqual(out["status"], "bad-config")
        self.assertIn("share it with the integration", out["error"])
        self.assertEqual(out["tasks"], [])

    def test_query_failure_keeps_its_status(self):
        routes = _routes()
        routes[("POST", f"/databases/{DB_ID}/query")] = notion.NotionResult(
            "upstream-error", code=502, error="Notion answered HTTP 502")
        out, _r = self._run(routes, lambda s: s.list())
        self.assertEqual(out["status"], "upstream-error")
        self.assertIn("502", out["error"])


class TestGetAndDetail(_Home):
    def setUp(self):
        super().setUp()
        _config(self.home, _section())
        _secret(self.home)

    def test_get_maps_the_page_through_its_database(self):
        routes = _routes()
        routes[("GET", f"/pages/{PAGE_A}")] = ok(
            _page(status=("o3", "Shipped")))
        out, _r = self._run(routes, lambda s: s.get(
            {"source": "notion", "kind": "page",
             "ref": PAGE_A.replace("-", "")}))
        self.assertEqual(out["state"], "closed")
        self.assertEqual(out["status_name"], "Shipped")
        self.assertEqual(out["title"], "Ship the roadmap")
        self.assertNotIn("description", out)

    def test_archived_page_is_closed(self):
        routes = _routes()
        routes[("GET", f"/pages/{PAGE_A}")] = ok(_page(archived=True))
        out, _r = self._run(routes, lambda s: s.get({"ref": PAGE_A}))
        self.assertEqual((out["state"], out["status_name"]),
                         ("closed", "Archived"))

    def test_page_outside_the_config_reads_its_title_but_no_state(self):
        page = _page()
        page["parent"] = {"type": "page_id", "page_id": "x"}
        routes = _routes()
        routes[("GET", f"/pages/{PAGE_A}")] = ok(page)
        out, _r = self._run(routes, lambda s: s.get({"ref": PAGE_A}))
        # No state: the row keeps its last-known one, never reopens.
        self.assertEqual((out["state"], out["title"]),
                         ("", "Ship the roadmap"))

    def test_broken_mapping_answers_no_state_rather_than_raising(self):
        _config(self.home, _section(databases=[{"id": DB, "properties": {
            "status": "Gone", "owner": "Owner"}}]))
        routes = _routes()
        routes[("GET", f"/pages/{PAGE_A}")] = ok(_page())
        out, _r = self._run(routes, lambda s: s.get({"ref": PAGE_A}))
        self.assertEqual(out["state"], "")

    def test_schema_outage_answers_no_state(self):
        routes = _routes()
        routes[("GET", f"/pages/{PAGE_A}")] = ok(_page())
        routes[("GET", f"/databases/{DB_ID}")] = notion.NotionResult(
            "upstream-error", code=502, error="Notion answered HTTP 502")
        out, _r = self._run(routes, lambda s: s.get({"ref": PAGE_A}))
        self.assertEqual(out["state"], "")

    def test_missing_page_and_bad_ref_are_none(self):
        routes = {("GET", "/pages/"): notion.NotionResult(
            "upstream-error", code=404, error="HTTP 404")}
        out, _r = self._run(routes, lambda s: s.get({"ref": PAGE_A}))
        self.assertIsNone(out)
        out, router = self._run({}, lambda s: s.get({"ref": "QUI-12"}))
        self.assertIsNone(out)
        self.assertEqual(router.calls, [])

    def test_detail_renders_the_body_as_markdown(self):
        def rt(text, **marks):
            item = {"type": "text", "plain_text": text,
                    "annotations": marks}
            if "href" in marks:
                item["href"] = marks.pop("href")
            return item

        def block(kind, bid="b", children=False, **body):
            return {"object": "block", "id": bid, "type": kind,
                    "has_children": children, kind: body}

        top = [
            block("heading_2", rich_text=[rt("Spec")]),
            block("paragraph", rich_text=[
                rt("Do "), rt("this", bold=True), rt(" via "),
                rt("api", code=True), rt(" see "),
                rt("docs", href="https://x.dev")]),
            block("bulleted_list_item", "li", True,
                  rich_text=[rt("one")]),
            block("numbered_list_item", rich_text=[rt("a")]),
            block("numbered_list_item", rich_text=[rt("b")]),
            block("to_do", checked=True, rich_text=[rt("done")]),
            block("to_do", checked=False, rich_text=[rt("todo")]),
            block("code", language="python", rich_text=[rt("x = 1")]),
            block("divider"),
            block("table", "tb", True, table_width=2),
            block("child_page", title="Notes"),
            block("image", type="file", file={"url": "https://s3/x"},
                  caption=[rt("diagram")]),
            block("unsupported"),
        ]
        routes = _routes()
        routes[("GET", f"/pages/{PAGE_A}")] = ok(_page())
        routes[("GET", f"/blocks/{PAGE_A}/children")] = [
            ok({"results": top[:5], "has_more": True,
                "next_cursor": "c1"}),
            ok({"results": top[5:], "has_more": False})]
        routes[("GET", "/blocks/li/children")] = ok({"results": [
            block("bulleted_list_item", rich_text=[rt("nested")])],
            "has_more": False})
        routes[("GET", "/blocks/tb/children")] = ok({"results": [
            block("table_row", cells=[[rt("k")], [rt("v")]]),
            block("table_row", cells=[[rt("a|b")], [rt("2")]])],
            "has_more": False})
        out, _r = self._run(routes, lambda s: s.detail({"ref": PAGE_A}))
        self.assertEqual(out["state"], "open")
        self.assertEqual(out["description"], "\n".join([
            "## Spec",
            "",
            "Do **this** via `api` see [docs](https://x.dev)",
            "",
            "- one",
            "  - nested",
            "1. a",
            "2. b",
            "- [x] done",
            "- [ ] todo",
            "",
            "```python",
            "x = 1",
            "```",
            "",
            "---",
            "",
            "| k | v |",
            "| --- | --- |",
            "| a\\|b | 2 |",
            "",
            "[child page: Notes]",
            "",
            "[image: diagram]",
        ]))

    def test_detail_budget_truncates_and_says_so(self):
        routes = _routes()
        routes[("GET", f"/pages/{PAGE_A}")] = ok(_page())
        routes[("GET", "/blocks/")] = ok({"results": [
            {"id": "p", "type": "paragraph", "has_children": False,
             "paragraph": {"rich_text": [{"plain_text": "x"}]}}],
            "has_more": True, "next_cursor": "again"})
        out, router = self._run(routes, lambda s: s.detail({"ref": PAGE_A}))
        blocks = [c for c in router.calls if c[1].startswith("/blocks/")]
        self.assertEqual(len(blocks), notion.MAX_BLOCK_CALLS)
        self.assertIn("truncated", out["description"])


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _http_error(code, body=None, headers=None):
    fp = io.BytesIO(json.dumps(body).encode()) if body is not None else None
    return urllib.error.HTTPError(notion.API_URL, code, "x",
                                  headers or {}, fp)


class TestTransport(unittest.TestCase):
    def test_headers_and_body(self):
        with patch("quiver.remote.notion.urllib.request.urlopen",
                   return_value=_Resp({"object": "list"})) as call:
            out = notion._request("ntn_k", "POST", "/databases/d/query",
                                  {"page_size": 1})
        self.assertEqual(out.status, "ok")
        sent = call.call_args.args[0]
        self.assertEqual(sent.full_url, notion.API_URL + "/databases/d/query")
        self.assertEqual(sent.headers["Authorization"], "Bearer ntn_k")
        self.assertEqual(sent.get_header("Notion-version"),
                         notion.NOTION_VERSION)
        self.assertEqual(json.loads(sent.data), {"page_size": 1})

    def test_429_waits_out_retry_after_then_succeeds(self):
        waits = []
        replies = [_http_error(429, headers={"Retry-After": "2"}),
                   _Resp({"ok": 1})]

        def urlopen(req, timeout=None, context=None):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with patch("quiver.remote.notion.urllib.request.urlopen",
                   side_effect=urlopen), \
                patch.object(notion, "_sleep", waits.append):
            out = notion._request("k", "GET", "/users/me")
        self.assertEqual(out.status, "ok")
        self.assertEqual(waits, [2.0])

    def test_429_gives_up_after_the_retries_and_caps_the_wait(self):
        waits = []
        with patch("quiver.remote.notion.urllib.request.urlopen",
                   side_effect=lambda *a, **k: (_ for _ in ()).throw(
                       _http_error(429, headers={"Retry-After": "600"}))), \
                patch.object(notion, "_sleep", waits.append):
            out = notion._request("k", "GET", "/users/me")
        self.assertEqual(out.status, "upstream-error")
        self.assertEqual(out.code, 429)
        self.assertIn("rate-limiting", out.error)
        self.assertEqual(waits, [notion.RETRY_CAP] * notion.MAX_RETRIES)

    def test_401_is_auth_required(self):
        with patch("quiver.remote.notion.urllib.request.urlopen",
                   side_effect=_http_error(401)):
            out = notion._request("k", "GET", "/users/me")
        self.assertEqual((out.status, out.code), ("auth-required", 401))

    def test_404_carries_notions_message(self):
        err = _http_error(404, {"object": "error", "code":
                                "object_not_found",
                                "message": "Could not find database"})
        with patch("quiver.remote.notion.urllib.request.urlopen",
                   side_effect=err):
            out = notion._request("k", "GET", "/databases/x")
        self.assertEqual((out.status, out.code), ("upstream-error", 404))
        self.assertIn("Could not find database", out.error)

    def test_network_failure_and_bad_json(self):
        with patch("quiver.remote.notion.urllib.request.urlopen",
                   side_effect=OSError("no route")):
            out = notion._request("k", "GET", "/users/me")
        self.assertIn("unreachable", out.error)

        class Bad(_Resp):
            def read(self):
                return b"<html>"

        with patch("quiver.remote.notion.urllib.request.urlopen",
                   return_value=Bad(None)):
            out = notion._request("k", "GET", "/users/me")
        self.assertEqual(out.status, "upstream-error")


class TestTasksRoute(unittest.TestCase):
    """``GET /v1/tasks`` with Notion beside GitHub and Linear."""

    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def _get(self, routes=None):
        router = _Router(routes or {})
        with patch("quiver.github.gh.probe_env", return_value=OK_ENV), \
                patch("quiver.remote.notion._request", side_effect=router):
            status, data = self.srv.request("GET", "/v1/tasks",
                                            headers=self.hdr)
        return status, json.loads(data)

    def test_disabled_by_default(self):
        status, body = self._get()
        self.assertEqual(status, 200, body)
        self.assertEqual(body["notion"], {"status": "disabled",
                                          "login": None, "error": None})

    def test_notion_rows_list(self):
        _config(self.srv.home, _section())
        _secret(self.srv.home)
        status, body = self._get(_routes([
            _page(status=("o2", "Building"))]))
        self.assertEqual(status, 200, body)
        self.assertEqual(body["notion"]["status"], "ok")
        self.assertEqual(body["notion"]["login"], "Kai")
        row = {t["id"]: t for t in body["tasks"]}[
            f"notion:page:roadmap:{PAGE_A}"]
        self.assertEqual((row["source"], row["kind"], row["project"]),
                         ("notion", "page", "Roadmap"))
        self.assertEqual(row["state"], "in_progress")
        self.assertEqual(row["status"], "Building")
        self.assertIn("fetched_at", body)

    def test_stored_page_marked_done_reads_closed(self):
        _config(self.srv.home, _section())
        _secret(self.srv.home)
        ref = {"source": "notion", "kind": "page", "project": "Roadmap",
               "ref": PAGE_A, "title": "Ship the roadmap", "url": ""}
        tasks_mod.link(self.srv.home, ref, session_key="claude:s1")
        routes = _routes([])
        routes[("GET", f"/pages/{PAGE_A}")] = ok(
            _page(status=("o3", "Shipped")))
        _s, body = self._get(routes)
        row = {t["id"]: t for t in body["tasks"]}[
            f"notion:page:roadmap:{PAGE_A}"]
        self.assertEqual(row["state"], "closed")
        self.assertEqual(row["status"], "Shipped")
        self.assertEqual(row["state_source"], "live")

        # The database's schema won't read on a later poll: the page
        # keeps its last-known closed state instead of reopening.
        G.CACHE.clear()
        routes = _routes([])
        routes[("GET", f"/pages/{PAGE_A}")] = ok(
            _page(status=("o3", "Shipped")))
        routes[("GET", f"/databases/{DB_ID}")] = notion.NotionResult(
            "upstream-error", code=502, error="Notion answered HTTP 502")
        _s, body = self._get(routes)
        row = {t["id"]: t for t in body["tasks"]}[
            f"notion:page:roadmap:{PAGE_A}"]
        self.assertEqual(row["state"], "closed")
        self.assertEqual(row["state_source"], "cached")

    def test_missing_property_is_a_clear_error_not_a_crash(self):
        _config(self.srv.home, _section(databases=[{
            "id": DB, "properties": {"status": "State",
                                     "owner": "Owner"}}]))
        _secret(self.srv.home)
        status, body = self._get(_routes())
        self.assertEqual(status, 200, body)
        self.assertEqual(body["notion"]["status"], "bad-config")
        self.assertIn('no property "State"', body["notion"]["error"])
        # GitHub and Linear blocks still answer.
        self.assertEqual(body["github"]["status"], "ok")
        self.assertEqual(body["linear"]["status"], "disabled")

    def test_missing_token_serves_the_store(self):
        _config(self.srv.home, _section())
        ref = {"source": "notion", "kind": "page", "project": "Roadmap",
               "ref": PAGE_A, "title": "Ship the roadmap", "url": ""}
        tasks_mod.link(self.srv.home, ref, session_key="claude:s1")
        _s, body = self._get()
        self.assertEqual(body["notion"]["status"], "auth-required")
        row = {t["id"]: t for t in body["tasks"]}[
            f"notion:page:roadmap:{PAGE_A}"]
        self.assertEqual(row["state"], "in_progress")  # linked, active


if __name__ == "__main__":
    unittest.main()
