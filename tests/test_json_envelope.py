"""The four discover/setup commands emit the standard {schema, data} envelope.

Every --json command writes exactly one JSON document to stdout; an
--apply result rides inside the same document's data, never a second
document. These tests pin that contract for the commands that once
printed bare json.dumps output.
"""

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock
from unittest.mock import patch


def _decode_one_document(raw: str):
    """Parse stdout and fail if it holds anything but one JSON document."""
    decoder = json.JSONDecoder()
    document, end = decoder.raw_decode(raw)
    if raw[end:].strip():
        raise AssertionError("stdout holds more than one JSON document")
    return document


class HarnessDiscoverEnvelopeTest(unittest.TestCase):
    def _finding(self, name, confidence="low", status="new"):
        from swe.harness.discover import HarnessFinding
        return HarnessFinding(name=name, command="", path=f"/h/.{name}",
                              confidence=confidence, source="home_scan",
                              status=status, description="", tags=(), aliases=())

    def _run(self, args, findings, apply_return=None):
        from swe.harness import discover_commands as dc
        apply_fn = mock.Mock(return_value=(apply_return or [], []))
        out = StringIO()
        with patch.object(dc, "discover_harnesses", lambda **k: findings), \
                patch.object(dc, "apply_findings", apply_fn), \
                redirect_stdout(out):
            code = dc.cmd_discover(args)
        return code, out.getvalue(), apply_fn

    def test_json_emits_the_envelope(self):
        code, raw, _ = self._run(["--json"], [self._finding("a")])
        self.assertEqual(code, 0)
        doc = _decode_one_document(raw)
        self.assertEqual(doc["schema"], 1)
        self.assertIn("findings", doc["data"])
        self.assertEqual(doc["data"]["findings"][0]["name"], "a")

    def test_json_apply_rides_in_the_same_document(self):
        code, raw, apply_fn = self._run(
            ["--json", "--apply"], [self._finding("a")], apply_return=["a"])
        self.assertEqual(code, 0)
        apply_fn.assert_called_once()
        doc = _decode_one_document(raw)
        self.assertEqual(doc["data"]["applied"], {"added": ["a"], "skipped": []})
        self.assertEqual(len(doc["data"]["findings"]), 1)


class McpDiscoverEnvelopeTest(unittest.TestCase):
    def _finding(self, name, status="new"):
        from swe.mcp.discover import McpFinding
        return McpFinding(name=name, tools=("claude",), status=status,
                          source_tool="claude",
                          server={"command": "npx", "args": ["-y", name]})

    def _run(self, args, findings, merge=None):
        from swe.mcp import discover_commands as dc
        from swe.mcp.discover import MergeResult
        merge = merge or MergeResult(added=[], updated=[], orphaned=[], pruned=[])
        apply_fn = mock.Mock(return_value=merge)
        out = StringIO()
        with patch.object(dc, "discover_mcp_servers", lambda **k: findings), \
                patch.object(dc, "apply_mcp_findings", apply_fn), \
                redirect_stdout(out):
            code = dc.cmd_discover(args)
        return code, out.getvalue(), apply_fn

    def test_json_emits_the_envelope(self):
        code, raw, _ = self._run(["--json"], [self._finding("linear")])
        self.assertEqual(code, 0)
        doc = _decode_one_document(raw)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["data"]["findings"][0]["name"], "linear")

    def test_json_apply_rides_in_the_same_document(self):
        from swe.mcp.discover import MergeResult
        merge = MergeResult(added=["linear"], updated=[], orphaned=["old"], pruned=[])
        code, raw, _ = self._run(["--json", "--apply"], [self._finding("linear")],
                                 merge=merge)
        self.assertEqual(code, 0)
        doc = _decode_one_document(raw)
        self.assertEqual(doc["data"]["applied"]["added"], ["linear"])
        self.assertEqual(doc["data"]["applied"]["orphaned"], ["old"])


class SkillsDiscoverEnvelopeTest(unittest.TestCase):
    def _finding(self, label, status="new"):
        from swe.skills.catalog_discover import SkillCatalogFinding
        return SkillCatalogFinding(path=Path("/x/skills"), label=label,
                                   skill_count=3, source="desktop", status=status)

    def _run(self, args, findings, apply_return=None):
        from swe.skills import catalog_commands as cc
        apply_fn = mock.Mock(return_value=apply_return if apply_return is not None else [])
        out = StringIO()
        with patch.object(cc, "discover_skill_catalogs", lambda **k: findings), \
                patch.object(cc, "apply_skill_catalog_findings", apply_fn), \
                redirect_stdout(out):
            code = cc.cmd_skills_discover(args)
        return code, out.getvalue(), apply_fn

    def test_json_emits_the_envelope(self):
        code, raw, _ = self._run(["--json"], [self._finding("mine")])
        self.assertEqual(code, 0)
        doc = _decode_one_document(raw)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["data"]["findings"][0]["label"], "mine")

    def test_json_apply_rides_in_the_same_document(self):
        code, raw, _ = self._run(["--json", "--apply"], [self._finding("mine")],
                                 apply_return=["mine"])
        self.assertEqual(code, 0)
        doc = _decode_one_document(raw)
        self.assertEqual(doc["data"]["applied"], {"added": ["mine"]})


class SetupEnvelopeTest(unittest.TestCase):
    def _run(self, args):
        from swe.setup import commands as sc
        from swe.mcp.discover import MergeResult
        merge = MergeResult(added=[], updated=[], orphaned=[], pruned=[])
        out = StringIO()
        # --apply links into the home it finds; keep that out of the real one.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        with patch.object(Path, "home", staticmethod(lambda: home)), \
                patch.object(sc, "discover_harnesses", lambda: []), \
                patch.object(sc, "discover_mcp_servers", lambda **k: []), \
                patch.object(sc, "skills_symlink_hints", lambda **k: []), \
                patch.object(sc, "apply_findings", mock.Mock(return_value=[])), \
                patch.object(sc, "apply_mcp_findings", mock.Mock(return_value=merge)), \
                patch.object(sc, "apply_skills_symlink_hints", mock.Mock(return_value=[])), \
                redirect_stdout(out):
            code = sc.cmd_setup(args)
        return code, out.getvalue()

    def test_json_emits_the_envelope(self):
        code, raw = self._run(["--json", "--non-interactive"])
        self.assertEqual(code, 0)
        doc = _decode_one_document(raw)
        self.assertEqual(doc["schema"], 1)
        for key in ("harness", "mcp", "skills"):
            self.assertIn(key, doc["data"])

    def test_json_apply_emits_one_document(self):
        # Regression: this used to print two separate json.dumps documents.
        code, raw = self._run(["--json", "--apply"])
        self.assertEqual(code, 0)
        doc = _decode_one_document(raw)
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(sorted(doc["data"]["applied"]), ["harness", "links", "mcp", "skills"])


if __name__ == "__main__":
    unittest.main()
