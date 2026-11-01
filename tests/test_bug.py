import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from swe import bug


def _query(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


class IssueUrlTests(unittest.TestCase):
    """The link prefills GitHub's issue forms by field id, so the names
    and the area option must match .github/ISSUE_TEMPLATE exactly."""

    def test_bug_link_fills_area_version_and_os(self):
        url = bug.issue_url("bug", "swe 1.1.1 · Python 3.12.8", "macOS 26.1")
        self.assertTrue(url.startswith("https://github.com/c-wenlong/swe/issues/new?"))
        self.assertEqual(_query(url), {
            "template": "bug_report.yml", "area": "CLI (swe)",
            "version": "swe 1.1.1 · Python 3.12.8", "os": "macOS 26.1",
        })

    def test_feature_link_carries_only_the_area(self):
        url = bug.issue_url("feature", "swe 1.1.1", "macOS 26.1")
        self.assertEqual(_query(url), {"template": "feature_request.yml", "area": "CLI (swe)"})

    def test_plus_and_space_are_percent_encoded(self):
        url = bug.issue_url("bug", "1.2+dev", "Ubuntu 24.04 LTS")
        self.assertIn("1.2%2Bdev", url)
        self.assertIn("Ubuntu%2024.04%20LTS", url)

    def test_areas_match_the_issue_forms(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"
        for name in bug.TEMPLATES.values():
            form = (root / name).read_text()
            self.assertIn("id: area", form, name)
            self.assertIn(f"- {bug.CLI_AREA}\n", form, name)
            self.assertIn("- Mac app\n", form, name)
        bug_form = (root / "bug_report.yml").read_text()
        self.assertIn("id: version", bug_form)
        self.assertIn("id: os", bug_form)


class OsLineTests(unittest.TestCase):
    def test_macos(self):
        with mock.patch("platform.system", return_value="Darwin"), \
                mock.patch("platform.mac_ver", return_value=("26.1", ("", "", ""), "arm64")):
            self.assertEqual(bug.os_line(), "macOS 26.1")

    def test_macos_without_a_release(self):
        with mock.patch("platform.system", return_value="Darwin"), \
                mock.patch("platform.mac_ver", return_value=("", ("", "", ""), "")):
            self.assertEqual(bug.os_line(), "macOS")

    def test_other_systems_use_name_and_release(self):
        with mock.patch("platform.system", return_value="Windows"), \
                mock.patch("platform.release", return_value="11"):
            self.assertEqual(bug.os_line(), "Windows 11")

    def test_linux_uses_the_distro_name(self):
        with mock.patch("platform.system", return_value="Linux"), \
                mock.patch("platform.freedesktop_os_release",
                           return_value={"PRETTY_NAME": "Ubuntu 24.04 LTS"}):
            self.assertEqual(bug.os_line(), "Ubuntu 24.04 LTS")

    def test_linux_without_os_release(self):
        with mock.patch("platform.system", return_value="Linux"), \
                mock.patch("platform.freedesktop_os_release", side_effect=OSError), \
                mock.patch("platform.release", return_value="6.8.0"):
            self.assertEqual(bug.os_line(), "Linux 6.8.0")


class CmdBugTests(unittest.TestCase):
    def _run(self, args, opened=True):
        out = io.StringIO()
        with mock.patch("webbrowser.open", return_value=opened) as browser, redirect_stdout(out):
            status = bug.cmd_bug(args)
        return status, out.getvalue(), browser

    def test_opens_the_bug_form(self):
        status, out, browser = self._run([])
        self.assertEqual(status, 0)
        browser.assert_called_once()
        self.assertIn("template=bug_report.yml", browser.call_args[0][0])
        self.assertIn("bug report", out)

    def test_print_does_not_open(self):
        status, out, browser = self._run(["--print", "--feature"])
        self.assertEqual(status, 0)
        browser.assert_not_called()
        self.assertIn("template=feature_request.yml", out)

    def test_prints_the_link_when_no_browser_opens(self):
        status, out, _ = self._run([], opened=False)
        self.assertEqual(status, 0)
        self.assertTrue(out.startswith("https://github.com/c-wenlong/swe/issues/new?"))

    def test_json_opens_nothing(self):
        status, out, browser = self._run(["--json"])
        self.assertEqual(status, 0)
        browser.assert_not_called()
        data = json.loads(out)["data"]
        self.assertEqual(data["kind"], "bug")
        self.assertEqual(set(data), {"kind", "url", "version", "os"})

    def test_rejects_unknown_args(self):
        status, out, browser = self._run(["--nope"])
        self.assertEqual(status, 1)
        browser.assert_not_called()
        self.assertIn("Unexpected arg: --nope", out)


if __name__ == "__main__":
    unittest.main()
