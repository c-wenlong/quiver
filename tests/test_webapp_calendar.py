"""The Calendar page: ranges, activity blocks, project grouping, the
legend's accounts, and /v1/app/calendar on the hub."""

import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from swe.ledger.models import LedgerRecord, UsageEvent
from swe.ledger.overview import Priced
from swe.ledger.pricing import PricingCatalog, rates_from_entry
from swe.ledger.store import LedgerStore
from swe.webapp.pages import calendar as cal

from tests.test_remote_hub import HubBase

CATALOG = PricingCatalog({"m": rates_from_entry(
    {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06})})
T0 = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc).timestamp()  # a Tuesday


def _ev(session, ts, harness="claude", cwd="/w", model="m", account="/h", out=200):
    return UsageEvent(harness=harness, account=account, session=session, cwd=cwd,
                      model=model, ts=ts, input=1000, output=out)


class _Store(unittest.TestCase):
    def setUp(self):
        self.store = LedgerStore(Path(tempfile.mkdtemp()) / "l.sqlite")
        self.addCleanup(self.store.close)
        self.n = 0

    def add(self, ev):
        self.n += 1
        self.store.upsert(LedgerRecord(key=f"k{self.n}", event=ev))

    def pieces(self):
        rows = self.store.activity_events(0, 1e12, list(cal.CALENDAR_HARNESSES))
        return cal.session_pieces(rows, Priced(self.store, CATALOG))


class TestRange(unittest.TestCase):
    def test_week_starts_on_the_first_weekday(self):
        tue = date(2026, 10, 20)
        self.assertEqual(cal.visible_days("week", 7, tue, 1)[0], date(2026, 10, 19))
        self.assertEqual(cal.visible_days("week", 7, tue, 7)[0], date(2026, 10, 18))
        self.assertEqual(len(cal.visible_days("week", 7, tue, 1)), 7)

    def test_month_is_six_week_rows_and_days_start_on_the_anchor(self):
        days = cal.visible_days("month", 42, date(2026, 10, 20), 1)
        self.assertEqual((len(days), days[0], days[-1]), (42, date(2026, 9, 28), date(2026, 11, 8)))
        days = cal.visible_days("days", 3, date(2026, 10, 20), 1)
        self.assertEqual(days, [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)])
        self.assertEqual(cal.visible_days("day", 1, date(2026, 10, 20), 1), [date(2026, 10, 20)])

    def test_modes_normalise(self):
        self.assertEqual(cal.normalized("days", 1), ("day", 1))
        self.assertEqual(cal.normalized("days", 12), ("days", 9))
        self.assertEqual(cal.normalized("bogus", 3), ("week", 7))
        self.assertEqual(cal.normalized("month", 3), ("month", 42))

    def test_steps(self):
        self.assertEqual(cal.shifted("month", 42, date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(cal.shifted("month", 42, date(2026, 1, 15), -1), date(2025, 12, 15))
        self.assertEqual(cal.shifted("week", 7, date(2026, 10, 20), -1), date(2026, 10, 13))
        self.assertEqual(cal.shifted("days", 3, date(2026, 10, 20), 1), date(2026, 10, 23))

    def test_week_numbers(self):
        # ISO for a Monday-first calendar: 1 Jan 2027 is a Friday, still 2026's week 53.
        self.assertEqual(cal.week_of_year(date(2026, 10, 19), 1), 43)
        self.assertEqual(cal.week_of_year(date(2027, 1, 1), 1), 53)
        self.assertEqual(cal.week_of_year(date(2026, 12, 28), 1), 53)
        self.assertEqual(cal.week_of_year(date(2027, 1, 4), 1), 1)
        # Sunday-first: the week holding 1 January is week 1.
        self.assertEqual(cal.week_of_year(date(2026, 12, 27), 7), 1)
        self.assertEqual(cal.week_of_year(date(2026, 10, 18), 7), 43)

    def test_buffer_days_each_side(self):
        """Whole weeks in week mode, which snaps by week; at least a week
        otherwise; none for the month, which does not slide."""
        self.assertEqual(cal.buffer_days("week", 7), 14)
        self.assertEqual(cal.buffer_days("day", 1), 7)
        self.assertEqual(cal.buffer_days("days", 3), 7)
        self.assertEqual(cal.buffer_days("days", 9), 9)
        self.assertEqual(cal.buffer_days("month", 42), 0)

    def test_empty_phrase(self):
        self.assertEqual(cal.empty_noun("week", 7), "this week")
        self.assertEqual(cal.empty_noun("days", 4), "in these 4 days")


class TestPieces(_Store):
    def test_silence_longer_than_the_gap_splits_a_session(self):
        for t in (0, 600, 1200, 1200 + cal.GAP_S + 1, 1200 + cal.GAP_S + 60):
            self.add(_ev("s1", T0 + t))
        pieces = self.pieces()
        self.assertEqual([(p["start"] - T0, p["end"] - T0, p["responses"]) for p in pieces],
                         [(0, 1200, 3), (1200 + cal.GAP_S + 1, 1200 + cal.GAP_S + 60, 2)])
        self.assertEqual(pieces[0]["tokens"], 3 * 1200)
        self.assertAlmostEqual(pieces[0]["cost_usd"], 3 * (1000e-06 + 200 * 2e-06))

    def test_counts_per_local_day(self):
        midnight = datetime(2026, 10, 21, tzinfo=timezone.utc).timestamp()
        for t in (-600, -300, 300):
            self.add(_ev("s1", midnight + t))
        rows = self.store.activity_events(0, 1e12)
        piece, = cal.session_pieces(rows, Priced(self.store, CATALOG, timezone.utc))
        self.assertEqual({d: c["responses"] for d, c in piece["days"].items()},
                         {"2026-10-20": 2, "2026-10-21": 1})
        self.assertEqual(piece["days"]["2026-10-21"]["tokens"], 1200)
        # Merged refs keep their per-day split.
        ref, = cal._union_refs([cal._ref(piece)], [cal._ref(piece)])
        self.assertEqual(ref["days"]["2026-10-20"]["responses"], 4)
        self.assertEqual(ref["responses"], 6)

    def test_the_folder_most_responses_used(self):
        self.add(_ev("s1", T0, cwd="/a"))
        self.add(_ev("s1", T0 + 1, cwd="/b"))
        self.add(_ev("s1", T0 + 2, cwd="/b"))
        self.add(_ev("s1", T0 + 3, cwd="/x/side-session-notes/local_1"))
        self.add(_ev("s1", T0 + 4, cwd="/x/side-session-notes/local_1"))
        self.add(_ev("s1", T0 + 5, cwd="/x/side-session-notes/local_1"))
        self.assertEqual(self.pieces()[0]["cwd"], "/b")
        self.assertEqual(cal._pick_folder({"/x/side-session-notes/l": 2}), "/x/side-session-notes/l")
        self.assertEqual(cal._pick_folder({"/a": 2, "/b": 2}), "/b")


class TestGrouping(_Store):
    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp())
        for repo in ("one/app", "two/app"):
            (self.root / repo / ".git").mkdir(parents=True)

    def test_sessions_of_one_repository_merge_within_the_gap(self):
        app = str(self.root / "one" / "app")
        self.add(_ev("s1", T0, cwd=app))
        self.add(_ev("s1", T0 + 600, cwd=app))
        self.add(_ev("s2", T0 + 1200, cwd=app + "/src"))
        self.add(_ev("s2", T0 + 1500, cwd=app + "/src"))
        # Same folder name, another repository: its own block.
        self.add(_ev("s3", T0 + 300, cwd=str(self.root / "two" / "app")))
        blocks = cal.group_by_project(self.pieces(), cal.Projects())
        self.assertEqual(len(blocks), 2)
        merged = next(b for b in blocks if len(b["sessions"]) == 2)
        self.assertEqual((merged["project"], merged["project_key"]), ("app", app))
        self.assertEqual((merged["start"] - T0, merged["end"] - T0, merged["responses"]), (0, 1500, 4))
        self.assertEqual([s["id"] for s in merged["sessions"]], ["s1", "s2"])
        self.assertEqual(merged["id"], f"claude|/h|{app}|{T0}")

    def test_a_later_stretch_is_its_own_block(self):
        self.add(_ev("s1", T0, cwd="/gone/site"))
        self.add(_ev("s2", T0 + cal.GAP_S + 100, cwd="/gone/site"))
        blocks = cal.group_by_project(self.pieces(), cal.Projects())
        self.assertEqual(len(blocks), 2)

    def test_every_grok_bot_shares_one_project(self):
        self.add(_ev("bot-a", T0, harness="grok-bot", cwd=None))
        self.add(_ev("bot-b", T0 + 120, harness="grok-bot", cwd=None))
        self.add(_ev("cc", T0, cwd=None))
        blocks = cal.group_by_project(self.pieces(), cal.Projects())
        grok = [b for b in blocks if b["harness"] == "grok-bot"]
        self.assertEqual(len(grok), 1)
        self.assertEqual((grok[0]["project"], len(grok[0]["sessions"])), ("Grok Bot", 2))
        other = next(b for b in blocks if b["harness"] == "claude")
        self.assertEqual((other["project"], other["project_key"]), (cal.NO_FOLDER, ""))

    def test_worktree_shapes_key_by_their_repository(self):
        projects = cal.Projects()
        self.assertEqual(projects.identity("/gone/app/.claude/worktrees/x"), ("/gone/app", "app"))
        self.assertEqual(projects.identity("/u/.codex/worktrees/ab/site"),
                         ("/u/.codex/worktrees/ab/site", "site"))
        self.assertEqual(projects.identity("/"), ("", cal.NO_FOLDER))


class TestReal(unittest.TestCase):
    def usage(self, models=("m",), **kw):
        base = {"tokens": 5000, "events": 10, "first_ts": 0, "last_ts": 900, "output": 900,
                "reasoning": 0, "models": [{"model": m, "tokens": 1} for m in models]}
        base.update(kw)
        return base

    def test_helper_only_and_accidental_sessions_draw_nothing(self):
        self.assertTrue(cal.is_real("devin", self.usage()))
        self.assertFalse(cal.is_real("devin", self.usage(models=("summarizer", "compactor"))))
        self.assertTrue(cal.is_real("devin", self.usage(models=("summarizer", "m"))))
        self.assertFalse(cal.is_real("claude", self.usage(events=1, last_ts=5, output=10)))
        self.assertTrue(cal.is_real("claude", None))


class TestTotals(unittest.TestCase):
    def test_parallel_blocks_count_once_and_clip(self):
        blocks = [{"harness": "a", "start": 0, "end": 100}, {"harness": "a", "start": 50, "end": 150},
                  {"harness": "a", "start": 300, "end": 400}, {"harness": "b", "start": -50, "end": 20}]
        out = cal.totals(blocks, 0, 350, lambda b: b["harness"])
        self.assertEqual(out, {"a": 200, "b": 20})


class TestAccounts(unittest.TestCase):
    def test_labels(self):
        home = Path(tempfile.mkdtemp())
        accounts_dir = home / ".quiver" / "accounts"
        accounts_dir.mkdir(parents=True)
        (accounts_dir / "profiles.json").write_text(json.dumps({"schema": 1, "profiles": [
            {"id": "claude-default", "vendor": "claude", "name": "Personal", "isDefault": True},
            {"id": "claude-work", "vendor": "claude", "name": "Work", "isDefault": False},
        ]}))
        a = cal.Accounts(home)
        self.assertEqual(a.label(str(home / ".claude")), "Claude: Personal")
        self.assertEqual(a.label(str(accounts_dir / "claude" / "claude-work" / "home")), "Claude: Work")
        self.assertEqual(a.label(str(home / ".codex")), "Codex: Default")
        self.assertEqual(a.label(str(home / ".local/share/devin/cli")), "Devin")
        self.assertEqual(a.label(str(home / ".local/share/opencode")), "OpenCode")
        self.assertEqual(a.label(str(home / "elsewhere")), "~/elsewhere")
        self.assertEqual(a.label("/opt/x"), "/opt/x")
        self.assertEqual(len(a.homes["claude"]), 2)

    def test_cursor_accounts_are_the_apps_user_data_dirs(self):
        home = Path(tempfile.mkdtemp())
        accounts_dir = home / ".quiver" / "accounts"
        accounts_dir.mkdir(parents=True)
        (accounts_dir / "profiles.json").write_text(json.dumps({"schema": 1, "profiles": [
            {"id": "cursor-a", "vendor": "cursor", "name": "Side", "isDefault": False},
            {"id": "devin-b", "vendor": "devin", "name": "Ops", "isDefault": False},
        ]}))
        a = cal.Accounts(home)
        app = str(home / "Library/Application Support/Cursor")
        desktop = str(accounts_dir / "cursor" / "cursor-a" / "desktop")
        # The app's own folder reads as the harness; a profile's by its name.
        self.assertEqual((a.label(app), a.label(desktop)), ("Cursor", "Cursor: Side"))
        # The CLI home is named but never a Cursor account in the legend.
        self.assertEqual(a.label(str(accounts_dir / "cursor" / "cursor-a" / "home")), "Cursor: Side")
        self.assertEqual(a.homes["cursor"], [app, desktop])
        self.assertEqual(a.homes["devin"][1], str(accounts_dir / "devin" / "devin-b" / "home"))

    def test_no_profiles_file(self):
        home = Path(tempfile.mkdtemp())
        self.assertEqual(cal.Accounts(home).label(str(home / ".claude")), "Claude: Default")


class TestTitles(unittest.TestCase):
    def test_fallbacks(self):
        self.assertEqual(cal.session_title("Fix it", "x", "claude"), "Fix it")
        self.assertEqual(cal.session_title("", "natural-riverbed", "devin"), "natural-riverbed")
        self.assertEqual(cal.session_title(None, "8bccd4fc-ec48-4114-a3b5-638b2c48eabf", "claude"),
                         "8bccd4fc…")
        self.assertEqual(cal.session_title(None, "b1", "grok-bot", {"b1": "Scout"}), "Scout")
        self.assertEqual(cal.session_title(None, "sand-subagent-1", "grok-bot"), "Subagent")


class TestGrokRoster(unittest.TestCase):
    """Bot names come from Grok Bot's own roster blob, never from the
    Mac app's grok-bot-names.json memory."""

    def setUp(self):
        import base64
        import tempfile
        self.home = Path(tempfile.mkdtemp())
        self.store = self.home / cal._GROK_STORE
        self.store.mkdir(parents=True)
        self.b32 = lambda key: base64.b32encode(key.encode()).decode().lower().rstrip("=")

    def test_reads_only_the_roster_blob(self):
        roster = {"schemaVersion": 1, "value": {"rows": [
            {"id": "b1", "name": "Scout", "lastEntry": "secret"}, {"id": "b2"}]}}
        (self.store / f"{self.b32('u.roster.last-roster')}.blob").write_text(json.dumps(roster))
        (self.store / f"{self.b32('u.transcript')}.blob").write_text(
            json.dumps({"value": {"rows": [{"id": "x", "name": "nope"}]}}))
        self.assertEqual(cal._grok_names(self.home), {"b1": "Scout"})

    def test_a_malformed_roster_is_an_empty_map(self):
        name = f"{self.b32('u.roster.last-roster')}.blob"
        for body in ('{"value": {"rows": true}}', '{"value": 3}', "[]", "not json"):
            (self.store / name).write_text(body)
            self.assertEqual(cal._grok_names(self.home), {}, body)

    def test_the_mac_apps_memory_is_not_read(self):
        data = self.home / ".quiver" / "data"
        data.mkdir(parents=True)
        (data / "grok-bot-names.json").write_text(json.dumps({"b9": "Old"}))
        self.assertEqual(cal._grok_names(self.home), {})


class TestRoute(HubBase):
    def seed(self):
        with self.srv.state.webapp.ledger() as (store, _catalog, _ok):
            for i, t in enumerate((0, 600, 1200)):
                store.upsert(LedgerRecord(key=f"a{i}", event=_ev("s1", T0 + t, cwd="/tmp/proj")))
            for i, t in enumerate((300, 900)):
                store.upsert(LedgerRecord(key=f"b{i}", event=_ev("gone", T0 + t, cwd="/tmp/proj")))
            # A one-response probe stays off the calendar.
            store.upsert(LedgerRecord(key="p", event=_ev("probe", T0 + 4000, out=5)))
            # Eight days later: in the next week's buffer columns only.
            for i, t in enumerate((0, 600)):
                store.upsert(LedgerRecord(key=f"c{i}", event=_ev("later", T0 + 8 * 86400 + t, cwd="/tmp/next")))
            store.db.commit()

    def test_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/calendar")[0], 401)

    def test_week_blocks_legend_and_session_rows(self):
        self.seed()
        q = "/v1/app/calendar?mode=week&anchor=2026-10-20&tz=UTC&first_weekday=1"
        status, data = self.srv.request("GET", q, headers=self.auth())
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        self.assertEqual((body["title"], body["subtitle"]), ("October 2026", "Week 43"))
        self.assertEqual((body["previous"], body["next"]), ("2026-10-13", "2026-10-27"))
        self.assertEqual(body["days"][0]["date"], "2026-10-19")
        self.assertEqual(len(body["days"]), 7)
        # Two weeks of buffer columns each side for the horizontal swipe.
        render = body["render_days"]
        self.assertEqual((len(render), render[0]["date"], render[14]), (35, "2026-10-05", body["days"][0]))
        self.assertEqual(render[-1]["end"], render[0]["start"] + 35 * 86400)
        # The buffer's blocks come along; the legend counts the visible week only.
        self.assertEqual(sorted(b["project"] for b in body["blocks"]), ["next", "proj"])
        block = next(b for b in body["blocks"] if b["project"] == "proj")
        self.assertEqual((block["harness"], block["project"], block["responses"]), ("claude", "proj", 5))
        self.assertEqual(block["end"] - block["start"], 1200)
        refs = {r["id"]: r for r in block["sessions"]}
        self.assertEqual(set(refs), {"s1", "gone"})
        # A listed session opens its catalog row; a gone one a stand-in.
        listed = body["sessions"][refs["s1"]["row"]]
        self.assertEqual((listed["title"], refs["s1"]["title"]), ("test", "test"))
        self.assertEqual(listed["usage"]["events"], 3)
        stand_in = body["sessions"][refs["gone"]["row"]]
        self.assertEqual((stand_in["id"], stand_in["resume"], refs["gone"]["title"]), ("gone", None, "gone"))
        legend = body["legend"]
        self.assertEqual([entry["harness"] for entry in legend], ["claude"])
        self.assertEqual(legend[0]["total_s"], 1200)
        self.assertEqual(body["machine"], self.srv.state.machine["name"])

    def test_month_and_an_empty_range(self):
        self.seed()
        q = "/v1/app/calendar?mode=month&anchor=2026-10-20&tz=UTC"
        body = json.loads(self.srv.request("GET", q, headers=self.auth())[1])
        self.assertEqual((len(body["days"]), body["subtitle"]), (42, ""))
        self.assertEqual(body["render_days"], body["days"])
        self.assertEqual(body["totals_range"]["start"],
                         datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())
        q = "/v1/app/calendar?mode=days&n=3&anchor=2026-11-20&tz=UTC"
        body = json.loads(self.srv.request("GET", q, headers=self.auth())[1])
        self.assertEqual((body["mode"], len(body["days"]), body["blocks"]), ("days", 3, []))
        self.assertEqual((len(body["render_days"]), body["render_days"][7]), (17, body["days"][0]))
        self.assertEqual(body["empty"], "No harness activity in these 3 days")

    def test_bad_arguments_fall_back(self):
        q = "/v1/app/calendar?mode=x&anchor=nope&first_weekday=9&tz=Not/AZone"
        status, data = self.srv.request("GET", q, headers=self.auth())
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["mode"], "week")
        status, data = self.srv.request("GET", "/v1/app/calendar?anchor=0001-01-01&first_weekday=7",
                                        headers=self.auth())
        self.assertEqual(status, 200, data)

    def test_an_unreadable_ledger_is_a_503(self):
        with patch("swe.webapp.api.by_session", side_effect=OSError("disk gone")):
            status, _ = self.srv.request("GET", "/v1/app/calendar", headers=self.auth())
        self.assertEqual(status, 503)

    def test_a_day_keeps_a_week_of_buffer(self):
        self.seed()
        q = "/v1/app/calendar?mode=day&anchor=2026-10-27&tz=UTC"
        body = json.loads(self.srv.request("GET", q, headers=self.auth())[1])
        self.assertEqual((len(body["render_days"]), body["render_days"][7]["date"]), (15, "2026-10-27"))
        # A quiet day still draws the week either side; its legend stays empty.
        self.assertEqual(sorted(b["project"] for b in body["blocks"]), ["next", "proj"])
        self.assertEqual(body["legend"], [])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestSlidingScript(unittest.TestCase):
    """pages/calendar.js's sliding-grid arithmetic, run under node with
    the globals it borrows from app.js stubbed."""

    def run_js(self, body):
        prelude = ("globalThis.window = { addEventListener() {} };\n"
                   "globalThis.document = { addEventListener() {} };\n"
                   "globalThis.location = { hash: '' };\n"
                   "globalThis.registerPage = () => {};\n")
        source = (Path(cal.__file__).parent.parent / "static" / "pages" / "calendar.js").read_text(encoding="utf-8")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(prelude + source + "\nconst T = window.CalendarPage._test;\n"
                    + "process.stdout.write(JSON.stringify((() => {" + body + "})()));\nprocess.exit(0);\n")
        self.addCleanup(Path(f.name).unlink)
        done = subprocess.run(["node", f.name], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_week_numbers_match_the_page_title(self):
        """The mini month's and month grid's week numbers use the rule the
        hub's "Week N" title does (week_of_year), for every first weekday:
        a Sunday-first Oct 25 2026 is week 44 in both, not ISO's 43."""
        from datetime import date, timedelta
        days = [date(2020, 12, 20) + timedelta(days=i) for i in range(0, 3 * 366, 3)]
        days += [date(2026, 10, 25), date(2027, 1, 1), date(2021, 1, 3)]
        for fw in (1, 6, 7):
            isos = json.dumps([d.isoformat() for d in days])
            got = self.run_js(f"return {isos}.map((d) => T.weekOfYear(T.D(d), {fw}));")
            self.assertEqual(got, [cal.week_of_year(d, fw) for d in days], fw)
        self.assertEqual(self.run_js("return T.weekOfYear(T.D('2026-10-25'), 7);"), 44)

    def test_a_swipe_rests_on_the_nearest_day(self):
        # Day mode: 15 columns of 100px, one visible.
        out = self.run_js("return [T.restingIndex(700, 100, 1, 15, 1), T.restingIndex(749, 100, 1, 15, 1),"
                          " T.restingIndex(751, 100, 1, 15, 1), T.restingIndex(-40, 100, 1, 15, 1),"
                          " T.restingIndex(5000, 100, 1, 15, 1)];")
        self.assertEqual(out, [7, 7, 8, 0, 14])

    def test_week_mode_rests_on_whole_weeks_inside_the_columns(self):
        # 35 columns of 60px, seven visible: rests at 0, 7, 14, 21 or 28.
        out = self.run_js("return [T.restingIndex(840, 60, 7, 35, 7), T.restingIndex(840 + 200, 60, 7, 35, 7),"
                          " T.restingIndex(840 + 220, 60, 7, 35, 7), T.restingIndex(99999, 60, 7, 35, 7)];")
        self.assertEqual(out, [14, 14, 21, 28])

    def test_n_days_rest_by_day_and_step_by_n(self):
        out = self.run_js("return [T.restingIndex(310, 50, 1, 17, 3), T.restingIndex(9999, 50, 1, 17, 3),"
                          " T.windowDays('days', 3), T.windowDays('week', 7), T.windowDays('day', 1),"
                          " T.snapDays('week'), T.snapDays('days')];")
        self.assertEqual(out, [6, 14, 3, 7, 1, 7, 1])

    def test_no_width_yet_rests_at_the_start(self):
        self.assertEqual(self.run_js("return [T.restingIndex(300, 0, 1, 15, 1), T.restingIndex(0, 60, 1, 7, 7)];"),
                         [0, 0])


if __name__ == "__main__":
    unittest.main()
