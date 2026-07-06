#!/usr/bin/env python3
"""Print steps to set quiver's GitHub social preview image.

Personal user repos do NOT have a separate repo avatar. The small icon next
to the repo name is your GitHub account avatar (github.com/settings/profile).

What you CAN set per repo:
  Settings → Social preview  (link unfurls on Slack, Twitter, etc.)

The README mascot (assets/mascot.png) is what visitors see on the repo page.
"""

import sys
from pathlib import Path

REPO = "c-wenlong/quiver"
SETTINGS = f"https://github.com/{REPO}/settings"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    social = root / "assets" / "social-preview.png"
    mascot = root / "assets" / "mascot.png"

    if not social.is_file():
        print(f"Missing: {social}", file=sys.stderr)
        return 1

    print(
        f"""
GitHub images for a personal repo (not an org)

There is no per-repo profile picture. The icon beside "quiver" on GitHub is
your account avatar — change that at https://github.com/settings/profile if
you want (optional; most people keep their face/logo there).

Repo page branding
  Already done: README shows {mascot.name} from assets/

Social preview (when someone pastes the repo link)
  1. Open {SETTINGS}
  2. Scroll to "Social preview" → Edit → Upload an image
  3. Choose: {social}
     (1280×640 PNG — also fine for this upload)

Direct link to settings: {SETTINGS}
"""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
