#!/usr/bin/env python3
"""Upload quiver repo images to GitHub (avatar + social preview).

GitHub does not expose a public API for repository avatar or social-preview
uploads. Bearer-token POSTs to the web upload endpoint are rejected (CSRF /
session required). Use this script to print exact manual steps, or automate
with a logged-in browser (Playwright) if you add that separately.

Assets live in the repo at:
  assets/mascot.png         — repo profile picture (~512×512 crop works well)
  assets/social-preview.png — social preview (1280×640)
"""

import sys
from pathlib import Path

REPO = "c-wenlong/quiver"
SETTINGS = f"https://github.com/{REPO}/settings"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    mascot = root / "assets" / "mascot.png"
    social = root / "assets" / "social-preview.png"

    for path in (mascot, social):
        if not path.is_file():
            print(f"Missing asset: {path}", file=sys.stderr)
            return 1

    print(
        f"""
GitHub repo images — manual upload (no public API)

1. Open repo settings:
   {SETTINGS}

2. Profile picture (repo avatar)
   • Scroll to the profile picture / repository image section
   • Upload: {mascot}

3. Social preview (link unfurls on Slack, Twitter, etc.)
   • Scroll to "Social preview" → Edit
   • Upload: {social}

Both files are committed in the repo under assets/ for drag-and-drop.
"""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
