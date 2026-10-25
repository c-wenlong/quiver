"""The join ``swe hooks`` renders: hub scripts x link state x declarations.

``plan_hooks`` says where each ``~/.quiver/hooks/<harness>/<script>`` should
land and what state the link is in; ``settings.py`` says which of those
scripts the harness will actually run. One row per script x destination.

The drift checks read the same join the other way: declarations that point
at nothing on disk, hub scripts linked but never declared, hub scripts the
destination never received. Hooks owned by other installers (herdr writes
into ``~/.claude/hooks``) are not drift — they live at the destination
without a hub copy, which is exactly the shape ``init/hooks.py``'s
docstring describes — so a finding needs the script to be missing from
*both* the hub and the destination, or unverifiable paths to stay silent.

Nothing in this module prints.
"""

from __future__ import annotations

import ast
from pathlib import Path

from quiver import paths as _paths
from quiver.init.hooks import HOOK_FALLBACK, hook_root, plan_hooks
from quiver.init.layout import LinkStatus, load_registry
from quiver.hooks.models import Declaration, HookDrift, HookRow
from quiver.hooks.settings import (
    SETTINGS_FALLBACK,
    expand_command_path,
    harness_declarations,
    is_script_token,
    split_command,
)


def collect(
    home: Path | None = None,
    registry: dict | None = None,
    patterns: list[str] | None = None,
) -> tuple[list[LinkStatus], dict]:
    """(plan statuses, registry) — the two reads every view shares."""
    home = home or Path.home()
    if registry is None:
        registry = load_registry(home)
    return plan_hooks(home, patterns=patterns, registry=registry), registry


def _harness_names(root: Path, registry: dict) -> list[str]:
    """Every harness a settings file could belong to.

    Hub dirs plus the fallback tables plus registered names: a harness with
    no hooks root and no hub dir (codex today) still declares hooks worth
    drift-checking.
    """
    names = set(registry) | set(HOOK_FALLBACK) | set(SETTINGS_FALLBACK)
    if root.is_dir():
        names |= {
            d.name for d in root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        }
    return sorted(names)


def declarations_by_harness(
    home: Path, registry: dict
) -> dict[str, list[Declaration]]:
    root = _paths.hooks_dir_for(home)
    return {
        name: harness_declarations(registry, name, home)
        for name in _harness_names(root, registry)
    }


def _decl_matches(decl: Declaration, script: str) -> bool:
    """The command invokes ``script``.

    Basename for file hooks; a directory hook is invoked through paths
    inside it (``hooks/bundle/guard.py``), so its own name matches as a
    path segment too.
    """
    for raw in split_command(decl.command):
        token = raw.strip("'\"").rstrip(";")
        base = token.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if base == script:
            return True
        if f"/{script}/" in token or token.startswith(f"{script}/"):
            return True
    return False


def hook_rows(
    statuses: list[LinkStatus],
    declared: dict[str, list[Declaration]],
) -> list[HookRow]:
    """One row per plan status, with the declarations naming its basename.

    The match is by basename, deliberately: settings spell the command as
    ``python3 "$HOME/.claude/hooks/guard.py"`` or a bare ``guard.py``, and
    both mean the script's own name.
    """
    rows: list[HookRow] = []
    for status in statuses:
        source = status.source or status.path
        decls = [
            d for d in declared.get(status.label, [])
            if _decl_matches(d, source.name)
        ]
        rows.append(HookRow(
            script=source.name,
            harness=status.label,
            source=source,
            dest=status.path,
            state=status.state,
            detail=status.detail,
            declared=decls,
        ))
    return rows


def find_script(name: str, rows: list[HookRow]) -> list[HookRow]:
    """Rows for ``name``: exact filename first, then every stem match.

    ``edit prose-guard`` finds prose-guard.py; ``edit guard`` finds both
    guard.py and guard.sh so the caller can ask which harness is meant
    rather than silently picking the .py.
    """
    exact = [r for r in rows if r.script == name]
    if exact:
        return exact
    return [r for r in rows if Path(r.script).stem == name]


def script_summary(path: Path, limit: int = 2) -> str:
    """The script's own description: docstring or leading comment lines.

    A hook carries no metadata, so its contract lives in its header —
    prose-guard.py's docstring states the exit-0/2 contract and the
    fail-open rule. ``limit`` lines, stopping at the first blank.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines: list[str] = []
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text))
        except (SyntaxError, ValueError, RecursionError):
            doc = None
        if doc:
            for line in doc.splitlines():
                if not line.strip():
                    break
                lines.append(line.strip())
    else:
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("#!"):
                continue
            if not line.startswith("#"):
                break
            content = line.lstrip("#").strip()
            if not content:
                break
            lines.append(content)
        if len(lines) >= limit:
            lines = lines[:limit]
    return " ".join(lines[:limit])


# --- drift findings --------------------------------------------------------


def _token_verdict(token: str, home: Path, candidates: list[Path]) -> str:
    """"resolved" | "missing" | "unverifiable" for one script-looking token."""
    bare = token.strip("'\"").rstrip(";")
    base = bare.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "/" not in bare and "\\" not in bare:
        # A bare basename: the hooks root, the hub dir and the settings
        # file's own hooks/ sibling are the homes it could mean.
        if any((d / base).exists() for d in candidates):
            return "resolved"
        return "missing"
    path = expand_command_path(bare, home)
    if path is None:
        return "unverifiable"
    if path.exists():
        return "resolved"
    # The token names a path inside a hooks dir swe knows about (the root,
    # the hub, the settings file's sibling hooks/): a missing file there is
    # the not-yet-linked case when the same basename exists in any of them.
    # Outside those dirs a dead literal path is simply missing — a
    # same-named file under ~/.quiver/hooks is not the script it means.
    if path.parent in candidates and any(
            (d / base).exists() for d in candidates):
        return "resolved"
    return "missing"


def _declared_missing(
    home: Path,
    registry: dict,
    statuses: list[LinkStatus],
    declared: dict[str, list[Declaration]],
) -> list[HookDrift]:
    """(a) A declaration whose script exists nowhere swe can find it.

    One finding per (harness, basename): a script invoked from six events
    is still one missing script. Installer-owned scripts live only at the
    destination, so a basename counts as present when it exists in *any*
    candidate dir — only pointing at nothing at all is drift.
    """
    root_dir = _paths.hooks_dir_for(home)
    dest_roots = {s.label: s.path.parent for s in statuses}
    # A directory hook is invoked through paths inside it, so its hub and
    # destination dirs are candidate homes for those child paths too.
    dir_homes = [p for s in statuses
                 if s.source is not None and s.source.is_dir()
                 for p in (s.source, s.path)]
    out: list[HookDrift] = []
    for name, decls in declared.items():
        root, _why = hook_root(registry, name, home)
        candidates = ([d for d in (root, dest_roots.get(name),
                                 root_dir / name)
                       if d is not None] + dir_homes)
        # basename -> the event labels declaring it, for one merged finding
        missing: dict[str, set[str]] = {}
        for decl in decls:
            toks = split_command(decl.command)
            homes = candidates + [decl.settings.parent / "hooks"]
            tokens = [t for t in toks if is_script_token(t)]
            if not tokens:
                continue
            # `[ -f p.sh ] && p.sh` handles its own absence: a tested path
            # being missing is the command working as written, not drift.
            guarded_bases: set[str] = set()
            guarded_paths: set[Path] = set()
            for i, t in enumerate(toks[:-1]):
                if t in ("-f", "-x"):
                    probe = toks[i + 1].strip("'\"").rstrip(";")
                    guarded_bases.add(probe.rsplit("/", 1)[-1])
                    expanded = expand_command_path(probe, home)
                    if expanded is not None:
                        guarded_paths.add(expanded)
            for token in tokens:
                if _token_verdict(token, home, homes) != "missing":
                    continue
                bare = token.strip("'\"").rstrip(";")
                base = bare.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                expanded = expand_command_path(bare, home)
                if base in guarded_bases or (
                        expanded is not None and expanded in guarded_paths):
                    continue
                missing.setdefault(base, set()).add(decl.label())
        for base, labels in sorted(missing.items()):
            where = ", ".join(sorted(labels))
            out.append(HookDrift(
                "declared-missing", name,
                f"declared {base} ({where}) but no such script is on disk",
            ))
    return out


def _undeclared(rows: list[HookRow]) -> list[HookDrift]:
    """(b) A hub script linked into place that nothing ever invokes."""
    return [
        HookDrift(
            "undeclared", row.harness,
            f"{row.script} is linked into {row.dest.parent} but no settings "
            f"file declares it — the hook will not fire",
        )
        for row in rows
        if row.state == "linked" and not row.declared
    ]


def _unlinked(rows: list[HookRow]) -> list[HookDrift]:
    """(c) A hub script the destination never received (or cannot receive).

    ``skipped`` and ``ignored`` stay quiet: a harness that is not installed
    and a path in .linkignore are deliberate, not drift.
    """
    out: list[HookDrift] = []
    for row in rows:
        if row.state == "create":
            out.append(HookDrift(
                "unlinked", row.harness,
                f"{row.script} is not linked into {row.dest.parent} "
                f"(swe init will link it)",
            ))
        elif row.state == "relink":
            out.append(HookDrift(
                "unlinked", row.harness,
                f"{row.script}'s link points elsewhere ({row.detail})",
            ))
        elif row.state == "absorb":
            out.append(HookDrift(
                "unlinked", row.harness,
                f"{row.script} sits at {row.dest.parent} as an identical "
                f"copy, not a link (swe init will absorb it)",
            ))
        elif row.state == "conflict":
            out.append(HookDrift(
                "unlinked", row.harness,
                f"{row.script} is blocked by a real file at {row.dest} "
                f"(swe init --force replaces it)",
            ))
    return out


def hook_findings(
    home: Path | None = None,
    registry: dict | None = None,
    patterns: list[str] | None = None,
) -> list[HookDrift]:
    """The three ``swe hooks check`` findings, in (a), (b), (c) order."""
    home = home or Path.home()
    statuses, registry = collect(home, registry, patterns)
    declared = declarations_by_harness(home, registry)
    rows = hook_rows(statuses, declared)
    return (
        _declared_missing(home, registry, statuses, declared)
        + _undeclared(rows)
        + _unlinked(rows)
    )
