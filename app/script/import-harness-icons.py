#!/usr/bin/env python3
"""Normalise harness logos into QuiverCore's resources.

    app/script/import-harness-icons.py SOURCES.tsv

SOURCES.tsv has columns: id, file, source_url, licence, notes (a header row
first). `file` is a path to an SVG. Each logo is written to
`app/Sources/QuiverCore/Resources/harness-<id>.svg` as a single-colour
mark: every painted fill and stroke becomes black, and titles, comments,
styles and fixed sizes are dropped, so the app can draw it as a template
image tinted to the harness colour. A logo with a full-bleed background
square keeps only the glyph, cut out of that square.

Refuses any SVG with scripts, event handlers, external references or
foreignObject. The provenance table is written beside the icons as
harness-icons.tsv and summarised in app/THIRD_PARTY_NOTICES.md by hand.
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
OUT = Path(__file__).resolve().parent.parent / "Sources" / "QuiverCore" / "Resources"
UNSAFE = re.compile(r"<script|\son[a-z]+\s*=|xlink:href|\shref\s*=|<foreignObject|<image", re.I)
PAINTS = ("fill", "stroke")


NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
ARITY = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}


def spell_out(d):
    """Path data with every number and arc flag separated by a space.

    The compact forms SVG allows (".5.5", arc flags run into the next
    number as in "0 01-4.45") trip up CoreSVG, which then draws nothing.
    """
    out, i = [], 0
    command = None
    while i < len(d):
        ch = d[i]
        if ch.isspace() or ch == ",":
            i += 1
            continue
        if ch.isalpha():
            command = ch
            out.append(ch)
            i += 1
            if ch in "Zz":
                command = None
            continue
        if command is None:
            raise ValueError(f"number outside a command in path data at {i}")
        arity = ARITY[command.upper()]
        for slot in range(arity):
            while i < len(d) and (d[i].isspace() or d[i] == ","):
                i += 1
            if command.upper() == "A" and slot in (3, 4):
                if i >= len(d) or d[i] not in "01":
                    raise ValueError("bad arc flag")
                out.append(d[i])
                i += 1
                continue
            match = NUMBER.match(d, i)
            if not match:
                raise ValueError(f"bad number in path data at {i}")
            out.append(match.group())
            i = match.end()
        # A repeated M continues as L.
        if command == "M":
            command = "L"
        elif command == "m":
            command = "l"
    return " ".join(out)


def tag(element):
    return element.tag.split("}")[-1]


def is_background(element, box):
    """A rect or path that paints the whole viewBox, square or rounded: the
    plate behind a light glyph. Dropped so the glyph stands on its own."""
    if element.get("fill") == "none":
        return False
    if tag(element) == "rect":
        at_origin = element.get("x", "0") in ("0", "0.0") and element.get("y", "0") in ("0", "0.0")
        return at_origin and (element.get("width"), element.get("height")) == (box[2], box[3])
    if tag(element) == "path":
        d = re.sub(r"\s+", "", element.get("d", ""))
        w, h = box[2], box[3]
        return d in {f"M{w}0H0V{h}H{w}V0Z", f"M00H{w}V{h}H0Z", f"M0,0H{w}V{h}H0Z"}
    return False


def normalise(text):
    if UNSAFE.search(text):
        raise ValueError("unsafe content")
    root = ET.fromstring(text)
    if tag(root) != "svg":
        raise ValueError("not an svg")
    if not root.get("viewBox"):
        width, height = (re.sub(r"px$", "", root.get(k, "")) for k in ("width", "height"))
        if not (width and height):
            raise ValueError("no viewBox and no size")
        root.set("viewBox", f"0 0 {width} {height}")
    box = [part.rstrip("0").rstrip(".") if "." in part else part
           for part in root.get("viewBox").replace(",", " ").split()]
    for attribute in ("width", "height", "style", "class"):
        root.attrib.pop(attribute, None)
    # A <style> block is dropped below; if it was what painted the shapes
    # over a root fill of none, paint them black instead.
    styled = any(tag(e) == "style" and "fill" in (e.text or "") for e in root.iter())
    if root.get("fill") not in (None, "none") or (styled and root.get("fill") == "none"):
        root.set("fill", "#000")

    def walk(parent):
        for child in list(parent):
            name = tag(child)
            if name in ("title", "desc", "metadata", "style"):
                parent.remove(child)
                continue
            if parent is root and is_background(child, box):
                parent.remove(child)
                continue
            for paint in PAINTS:
                value = child.get(paint)
                if value and value != "none" and not value.startswith("url("):
                    child.set(paint, "#000")
            child.attrib.pop("style", None)
            child.attrib.pop("class", None)
            if name == "path" and child.get("d"):
                child.set("d", spell_out(child.get("d")))
            walk(child)

    walk(root)
    out = ET.tostring(root, encoding="unicode")
    return re.sub(r"<!--.*?-->", "", out, flags=re.S)


def main(sources):
    rows = list(csv.DictReader(open(sources), delimiter="\t"))
    base = Path(sources).parent
    kept = []
    for row in rows:
        path = Path(row["file"])
        path = path if path.is_absolute() else base / path
        try:
            svg = normalise(path.read_text())
        except (ValueError, ET.ParseError, OSError) as error:
            print(f"skip {row['id']}: {error}", file=sys.stderr)
            continue
        (OUT / f"harness-{row['id']}.svg").write_text(svg + "\n")
        kept.append(row)
        print(f"wrote harness-{row['id']}.svg")
    with open(OUT / "harness-icons.tsv", "w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["id", "source_url", "licence", "notes"])
        for row in sorted(kept, key=lambda r: r["id"]):
            writer.writerow([row["id"], row["source_url"], row["licence"], row.get("notes", "")])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
