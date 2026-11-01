#!/usr/bin/env python3
"""Point the Homebrew tap at a new release.

Rewrites ``Casks/swe.rb`` and ``Formula/swe.rb`` in a checkout of
c-wenlong/homebrew-swe with a release's version and the SHA-256 of its
DMG and wheel. release.yml runs it after the assets are on the release.

    python3 scripts/bump_tap.py TAP_DIR VERSION DMG_SHA256 WHEEL_SHA256

The first SWE release materializes ``Casks/swe.rb``: there is no SWE DMG
before then, so the file cannot exist earlier (``brew audit --online``
would 404). ``Casks/quiver.rb`` is left frozen at the last Quiver build
so installs and uninstalls of that cask keep working.

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

# The whole cask, written by the first SWE release. Later releases only
# bump ``version`` and ``sha256``. The uninstall and zap stanzas cover
# the Quiver identifiers too, so a machine that ran Quiver.app gets the
# leftovers cleaned when the new cask is removed.
_CASK = """\
cask "swe" do
  version "{version}"
  sha256 "{sha256}"

  url "https://github.com/c-wenlong/swe/releases/download/v#{{version}}/SWE-#{{version}}.dmg"
  name "SWE"
  desc "Manage AI coding CLIs, their sessions, usage and rate limits"
  homepage "https://github.com/c-wenlong/swe"

  livecheck do
    url "https://github.com/c-wenlong/swe/releases/latest/download/appcast.xml"
    strategy :sparkle, &:short_version
  end

  auto_updates true
  depends_on arch: :arm64
  depends_on macos: :tahoe

  app "SWE.app"

  uninstall launchctl: [
              "com.cwenlong.swe.fan",
              "com.cwenlong.quiver.fan",
            ],
            delete:    [
              "/Library/LaunchDaemons/com.cwenlong.swe.fan.plist",
              "/Library/LaunchDaemons/com.cwenlong.quiver.fan.plist",
              "/Library/PrivilegedHelperTools/com.cwenlong.swe.fan",
              "/Library/PrivilegedHelperTools/com.cwenlong.quiver.fan",
            ]

  zap trash: [
    "~/Library/Caches/com.cwenlong.swe",
    "~/Library/Caches/com.cwenlong.quiver",
    "~/Library/Containers/com.cwenlong.swe.widgets",
    "~/Library/Containers/com.cwenlong.quiver.widgets",
    "~/Library/HTTPStorages/com.cwenlong.swe",
    "~/Library/HTTPStorages/com.cwenlong.quiver",
    "~/Library/Preferences/com.cwenlong.swe.plist",
    "~/Library/Preferences/com.cwenlong.quiver.plist",
    "~/Library/Saved Application State/com.cwenlong.swe.savedState",
    "~/Library/Saved Application State/com.cwenlong.quiver.savedState",
    "~/Library/WebKit/com.cwenlong.swe",
    "~/Library/WebKit/com.cwenlong.quiver",
  ]
end
"""


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


def new_cask(version: str, sha256: str) -> str:
    """``Casks/swe.rb`` as the first SWE release writes it."""
    return _CASK.format(version=version, sha256=sha256)


def bump_cask(text: str, version: str, sha256: str) -> str:
    """The cask's url interpolates ``#{version}``, so only these two change."""
    text = _sub_once(r'^(  version )"[^"]*"$', rf'\g<1>"{version}"', text, "cask version line")
    return _sub_once(r'^(  sha256 )"[0-9a-f]{64}"$', rf'\g<1>"{sha256}"', text, "cask sha256 line")


def bump_formula(text: str, version: str, sha256: str) -> str:
    """The formula spells the version out in its url, so rewrite the url."""
    text = _sub_once(
        r"(/releases/download/)v[^/\"]+/(?:quiver|swe)-[^/\"]+-py3-none-any\.whl",
        rf"\g<1>v{version}/swe-{version}-py3-none-any.whl",
        text,
        "formula wheel url",
    )
    text = _sub_once(
        r"(?:quiver|swe)-#\{version\}-py3-none-any\.whl",
        "swe-#{version}-py3-none-any.whl",
        text,
        "formula wheel filename",
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
    cask_path = Path(tap) / "Casks/swe.rb"
    current_path = cask_path if cask_path.exists() else Path(tap) / "Casks/quiver.rb"
    try:
        current = cask_version(current_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"bump_tap: {current_path.name}: {exc}", file=sys.stderr)
        return 1
    if _key(version) < _key(current):
        print(f"bump_tap: the tap is already on {current}, newer than {version}; leaving it")
        return 0
    if cask_path.exists():
        try:
            cask_path.write_text(
                bump_cask(cask_path.read_text(encoding="utf-8"), version, dmg_sha),
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            print(f"bump_tap: Casks/swe.rb: {exc}", file=sys.stderr)
            return 1
    else:
        cask_path.write_text(new_cask(version, dmg_sha), encoding="utf-8")
    print(f"bump_tap: Casks/swe.rb -> {version}")
    path = Path(tap) / "Formula/swe.rb"
    try:
        path.write_text(
            bump_formula(path.read_text(encoding="utf-8"), version, wheel_sha),
            encoding="utf-8",
        )
    except (OSError, ValueError) as exc:
        print(f"bump_tap: Formula/swe.rb: {exc}", file=sys.stderr)
        return 1
    print(f"bump_tap: Formula/swe.rb -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
