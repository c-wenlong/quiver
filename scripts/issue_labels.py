"""Label an issue by its form's Area answer, for .github/workflows/issue-labels.yml.

    python3 scripts/issue_labels.py

Reads the issue from the environment the workflow sets (REPO, NUMBER, BODY,
LABELS as a JSON list of names) and, when the Area answer maps to a label,
adds it and takes off any other area label, so editing the answer moves the
label. GitHub renders a form answer as "### Area" then the option on its own
line. An issue with no Area (a blank issue, an old form) is left alone.
"""

import json
import os
import re
import subprocess

# Every option of the Area dropdown in .github/ISSUE_TEMPLATE/*.yml.
AREAS = {
    "Mac app": "mac",
    "CLI (swe)": "cli",
    "iPhone app": "ios",
    "Web app": "web",
    "Website or docs": "documentation",
}

_ANSWER = re.compile(r"^###\s+Area\s*\n+\s*(.+?)\s*$", re.MULTILINE)


def area_label(body: str | None) -> str | None:
    match = _ANSWER.search(body or "")
    return AREAS.get(match.group(1)) if match else None


def plan(body: str | None, current: list[str]) -> tuple[list[str], list[str]]:
    """(labels to add, labels to remove) for an issue with these labels."""
    label = area_label(body)
    if label is None:
        return [], []
    add = [] if label in current else [label]
    remove = [name for name in current if name in AREAS.values() and name != label]
    return add, remove


def main() -> int:
    add, remove = plan(os.environ.get("BODY"), json.loads(os.environ.get("LABELS") or "[]"))
    if not add and not remove:
        return 0
    command = ["gh", "issue", "edit", os.environ["NUMBER"], "--repo", os.environ["REPO"]]
    for name in add:
        command += ["--add-label", name]
    for name in remove:
        command += ["--remove-label", name]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
