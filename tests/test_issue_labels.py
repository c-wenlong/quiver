import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("issue_labels", ROOT / "scripts" / "issue_labels.py")
issue_labels = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(issue_labels)

FORMS = ROOT / ".github" / "ISSUE_TEMPLATE"


def _area_options(form: str) -> list[str]:
    """The options listed under the form's `id: area` dropdown."""
    block = form.split("id: area", 1)[1].split("validations:", 1)[0]
    return re.findall(r"^\s+- (.+)$", block, re.MULTILINE)


class AreaLabelTests(unittest.TestCase):
    def test_every_form_option_has_a_label(self):
        for name in ("bug_report.yml", "feature_request.yml"):
            options = _area_options((FORMS / name).read_text())
            self.assertEqual(options, list(issue_labels.AREAS), name)

    def test_reads_the_rendered_answer(self):
        body = "### Area\n\nCLI (swe)\n\n### What happened?\n\nIt broke."
        self.assertEqual(issue_labels.area_label(body), "cli")

    def test_no_area_or_an_unknown_one_means_no_label(self):
        self.assertIsNone(issue_labels.area_label(None))
        self.assertIsNone(issue_labels.area_label("A blank issue"))
        self.assertIsNone(issue_labels.area_label("### Area\n\nSomething new"))

    def test_plan_adds_the_label_once(self):
        body = "### Area\n\nMac app\n"
        self.assertEqual(issue_labels.plan(body, ["bug", "needs-triage"]), (["mac"], []))
        self.assertEqual(issue_labels.plan(body, ["bug", "mac"]), ([], []))

    def test_plan_moves_the_label_when_the_answer_changes(self):
        body = "### Area\n\nWebsite or docs\n"
        self.assertEqual(issue_labels.plan(body, ["bug", "mac"]), (["documentation"], ["mac"]))

    def test_plan_leaves_an_issue_without_an_area_alone(self):
        self.assertEqual(issue_labels.plan("No form here", ["mac"]), ([], []))


if __name__ == "__main__":
    unittest.main()
