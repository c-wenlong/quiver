#!/usr/bin/env python3
"""Point the Homebrew tap at a new release.

Rewrites ``Casks/quiver.rb`` and ``Formula/swe.rb`` in a checkout of
c-wenlong/homebrew-quiver with a release's version and the SHA-256 of its
DMG and wheel. release.yml runs it after the assets are on the release.

    python3 scripts/bump_tap.py TAP_DIR VERSION DMG_SHA256 WHEEL_SHA256

Each substitution must match exactly once; anything else means the tap
file changed shape, and the script fails rather than guess. A version
older than the one the cask already has is a no-op, never a rollback.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_VERSION = re.compile(r"^\d+(\.\d+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sub_once(pattern: str, replacement: str, text: str, what: str) -> str:
    new, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"expected exactly one {what}, found {count}")
    return new


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def cask_version(text: str) -> str:
    """The version the tap's cask points at now."""
    found = re.findall(r'^  version "([^"]*)"$', text, flags=re.MULTILINE)
    if len(found) != 1 or not _VERSION.match(found[0]):
        raise ValueError(f"expected exactly one plain cask version line, found {found!r}")
    return found[0]


def bump_cask(text: str, version: str, sha256: str) -> str:
    """The cask's url interpolates ``#{version}``, so only these two change."""
    text = _sub_once(r'^(  version )"[^"]*"$', rf'\g<1>"{version}"', text, "cask version line")
    return _sub_once(r'^(  sha256 )"[0-9a-f]{64}"$', rf'\g<1>"{sha256}"', text, "cask sha256 line")


def bump_formula(text: str, version: str, sha256: str) -> str:
    """The formula spells the version out in its url, so rewrite the url."""
    text = _sub_once(
        r"(/releases/download/)v[^/\"]+/quiver-[^/\"]+-py3-none-any\.whl",
        rf"\g<1>v{version}/swe-{version}-py3-none-any.whl",
        text,
        "formula wheel url",
    )
    return _sub_once(r'^(  sha256 )"[0-9a-f]{64}"$', rf'\g<1>"{sha256}"', text, "formula sha256 line")


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    tap, version, dmg_sha, wheel_sha = argv
    version = version.removeprefix("v")
    if not _VERSION.match(version):
        print(f"bump_tap: {version!r} is not a plain dotted version", file=sys.stderr)
        return 2
    for sha in (dmg_sha, wheel_sha):
        if not _SHA256.match(sha):
            print(f"bump_tap: {sha!r} is not a lowercase SHA-256", file=sys.stderr)
            return 2
    # A slow run for an older tag can reach the tap after a newer one
    # already bumped it; it must leave the newer version in place.
    try:
        current = cask_version((Path(tap) / "Casks/quiver.rb").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"bump_tap: Casks/quiver.rb: {exc}", file=sys.stderr)
        return 1
    if _key(version) < _key(current):
        print(f"bump_tap: the tap is already on {current}, newer than {version}; leaving it")
        return 0
    for rel, bump, sha in (
        ("Casks/quiver.rb", bump_cask, dmg_sha),
        ("Formula/swe.rb", bump_formula, wheel_sha),
    ):
        path = Path(tap) / rel
        try:
            path.write_text(bump(path.read_text(encoding="utf-8"), version, sha), encoding="utf-8")
        except (OSError, ValueError) as exc:
            print(f"bump_tap: {rel}: {exc}", file=sys.stderr)
            return 1
        print(f"bump_tap: {rel} -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
