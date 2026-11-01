"""Service status pushes: the ported rule (``quiver.status.alerts``), its
state across hub restarts, the hub's check and the ntfy post.

The rule's cases mirror the Mac's ``ServiceStatusAlertsTests.swift``.
No network: statuses are built by hand and a stub poller hands them out.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from quiver.remote import notify
from quiver.remote.hub import GLOBAL_KEY, HubState
from quiver.status import alerts
from quiver.status.catalog import Health, service
from quiver.status.parsers import Incident, Report
from quiver.status.poller import ServiceStatus
from tests._tempdir import temp_dir


def _incident(id: str, impact: Health = Health.MINOR, maintenance: bool = False,
              update: str | None = None) -> Incident:
    return Incident(id=id, name=f"Incident {id}", impact=impact, latest_update=update,
                    maintenance=maintenance)


def _status(id: str, health: Health | None, incidents: list[Incident] | None = None) -> ServiceStatus:
    report = None if health is None else Report(health=health, incidents=incidents or [])
    return ServiceStatus(service(id), report, checked_at=1.0)


def _primed() -> alerts.State:
    state = alerts.State()
    alerts.step(state, [_status("claude", Health.OPERATIONAL)])
    return state


class TestRule(unittest.TestCase):
    def test_start_update_resolve(self):
        state = _primed()
        start = alerts.step(state, [_status("claude", Health.MINOR, [_incident("a")])])
        self.assertEqual([a.kind for a in start], ["started"])
        self.assertEqual(start[0].title, "Claude: minor incident")
        self.assertEqual(start[0].body, "Incident a")
        # A new update or a worse impact on the same incident is not news.
        self.assertEqual(alerts.step(state, [_status(
            "claude", Health.MAJOR, [_incident("a", Health.MAJOR, update="Monitoring")])]), [])
        resolved = alerts.step(state, [_status("claude", Health.OPERATIONAL)])
        self.assertEqual([a.kind for a in resolved], ["resolved"])
        self.assertEqual(resolved[0].title, "Claude is back to normal")
        self.assertEqual(resolved[0].body, "Resolved: Incident a")
        self.assertEqual(alerts.step(state, [_status("claude", Health.OPERATIONAL)]), [])

    def test_new_incident_while_down(self):
        state = _primed()
        alerts.step(state, [_status("claude", Health.MINOR, [_incident("a")])])
        more = alerts.step(state, [_status("claude", Health.MINOR, [_incident("a"), _incident("b")])])
        self.assertEqual([a.incident for a in more], ["Incident b"])

    def test_flapping_incident_is_quiet(self):
        state = _primed()
        down = _status("claude", Health.MINOR, [_incident("a")])
        up = _status("claude", Health.OPERATIONAL)
        kinds = [a.kind for reading in (down, up, down, up, down) for a in alerts.step(state, [reading])]
        self.assertEqual(kinds, ["started", "resolved"])

    def test_maintenance_and_other_groups_never_notify(self):
        state = _primed()
        self.assertEqual(alerts.step(state, [
            _status("claude", Health.MAINTENANCE, [_incident("m", Health.MAINTENANCE, maintenance=True)]),
            _status("vercel", Health.CRITICAL, [_incident("v", Health.CRITICAL)]),
            _status("npm", Health.MAJOR, [_incident("n", Health.MAJOR)]),
        ]), [])

    def test_maintenance_beside_an_incident(self):
        state = _primed()
        out = alerts.step(state, [_status(
            "openai", Health.MINOR, [_incident("m", Health.MAINTENANCE, maintenance=True), _incident("i")])])
        self.assertEqual([a.incident for a in out], ["Incident i"])
        self.assertNotIn("openai@m", state.delivered)

    def test_incidentless_outage(self):
        state = _primed()
        down = _status("cursor", Health.MINOR)
        self.assertEqual([a.incident for a in alerts.step(state, [down])], [None])
        self.assertEqual(alerts.step(state, [down]), [])
        resolved = alerts.step(state, [_status("cursor", Health.OPERATIONAL)])
        self.assertEqual(resolved[0].body, "The status page reports no open incident.")
        self.assertEqual(len(alerts.step(state, [down])), 1)

    def test_first_poll_only_seeds(self):
        state = alerts.State()
        down = _status("claude", Health.MINOR, [_incident("a")])
        self.assertEqual(alerts.step(state, [down]), [])
        self.assertTrue(state.primed)
        self.assertEqual(alerts.step(state, [down]), [])
        self.assertEqual(alerts.step(state, [_status("claude", Health.OPERATIONAL)]), [])

    def test_no_reading_leaves_state_alone(self):
        state = _primed()
        alerts.step(state, [_status("claude", Health.MINOR, [_incident("a")])])
        self.assertEqual(alerts.step(state, [_status("claude", None)]), [])
        self.assertEqual(state.down, {"claude": "Incident a"})

    def test_notify_off_records_silently(self):
        state = _primed()
        down = _status("claude", Health.MINOR, [_incident("a")])
        self.assertEqual(alerts.step(state, [down], notify=False), [])
        self.assertEqual(alerts.step(state, [down]), [])
        self.assertEqual(alerts.step(state, [_status("claude", Health.OPERATIONAL)]), [])

    def test_untracked_service_is_dropped(self):
        state = _primed()
        alerts.step(state, [_status("claude", Health.MINOR, [_incident("a")])])
        alerts.step(state, [_status("openai", Health.OPERATIONAL)])
        self.assertEqual(state.down, {})

    def test_delivered_is_capped(self):
        state = _primed()
        many = [_incident(f"i{n}") for n in range(alerts.CAP + 10)]
        alerts.step(state, [_status("claude", Health.MINOR, many)])
        self.assertEqual(len(state.delivered), alerts.CAP)
        self.assertEqual(state.delivered[-1], f"claude@i{alerts.CAP + 9}")

    def test_state_round_trip_and_bad_file(self):
        state = alerts.State(["claude@a"], {"claude": "x"}, True)
        self.assertEqual(alerts.State.from_dict(state.to_dict()), state)
        self.assertEqual(alerts.State.from_dict({"delivered": "nope", "down": [1], "primed": 1}),
                         alerts.State())
        self.assertEqual(alerts.State.from_dict(None), alerts.State())


class _Poller:
    """Hands out one prepared reading per call, like poller_for's."""

    def __init__(self):
        self.readings: list[list[ServiceStatus]] = []
        self.asked: list[list[str]] = []

    def statuses(self, services, force=False):
        self.asked.append([s.id for s in services])
        return self.readings.pop(0)


class TestHub(unittest.TestCase):
    """The issue's hub acceptance: one push per transition, none after a
    restart, none with the switch off."""

    def setUp(self):
        self.home = temp_dir(self)
        self.cfg = {"enabled": True, "url": "https://ntfy.test", "topic": "qw", "service_status": True}
        notify.save_config(self.home, self.cfg)

    def _hub(self):
        state = HubState(self.home)
        state.base_url = "http://100.64.1.2:8477"
        events = []
        state.emit = lambda key, ev: events.append((key, ev))
        return state, events

    def _posts(self, state, events):
        posts = []
        n = notify.Notifier(state, state.base_url, subscribe_key=GLOBAL_KEY)
        n._post = lambda cfg, payload: posts.append(payload)
        for _key, ev in events:
            n._handle(ev, self.cfg)
        return posts

    def test_transition_pushes_once_and_survives_restart(self):
        down = [_status("claude", Health.MINOR, [_incident("a")])]
        up = [_status("claude", Health.OPERATIONAL)]
        poller = _Poller()
        poller.readings = [up, down, down]
        state, events = self._hub()
        for _ in range(3):
            state._service_status_tick(poller)
        self.assertEqual([k for k, _ in events], [GLOBAL_KEY])
        data = events[0][1]["data"]
        self.assertEqual((data["kind"], data["change"], data["service"]), ("service", "started", "claude"))
        posts = self._posts(state, events)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "Claude: minor incident")
        self.assertEqual(posts[0]["message"], "Incident a")
        self.assertEqual(posts[0]["click"], "https://status.claude.com")
        self.assertEqual(posts[0]["tags"], ["warning"])
        # The tracked list is every built-in.
        self.assertIn("gemini", poller.asked[0])

        restarted, again = self._hub()
        poller.readings = [down, up]
        restarted._service_status_tick(poller)
        self.assertEqual(again, [])
        restarted._service_status_tick(poller)
        posts = self._posts(restarted, again)
        self.assertEqual([p["title"] for p in posts], ["Claude is back to normal"])
        self.assertEqual(posts[0]["tags"], ["white_check_mark"])

    def _started_targets(self, cfg):
        notify.save_config(self.home, cfg)
        names = []

        class _Thread:
            def __init__(self, target=None, name=None, daemon=None):
                names.append(name)

            def start(self):
                pass

        state = HubState(self.home)
        with patch("quiver.remote.hub.threading.Thread", _Thread):
            state.start()
        return names

    def test_switch_starts_the_loop(self):
        self.assertIn("remote-service-status", self._started_targets(self.cfg))
        off = {k: v for k, v in self.cfg.items() if k != "service_status"}
        self.assertNotIn("remote-service-status", self._started_targets(off))


class TestCmdNotify(unittest.TestCase):
    def setUp(self):
        from quiver.remote import commands
        self.commands = commands
        self.home = temp_dir(self)
        patcher = patch("quiver.remote.notify.remote_dir_for",
                        side_effect=lambda home=None: self.home / "remote")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, flags):
        with patch("sys.stdout", io.StringIO()) as out, patch("sys.stderr", io.StringIO()):
            rc = self.commands._cmd_notify(flags, json_view=False)
        return rc, out.getvalue()

    def test_service_status_flag(self):
        rc, out = self._run({"--ntfy": "https://n.test", "--topic": "qw"})
        self.assertIn("service status pushes off", out)
        rc, out = self._run({"--service-status": "on"})
        self.assertEqual(rc, 0)
        self.assertIs(notify.load_config(self.home)["service_status"], True)
        self.assertIn("service status pushes on", out)
        self._run({"--service-status": "off"})
        self.assertIs(notify.load_config(self.home)["service_status"], False)
        rc, _ = self._run({"--service-status": "maybe"})
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
