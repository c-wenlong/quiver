"""The shape `swe find --interactive` browses.

A resource type answers one question: what are the top-level things a
reader would want to open? Everything below that is ordinary filesystem
navigation, so the browser walks real directories rather than a tree each
command has to invent. Only the roots differ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Directories that are never worth showing. Plugin caches keep lock and
# marker directories next to the real content, and showing them buries
# the skills someone opened the browser to find.
HIDE_DIRS = frozenset({
    ".git", ".in_use", "__pycache__", "node_modules", ".DS_Store",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
})

# Files worth rendering in the preview pane. Anything else is reported by
# name and size rather than dumped as bytes.
TEXT_SUFFIXES = frozenset({
    ".md", ".markdown", ".txt", ".json", ".toml", ".yaml", ".yml",
    ".py", ".ts", ".js", ".sh", ".rst", ".cfg", ".ini", "",
})


@dataclass
class Entry:
    """One row in the browser.

    ``path`` is None for a grouping row (a marketplace, say) that exists
    only to organise the level below it. Such a row still descends, into
    ``children``, so grouping and real directories navigate alike.
    """

    label: str
    path: Path | None = None
    detail: str = ""
    children: list["Entry"] = field(default_factory=list)

    @property
    def is_dir(self) -> bool:
        if self.path is None:
            return bool(self.children)
        try:
            return self.path.is_dir()
        except OSError:
            return False

    @property
    def can_descend(self) -> bool:
        return self.is_dir or bool(self.children)
