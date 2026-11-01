"""`swe skills new`: write a SKILL.md skeleton into the hub."""

import os
import re
import shutil
import tempfile
from pathlib import Path

from swe import paths

# Agent Skills spec: lowercase letters, digits and single hyphens.
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_MAX_NAME = 64
_MAX_DESCRIPTION = 1024

_TEMPLATE = """\
---
name: {quoted_name}
description: {description}
---

# {name}

Write the instructions here.
"""

_DEFAULT_DESCRIPTION = "Describe when an agent should use this skill."


def validate_name(name: str) -> str | None:
    """None when the name is a valid skill name, else the reason."""
    if len(name) > _MAX_NAME:
        return f"{name!r} is over {_MAX_NAME} characters"
    if not _NAME_RE.fullmatch(name):
        return (f"{name!r} isn't a valid skill name: lowercase letters, "
                "digits and single hyphens between them")
    return None


def description_line(description: str) -> str:
    """The text as one YAML string scalar.

    Harnesses parse frontmatter as real YAML, where a bare ``123``,
    ``true``, ``null`` or ``~`` is not text, so every value goes in single
    quotes, with ``'`` doubled as YAML wants; discovery's reader undoes
    exactly that.
    """
    return "'" + description.replace("'", "''") + "'"


def create(name: str, description: str | None = None,
           home: Path | None = None) -> tuple[Path, bool]:
    """Write ~/.quiver/skills/<name>/SKILL.md, atomically.

    Returns (path, created_dir). Raises ValueError for anything the user
    can fix; the command turns it into exit 1.
    """
    if problem := validate_name(name):
        raise ValueError(problem)
    if description is not None:
        if "\n" in description or "\r" in description:
            raise ValueError("the description must be one line")
        if len(description) > _MAX_DESCRIPTION:
            raise ValueError(
                f"the description is over {_MAX_DESCRIPTION} characters")
    text = _TEMPLATE.format(
        name=name,
        quoted_name=description_line(name),
        description=description_line(description or _DEFAULT_DESCRIPTION),
    )

    skills = paths.skills_dir_for(home)
    if not skills.is_dir():
        swe_dir = skills.parent
        if not swe_dir.is_dir():
            raise ValueError(
                f"{swe_dir} doesn't exist — run swe init or "
                "swe setup --apply first")
        skills.mkdir()
    folder = skills / name
    if folder.exists() or folder.is_symlink():
        raise ValueError(f"{name} already exists at {folder}")
    folder.mkdir()

    target = folder / "SKILL.md"
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".SKILL.md.")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, target)
    except BaseException:
        # Leave nothing behind, or a retry would find the name taken.
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return target, False
