"""tsid.py: tailscale whois parsing, failure modes."""

import unittest
from unittest.mock import MagicMock, patch

from quiver.remote import tsid


def _cp(rc=0, stdout=""):
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    return cp


_WHOIS = """{
  "Node": {"Name": "iphone-13.taild6a23f.ts.net.",
           "StableID": "npKjnYUTsZ11CNTRL", "Tags": null,
           "Hostinfo": {"Hostname": "iphone-13"}},
  "UserProfile": {"LoginName": "c-wenlong@github", "DisplayName": "Kai"}
}"""


class TestWhois(unittest.TestCase):
    def _run(self, cp=_cp(stdout=_WHOIS), exe="/x/tailscale"):
        with patch.object(tsid.shutil, "which", return_value=exe), \
             patch.object(tsid.subprocess, "run", return_value=cp) as run:
            return tsid.whois("100.66.1.2"), run

    def test_parses_fields(self):
        out, run = self._run()
        self.assertEqual(out["node_name"], "iphone-13.taild6a23f.ts.net")
        self.assertEqual(out["node_short"], "iphone-13")
        self.assertEqual(out["node_id"], "npKjnYUTsZ11CNTRL")
        self.assertEqual(out["login"], "c-wenlong@github")
        self.assertEqual(out["display_name"], "Kai")
        self.assertFalse(out["tagged"])
        self.assertEqual(
            list(run.call_args[0][0]),
            ["/x/tailscale", "whois", "--json", "100.66.1.2"],
        )

    def test_tagged_node_gets_no_login(self):
        import json
        data = json.loads(_WHOIS)
        data["Node"]["Tags"] = ["tag:server"]
        out, _ = self._run(_cp(stdout=json.dumps(data)))
        self.assertTrue(out["tagged"])
        self.assertEqual(out["login"], "")

    def test_nonzero_exit_is_none(self):
        out, _ = self._run(_cp(rc=1, stdout="peer not found"))
        self.assertIsNone(out)

    def test_bad_json_is_none(self):
        out, _ = self._run(_cp(stdout="not json"))
        self.assertIsNone(out)

    def test_run_raises_is_none(self):
        with patch.object(tsid.shutil, "which", return_value="/x/ts"), \
             patch.object(tsid.subprocess, "run", side_effect=OSError):
            self.assertIsNone(tsid.whois("100.66.1.2"))

    def test_no_binary_is_none(self):
        from pathlib import Path
        with patch.object(tsid.shutil, "which", return_value=None), \
             patch.object(tsid, "_APPSTORE_EXE",
                          Path("/nonexistent/Tailscale")):
            self.assertIsNone(tsid.whois("100.66.1.2"))

    def test_appstore_fallback(self):
        exe = MagicMock()
        exe.exists.return_value = True
        exe.__str__.return_value = (
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
        )
        with patch.object(tsid.shutil, "which", return_value=None), \
             patch.object(tsid, "_APPSTORE_EXE", exe), \
             patch.object(tsid.subprocess, "run",
                          return_value=_cp(stdout=_WHOIS)) as run:
            out = tsid.whois("100.66.1.2")
        self.assertIsNotNone(out)
        self.assertTrue(run.call_args[0][0][0].endswith("Tailscale"))


if __name__ == "__main__":
    unittest.main()
