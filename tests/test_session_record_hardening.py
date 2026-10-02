"""One malformed session record must not poison the whole listing.

json.loads accepts ``NaN``/``Infinity`` literals and sqlite REAL columns can
hold ±Inf; a non-string ``cwd`` (int, dict) used to reach ``path.replace``
and crash ``swe session`` for every harness. The Session dataclass now
coerces at construction, and ``parse_iso_ts`` rejects non-finite values.
"""

import io
import math
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from quiver.sessions.engines.common import parse_iso_ts
from quiver.sessions.models import Session

PROJECT_SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def _env(home: pathlib.Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(PROJECT_SRC) if not existing else f"{PROJECT_SRC}{os.pathsep}{existing}"
    )
    return env


class ParseIsoTsFiniteTest(unittest.TestCase):
    def test_nan_and_inf_return_zero(self):
        for bad in (float("nan"), float("inf"), float("-inf"), "1e999"):
            self.assertEqual(parse_iso_ts(bad), 0.0, msg=repr(bad))

    def test_normal_values_unchanged(self):
        self.assertEqual(parse_iso_ts(1700000000), 1700000000 * 1000)
        self.assertEqual(parse_iso_ts(1700000000123), 1700000000123)
        self.assertGreater(parse_iso_ts("2024-01-01T00:00:00Z"), 0)


class SessionCoercionTest(unittest.TestCase):
    def _mk(self, **over):
        fields = dict(timestamp=0.0, agent="claude", path="/tmp")
        fields.update(over)
        return Session(**fields)

    def test_nan_timestamp_coerced(self):
        s = self._mk(timestamp=float("nan"))
        self.assertEqual(s.timestamp, 0.0)
        self.assertTrue(math.isfinite(s.timestamp))

    def test_inf_timestamp_coerced(self):
        self.assertEqual(self._mk(timestamp=float("inf")).timestamp, 0.0)

    def test_string_timestamp_coerced_to_float(self):
        self.assertEqual(self._mk(timestamp="x").timestamp, 0.0)
        self.assertEqual(self._mk(timestamp="1700000000").timestamp, 1700000000.0)

    def test_non_string_path_coerced(self):
        # Non-string non-path values become "" (missing): "123" would be a
        # relative path a --here filter could false-match against cwd.
        self.assertEqual(self._mk(path=123).path, "")
        self.assertEqual(self._mk(path=None).path, "")
        self.assertEqual(self._mk(path={"cwd": "/x"}).path, "")
        self.assertEqual(
            self._mk(path=pathlib.Path("/real/dir")).path, "/real/dir"
        )

    def test_non_string_fields_coerced(self):
        s = self._mk(agent=123, title=None, session_id=45)
        self.assertEqual(s.agent, "123")
        self.assertEqual(s.title, "")
        self.assertEqual(s.session_id, "45")


class DaysOverflowTest(unittest.TestCase):
    def test_absurd_days_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            r = subprocess.run(
                [sys.executable, "-m", "quiver.cli", "session", "--days=1000000"],
                env=_env(home), capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stdout + r.stderr)

    def test_absurd_end_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            r = subprocess.run(
                [sys.executable, "-m", "quiver.cli", "session",
                 "--start=9999-12-30", "--end=9999-12-31"],
                env=_env(home), capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 1)
            self.assertNotIn("Traceback", r.stdout + r.stderr)


class MalformedSessionListingTest(unittest.TestCase):
    """The verified critical: one NaN cline record killed `swe session`."""

    def _write_cline_session(
        self, home: pathlib.Path, sid: str, body: str
    ) -> None:
        session_dir = home / ".cline" / "data" / "sessions" / sid
        session_dir.mkdir(parents=True)
        (session_dir / f"{sid}.json").write_text(body)

    def _run_session(self, home: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "quiver.cli", "session"],
            env=_env(home), capture_output=True, text=True,
        )

    def test_nan_session_file_does_not_crash_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            self._write_cline_session(
                home, "x",
                '{"session_id": "x", "cwd": "/tmp", "started_at": NaN}',
            )
            # A valid neighbour proves the bad record did not take the
            # whole listing down with it: "no output" would also exit 0.
            self._write_cline_session(
                home, "good",
                '{"session_id": "good", "cwd": "/tmp", '
                '"started_at": "2024-01-01T00:00:00Z"}',
            )
            r = self._run_session(home)
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stdout + r.stderr)
            self.assertIn("#good", r.stdout)

    def test_non_string_cwd_does_not_crash_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            self._write_cline_session(
                home, "y",
                '{"session_id": "y", "cwd": 123, '
                '"started_at": "2024-01-01T00:00:00Z"}',
            )
            self._write_cline_session(
                home, "good",
                '{"session_id": "good", "cwd": "/tmp", '
                '"started_at": "2024-01-01T00:00:00Z"}',
            )
            r = self._run_session(home)
            self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stdout + r.stderr)
            self.assertIn("#good", r.stdout)


if __name__ == "__main__":
    unittest.main()
