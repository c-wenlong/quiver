#!/usr/bin/env python3
"""Copy the Mac app's harness logos into the iOS asset catalog.

The source of truth is app/mac/Sources/SWECore/Resources/harness-*.svg
(imported there by app/mac/script/import-harness-icons.py). Each becomes an
imageset named harness-<id>: colour logos (`-color.svg`) render as is,
single-colour marks render as templates in the text colour. Re-run after
the Mac set changes:

    python3 app/ios/script/import-harness-icons.py
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "app/mac/Sources/SWECore/Resources"
DEST = ROOT / "app/ios/Assets.xcassets/Harnesses"


def main() -> None:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    (DEST / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n")
    picked: dict[str, tuple[Path, bool]] = {}
    for svg in sorted(SRC.glob("harness-*.svg")):
        name = svg.stem.removeprefix("harness-")
        is_color = name.endswith("-color")
        hid = name.removesuffix("-color")
        # A colour logo wins over a mono one for the same harness, as on the Mac.
        if hid not in picked or is_color:
            picked[hid] = (svg, is_color)
    for hid, (svg, is_color) in sorted(picked.items()):
        imageset = DEST / f"harness-{hid}.imageset"
        imageset.mkdir()
        shutil.copy(svg, imageset / f"{hid}.svg")
        contents = {
            "images": [{"filename": f"{hid}.svg", "idiom": "universal"}],
            "info": {"author": "xcode", "version": 1},
            "properties": {
                "preserves-vector-representation": True,
                "template-rendering-intent": "original" if is_color else "template",
            },
        }
        (imageset / "Contents.json").write_text(json.dumps(contents, indent=2) + "\n")
    print(f"{len(picked)} harness icons -> {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
