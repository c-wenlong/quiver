"""The Service status page: the status catalog, the feed parsers, the polite
poller and its cache, and GET /v1/app/status.

The fixtures under tests/fixtures/status are the Mac app's (real responses
recorded on 2026-10-11; Cloudflare's trimmed to three regions, Google
Cloud's to one Gemini and one unrelated incident). Nothing here touches
the network: the poller gets a fake transport and a fake clock.
"""

import copy
import datetime
import http.client
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from quiver.status import catalog, catalogue, parsers, poller, tracked
from quiver.status.catalog import ComponentState, Health
from quiver.status.parsers import Component
from quiver.webapp.pages import status as status_page

from tests.test_remote_hub import HubBase
from tests.test_webapp import app_js_function

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "status"
EPOCH = 1_791_720_000.0  # the Swift tests' usageEpoch, 2026-10-11


def fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def iso(text: str) -> float:
    return datetime.datetime.fromisoformat(text).timestamp()


def open_gemini_incident() -> bytes:
    """The recorded Google Cloud file with the Gemini incident still open."""
    incidents = json.loads(fixture("gcloud-incidents"))
    incidents[0]["end"] = None
    incidents[0]["severity"] = "high"
    return json.dumps(incidents).encode()


class TestStatuspageParser(unittest.TestCase):
    def test_with_an_incident(self):
        report = parsers.statuspage(fixture("statuspage-claude"))
        self.assertEqual(report.health, Health.MINOR)
        self.assertEqual(report.summary, "Partially Degraded Service")
        self.assertEqual(report.components,
                         [Component("Claude Console (platform.claude.com)", ComponentState.DEGRADED)])
        self.assertEqual(len(report.incidents), 1)
        incident = report.incidents[0]
        self.assertEqual(incident.name, "Elevated errors on platform.claude.com")
        self.assertEqual(incident.impact, Health.MAJOR)
        self.assertEqual(incident.stage, "Monitoring")
        self.assertAlmostEqual(incident.started_at, iso("2026-10-07T13:25:04.155Z"))
        self.assertTrue(incident.latest_update.startswith("We've mitigated the errors loading usage data"))
        self.assertAlmostEqual(incident.updated_at, iso("2026-10-07T21:44:41.466Z"))
        self.assertEqual(incident.url, "https://stspg.io/sjkw7njwf0mt")
        self.assertFalse(incident.maintenance)

    def test_incident_io_flavour(self):
        """OpenAI's page is incident.io serving the Statuspage shape, without
        `started_at`, incident components or a maintenance list."""
        report = parsers.statuspage(fixture("statuspage-openai"))
        self.assertEqual(report.health, Health.MINOR)
        self.assertEqual(report.components, [Component("Compliance API", ComponentState.DEGRADED)])
        incident = report.incidents[0]
        self.assertEqual(incident.name, "Delayed Costs data in the Compliance API")
        self.assertEqual(incident.impact, Health.MINOR)
        self.assertEqual(incident.started_at, iso("2026-10-09T17:23:18Z"))
        self.assertTrue(incident.latest_update.startswith("We have implemented a mitigation"))
        self.assertIsNone(incident.url)

    def test_all_operational(self):
        report = parsers.statuspage(fixture("statuspage-github"))
        self.assertEqual(report.health, Health.OPERATIONAL)
        self.assertEqual(report.components, [])
        self.assertEqual(report.incidents, [])

    def test_component_groups_and_maintenance(self):
        """Groups are not listed when their own members are, the worst state
        comes first, and a maintenance that has not started is left out."""
        report = parsers.statuspage(fixture("statuspage-cloudflare"))
        self.assertEqual(report.health, Health.MAJOR)
        self.assertEqual(len(report.components), 8)
        self.assertEqual(report.components[0], Component("Annaba, Algeria - (AAE)", ComponentState.MAJOR_OUTAGE))
        names = {c.name for c in report.components}
        self.assertNotIn("Africa", names)
        self.assertNotIn("Cloudflare Sites and Services", names)
        self.assertEqual(len(report.incidents), 3)
        self.assertFalse(any(i.maintenance for i in report.incidents))
        states = [c.state for c in report.components]
        self.assertEqual(states, sorted(states, reverse=True))

    def test_maintenance_in_progress_is_listed(self):
        body = json.loads(fixture("statuspage-cloudflare"))
        body["scheduled_maintenances"][0]["status"] = "in_progress"
        body["incidents"] = []
        body["status"] = {"indicator": "maintenance", "description": "Service Under Maintenance"}
        report = parsers.statuspage(json.dumps(body).encode())
        self.assertEqual(report.health, Health.MAINTENANCE)
        maintenance = report.incidents[0]
        self.assertTrue(maintenance.maintenance)
        self.assertEqual(maintenance.impact, Health.MAINTENANCE)
        self.assertEqual(maintenance.stage, "In progress")
        self.assertEqual(maintenance.name, "ZRH (Zurich) on 2026-10-12")
        # When it is scheduled for, not when it was announced (2026-10-08).
        self.assertEqual(maintenance.started_at, iso("2026-10-12T00:00:00.000Z"))

    def test_groups_listed_when_no_member_is(self):
        body = {"status": {"indicator": "minor"}, "components": [
            {"name": "API", "status": "partial_outage", "group": True},
            {"name": "Web", "status": "operational"},
        ]}
        report = parsers.statuspage(json.dumps(body).encode())
        self.assertEqual(report.components, [Component("API", ComponentState.PARTIAL_OUTAGE)])

    def test_impact_none_still_counts_as_minor(self):
        body = {"status": {"indicator": "minor"}, "incidents": [
            {"id": "a", "name": "Slow", "impact": "none", "status": "investigating"},
            {"id": "b", "name": "Done", "impact": "major", "status": "resolved"},
        ]}
        report = parsers.statuspage(json.dumps(body).encode())
        self.assertEqual([(i.id, i.impact, i.stage) for i in report.incidents],
                         [("a", Health.MINOR, "Investigating")])

    def test_not_a_status_page(self):
        self.assertIsNone(parsers.statuspage(b"<html>"))
        self.assertIsNone(parsers.statuspage(b'{"page":{"status":"UP"}}'))
        self.assertIsNone(parsers.statuspage(b'{"status":{"indicator":"bogus"}}'))
        self.assertIsNone(parsers.google_cloud(b"{}", {"Vertex Gemini API"}, EPOCH))

    def test_report_round_trips_through_the_cache_shape(self):
        report = parsers.statuspage(fixture("statuspage-cloudflare"))
        self.assertEqual(parsers.Report.from_dict(json.loads(json.dumps(report.to_dict()))), report)


class TestGoogleCloudParser(unittest.TestCase):
    def test_nothing_open(self):
        report = parsers.google_cloud(fixture("gcloud-incidents"), {"Vertex Gemini API"}, EPOCH)
        self.assertEqual(report.health, Health.OPERATIONAL)
        self.assertEqual(report.incidents, [])
        self.assertEqual(report.components, [])

    def test_open_gemini_incident(self):
        report = parsers.google_cloud(open_gemini_incident(), {"Vertex Gemini API"}, EPOCH)
        self.assertEqual(report.health, Health.CRITICAL)
        self.assertEqual(report.components, [Component("Gemini on Agent Platform", ComponentState.MAJOR_OUTAGE)])
        self.assertEqual(len(report.incidents), 1)
        incident = report.incidents[0]
        self.assertTrue(incident.name.startswith("Vertex AI Gemini API customers experienced increased error rates"))
        self.assertEqual(incident.started_at, iso("2026-02-27T12:37:00+00:00"))
        self.assertEqual(incident.url, "https://status.cloud.google.com/incidents/41E5S3mkTGDfkZuJZH5k")
        # Markdown headings and bold markers are dropped from the update.
        self.assertNotIn("#", incident.latest_update)
        self.assertNotIn("**", incident.latest_update)
        self.assertTrue(incident.latest_update.startswith("On Friday, 27 February 2026"))

    def test_worst_state_per_product(self):
        """Two open incidents on one product: the component takes the worse
        state whichever comes first in the feed."""
        for reverse in (False, True):
            incidents = json.loads(open_gemini_incident())
            minor = copy.deepcopy(incidents[0])
            minor["id"], minor["severity"] = "minor-one", "low"
            ordered = [incidents[0], minor] if reverse else [minor, incidents[0]]
            report = parsers.google_cloud(json.dumps(ordered).encode(), {"Vertex Gemini API"}, EPOCH)
            self.assertEqual(report.health, Health.CRITICAL)
            self.assertEqual(report.components,
                             [Component("Gemini on Agent Platform", ComponentState.MAJOR_OUTAGE)])
            self.assertEqual(report.incidents[-1].id, "minor-one")

    def test_other_product(self):
        """An incident for another product never counts against Gemini."""
        report = parsers.google_cloud(open_gemini_incident(), {"Google Cloud Storage"}, EPOCH)
        self.assertEqual(report.health, Health.OPERATIONAL)

    def test_an_end_still_ahead_is_open(self):
        incidents = json.loads(fixture("gcloud-incidents"))
        incidents[0]["end"] = "2026-10-12T00:00:00+00:00"
        incidents[0]["severity"] = "medium"
        report = parsers.google_cloud(json.dumps(incidents).encode(), {"Vertex Gemini API"}, EPOCH)
        self.assertEqual(report.health, Health.MAJOR)
        self.assertEqual(report.components[0].state, ComponentState.PARTIAL_OUTAGE)


class TestCatalog(unittest.TestCase):
    def test_every_service_has_a_json_feed_on_https(self):
        ids = [s.id for s in catalog.SERVICES]
        self.assertEqual(len(set(ids)), len(ids))
        for service in catalog.SERVICES:
            self.assertTrue(service.feed_url.startswith("https://"), service.id)
            self.assertTrue(service.page_url.startswith("https://"), service.id)
        self.assertEqual([s.name for s in catalog.SERVICES if s.group == "modelProviders"],
                         ["Claude", "OpenAI / Codex", "Gemini"])
        self.assertEqual([s.name for s in catalog.SERVICES if s.group == "agentPlatforms"],
                         ["GitHub / Copilot", "Cursor", "Devin"])
        self.assertEqual([s.name for s in catalog.SERVICES if s.group == "registries"], ["npm"])
        self.assertEqual([s.name for s in catalog.SERVICES if s.group == "cloud"],
                         ["Vercel", "Cloudflare"])

    def test_the_app_catalog(self):
        self.assertEqual(catalog.service("claude").feed_url, "https://status.claude.com/api/v2/summary.json")
        self.assertEqual(catalog.service("claude").host, "status.claude.com")
        gemini = catalog.service("gemini")
        self.assertEqual(gemini.feed, "google_cloud")
        self.assertEqual(gemini.feed_url, "https://status.cloud.google.com/incidents.json")
        self.assertEqual(gemini.page_url, "https://aistudio.google.com/status")
        self.assertIn("Vertex Gemini API", gemini.products)
        self.assertEqual(catalog.service("github").logo, "copilot")

    def test_health_titles_are_sentence_case(self):
        self.assertEqual([h.title for h in Health],
                         ["Operational", "Under maintenance", "Minor incident", "Major outage", "Critical outage"])


# ------------------------------------------------------------------ poller

class FakeTransport:
    """Answers with the queued replies in order, the last one repeating."""

    def __init__(self, clock=None, advance=0.0):
        self.replies = []
        self.requests = []
        self.clock, self.advance = clock, advance
        self.gate = None

    def reply(self, status, body=b"", headers=None):
        self.replies.append(poller.Response(status, body, headers or {}))

    def fail(self):
        self.replies.append(OSError("offline"))

    def __call__(self, url, headers, timeout):
        self.requests.append((url, dict(headers), timeout))
        if self.gate is not None:
            self.gate.wait(5)
        if self.clock is not None:
            self.clock.now += self.advance
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


class Clock:
    def __init__(self, now=EPOCH):
        self.now = now

    def __call__(self):
        return self.now


class PollerBase(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.clock = Clock()
        self.transport = FakeTransport()

    def make(self, transport=None, path=None, **kwargs):
        return poller.StatusPoller(path or self.dir / "web-service-status.json",
                                   transport=transport or self.transport, now=self.clock, **kwargs)

    def one(self, p, id, force=False):
        return p.statuses([catalog.service(id)], force=force)[0]


class TestPoller(PollerBase):
    def test_fetches_and_parses(self):
        self.transport.reply(200, fixture("statuspage-claude"), {"ETag": 'W/"abc"'})
        status = self.one(self.make(), "claude")
        self.assertEqual(status.report.health, Health.MINOR)
        self.assertEqual(status.checked_at, EPOCH)
        self.assertIsNone(status.problem)
        self.assertFalse(status.stale)
        url, headers, timeout = self.transport.requests[0]
        self.assertEqual(url, "https://status.claude.com/api/v2/summary.json")
        self.assertNotIn("If-None-Match", headers)
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(timeout, poller.TIMEOUT)

    def test_polls_at_most_every_five_minutes(self):
        self.transport.reply(200, fixture("statuspage-github"))
        p = self.make()
        self.one(p, "github")
        self.clock.now += 120
        again = self.one(p, "github")
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(again.checked_at, EPOCH)
        # A manual refresh still waits out a one-minute floor, then goes.
        self.clock.now -= 90
        self.one(p, "github", force=True)
        self.assertEqual(len(self.transport.requests), 1)
        self.clock.now += 90
        self.one(p, "github", force=True)
        self.assertEqual(len(self.transport.requests), 2)

    def test_revalidates_with_the_etag(self):
        self.transport.reply(200, fixture("statuspage-claude"),
                             {"ETag": 'W/"abc"', "Last-Modified": "Sun, 11 Oct 2026 06:45:55 GMT"})
        self.transport.reply(304)
        p = self.make()
        self.one(p, "claude")
        self.clock.now += 301
        again = self.one(p, "claude")
        _, headers, _ = self.transport.requests[-1]
        self.assertEqual(headers["If-None-Match"], 'W/"abc"')
        self.assertEqual(headers["If-Modified-Since"], "Sun, 11 Oct 2026 06:45:55 GMT")
        self.assertEqual(again.report.health, Health.MINOR)
        # The page confirmed nothing changed, so the reading is current.
        self.assertEqual(again.checked_at, EPOCH + 301)
        self.assertFalse(again.stale)

    def test_keeps_the_last_reading_when_offline(self):
        self.transport.reply(200, fixture("statuspage-claude"))
        self.transport.fail()
        p = self.make()
        self.one(p, "claude")
        self.clock.now += 301
        offline = self.one(p, "claude")
        self.assertEqual(offline.report.health, Health.MINOR)
        self.assertEqual(offline.checked_at, EPOCH)
        self.assertTrue(offline.stale)
        self.assertEqual(offline.problem, "Couldn't reach status.claude.com")

    def test_expires_old_readings(self):
        """Past an hour an old reading is not shown as the state of the service."""
        self.transport.reply(200, fixture("statuspage-claude"))
        self.transport.reply(500)
        p = self.make()
        self.one(p, "claude")
        self.clock.now += 3601
        expired = self.one(p, "claude")
        self.assertIsNone(expired.report)
        self.assertEqual(expired.problem, "status.claude.com answered 500")

    def test_honours_retry_after(self):
        self.transport.reply(429, headers={"Retry-After": "1200"})
        self.transport.reply(200, fixture("statuspage-github"))
        p = self.make()
        first = self.one(p, "cursor")
        self.assertEqual(first.problem, "status.x.ai asked us to wait until")
        self.assertEqual(first.problem_until, EPOCH + 1200)
        self.clock.now += 900
        self.one(p, "cursor", force=True)
        self.assertEqual(len(self.transport.requests), 1)
        self.clock.now += 301
        after = self.one(p, "cursor")
        self.assertEqual(len(self.transport.requests), 2)
        self.assertIsNone(after.problem)
        self.assertIsNone(after.problem_until)

    def test_retry_after_is_capped_and_defaulted(self):
        self.transport.reply(503, headers={"Retry-After": str(48 * 3600)})
        self.assertEqual(self.one(self.make(), "npm").problem_until, EPOCH + 6 * 3600)
        transport = FakeTransport()
        transport.reply(429)
        self.assertEqual(self.one(self.make(transport, self.dir / "other.json"), "npm").problem_until, EPOCH + 900)

    def test_retry_after_counts_from_the_answer(self):
        """Retry-After counts from when the answer arrived, not when the
        request went out."""
        transport = FakeTransport(self.clock, advance=20)
        transport.reply(429, headers={"Retry-After": "120"})
        p = self.make(transport)
        self.one(p, "cursor")
        self.clock.now += 110  # 130 s after the request, 110 s after the answer
        self.one(p, "cursor", force=True)
        self.assertEqual(len(transport.requests), 1)

    def test_unreadable_body_is_a_problem_not_an_outage(self):
        self.transport.reply(200, b"<html>")
        first = self.one(self.make(), "npm")
        self.assertIsNone(first.report)
        self.assertEqual(first.problem, "Couldn't read status.npmjs.org")

    def test_cache_survives_a_restart(self):
        """The cache file carries readings across restarts without a new request."""
        self.transport.reply(200, fixture("statuspage-openai"))
        self.one(self.make(), "openai")
        self.clock.now += 60
        restarted = self.make()
        cached = restarted.cached([catalog.service("openai")])[0]
        self.assertEqual(cached.report.health, Health.MINOR)
        polled = self.one(restarted, "openai")
        self.assertEqual(polled.checked_at, EPOCH)
        self.assertEqual(len(self.transport.requests), 1)
        saved = json.loads((self.dir / "web-service-status.json").read_text())
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["services"]["openai"]["report"]["health"], "minor")

    def test_cached_never_asks(self):
        p = self.make()
        statuses = p.cached()
        self.assertEqual([s.service.id for s in statuses], [s.id for s in catalog.SERVICES])
        self.assertTrue(all(s.report is None and s.checked_at is None for s in statuses))
        self.assertEqual(self.transport.requests, [])

    def test_overlapping_calls_share_one_request(self):
        """A second call while the first is waiting on the network joins its
        request instead of sending another."""
        self.transport.reply(200, fixture("statuspage-github"))
        self.transport.gate = threading.Event()
        p = self.make()
        results = {}
        first = threading.Thread(target=lambda: results.update(a=self.one(p, "github")))
        first.start()
        while not self.transport.requests:
            time.sleep(0.01)
        second = threading.Thread(target=lambda: results.update(b=self.one(p, "github", force=True)))
        second.start()
        self.transport.gate.set()
        first.join(5)
        second.join(5)
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(results["a"].report.health, Health.OPERATIONAL)
        self.assertEqual(results["b"].report.health, Health.OPERATIONAL)

    def test_a_slow_page_does_not_hold_the_answer(self):
        self.transport.reply(200, fixture("statuspage-github"))
        self.transport.gate = threading.Event()
        p = self.make(wait=0.2)
        started = time.monotonic()
        waiting = self.one(p, "github")
        self.assertLess(time.monotonic() - started, 2)
        self.assertIsNone(waiting.report)
        # It finishes in the background and the next load has it.
        self.transport.gate.set()
        for _ in range(100):
            if p.cached([catalog.service("github")])[0].report is not None:
                break
            time.sleep(0.02)
        self.assertEqual(p.cached([catalog.service("github")])[0].report.health, Health.OPERATIONAL)
        self.assertEqual(len(self.transport.requests), 1)

    def test_unwritable_cache_keeps_the_cooldown(self):
        """With a cache that cannot be written, the limits still hold in memory."""
        blocker = self.dir / "blocker"
        blocker.write_bytes(b"")
        self.transport.reply(429, headers={"Retry-After": "1200"})
        p = self.make(path=blocker / "web-service-status.json")
        self.one(p, "cursor")
        self.clock.now += 600
        waiting = self.one(p, "cursor", force=True)
        self.assertEqual(len(self.transport.requests), 1)
        self.assertEqual(waiting.problem, "status.x.ai asked us to wait until")

    def test_newer_entry_wins(self):
        """Another hub's later attempt wins over this one's older entry."""
        early = poller.Entry(attempted_at=EPOCH)
        late = poller.Entry(attempted_at=EPOCH + 30, cooldown_until=EPOCH + 900)
        self.assertIs(poller.newer(early, late), late)
        self.assertIs(poller.newer(late, early), late)
        self.assertIs(poller.newer(None, early), early)

    def test_a_failing_check_never_wedges_the_page(self):
        self.transport.replies.append(RuntimeError("bug"))
        self.transport.replies.append(http.client.IncompleteRead(b"{"))
        p = self.make()
        self.assertEqual(self.one(p, "npm").problem, "Couldn't read status.npmjs.org")
        self.clock.now += 301
        self.assertEqual(self.one(p, "npm").problem, "Couldn't reach status.npmjs.org")
        self.assertEqual(len(self.transport.requests), 2)

    def test_google_cloud_feed(self):
        self.transport.reply(200, open_gemini_incident())
        gemini = self.one(self.make(), "gemini")
        self.assertEqual(gemini.report.health, Health.CRITICAL)
        self.assertEqual(self.transport.requests[0][0], "https://status.cloud.google.com/incidents.json")

    def test_a_corrupt_cache_reads_as_empty(self):
        path = self.dir / "web-service-status.json"
        path.write_text("{not json")
        self.assertTrue(all(s.report is None for s in self.make().cached()))
        path.write_text(json.dumps({"version": 1, "services": {"claude": {"report": {"health": "nope"}}}}))
        self.assertIsNone(self.make().cached([catalog.service("claude")])[0].report)

    def test_poller_for_is_one_per_home(self):
        home = Path(tempfile.mkdtemp())
        self.assertIs(poller.poller_for(home), poller.poller_for(home))
        self.assertEqual(poller.poller_for(home).cache_path, home / ".quiver" / "cache" / "web-service-status.json")


class TestRetryAfter(unittest.TestCase):
    def test_seconds_and_dates(self):
        self.assertEqual(poller.retry_after("120", EPOCH), EPOCH + 120)
        self.assertEqual(poller.retry_after("-5", EPOCH), EPOCH)
        self.assertEqual(poller.retry_after("Sun, 11 Oct 2026 06:45:55 GMT", EPOCH),
                         iso("2026-10-11T06:45:55+00:00"))
        for bad in (None, "", "soon", "nan"):
            self.assertIsNone(poller.retry_after(bad, EPOCH), bad)


class _Resp:
    status = 200
    headers = {"ETag": "x"}

    def __init__(self, body):
        self.body = body

    def read(self, limit=-1):
        body, self.body = self.body, b""
        return body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestHttpGet(unittest.TestCase):
    def test_retries_a_failed_verification_with_the_os_store(self):
        calls = []

        def urlopen(request, timeout, context):
            calls.append(context)
            if context is None:
                raise urllib.error.URLError(ssl.SSLCertVerificationError("unable to get local issuer"))
            return _Resp(b"{}")
        sentinel = ssl.create_default_context()
        with patch("urllib.request.urlopen", side_effect=urlopen), \
                patch("quiver.harness.rate_limits._verified_context", return_value=sentinel):
            response = poller.http_get("https://example.test/", {}, 1)
        self.assertEqual(calls, [None, sentinel])
        self.assertEqual((response.status, response.body, response.header("etag")), (200, b"{}", "x"))

    def test_never_connects_unverified(self):
        error = urllib.error.URLError(ssl.SSLCertVerificationError("bad"))
        with patch("urllib.request.urlopen", side_effect=error), \
                patch("quiver.harness.rate_limits._verified_context", return_value=None):
            with self.assertRaises(OSError):
                poller.http_get("https://example.test/", {}, 1)

    def test_http_errors_are_answers(self):
        error = urllib.error.HTTPError("https://example.test/", 304, "Not Modified", {"ETag": "y"}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            response = poller.http_get("https://example.test/", {}, 1)
        self.assertEqual(response.status, 304)

    def test_a_trickling_feed_gives_up(self):
        class Trickle(_Resp):
            def read(self, limit=-1):
                time.sleep(0.02)
                return b"x"
        with patch("urllib.request.urlopen", return_value=Trickle(b"")):
            started = time.monotonic()
            with self.assertRaises(OSError):
                poller.http_get("https://example.test/", {}, 0.1)
        self.assertLess(time.monotonic() - started, 2)

    def test_reads_a_body_in_chunks(self):
        class Chunks(_Resp):
            def __init__(self):
                self.parts = [b"ab", b"cd", b""]

            def read(self, limit=-1):
                return self.parts.pop(0)
        self.assertEqual(poller.read_body(Chunks(), time.monotonic() + 5), b"abcd")

    def test_network_errors_raise(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(OSError):
                poller.http_get("https://example.test/", {}, 1)


# ------------------------------------------------------------------- page

class TestPayload(PollerBase):
    def test_groups_cards_and_details(self):
        self.transport.reply(200, fixture("statuspage-claude"))
        p = self.make()
        body = status_page.payload(p.statuses([catalog.service("claude"), catalog.service("npm")]),
                                   machine="hub")
        self.assertEqual(body["machine"], "hub")
        self.assertEqual(body["checked_at"], EPOCH)
        self.assertEqual([g["id"] for g in body["groups"]], ["modelProviders", "registries"])
        self.assertEqual(body["groups"][0]["title"], "Model providers")
        claude = body["groups"][0]["services"][0]
        self.assertEqual((claude["health"], claude["health_title"]), ("minor", "Minor incident"))
        self.assertEqual(claude["logo"], "claude")
        self.assertEqual(claude["components"][0]["state_title"], "Degraded performance")
        self.assertEqual(claude["incidents"][0]["impact"], "major")
        self.assertEqual(claude["incidents"][0]["url"], "https://stspg.io/sjkw7njwf0mt")
        json.dumps(body)

    def test_only_web_links_reach_an_href(self):
        self.assertIsNone(status_page.web_url("javascript:alert(1)"))
        self.assertEqual(status_page.web_url("https://x.test/i"), "https://x.test/i")

    def test_carries_the_hub_clock(self):
        self.assertEqual(status_page.payload([], now=EPOCH)["server_time"], EPOCH)
        self.assertAlmostEqual(status_page.payload([])["server_time"], time.time(), delta=5)

    def test_nothing_known_yet(self):
        body = status_page.payload(self.make().cached())
        self.assertIsNone(body["checked_at"])
        services = [s for g in body["groups"] for s in g["services"]]
        self.assertEqual(len(services), len(catalog.SERVICES))
        self.assertTrue(all(s["health"] is None and s["incidents"] == [] for s in services))


class TestRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()
        self.transport.reply(200, fixture("statuspage-github"))
        self.poller = poller.StatusPoller(self.srv.home / "status.json", transport=self.transport,
                                          now=Clock(time.time()))
        p = patch("quiver.webapp.pages.status.poller_for", return_value=self.poller)
        p.start()
        self.addCleanup(p.stop)

    def get(self, path):
        status, data = self.srv.request("GET", path, headers=self.auth())
        return status, json.loads(data)

    def test_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/status")[0], 401)

    def test_cached_first_then_checked(self):
        status, body = self.get("/v1/app/status?cached=1")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["cached"])
        self.assertEqual(self.transport.requests, [])
        status, body = self.get("/v1/app/status")
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.transport.requests), len(catalog.SERVICES))
        self.assertEqual(body["machine"], self.srv.state.machine["name"])
        self.assertIsNotNone(body["checked_at"])
        # Right away again, even forced, nothing is asked twice.
        self.get("/v1/app/status?force=1")
        self.assertEqual(len(self.transport.requests), len(catalog.SERVICES))

    def test_page_assets_are_served(self):
        status, data = self.srv.request("GET", "/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b".status-grid", self.srv.request("GET", "/pages.css")[1])
        for logo in ("harness-openai.svg", "harness-copilot.svg", "harness-npm.svg",
                     "harness-vercel.svg", "harness-cloudflare-color.svg"):
            self.assertEqual(self.srv.request("GET", f"/{logo}")[0], 200, logo)


# -------------------------------------------------- catalogue and tracking

class TestCatalogue(unittest.TestCase):
    def test_mirrors_the_mac_catalogue(self):
        # The mirror is literal: the rows the Mac's StatusCatalogue.swift
        # declares, in order, are this catalogue.
        swift = (Path(__file__).resolve().parents[1]
                 / "app/mac/Sources/QuiverCore/Status/StatusCatalogue.swift")
        if not swift.exists():
            self.skipTest("no app/mac/ in this checkout")
        rows = re.findall(r'statuspage\("([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*\.(\w+)\)',
                          swift.read_text(encoding="utf-8"))
        self.assertEqual([(e.id, e.name, e.page_url, e.group) for e in catalogue.CATALOGUE], rows)
        for e in catalogue.CATALOGUE:
            self.assertTrue(e.page_url.startswith("https://"), e.id)
            self.assertEqual(e.feed_url, f"{e.page_url}/api/v2/summary.json", e.id)
            self.assertEqual(e.group in dict(catalog.GROUPS), True, e.id)
            self.assertIsNotNone(e.logo, e.id)

    def test_filter_matches_name_and_host(self):
        self.assertEqual([e.id for e in catalogue.filter_(query=" docker ")], ["docker"])
        self.assertEqual([e.id for e in catalogue.filter_(query="notion-status")], ["notion"])
        self.assertEqual(catalogue.filter_(group="modelProviders")[0].id, "groq")
        self.assertEqual([e.id for e in catalogue.filter_(group="registries", query="api")], [])
        self.assertEqual(catalogue.filter_(query="zzz"), [])
        # A catalogue id is never a built-in's, so tracking is one id space.
        self.assertFalse({e.id for e in catalogue.CATALOGUE} & {s.id for s in catalog.SERVICES})


class TestTracked(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.path = tracked.tracked_file_for(self.home)

    def test_path_is_under_the_quiver_data_dir(self):
        self.assertEqual(self.path, self.home / ".quiver" / "data" / "web-status-services.json")

    def test_add_load_remove(self):
        entry = catalogue.entry("groq")
        service = tracked.add(self.home, entry)
        self.assertTrue(service.custom)
        self.assertEqual((service.id, service.name, service.group, service.host),
                         ("groq", "Groq", "modelProviders", "groqstatus.com"))
        services = tracked.load(self.home)
        self.assertEqual([s.id for s in services], ["groq"])
        self.assertTrue(all(s.custom for s in services))
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["version"], 1)
        self.assertEqual([s["id"] for s in saved["services"]], ["groq"])
        self.assertTrue(tracked.remove(self.home, "groq"))
        self.assertEqual(tracked.load(self.home), [])
        self.assertFalse(tracked.remove(self.home, "groq"))

    def test_adding_twice_keeps_one(self):
        tracked.add(self.home, catalogue.entry("docker"))
        tracked.add(self.home, catalogue.entry("docker"))
        self.assertEqual([s.id for s in tracked.load(self.home)], ["docker"])

    def test_old_groups_and_catalogue_entries_load_forward(self):
        # Records written before the group split: a catalogue page takes
        # the catalogue's new group, an unknown custom's old group maps.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"version": 1, "services": [
            {"id": "groq", "name": "Groq", "page_url": "https://groqstatus.com",
             "feed_url": "https://groqstatus.com/api/v2/summary.json", "group": "providers"},
            {"id": "mine", "name": "Mine", "page_url": "https://status.example.com",
             "feed_url": "https://status.example.com/api/v2/summary.json",
             "group": "infrastructure"}]}))
        services = tracked.load(self.home)
        self.assertEqual([(s.id, s.group) for s in services],
                         [("groq", "modelProviders"), ("mine", "services")])

    def test_a_chosen_group_survives_a_reload(self):
        # Only a pre-split group migrates: a group the user picked under
        # the new layout wins over the catalogue's, even on its own host.
        tracked.add(self.home, catalogue.entry("groq"))
        self.path.write_text(self.path.read_text().replace('"modelProviders"', '"services"'))
        self.assertEqual(tracked.load(self.home)[0].group, "services")

    def test_bad_data_reads_as_empty(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json")
        self.assertEqual(tracked.load(self.home), [])
        self.path.write_text(json.dumps({"version": 1, "services": [{"id": "x"}, "junk", 42]}))
        self.assertEqual(tracked.load(self.home), [])
        self.path.write_text(json.dumps({"version": 1, "services": [
            {"id": "x", "name": "X", "page_url": "javascript:1", "feed_url": "https://f.test/x",
             "group": "providers"}]}))
        self.assertEqual(tracked.load(self.home), [])


class TestPollerForget(PollerBase):
    def test_drops_memory_and_cache(self):
        self.transport.reply(200, fixture("statuspage-github"))
        p = self.make()
        self.one(p, "github")
        self.assertIsNotNone(p.cached([catalog.service("github")])[0].report)
        p.forget("github")
        status = p.cached([catalog.service("github")])[0]
        self.assertIsNone(status.report)
        saved = json.loads((self.dir / "web-service-status.json").read_text())
        # A tombstone stays where the reading was, so a late landing from
        # another hub merges against it instead of an empty slot.
        self.assertIsInstance(saved["services"]["github"]["removed_at"], float)

    def test_a_forgotten_flight_does_not_land(self):
        """An answer already in flight when the page is removed is dropped."""
        self.transport.reply(200, fixture("statuspage-github"))
        self.transport.gate = threading.Event()
        p = self.make(wait=0.2)
        self.one(p, "github")  # leaves the flight running in the background
        p.forget("github")
        self.transport.gate.set()
        for _ in range(50):
            if self.transport.gate.is_set() and len(self.transport.requests) == 1:
                break
            time.sleep(0.01)
        time.sleep(0.05)  # let the landing attempt run
        self.assertIsNone(p.cached([catalog.service("github")])[0].report)
        saved = json.loads((self.dir / "web-service-status.json").read_text())
        self.assertIsInstance(saved["services"]["github"]["removed_at"], float)

    def test_another_hubs_stale_landing_does_not_resurrect_the_page(self):
        """A second poller sharing the cache file lands a reading that
        started before the removal: the tombstone beats it, on disk and
        in that poller's own reads."""
        self.transport.reply(200, fixture("statuspage-github"))
        self.transport.gate = threading.Event()
        other = self.make(wait=0.2)  # another hub, same cache file
        self.one(other, "github")  # its flight starts, held at the gate
        p = self.make()
        p.forget("github")
        self.transport.gate.set()
        for _ in range(100):
            if "github" not in other._in_flight:  # the landing ran
                break
            time.sleep(0.01)
        self.assertIsNone(p.cached([catalog.service("github")])[0].report)
        self.assertIsNone(other.cached([catalog.service("github")])[0].report)
        saved = json.loads((self.dir / "web-service-status.json").read_text())
        entry = saved["services"]["github"]
        self.assertIsInstance(entry["removed_at"], float)
        self.assertIsNone(entry["report"])

    def test_a_readded_page_polls_over_its_tombstone(self):
        """Track again after untrack: the tombstone is not a reading and
        the fresh answer lands over it."""
        self.transport.reply(200, fixture("statuspage-github"))
        p = self.make()
        p.forget("github")
        self.clock.now += 60  # re-added later; its attempt is newer
        status = self.one(p, "github", force=True)
        self.assertIsNotNone(status.report)
        saved = json.loads((self.dir / "web-service-status.json").read_text())
        self.assertIsNone(saved["services"]["github"]["removed_at"])


class TestTrackRoute(HubBase):
    def setUp(self):
        super().setUp()
        self.transport = FakeTransport()
        self.transport.reply(200, fixture("statuspage-github"))
        self.poller = poller.StatusPoller(self.srv.home / "status.json", transport=self.transport,
                                          now=Clock(time.time()))
        p = patch("quiver.webapp.pages.status.poller_for", return_value=self.poller)
        p.start()
        self.addCleanup(p.stop)
        self.headers = self.auth()

    def get(self, path):
        status, data = self.srv.request("GET", path, headers=self.headers)
        return status, json.loads(data)

    def post(self, action, body=None, headers=None):
        status, data = self.srv.request("POST", f"/v1/app/status/{action}", body,
                                        headers=headers or self.headers)
        return status, json.loads(data)

    def store(self):
        return self.srv.home / ".quiver" / "data" / "web-status-services.json"

    def card(self, body, id):
        return next((s for g in body["groups"] for s in g["services"] if s["id"] == id), None)

    def test_catalogue_view(self):
        status, body = self.get("/v1/app/status?view=catalogue")
        self.assertEqual(status, 200, body)
        self.assertEqual(len(body["entries"]), 104)
        groq = next(e for e in body["entries"] if e["id"] == "groq")
        self.assertEqual((groq["name"], groq["host"], groq["group"], groq["logo"]),
                         ("Groq", "groqstatus.com", "modelProviders", "groq"))
        self.assertFalse(any(e["added"] for e in body["entries"]))

    def test_track_adds_and_persists(self):
        status, body = self.post("track", {"id": "groq"})
        self.assertEqual(status, 200, body)
        groq = self.card(body, "groq")
        self.assertIsNotNone(groq)
        self.assertTrue(groq["custom"])
        self.assertEqual(groq["group"], "modelProviders")
        saved = json.loads(self.store().read_text())
        self.assertEqual([s["id"] for s in saved["services"]], ["groq"])
        # The catalogue now marks it added and the next poll asks it too.
        _, cat_body = self.get("/v1/app/status?view=catalogue")
        self.assertTrue(next(e for e in cat_body["entries"] if e["id"] == "groq")["added"])
        self.assertTrue(any(url == "https://groqstatus.com/api/v2/summary.json"
                            for url, _, _ in self.transport.requests))

    def test_track_validates_the_id(self):
        for body, want in [({}, 422), ({"id": 5}, 422), ({"id": "nope"}, 404)]:
            status, data = self.post("track", body)
            self.assertEqual(status, want, (body, data))
            self.assertIn(data["error"]["code"], ("bad_id", "unknown_service"))
        self.assertFalse(self.store().exists())

    def test_track_twice_is_refused(self):
        self.assertEqual(self.post("track", {"id": "docker"})[0], 200)
        status, data = self.post("track", {"id": "docker"})
        self.assertEqual(status, 409, data)
        self.assertEqual(data["error"]["code"], "already_tracked")
        self.assertEqual(len(json.loads(self.store().read_text())["services"]), 1)

    def test_untrack_removes_and_forgets(self):
        self.post("track", {"id": "tailscale"})
        self.get("/v1/app/status")  # a reading lands in the poll cache
        self.assertIn("tailscale", json.loads((self.srv.home / "status.json").read_text())["services"])
        status, body = self.post("untrack", {"id": "tailscale"})
        self.assertEqual(status, 200, body)
        self.assertIsNone(self.card(body, "tailscale"))
        self.assertEqual(json.loads(self.store().read_text())["services"], [])
        # The poll cache keeps only a tombstone, so a stale landing from
        # another hub cannot bring the card back.
        tomb = json.loads((self.srv.home / "status.json").read_text())["services"]["tailscale"]
        self.assertIsInstance(tomb["removed_at"], float)
        self.assertIsNone(tomb["report"])
        _, cat_body = self.get("/v1/app/status?view=catalogue")
        self.assertFalse(next(e for e in cat_body["entries"] if e["id"] == "tailscale")["added"])

    def test_untrack_refuses_built_ins_and_unknown(self):
        status, data = self.post("untrack", {"id": "npm"})
        self.assertEqual(status, 422, data)
        self.assertEqual(data["error"]["code"], "built_in")
        status, data = self.post("untrack", {"id": "groq"})
        self.assertEqual(status, 404, data)
        self.assertEqual(data["error"]["code"], "unknown_service")

    def test_posts_need_a_device_and_a_local_origin(self):
        self.assertEqual(self.srv.request("POST", "/v1/app/status/track", {"id": "groq"})[0], 401)
        status, data = self.srv.request(
            "POST", "/v1/app/status/track", {"id": "groq"},
            headers={**self.headers, "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(data)["error"]["code"], "bad_origin")
        self.assertFalse(self.store().exists())

    def test_tracked_services_survive_into_the_normal_view(self):
        self.post("track", {"id": "linear"})
        status, body = self.get("/v1/app/status?cached=1")
        self.assertEqual(status, 200, body)
        linear = self.card(body, "linear")
        self.assertIsNotNone(linear)
        self.assertEqual(linear["group"], "services")
        self.assertTrue(linear["custom"])



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestLateWriteGate(unittest.TestCase):
    """pages/status.js under node: a Track or Untrack reply that answers
    401 after the browser left Service status shows no gate over the page
    it moved to; on the page it does."""

    def gates_after_untrack(self, hash_):
        page = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages/status.js"
        prelude = ("globalThis.location = { hash: " + json.dumps(hash_) + " };\n"
                   "const gates = []; function renderGate(m) { gates.push(m); }\n"
                   "function registerPage() {}\n" + app_js_function("currentRoute")
                   + "const apiPost = async () => ({ status: 401, data: null });\n")
        body = "statusUntrack('x').then(() => process.stdout.write(String(gates.length)));\n"
        done = subprocess.run(["node", "-e", prelude + page.read_text(encoding="utf-8") + "\n" + body],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return int(done.stdout)

    def test_only_on_the_page(self):
        self.assertEqual(self.gates_after_untrack("#/overview"), 0)
        self.assertEqual(self.gates_after_untrack("#/status"), 1)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestUntrackOnce(unittest.TestCase):
    """pages/status.js under node: a double-click on Stop tracking sends
    one write (the second used to move the generation past the first's
    200 and come back 404, leaving the card up with an error), and a 404
    for a page already gone reads the list again instead of erroring."""

    def run_js(self, body):
        page = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/pages/status.js"
        prelude = ("globalThis.location = { hash: '#/status' };\n"
                   "function registerPage() {}\n" + app_js_function("currentRoute")
                   + "const posts = []; const replies = [];\n"
                   "const apiPost = (path, body) => { posts.push(body.id); return new Promise((r) => replies.push(r)); };\n")
        tail = ("let paints = 0, reloads = 0;\n"
                "paintStatus = () => { paints++; }; renderStatus = () => { reloads++; };\n"
                "(async () => {" + body + "})();\n")
        done = subprocess.run(["node", "-e", prelude + page.read_text(encoding="utf-8") + "\n" + tail],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_a_double_click_sends_one_write(self):
        out = self.run_js(
            "statusPage.removing = 'x';"
            " const first = statusUntrack('x'); statusUntrack('x');"
            " const during = { posts: posts.length, busy: statusPage.untracking };"
            " replies[0]({ status: 200, data: { services: [] } }); await first;"
            " process.stdout.write(JSON.stringify({ during, removing: statusPage.removing,"
            "   busy: statusPage.untracking, error: statusPage.removeError, paints }));")
        self.assertEqual(out, {"during": {"posts": 1, "busy": "x"}, "removing": None,
                               "busy": None, "error": "", "paints": 1})

    def test_a_page_already_gone_reads_the_list_again(self):
        out = self.run_js(
            "statusPage.removing = 'x'; statusPage.expanded.add('x');"
            " const done = statusUntrack('x');"
            " replies[0]({ status: 404, data: { error: { code: 'unknown_service', message: 'No tracked page has id x.' } } });"
            " await done;"
            " process.stdout.write(JSON.stringify({ removing: statusPage.removing, error: statusPage.removeError,"
            "   expanded: statusPage.expanded.has('x'), reloads, paints }));")
        self.assertEqual(out, {"removing": None, "error": "", "expanded": False, "reloads": 1, "paints": 0})



@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestRelativeTime(unittest.TestCase):
    """Fmt.relative, the one copy of Foundation's named relative time that
    Service status and Settings' device rows show."""

    def test_named_past_times(self):
        fmt = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/format.js"
        now = 1_800_000_000
        ages = [0, 30, 5 * 60, 3 * 3600, 26 * 3600, 9 * 86400, -120]
        src = (fmt.read_text(encoding="utf-8") + "\nprocess.stdout.write(JSON.stringify("
               + json.dumps(ages) + f".map((a) => Fmt.relative({now} - a, {now}))));\n")
        done = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        # A hub clock ahead of the browser (-120) still reads as now.
        self.assertEqual(json.loads(done.stdout), ["now", "30 seconds ago", "5 minutes ago", "3 hours ago",
                                                  "yesterday", "last week", "now"])

    def test_tokens_pick_the_unit_after_rounding(self):
        # The same cases as the Mac app's UsageFormatTests: never "1000K".
        fmt = Path(__file__).resolve().parents[1] / "src/quiver/webapp/static/format.js"
        counts = [0, 950, 12_340, 1_000_000, 999_949, 999_950, 999_950_000, 999_999_999_999]
        src = (fmt.read_text(encoding="utf-8") + "\nprocess.stdout.write(JSON.stringify("
               + json.dumps(counts) + ".map((n) => Fmt.tokens(n))));\n")
        done = subprocess.run(["node", "-e", src], capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout),
                         ["0", "950", "12.3K", "1M", "999.9K", "1M", "1B", "1000B"])


if __name__ == "__main__":
    unittest.main()
