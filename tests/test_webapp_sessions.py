"""The Sessions page: per-session usage, projects, accidental sessions,
and /v1/app/sessions on the hub."""

import json
import tempfile
import unittest
from pathlib import Path

from quiver.ledger.models import LedgerRecord, UsageEvent
from quiver.ledger.pricing import PricingCatalog, rates_from_entry
from quiver.ledger.sessions import by_session
from quiver.ledger.store import LedgerStore
from quiver.webapp import sessions
from quiver.webapp.api import WebApp

from tests.test_remote_hub import HubBase

CATALOG = PricingCatalog({"m": rates_from_entry(
    {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06})})


def _ev(session, ts, inp, out=0, model="m", cwd="/w", harness="claude"):
    return UsageEvent(harness=harness, account="/h", session=session, cwd=cwd,
                      model=model, ts=ts, input=inp, output=out)


class TestBySession(unittest.TestCase):
    def setUp(self):
        self.store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(self.store.close)

    def add(self, key, ev):
        self.store.upsert(LedgerRecord(key=key, event=ev))

    def test_totals_models_span_and_folder(self):
        self.add("a", _ev("s1", 100, 1000, 10, cwd="/repo"))
        self.add("b", _ev("s1", 400, 500, 0, model="other", cwd="/repo"))
        self.add("c", _ev("s1", 200, 1, 0, cwd="/x/side-session-notes/local_1"))
        self.add("d", _ev("s1", 250, 1, 0, cwd="/x/side-session-notes/local_1"))
        self.add("e", _ev("s2", 50, 7))
        u = by_session(self.store, CATALOG)[("claude", "s1")]
        self.assertEqual((u["tokens"], u["events"], u["first_ts"], u["last_ts"]), (1512, 4, 100, 400))
        self.assertAlmostEqual(u["cost_usd"], 1002 * 1e-06 + 10 * 2e-06)
        self.assertEqual(u["unpriced_tokens"], 500)
        self.assertEqual([m["model"] for m in u["models"]], ["m", "other"])
        # A real folder beats the notes folder, even with fewer events.
        self.assertEqual(u["cwd"], "/repo")


class TestProjects(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_repository_and_its_subfolders(self):
        repo = self.root / "quiver"
        (repo / ".git").mkdir(parents=True)
        (repo / "src" / "deep").mkdir(parents=True)
        self.assertEqual(sessions.project_of(str(repo / "src" / "deep")), "quiver")

    def test_worktree_names_its_main_repository(self):
        (self.root / "main-repo" / ".git" / "worktrees" / "wt").mkdir(parents=True)
        wt = self.root / "elsewhere" / "wt"
        wt.mkdir(parents=True)
        (wt / ".git").write_text(f"gitdir: {self.root}/main-repo/.git/worktrees/wt\n")
        self.assertEqual(sessions.project_of(str(wt)), "main-repo")

    def test_gone_paths_fall_back_to_worktree_shapes(self):
        self.assertEqual(sessions.project_of("/gone/app/.claude/worktrees/x"), "app")
        self.assertEqual(sessions.project_of("/home/u/.codex/worktrees/ab12/site"), "site")
        self.assertEqual(sessions.project_of("/gone/plain"), "plain")
        self.assertIsNone(sessions.project_of(None))

    def test_hash_like(self):
        self.assertTrue(sessions.is_hash_like("01a03a60389b"))
        self.assertTrue(sessions.is_hash_like("080c2c1b-bdc1-4f92-96f9-c83b228871c7"))
        self.assertFalse(sessions.is_hash_like("setup-server"))
        self.assertFalse(sessions.is_hash_like("abc"))


class TestTrivial(unittest.TestCase):
    def u(self, **kw):
        base = {"tokens": 500, "events": 1, "first_ts": 0, "last_ts": 10,
                "output": 20, "reasoning": 0}
        base.update(kw)
        return base

    def test_rules(self):
        self.assertTrue(sessions.is_trivial("claude", self.u(), True))
        self.assertFalse(sessions.is_trivial("claude", self.u(output=500), True))
        self.assertFalse(sessions.is_trivial("claude", self.u(last_ts=120), True))
        self.assertFalse(sessions.is_trivial("claude", self.u(events=3), True))
        self.assertTrue(sessions.is_trivial("claude", self.u(tokens=0), True))
        self.assertFalse(sessions.is_trivial("cursor", self.u(), True))
        self.assertFalse(sessions.is_trivial("gemini", self.u(tokens=0), True))
        # No usage at all: only for a harness this ledger reads, after a scan.
        self.assertTrue(sessions.is_trivial("codex", None, True))
        self.assertFalse(sessions.is_trivial("codex", None, False))
        self.assertFalse(sessions.is_trivial("pi", None, True))

    def test_location(self):
        self.assertEqual(sessions.location("cursor", "bc-123"), "Cloud")
        self.assertEqual(sessions.location("grok-bot", "x"), "Cloud")
        self.assertEqual(sessions.location("claude", "x"), "Local")


class TestBuild(unittest.TestCase):
    def test_join_is_local_only_and_sorted(self):
        rows = [
            {"key": "claude:s1", "id": "s1", "harness": "claude", "machine_id": "hub",
             "cwd": "/gone/one", "last_active_at": 10, "title": "a"},
            {"key": "claude:s1@mac", "id": "s1", "harness": "claude", "machine_id": "mac",
             "remote": True, "cwd": "/Users/me/x", "last_active_at": 20, "title": "b"},
        ]
        usage = {("claude", "s1"): {"tokens": 9, "events": 5, "first_ts": 0, "last_ts": 300,
                                    "output": 500, "reasoning": 0, "models": [], "cwd": "/w"}}
        out = sessions.build(rows, usage, "hub", {"mac": {"name": "MacBook"}}, scanned=True)
        self.assertEqual([r["key"] for r in out], ["claude:s1@mac", "claude:s1"])
        remote, here = out
        self.assertIsNone(remote["usage"])
        self.assertTrue(remote["remote"])
        self.assertEqual(remote["machine"], "MacBook")
        self.assertFalse(remote["trivial"])
        self.assertEqual(here["usage"]["tokens"], 9)
        self.assertNotIn("cwd", here["usage"])
        self.assertEqual((here["duration_s"], here["project"], here["project_key"]), (300, "one", "one"))


class TestSessionsRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.srv.state.webapp = WebApp(self.srv.home, download_prices=False)
        proj = self.srv.home / ".claude" / "projects" / "p"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(json.dumps({
            "timestamp": "2026-10-03T10:00:00Z", "sessionId": "s1", "requestId": "r",
            "message": {"id": "m", "model": "claude-opus-5",
                        "usage": {"input_tokens": 1000, "output_tokens": 10}},
        }) + "\n")

    def test_rows_carry_usage(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/sessions")[0], 401)
        status, data = self.srv.request("GET", "/v1/app/sessions", headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        [row] = body["sessions"]
        self.assertEqual(row["key"], "claude:s1")
        self.assertEqual(row["usage"]["tokens"], 1010)
        self.assertEqual(row["usage"]["models"], [{"model": "claude-opus-5", "tokens": 1010}])
        self.assertEqual(row["project"], "proj")
        self.assertIn("claude", body["ledger_harnesses"])

    def test_page_script_is_served(self):
        self.assertEqual(self.srv.request("GET", "/app/sessions.js")[0], 200)


if __name__ == "__main__":
    unittest.main()
