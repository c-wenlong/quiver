"""Skills CLI commands."""

from pathlib import Path

from quiver.console import c, cpad, truncate
from quiver.skills.catalog_commands import cmd_skills_catalog, cmd_skills_discover
from quiver.skills.discovery import discover_skills, skill_roots
from quiver.skills.help_text import cmd_skills_help, print_skills_overview
from quiver.skills.layout import enumerate_skill_roots
from quiver.skills.layout_commands import (
    cmd_skills_link,
    cmd_skills_move,
    cmd_skills_tree,
    cmd_skills_unlink,
)
from quiver.table import Table


def cmd_skills_scopes(args):
    if args and args[0] in ("-h", "--help", "help"):
        from quiver.skills.help_text import print_skills_scope_help

        print_skills_scope_help()
        return 0

    skills = discover_skills()
    counts: dict[str, int] = {}
    for skill in skills:
        counts[skill["scope"]] = counts.get(skill["scope"], 0) + 1

    home = Path.home()
    home_str = str(home)
    entries = enumerate_skill_roots(home=home)

    print(f"\n{c('bold', 'Skill Scopes')}\n")

    # Four-column SCOPE | KIND | SKILLS | PATH table. SCOPE / KIND /
    # SKILLS ship pre-coloured ANSI strings, so they use
    # ``kind="preformatted"``+``trust_cell_width=True`` and are routed
    # through ``cpad`` so each cell visibly matches its column width.
    # PATH also carries a dim-coloured arrow suffix (`  → tgt`) when
    # the entry is a symlink or alias, so it uses
    # ``kind="preformatted"`` — ``kind="text"`` would strip the ANSI
    # escapes from the dim arrow (``table._text`` explicitly strips
    # ANSI before measuring). The dash-separator measures via
    # ``visible_len`` which understands ANSI escape sequences, so the
    # separator still spans row-to-row even with painted arrows. PATH
    # is the rightmost column so the missing auto-pad in preformatted
    # cells does not misalign subsequent columns.
    table = Table()
    table.add_column(
        "scope", "SCOPE", width=16,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "kind", "KIND", width=10,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "skills", "SKILLS", width=8,
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "path", "PATH", width=30,
        kind="preformatted", fit="content",
    )

    for entry in entries:
        if not entry.exists:
            continue
        rp = str(entry.path).replace(home_str, "~")
        n = entry.skill_count if entry.skill_count else counts.get(entry.label, 0)

        if entry.kind == "symlink":
            kind_cell = cpad("yellow", "symlink", 10)
            tgt = entry.link_target_label or (
                str(entry.link_target).replace(home_str, "~") if entry.link_target else "?"
            )
            link_note = c("dim", f"  → {tgt}")
        elif entry.canonical_label and entry.canonical_label != entry.label:
            kind_cell = cpad("dim", "alias", 10)
            link_note = c("dim", f"  → {entry.canonical_label}")
        else:
            kind_cell = cpad("green", entry.kind, 10)
            link_note = ""

        skills_cell = (
            c("green", f"{n:>8}") if n > 0 else c("dim", f"{0:>8}")
        )

        path_cell = rp + (link_note if link_note else "")
        table.add_row({
            "scope": cpad("cyan", entry.label, 16),
            "kind": kind_cell,
            "skills": skills_cell,
            "path": path_cell,
        })

    for line in table.render():
        print(line)
    print()
    print(
        c(
            "dim",
            f"  {sum(1 for e in entries if e.exists)} roots  ·  {len(skills)} unique skills"
            f"  ·  swe skills tree  │  swe skills help scope",
        )
    )
    print()


def cmd_skills(args):
    if not args or args[0] in ("-h", "--help"):
        print_skills_overview()
        return 0
    if args[0] == "help":
        return cmd_skills_help(args[1:])
    if args[0] in ("discover",):
        return cmd_skills_discover(args[1:])
    if args[0] in ("catalog", "catalogs"):
        return cmd_skills_catalog(args[1:])
    if args[0] in ("scope", "scopes"):
        return cmd_skills_scopes(args[1:])
    if args[0] == "tree":
        return cmd_skills_tree(args[1:])
    if args[0] == "link":
        return cmd_skills_link(args[1:])
    if args[0] == "unlink":
        return cmd_skills_unlink(args[1:])
    if args[0] == "move":
        return cmd_skills_move(args[1:])

    show_desc = False
    filt = None
    for arg in args:
        if arg in ("-d", "--desc"):
            show_desc = True
        elif arg in ("list", "ls"):
            continue
        elif not arg.startswith("-"):
            filt = arg.lower()

    skills = discover_skills()
    if filt:
        skills = [
            s
            for s in skills
            if filt in s["name"].lower()
            or filt in s["scope"].lower()
            or any(filt in v.lower() for v in s.get("visible_via", []))
        ]

    if not skills:
        print(c("dim", "\n  No skills found.\n"))
        print(c("dim", "  Try: swe skills discover  │  swe skills catalog .  │  swe skills help\n"))
        return 0

    skills.sort(key=lambda s: (s["scope"], s["name"].lower()))

    print(f"\n{c('bold', 'Agent Skills')}\n")

    # 3-column NAME | SCOPE | VISIBLE_VIA Table. All three columns
    # ship pre-coloured ANSI strings, so they use
    # ``kind="preformatted"``+``trust_cell_width=True`` and are routed
    # through ``cpad`` so each cell visibly matches its declared
    # column width. The VISIBLE_VIA width is pre-measured — find the
    # longest comma-joined via list across the current skill set,
    # floor it at the header label width (``len("VISIBLE VIA") = 11``),
    # and pass that exact same value to ``add_column(width=...)`` AND
    # the per-row ``cpad(..., width=...)`` call. This guarantees the
    # column and cpad agree by construction, so every body row's
    # visible cell width matches the header without drift.
    #
    # PATH and DESCRIPTION are NOT columns of the table — they are
    # emitted as plain ``print()`` lines below the row, with PATH
    # aligned under VISIBLE_VIA (indented 28 + 2 + 14 + 2 = 46 spaces)
    # and DESCRIPTION indented to the same column. This restores the
    # pre-migration 2-3-line-per-skill layout rather than collapsing
    # PATH onto a rightmost-column cell.
    column_widths = {"name": 28, "scope": 14}
    # Pre-measure visible_via: longest comma-joined via chunk across
    # the current skill set, floored at the header label width. The
    # same value is then used in ``add_column(width=...)`` AND the
    # per-row ``cpad(..., width=...)`` call so column and cpad agree
    # by construction — every body row arrives at the column visible
    # width without drift.
    via_texts: list[str] = []
    via_colors: list[str] = []
    for skill in skills:
        via = skill.get("visible_via", [skill["scope"]])
        if len(via) > 1:
            via_texts.append(", ".join(via))
            via_colors.append("cyan")
        else:
            via_texts.append(via[0])
            via_colors.append("dim")
    column_widths["visible_via"] = max(
        len("VISIBLE VIA"),  # header label width
        max((len(t) for t in via_texts), default=0),
    )
    path_indent = (
        column_widths["name"]
        + 2  # default column_gap
        + column_widths["scope"]
        + 2
    )

    table = Table()
    table.add_column(
        "name", "NAME", width=column_widths["name"],
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "scope", "SCOPE", width=column_widths["scope"],
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "visible_via", "VISIBLE VIA", width=column_widths["visible_via"],
        kind="preformatted", trust_cell_width=True,
    )

    home_str = str(Path.home())
    for skill, via_text, via_color in zip(skills, via_texts, via_colors):
        name_plain = truncate(skill["name"], column_widths["name"])
        table.add_row({
            "name": cpad("bold", name_plain, column_widths["name"]),
            "scope": cpad("cyan", skill["scope"], column_widths["scope"]),
            "visible_via": cpad(
                via_color, via_text, column_widths["visible_via"],
            ),
        })

    # Render the table once, peel off the header + separator, and
    # interleave the rows 1-to-1 with the skills list so PATH and
    # DESCRIPTION can be plain ``print()`` lines between each rendered
    # row. ``render()`` always returns [header, separator, rows...],
    # so ``body_lines`` is always length-equal to ``self._rows`` (one
    # add_row per skill), and ``zip(skills, body_lines)`` walks them
    # 1-to-1.
    rendered = table.render()
    print(rendered[0])  # dim header line
    print(rendered[1])  # dashed separator
    body_lines = rendered[2:]
    for skill, row_line in zip(skills, body_lines):
        print(row_line)
        path_display = skill["path"].replace(home_str, "~")
        print(c("dim", " " * path_indent + path_display))
        if show_desc and skill.get("description"):
            print(
                c(
                    "dim",
                    " " * path_indent
                    + truncate(skill["description"], 80),
                )
            )
        print()
    print()
    n_scopes = len({s["scope"] for s in skills})
    print(
        c(
            "dim",
            f"  {len(skills)} skills across {n_scopes} canonical scopes"
            f"  ·  swe skills tree  │  swe skills help",
        )
    )
    print()
