#!/usr/bin/env python3
"""Normalise harness logos into SWECore's resources.

    app/mac/script/import-harness-icons.py SOURCES.tsv

SOURCES.tsv has columns: id, kind, file, source_url, licence, notes (a
header row first). `file` is a path to an SVG; `kind` is `color` or `mono`.

- `color` logos keep their own colours, as TokenTracker shows them, and are
  written to `harness-<id>-color.svg`.
- `mono` logos become single-colour marks, written to `harness-<id>.svg`:
  every paint turns black, and a full-bleed background plate is dropped so
  the glyph stands alone. The app draws them as template images in the
  text colour.

Either way titles, comments, root styles and fixed sizes are dropped and
path data is spelled out in full, because CoreSVG silently draws nothing
for some compact forms. An SVG with scripts, event handlers, external
references, embedded images or foreignObject is refused.

Every row is checked before anything is written, so a bad source leaves
the resources as they were. The output is exactly the rows given: icons
not listed are removed, and the provenance table is rewritten beside them
as harness-icons.tsv (summarised in app/mac/THIRD_PARTY_NOTICES.md by hand).
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)
OUT = Path(__file__).resolve().parent.parent / "Sources" / "SWECore" / "Resources"
# Internal references (href="#gradient") are fine; anything else is not.
UNSAFE = re.compile(r"<script|\son[a-z]+\s*=|href\s*=\s*[\"'](?!#)|<foreignObject|<image|url\((?!\s*#)", re.I)
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


def drop_box_subpath(d, box):
    """Path data without a subpath that just traces the whole viewBox, when
    other subpaths remain: the plate a light glyph was cut out of."""
    tokens = d.split()
    starts = [i for i, t in enumerate(tokens) if t in ("M", "m")]
    if len(starts) < 2:
        return d
    w, h = float(box[2]), float(box[3])
    corners = {(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)}
    kept = []
    dropped_start = None  # where the pen is after a dropped box closes
    for n, i in enumerate(starts):
        sub = tokens[i:starts[n + 1] if n + 1 < len(starts) else len(tokens)]
        points, x, y, k, absolute = set(), 0.0, 0.0, 0, True
        start = None
        try:
            while k < len(sub):
                command = sub[k]
                if command in "MLHVZ" or command in "mlhvz":
                    absolute = command.isupper()
                    k += 1
                if command.upper() in "ML":
                    nx, ny = float(sub[k]), float(sub[k + 1])
                    x, y = (nx, ny) if absolute else (x + nx, y + ny)
                    start = start or (x, y)
                    k += 2
                elif command.upper() == "H":
                    x = float(sub[k]) if absolute else x + float(sub[k])
                    k += 1
                elif command.upper() == "V":
                    y = float(sub[k]) if absolute else y + float(sub[k])
                    k += 1
                elif command.upper() == "Z":
                    continue
                else:
                    raise ValueError
                points.add((x, y))
        except (ValueError, IndexError):
            points = set()
        if points == corners:
            # A box closes back at its first point, which is where a
            # following relative move is measured from.
            dropped_start = start
            continue
        if dropped_start and sub[0] == "m":
            rest = sub[3:]
            # Pairs straight after a relative move are relative lines; an
            # absolute M would make them absolute, so name them `l`.
            if rest and not rest[0].isalpha():
                rest = ["l"] + rest
            sub = ["M", fmt(dropped_start[0] + float(sub[1])), fmt(dropped_start[1] + float(sub[2]))] + rest
        dropped_start = None
        kept.append(" ".join(sub))
    return " ".join(kept) if kept else d


def fmt(value):
    return f"{value:g}"


def normalise(text, mono):
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
    if mono:
        # A <style> block is dropped below; if it was what painted the
        # shapes over a root fill of none, paint them black instead.
        styled = any(tag(e) == "style" and "fill" in (e.text or "") for e in root.iter())
        if root.get("fill") not in (None, "none") or (styled and root.get("fill") == "none"):
            root.set("fill", "#000")

    def walk(parent):
        for child in list(parent):
            name = tag(child)
            if name in ("title", "desc", "metadata") or (mono and name == "style"):
                parent.remove(child)
                continue
            if mono and parent is root and is_background(child, box):
                parent.remove(child)
                continue
            if mono:
                # Gradients included: a mono mark has one paint.
                for paint in PAINTS:
                    value = child.get(paint)
                    if value and value != "none":
                        child.set(paint, "#000")
                child.attrib.pop("style", None)
            child.attrib.pop("class", None)
            if name == "path" and child.get("d"):
                d = spell_out(child.get("d"))
                child.set("d", drop_box_subpath(d, box) if mono else d)
            walk(child)

    walk(root)
    out = ET.tostring(root, encoding="unicode")
    return re.sub(r"<!--.*?-->", "", out, flags=re.S)


REQUIRED = ("id", "kind", "file", "source_url", "licence")


def main(sources):
    with open(sources) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        rows = list(reader)
    if missing:
        sys.exit(f"refusing to import: {sources} has no {', '.join(missing)} column")
    base = Path(sources).parent
    outputs, problems = {}, []
    for row in rows:
        # The id becomes a file name under OUT: no separators, no "..".
        if not re.fullmatch(r"[a-z0-9-]+", row["id"] or ""):
            problems.append(f"{row['id']!r}: id must be lowercase letters, digits and dashes")
            continue
        kind = row["kind"]
        if kind not in ("mono", "color"):
            problems.append(f"{row['id']}: kind must be mono or color, not {kind!r}")
            continue
        path = Path(row["file"])
        path = path if path.is_absolute() else base / path
        try:
            svg = normalise(path.read_text(), mono=kind == "mono")
        except (ValueError, ET.ParseError, OSError) as error:
            problems.append(f"{row['id']}: {error}")
            continue
        name = f"harness-{row['id']}{'-color' if kind == 'color' else ''}.svg"
        if any(n.startswith(f"harness-{row['id']}.") or n.startswith(f"harness-{row['id']}-color.") for n in outputs):
            problems.append(f"{row['id']}: listed twice")
        outputs[name] = svg
    if problems:
        # Nothing is written, so icons and their provenance stay in step.
        sys.exit("refusing to import:\n  " + "\n  ".join(problems))
    provenance = [[row["id"], row["kind"], row["source_url"], row["licence"], row.get("notes") or ""]
                  for row in sorted(rows, key=lambda r: r["id"])]
    # Everything is checked and built; only now is anything changed.
    for stale in OUT.glob("harness-*.svg"):
        if stale.name not in outputs:
            stale.unlink()
    for name, svg in outputs.items():
        (OUT / name).write_text(svg + "\n")
        print(f"wrote {name}")
    with open(OUT / "harness-icons.tsv", "w", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(["id", "kind", "source_url", "licence", "notes"])
        writer.writerows(provenance)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
