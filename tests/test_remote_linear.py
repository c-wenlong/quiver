"""remote/linear.py: the Linear TaskSource — config + secrets, the
GraphQL transport, the workflow-state mapping, and its share of
``GET /v1/tasks``.

Nothing real runs: ``_graphql`` is stubbed at the transport layer, the
way the GitHub tests stub ``gh``, so no network leaves the test."""

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from swe import paths
from swe.github import gh
from swe.mcp import secrets as mcp_secrets
from swe.remote import linear
from swe.remote import tasks as tasks_mod
from swe.github import shared as G

from tests.test_remote_hub import _Server, _session

OK_ENV = gh.GhEnv(installed=True, authenticated=True, login="octocat")


def _node(identifier="QUI-12", title="Ship the thing", stype="unstarted",
          team="QUI", **kw):
    """A recorded ``issues.nodes[]`` entry, shaped as GraphQL serves it."""
    node = {"identifier": identifier, "title": title,
            "url": f"https://linear.app/acme/issue/{identifier.lower()}",
            "updatedAt": "2026-02-03T04:05:06.000Z",
            "state": {"name": "Todo", "type": stype},
            "team": {"key": team}}
    node.update(kw)
    return node


def _config(home, section):
    """Write a ``linear`` section into the throwaway home's config.json."""
    path = paths.config_file_for(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"linear": section}), encoding="utf-8")


def _secret(home, value="lin_api_test"):
    mcp_secrets.set_secret("LINEAR_API_KEY", value, home=home)


class TestMapping(unittest.TestCase):
    """The workflow-state → task-state table."""

    def test_every_state_type(self):
        table = {"triage": "open", "backlog": "open",
                 "unstarted": "open", "started": "in_progress",
                 "completed": "closed", "canceled": "closed",
                 "cancelled": "closed", "duplicate": "closed",
                 "madeup": "open"}
        for stype, want in table.items():
            got = linear._task_state(_node(stype=stype))
            self.assertEqual(got, want, stype)

    def test_missing_state_reads_open(self):
        self.assertEqual(linear._task_state({"state": None}), "open")
        self.assertEqual(linear._task_state({}), "open")

    def test_ref_carries_the_team_as_project(self):
        ref = linear._ref(_node("QUI-12", team="QUI"))
        self.assertEqual(
            ref, {"source": "linear", "kind": "issue", "ref": "QUI-12",
                  "title": "Ship the thing",
                  "url": "https://linear.app/acme/issue/qui-12",
                  "project": "QUI"})
        self.assertEqual(tasks_mod.task_id(ref),
                         "linear:issue:qui:qui-12")
        # The ref validates through the store's own parser.
        self.assertEqual(tasks_mod.parse_ref(ref)["ref"], "QUI-12")

    def test_ref_without_a_team_has_no_project(self):
        ref = linear._ref(_node(team=None))
        self.assertNotIn("project", ref)
        self.assertEqual(tasks_mod.task_id(ref),
                         "linear:issue::qui-12")


class TestListFilter(unittest.TestCase):
    def test_assigned_to_me_by_default(self):
        f = linear._list_filter([])
        self.assertTrue(f["assignee"]["isMe"]["eq"])
        self.assertNotIn("or", f)
        self.assertEqual(
            f["state"]["type"]["nin"],
            ["completed", "canceled", "cancelled", "duplicate"])

    def test_teams_widen_to_an_or(self):
        f = linear._list_filter(["QUI", "ENG"])
        self.assertEqual(len(f["or"]), 2)
        self.assertTrue(f["or"][0]["assignee"]["isMe"]["eq"])
        self.assertEqual(f["or"][1]["team"]["key"]["in"], ["QUI", "ENG"])
        self.assertIn("state", f)  # the terminal filter still applies

    def test_list_query_orderby_is_a_valid_enum(self):
        # PaginationOrderBy is lowercase; "UpdatedAt" once shipped and the
        # real API answered GRAPHQL_VALIDATION_FAILED (HTTP 400).
        self.assertIn("orderBy: updatedAt", linear._LIST_QUERY)
        self.assertNotIn("orderBy: UpdatedAt", linear._LIST_QUERY)


class TestEnv(unittest.TestCase):
    """probe(): section, key resolution, the viewer call."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def test_no_section_is_disabled(self):
        env = linear.LinearTasks(self.home).probe()
        self.assertEqual(env.status, "disabled")
        self.assertFalse(env.configured)
        self.assertEqual(linear.env_payload(env),
                         {"status": "disabled", "login": None,
                          "error": None})

    def test_missing_secret_is_auth_required(self):
        _config(self.home, {})
        with patch("swe.remote.linear._graphql") as call:
            env = linear.LinearTasks(self.home).probe()
        call.assert_not_called()  # never reaches the wire
        self.assertEqual(env.status, "auth-required")
        self.assertIn("${LINEAR_API_KEY}", env.error)
        self.assertIn("swe mcp secret set", env.error)

    def test_a_literal_key_is_refused(self):
        """The key itself in config.json would be a plaintext credential."""
        _config(self.home, {"key_ref": "lin_api_plaintext"})
        env = linear.LinearTasks(self.home).probe()
        self.assertEqual(env.status, "auth-required")
        self.assertIn("credential-free", env.error)

    def test_default_key_ref_resolves(self):
        """``key_ref`` unset means ${LINEAR_API_KEY}."""
        _config(self.home, {})
        _secret(self.home)
        with patch("swe.remote.linear._graphql",
                   return_value=linear.GraphQLResult(
                       "ok", data={"viewer": {"displayName": "Kai"}})
                   ) as call:
            env = linear.LinearTasks(self.home).probe()
        self.assertEqual(env.status, "ok")
        self.assertEqual(env.login, "Kai")
        self.assertEqual(call.call_args.args[0], "lin_api_test")

    def test_named_key_ref_resolves(self):
        _config(self.home, {"key_ref": "${WORK_LINEAR_TOKEN}"})
        mcp_secrets.set_secret("WORK_LINEAR_TOKEN", "lin_api_w",
                               home=self.home)
        with patch("swe.remote.linear._graphql",
                   return_value=linear.GraphQLResult("ok", data={})):
            env = linear.LinearTasks(self.home).probe()
        self.assertEqual(env.status, "ok")

    def test_refused_key_is_auth_required(self):
        _config(self.home, {})
        _secret(self.home)
        with patch("swe.remote.linear._graphql",
                   return_value=linear.GraphQLResult(
                       "auth-required",
                       error="Linear refused the API key (HTTP 401)")):
            env = linear.LinearTasks(self.home).probe()
        self.assertEqual(env.status, "auth-required")
        self.assertIn("401", env.error)

    def test_unreachable_is_upstream_error(self):
        _config(self.home, {})
        _secret(self.home)
        with patch("swe.remote.linear._graphql",
                   return_value=linear.GraphQLResult(
                       "upstream-error", error="Linear is unreachable")):
            env = linear.LinearTasks(self.home).probe()
        self.assertEqual(env.status, "upstream-error")


class TestListAndGet(unittest.TestCase):
    """list()/get() over recorded GraphQL replies."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        _config(self.home, {})
        _secret(self.home)
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def _stub(self, nodes, status="ok", error=""):
        replies = {
            linear._LIST_QUERY: linear.GraphQLResult(
                status, data={"issues": {"nodes": nodes}}, error=error),
        }
        return patch("swe.remote.linear._graphql",
                   side_effect=lambda key, query, variables=None:
                       replies.get(query) or
                       (_ for _ in ()).throw(AssertionError(query)))

    def test_list_maps_nodes_to_task_rows(self):
        nodes = [_node("QUI-12", stype="unstarted"),
                 _node("QUI-13", title="Done one", stype="started")]
        with self._stub(nodes):
            out = linear.LinearTasks(self.home).list()
        self.assertEqual(out["status"], "ok")
        self.assertIn("fetched_at", out)
        rows = {t["task"]["ref"]: t for t in out["tasks"]}
        self.assertEqual(rows["QUI-12"]["task"]["project"], "QUI")
        self.assertEqual(rows["QUI-12"]["state"], "open")
        self.assertEqual(rows["QUI-13"]["state"], "in_progress")
        # The workflow state's own name, which the task row shows.
        self.assertEqual(rows["QUI-12"]["status_name"], "Todo")
        self.assertEqual(rows["QUI-12"]["updated_at"],
                         "2026-02-03T04:05:06.000Z")

    def test_list_sends_the_team_filter(self):
        _config(self.home, {"teams": ["QUI", "ENG"]})
        seen = {}

        def fake(key, query, variables=None):
            seen.update(variables or {})
            return linear.GraphQLResult(
                "ok", data={"issues": {"nodes": []}})

        with patch("swe.remote.linear._graphql", side_effect=fake):
            linear.LinearTasks(self.home).list()
        team = seen["filter"]["or"][1]["team"]["key"]["in"]
        self.assertEqual(team, ["QUI", "ENG"])

    def test_list_follows_pages_and_flags_truncation(self):
        # One page of 50 used to pass for every open issue.
        calls = []

        def fake(key, query, variables=None):
            calls.append(variables.get("after"))
            n = len(calls)
            return linear.GraphQLResult("ok", data={"issues": {
                "nodes": [_node(f"QUI-{n}")],
                "pageInfo": {"hasNextPage": True, "endCursor": f"c{n}"}}})

        with patch("swe.remote.linear._graphql", side_effect=fake):
            out = linear.LinearTasks(self.home).list()
        self.assertEqual(calls, [None, "c1", "c2", "c3", "c4"])
        self.assertEqual(len(out["tasks"]), linear.LIST_PAGES)
        self.assertTrue(out["truncated"])

    def test_slow_pages_stop_at_the_time_budget(self):
        clock = [0.0]

        def fake(key, query, variables=None):
            clock[0] += 5.0  # each page takes 5 s
            return linear.GraphQLResult("ok", data={"issues": {
                "nodes": [_node(f"QUI-{clock[0]}")],
                "pageInfo": {"hasNextPage": True, "endCursor": "c"}}})

        with patch("swe.remote.linear._graphql", side_effect=fake), \
                patch("swe.remote.linear.time.monotonic",
                      side_effect=lambda: clock[0]):
            out = linear.LinearTasks(self.home).list()
        # 0 s start: pages at 5 s and 10 s; the third would start past 8 s.
        self.assertEqual(len(out["tasks"]), 2)
        self.assertTrue(out["truncated"])

    def test_list_stops_on_the_last_page(self):
        pages = [{"nodes": [_node("QUI-1")],
                  "pageInfo": {"hasNextPage": True, "endCursor": "c1"}},
                 {"nodes": [_node("QUI-2")],
                  "pageInfo": {"hasNextPage": False, "endCursor": "c2"}}]
        with patch("swe.remote.linear._graphql",
                   side_effect=[linear.GraphQLResult("ok", data={"issues": p})
                                for p in pages]):
            out = linear.LinearTasks(self.home).list()
        self.assertEqual([t["task"]["ref"] for t in out["tasks"]],
                         ["QUI-1", "QUI-2"])
        self.assertNotIn("truncated", out)

    def test_bad_teams_is_bad_config_not_a_crash(self):
        """``swe config set linear.teams 5`` writes a scalar; a bare
        ``"QUI"`` would otherwise iterate into ["Q","U","I"]. Neither
        may raise or send a mangled filter."""
        for raw in (5, "QUI", ["QUI", 7], {"k": "QUI"}, True):
            _config(self.home, {"teams": raw})
            with patch("swe.remote.linear._graphql") as call:
                out = linear.LinearTasks(self.home).list()
            call.assert_not_called()  # never reaches the wire
            self.assertEqual(out["status"], "bad-config", raw)
            self.assertIn("list of team keys", out["error"])
            self.assertEqual(out["tasks"], [])

    def test_non_string_key_ref_is_bad_config(self):
        _config(self.home, {"key_ref": 42})
        env = linear.LinearTasks(self.home).probe()
        self.assertEqual(env.status, "auth-required")
        self.assertIn("key_ref", env.error)

    def test_list_failure_keeps_its_status(self):
        with self._stub([], status="upstream-error",
                        error="Linear answered HTTP 502"):
            out = linear.LinearTasks(self.home).list()
        self.assertEqual(out["status"], "upstream-error")
        self.assertIn("502", out["error"])
        self.assertEqual(out["tasks"], [])

    def test_get_answers_state_title_url_and_description(self):
        issue = {**_node("QUI-12", stype="started"),
                 "description": "## Spec\nDo the thing."}
        with patch("swe.remote.linear._graphql",
                   return_value=linear.GraphQLResult(
                       "ok", data={"issue": issue})) as call:
            src = linear.LinearTasks(self.home)
            out = src.get({"source": "linear", "kind": "issue",
                           "ref": "QUI-12"})
        self.assertEqual(call.call_args.kwargs["variables"] if
                         call.call_args.kwargs else
                         call.call_args.args[2], {"id": "QUI-12"})
        self.assertEqual(out["state"], "in_progress")
        self.assertEqual(out["description"], "## Spec\nDo the thing.")
        self.assertEqual(out["title"], "Ship the thing")

    def test_get_missing_issue_is_none(self):
        with patch("swe.remote.linear._graphql",
                   return_value=linear.GraphQLResult(
                       "ok", data={"issue": None})):
            out = linear.LinearTasks(self.home).get(
                {"ref": "QUI-99"})
        self.assertIsNone(out)

    def test_get_unreachable_is_none_not_a_crash(self):
        with patch("swe.remote.linear._graphql",
                   return_value=linear.GraphQLResult(
                       "upstream-error", error="timeout")):
            out = linear.LinearTasks(self.home).get({"ref": "QUI-12"})
        self.assertIsNone(out)


class TestGraphQL(unittest.TestCase):
    """The transport itself: errors map, nothing raises."""

    def test_401_is_auth_required(self):
        import urllib.error
        err = urllib.error.HTTPError(
            linear.GRAPHQL_URL, 401, "unauthorized", None, None)
        with patch("swe.remote.linear.urllib.request.urlopen",
                   side_effect=err):
            out = linear._graphql("k", "query { viewer { id } }")
        self.assertEqual(out.status, "auth-required")

    def test_certificate_failure_retries_against_the_os_bundle(self):
        # python.org builds ship no CA store: the request is retried,
        # still verified, with the OS bundle the agent's urlopen loads.
        import io
        import ssl
        import urllib.error
        from swe.remote import agent as agent_mod
        agent_mod._fallback_ctx = None
        self.addCleanup(setattr, agent_mod, "_fallback_ctx", None)
        sentinel = ssl.create_default_context()
        calls = []

        def urlopen(req, timeout=None, context=None):
            calls.append(context)
            if context is None:
                raise urllib.error.URLError(
                    ssl.SSLCertVerificationError("no CA store"))
            return io.BytesIO(b'{"data": {"viewer": {"id": "u"}}}')

        with patch("swe.remote.linear.urllib.request.urlopen",
                   side_effect=urlopen), \
             patch("swe.harness.rate_limits._verified_context",
                   return_value=sentinel):
            out = linear._graphql("k", "query { viewer { id } }")
            again = linear._graphql("k", "query { viewer { id } }")
        self.assertEqual((out.status, again.status), ("ok", "ok"))
        # The second request goes straight to the bundle, no failed
        # handshake first (the removed copy retried every time).
        self.assertEqual(calls, [None, sentinel, sentinel])

    def test_500_is_upstream_error(self):
        import urllib.error
        err = urllib.error.HTTPError(
            linear.GRAPHQL_URL, 500, "server", None, None)
        with patch("swe.remote.linear.urllib.request.urlopen",
                   side_effect=err):
            out = linear._graphql("k", "query { viewer { id } }")
        self.assertEqual(out.status, "upstream-error")
        self.assertIn("500", out.error)

    def test_network_failure_is_upstream_error(self):
        with patch("swe.remote.linear.urllib.request.urlopen",
                   side_effect=OSError("no route")):
            out = linear._graphql("k", "query { viewer { id } }")
        self.assertEqual(out.status, "upstream-error")
        self.assertIn("unreachable", out.error)

    def test_graphql_errors_block_reads_as_failure(self):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"errors": [{"message": "Field 'x' doesn't exist"}]}
                ).encode()

        with patch("swe.remote.linear.urllib.request.urlopen",
                   return_value=Resp()):
            out = linear._graphql("k", "query { x }")
        self.assertEqual(out.status, "upstream-error")
        self.assertIn("Field 'x'", out.error)

    def test_data_parses_through(self):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps(
                    {"data": {"viewer": {"displayName": "Kai"}}}).encode()

        with patch("swe.remote.linear.urllib.request.urlopen",
                   return_value=Resp()) as call:
            out = linear._graphql("lin_api_k", "query { viewer { id } }")
        self.assertEqual(out.status, "ok")
        self.assertEqual(out.data["viewer"]["displayName"], "Kai")
        sent = call.call_args.args[0]
        # A personal key goes bare in Authorization — no Bearer scheme.
        self.assertEqual(sent.headers["Authorization"], "lin_api_k")
        self.assertEqual(sent.full_url, linear.GRAPHQL_URL)


class _Base(unittest.TestCase):
    """The route-level half: ``GET /v1/tasks`` with both sources."""

    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}
        self.addCleanup(G.CACHE.clear)
        G.CACHE.clear()

    def _get(self, path):
        status, data = self.srv.request("GET", path, headers=self.hdr)
        return status, json.loads(data)

    @contextlib.contextmanager
    def _stubs(self, graphql=None):
        """GitHub ok with no pins; Linear's transport recorded."""
        graphql = graphql or (lambda key, query, variables=None:
                              linear.GraphQLResult("ok", data={}))
        with patch("swe.github.gh.probe_env", return_value=OK_ENV), \
             patch("swe.remote.linear._graphql",
                   side_effect=graphql):
            yield


class TestTasksRoute(_Base):
    def test_disabled_by_default(self):
        with self._stubs():
            status, body = self._get("/v1/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["linear"]["status"], "disabled")
        self.assertIsNone(body["linear"]["error"])

    def test_missing_key_reports_but_serves_the_store(self):
        _config(self.srv.home, {})
        ref = {"source": "linear", "kind": "issue", "project": "QUI",
               "ref": "QUI-12", "title": "Ship the thing",
               "url": "https://linear.app/acme/issue/qui-12"}
        tasks_mod.link(self.srv.home, ref, session_key="claude:s1")
        with self._stubs():
            status, body = self._get("/v1/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["linear"]["status"], "auth-required")
        self.assertIn("${LINEAR_API_KEY}", body["linear"]["error"])
        # The stored task still lists, marked by its linked session.
        row = {t["id"]: t for t in body["tasks"]}[
            "linear:issue:qui:qui-12"]
        self.assertTrue(row["agent_running"])
        self.assertEqual(row["state"], "in_progress")

    def test_a_truncated_list_says_so_on_the_linear_block(self):
        _config(self.srv.home, {})
        _secret(self.srv.home)

        def fake(key, query, variables=None):
            if query is linear._VIEWER_QUERY:
                return linear.GraphQLResult(
                    "ok", data={"viewer": {"displayName": "Kai"}})
            return linear.GraphQLResult("ok", data={"issues": {
                "nodes": [_node("QUI-12")],
                "pageInfo": {"hasNextPage": True, "endCursor": "c"}}})

        with self._stubs(fake):
            _s, body = self._get("/v1/tasks")
        self.assertTrue(body["linear"]["truncated"])

    def test_linear_rows_merge_with_githubs(self):
        _config(self.srv.home, {})
        _secret(self.srv.home)
        nodes = [_node("QUI-12"), _node("ENG-7", team="ENG")]

        def fake(key, query, variables=None):
            if query is linear._VIEWER_QUERY:
                return linear.GraphQLResult(
                    "ok", data={"viewer": {"displayName": "Kai"}})
            if query is linear._LIST_QUERY:
                return linear.GraphQLResult(
                    "ok", data={"issues": {"nodes": nodes}})
            return linear.GraphQLResult("upstream-error", error="?")

        with self._stubs(fake):
            status, body = self._get("/v1/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["linear"]["status"], "ok")
        self.assertEqual(body["linear"]["login"], "Kai")
        self.assertIn("fetched_at", body)
        by_id = {t["id"]: t for t in body["tasks"]}
        row = by_id["linear:issue:qui:qui-12"]
        self.assertEqual((row["source"], row["kind"], row["project"]),
                         ("linear", "issue", "QUI"))
        self.assertEqual(row["state"], "open")
        self.assertFalse(row["agent_running"])
        self.assertIn("linear:issue:eng:eng-7", by_id)

    def test_a_started_issue_reads_in_progress_unlinked(self):
        _config(self.srv.home, {})
        _secret(self.srv.home)

        def fake(key, query, variables=None):
            if query is linear._LIST_QUERY:
                return linear.GraphQLResult(
                    "ok", data={"issues": {
                        "nodes": [_node("QUI-12", stype="started")]}})
            return linear.GraphQLResult("ok", data={})

        with self._stubs(fake):
            _s, body = self._get("/v1/tasks")
        row = {t["id"]: t for t in body["tasks"]}[
            "linear:issue:qui:qui-12"]
        self.assertFalse(row["linked"])
        self.assertEqual(row["state"], "in_progress")

    def test_stored_task_gets_fresh_state(self):
        """A Linear issue the open list no longer shows (completed is
        filtered server-side) still refreshes its stored row."""
        _config(self.srv.home, {})
        _secret(self.srv.home)
        ref = {"source": "linear", "kind": "issue", "project": "QUI",
               "ref": "QUI-12", "title": "Ship the thing",
               "url": "https://linear.app/acme/issue/qui-12"}
        tasks_mod.link(self.srv.home, ref, session_key="claude:s1")
        done = _node("QUI-12", stype="completed")

        def fake(key, query, variables=None):
            if query is linear._LIST_QUERY:
                return linear.GraphQLResult(
                    "ok", data={"issues": {"nodes": []}})
            if query is linear._GET_QUERY:
                return linear.GraphQLResult(
                    "ok", data={"issue": done})
            return linear.GraphQLResult("ok", data={})

        with self._stubs(fake):
            _s, body = self._get("/v1/tasks")
        row = {t["id"]: t for t in body["tasks"]}[
            "linear:issue:qui:qui-12"]
        self.assertEqual(row["state"], "closed")
        self.assertTrue(row["agent_running"])

    def test_bad_teams_reports_but_does_not_break_the_route(self):
        """A malformed ``teams`` blocks the linear list only — GitHub
        rows and the answer itself still come back."""
        _config(self.srv.home, {"teams": "QUI"})
        _secret(self.srv.home)

        def fake(key, query, variables=None):
            return linear.GraphQLResult(
                "ok", data={"viewer": {"displayName": "Kai"}})

        with self._stubs(fake):
            status, body = self._get("/v1/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["linear"]["status"], "bad-config")
        self.assertIn("team keys", body["linear"]["error"])
        self.assertEqual(body["tasks"], [])

    def test_a_failed_list_reports_on_the_block(self):
        """probe ok but the fetch itself failed: the linear block says
        so instead of crashing the answer."""
        _config(self.srv.home, {})
        _secret(self.srv.home)

        def fake(key, query, variables=None):
            if query is linear._VIEWER_QUERY:
                return linear.GraphQLResult(
                    "ok", data={"viewer": {"displayName": "Kai"}})
            return linear.GraphQLResult(
                "upstream-error", error="Linear answered HTTP 502")

        with self._stubs(fake):
            status, body = self._get("/v1/tasks")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["linear"]["status"], "upstream-error")
        self.assertIn("502", body["linear"]["error"])
        self.assertEqual(body["tasks"], [])
