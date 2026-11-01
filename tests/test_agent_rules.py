"""Mechanical checks for rules AGENTS.md would otherwise state in prose.

Rules an agent can break without noticing belong in a check, not a
paragraph (#751). This file holds the ones that need nothing but the repo:

- the full suite runs against a throwaway HOME, because integration tests
  spawn ``swe`` and write registries and caches into whatever HOME they get;
- AGENTS.md stays small, since every session loads it, and every relative
  link in it resolves, since its reference material lives in the docs.
"""

import os
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENTS = ROOT / "AGENTS.md"
AGENTS_MAX_BYTES = 25_000

_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _slug(heading: str) -> str:
    """GitHub's anchor for a markdown heading."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def _anchors(path: pathlib.Path) -> set[str]:
    anchors = set()
    in_fence = False
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("#"):
            anchors.add(_slug(line.lstrip("#")))
    return anchors


class ThrowawayHomeTest(unittest.TestCase):
    def test_suite_runs_against_a_throwaway_home(self):
        try:
            import pwd
        except ImportError:  # pragma: no cover - not a supported platform
            self.skipTest("no pwd module")
        # expanduser falls back to the account record when HOME is unset,
        # which is exactly where swe would then write.
        home = os.path.expanduser("~")
        try:
            real = pwd.getpwuid(os.getuid()).pw_dir
        except KeyError:  # a container uid with no passwd entry has no real home
            self.skipTest("no account record for this uid")
        self.assertNotEqual(
            os.path.realpath(home), os.path.realpath(real),
            "the suite is running against your real HOME, so integration "
            "tests write into ~/.swe. Rerun with "
            "HOME=\"$(mktemp -d)\" python3 -m unittest discover -s tests "
            "-p 'test_*.py'",
        )


# The Nix derivation builds from a filtered source (src/, tests/, scripts/)
# that leaves the docs out on purpose, so these checks run only in a full
# checkout, which is what CI tests.
@unittest.skipUnless(AGENTS.exists(), "AGENTS.md is not in this source tree")
class AgentsMdTest(unittest.TestCase):
    def test_stays_under_the_size_cap(self):
        size = len(AGENTS.read_bytes())
        self.assertLessEqual(
            size, AGENTS_MAX_BYTES,
            f"AGENTS.md is {size} bytes (cap {AGENTS_MAX_BYTES}). Move "
            "reference material into ARCHITECTURE.md or docs/ and link it.",
        )

    def test_relative_links_resolve(self):
        broken = []
        for target in _LINK.findall(AGENTS.read_text()):
            if re.match(r"[a-z]+:", target):
                continue
            file_part, _, anchor = target.partition("#")
            path = (ROOT / file_part) if file_part else AGENTS
            if not path.exists():
                broken.append(target)
            elif anchor and path.suffix == ".md" and anchor not in _anchors(path):
                broken.append(target)
        self.assertEqual(broken, [], "AGENTS.md links to missing files or headings")


if __name__ == "__main__":
    unittest.main()
