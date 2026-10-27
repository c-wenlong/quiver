"""Label an issue by its form's Area answer, for .github/workflows/issue-labels.yml.

    python3 scripts/issue_labels.py

Reads the issue's current body and labels with `gh issue view` (REPO and
NUMBER come from the workflow's environment), not the event's copy, which
can be stale by the time a queued run starts. When the Area answer maps to a
label it adds that one and takes off any other area label, so editing the
answer moves the label. GitHub renders a form answer as "### Area" then the
option on its own line. An issue with no Area (a blank issue, an old form)
is left alone.
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
    repo, number = os.environ["REPO"], os.environ["NUMBER"]
    view = subprocess.run(["gh", "issue", "view", number, "--repo", repo, "--json", "body,labels"],
                          capture_output=True, text=True, check=True)
    issue = json.loads(view.stdout)
    add, remove = plan(issue.get("body"), [label["name"] for label in issue.get("labels") or []])
    if not add and not remove:
        return 0
    command = ["gh", "issue", "edit", number, "--repo", repo]
    for name in add:
        command += ["--add-label", name]
    for name in remove:
        command += ["--remove-label", name]
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
