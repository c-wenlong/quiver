"""The Skills page: ``swe skills --json`` built in process, less the skills
that ship inside a plugin (the Plugins page lists those), and one skill's
detail: its frontmatter, its SKILL.md and its folder's files.

Read only. Linking, unlinking, New skill and frontmatter edits stay in
``swe skills`` and the Mac app; see docs/web-app/skills.md.

``GET /v1/app/skills`` is the list. ``?skill=<path>`` is one skill's
detail, and ``?skill=<path>&file=<name>`` one file inside its folder. The
skill must be one the inventory lists, and the file must stay inside the
skill's folder once symlinks resolve, so no request reads anything else.
"""

from __future__ import annotations

import os
from pathlib import Path

from quiver.webapp.pages import PageError

# PrimitiveTree.isTextFile: under a megabyte, and UTF-8 with no NUL in
# its first 8 KB.
MAX_TEXT = 1_000_000
_HEAD = 8192
# PrimitiveTree's section folders, in the order the detail lists them.
_KINDS = (("skills", "Skills"), ("commands", "Commands"), ("agents", "Agents"), ("hooks", "Hooks"))


def get(ctx, args: dict) -> dict:
    home = Path(ctx.home) if ctx.home is not None else Path.home()
    inventory = _inventory(home)
    if args.get("skill"):
        skill = _find(inventory, args["skill"])
        if args.get("file"):
            return file_view(skill, args["file"])
        return {**detail(skill, home), "machine": ctx.machine.get("name"),
                "shared_dir": inventory["shared_dir"], "harnesses": inventory["harnesses"]}
    standalone, in_plugins, catalog_error = placement(inventory["skills"], home)
    return {
        "machine": ctx.machine.get("name"),
        "home": str(home),
        "shared_dir": inventory["shared_dir"],
        "harnesses": inventory["harnesses"],
        "skills": standalone,
        "plugin_skill_count": in_plugins,
        "catalog_error": catalog_error,
    }


def _inventory(home: Path) -> dict:
    from quiver.skills.inventory import installed_skills

    # The Mac app's swe runs from "/", so no project's .cursor/skills is
    # read; the hub does the same whatever folder it was started in.
    return installed_skills(home=home, cwd=Path(os.sep))


def _find(inventory: dict, path: str) -> dict:
    for skill in inventory["skills"]:
        if skill["path"] == path:
            return skill
    raise PageError(404, "unknown_skill", "No skill at that path on the hub.")


def placement(skills: list[dict], home: Path) -> tuple[list[dict], int, str | None]:
    """SkillPlacement: the skills the page lists, how many moved to the
    Plugins page, and why the catalog was unreadable (every skill then
    stays here, so none is lost).

    A skill moves only when its source is ``plugin`` and exactly one
    catalog plugin owns the deepest folder holding it, as
    ``quiver.plugins.catalog.owners`` decides.
    """
    try:
        from quiver.plugins.catalog import build_catalog, owners, plugin_dirs

        dirs = plugin_dirs(build_catalog(home))
    except Exception as exc:  # the Mac app's CatalogLookup.unavailable
        return list(skills), 0, str(exc) or exc.__class__.__name__
    standalone = [
        s for s in skills
        if not (s["source"] == "plugin" and len(owners(dirs, s["path"])) == 1)
    ]
    return standalone, len(skills) - len(standalone), None


# -- one skill ---------------------------------------------------------------


def _real(path: Path | str) -> str:
    return os.path.realpath(path)


def _inside(path: Path | str, root: Path | str) -> bool:
    """PrimitiveOwnership.isInside: ``path`` stays under ``root`` once
    symlinks resolve on both."""
    inside = _real(root).rstrip(os.sep)
    return _real(path).startswith(inside + os.sep)


def editable(skill_path: str, home: Path) -> bool:
    """PrimitiveOwnership.isEditable(skillPath:): under ~/.quiver after
    symlinks resolve, and not in its cache. The web app edits nothing; this
    only picks the lock note's words."""
    quiver = _real(home / ".quiver")
    resolved = _real(skill_path)
    return resolved.startswith(quiver + os.sep) and not resolved.startswith(quiver + os.sep + "cache" + os.sep)


def frontmatter(text: str) -> list[dict] | None:
    """MarkdownFrontmatter's fields: each ``key: value`` line between the
    opening and closing ``---`` with nothing indented under it; None when
    the file has no frontmatter."""
    lines = text.split("\n")
    if not text.startswith("---") or len(lines) < 2 or lines[0].strip() != "---":
        return None
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    inside = lines[1:close]
    fields = []
    for i, line in enumerate(inside):
        key, colon, value = line.partition(":")
        if not colon or not key or not all(ch.isalnum() or ch in "-_" for ch in key):
            continue
        following = inside[i + 1] if i + 1 < len(inside) else ""
        if following[:1] in (" ", "\t"):
            continue
        if value.startswith(" "):
            value = value[1:]
        fields.append({"key": key, "value": value.strip(" \t")})
    return fields


def read_text(path: Path) -> str | None:
    """The file's text when it can preview (PrimitiveTree.isTextFile), else
    None. Undecodable bytes past the first 8 KB read as replacement marks."""
    try:
        if not path.is_file() or path.stat().st_size > MAX_TEXT:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    head = data[:_HEAD]
    if b"\0" in head:
        return None
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A character cut at the 8 KB mark is not a binary file.
        if len(data) <= _HEAD or exc.start < len(head) - 3:
            return None
    return data.decode("utf-8", errors="replace")


def _entries(folder: Path, prefix: str = "", depth: int = 0, limit: int = 6, skip=()) -> list[dict]:
    """Every file under ``folder``, names relative to it, dotfiles skipped
    at every level; ``skip`` names top-level folders left to a section."""
    if depth > limit:
        return []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        if name.startswith(".") or (depth == 0 and name in skip):
            continue
        item = folder / name
        if item.is_dir():
            out += _entries(item, prefix + name + "/", depth + 1, limit)
        else:
            out.append({"name": prefix + name, "folder": False})
    return out


def tree(root: Path) -> list[dict]:
    """PrimitiveTree: files under Skills, Commands, Agents, Hooks, then
    Other files, each name relative to ``root``. A ``skills/<name>`` folder
    is one entry that opens its SKILL.md, with its files as children."""
    sections = []
    for kind, title in _KINDS:
        folder = root / kind
        if not folder.is_dir():
            continue
        entries = []
        try:
            top = sorted(os.listdir(folder))
        except OSError:
            top = []
        for name in top:
            if name.startswith("."):
                continue
            item = folder / name
            if kind == "skills" and item.is_dir():
                entries.append({
                    "name": name, "folder": True, "path": f"{kind}/{name}",
                    "preview": f"{kind}/{name}/SKILL.md",
                    "children": [{"name": e["name"], "path": f"{kind}/{name}/{e['name']}"}
                                 for e in _entries(item)],
                })
            elif item.is_dir():
                try:
                    inner = sorted(os.listdir(item))
                except OSError:
                    inner = []
                for leaf in inner:
                    if not leaf.startswith(".") and not (item / leaf).is_dir():
                        rel = f"{name}/{leaf}"
                        entries.append({"name": rel, "folder": False, "path": f"{kind}/{rel}"})
            else:
                entries.append({"name": name, "folder": False, "path": f"{kind}/{name}"})
        if entries:
            sections.append({"kind": kind, "title": title, "entries": entries})
    other = [{"name": e["name"], "folder": False, "path": e["name"]}
             for e in _entries(root, limit=4, skip={k for k, _ in _KINDS})]
    if other:
        sections.append({"kind": "other", "title": "Other files", "entries": other})
    return sections


def detail(skill: dict, home: Path) -> dict:
    root = Path(skill["path"])
    skill_md = root / "SKILL.md"
    text = read_text(skill_md) if _inside(skill_md, root) else None
    return {
        "skill": skill,
        "home": str(home),
        "editable": editable(skill["path"], home),
        "skill_file": str(skill_md) if skill_md.is_file() else None,
        "skill_md": text,
        "frontmatter": frontmatter(text) if text is not None else None,
        "tree": tree(root),
    }


def file_view(skill: dict, name: str) -> dict:
    """One file of the skill's folder, by its name relative to the folder.
    ``text`` is None when the file can't preview: binary, over a megabyte,
    or resolving outside the folder."""
    root = Path(skill["path"])
    if not name or name.startswith("/") or "\0" in name:
        raise PageError(400, "bad_file", "Give a file name inside the skill's folder.")
    target = root / name
    outside = not _inside(target, root)
    if not outside and not target.is_file():
        raise PageError(404, "unknown_file", "No such file in the skill's folder.")
    return {
        "name": name,
        "path": str(target),
        "outside": outside,
        "text": None if outside else read_text(target),
    }
