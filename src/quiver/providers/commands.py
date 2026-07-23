"""CLI command handlers for ``swe providers``: list, info, add, remove."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from quiver.console import c, cpad, truncate
from quiver.providers.defaults import DEFAULT_PROVIDERS
from quiver.providers.discover import discover_provider_keys
from quiver.providers.help_text import print_providers_help
from quiver.providers.keys import default_keys_dir
from quiver.providers.registry import load_registry, resolve, save_registry
from quiver.table import Table


def _display_keys_dir(keys_dir: Path) -> str:
    """Render a key-dir path with ``~`` substitution when under ``$HOME``.

    Centralised so the rule can be unit-tested directly and so ``cmd_list``
    doesn't have to call ``Path.home()`` inline (which would expose the
    global Path class to the test surface).
    """
    s = str(keys_dir)
    if not s or s == ".":
        return "/"
    try:
        home = Path.home()
        h = str(home)
        if s == h or s.startswith(h + "/"):
            return "~" + s[len(h):]
        return s
    except (RuntimeError, ValueError):
        return s


def _parse_api_keys_dir(args: list[str]) -> tuple[Path | None, list[str]]:
    """Pull ``--api-keys-dir`` (space or ``=`` form) out of ``args``."""
    keys_dir: Path | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--api-keys-dir="):
            keys_dir = Path(a.split("=", 1)[1])
            args.pop(i)
        elif a == "--api-keys-dir" and i + 1 < len(args):
            keys_dir = Path(args[i + 1])
            args.pop(i)
            args.pop(i)
        else:
            i += 1
    return keys_dir, args


def cmd_list(args: list[str]) -> int:
    if args and args[0] in ("-h", "--help", "help"):
        print_providers_help()
        return 0

    api_keys_dir, args = _parse_api_keys_dir(list(args))
    show_desc = any(a in ("-d", "--desc") for a in args)
    args = [a for a in args if a not in ("-d", "--desc")]

    providers = load_registry()
    keys_dir = (api_keys_dir or default_keys_dir()).expanduser()
    rows = discover_provider_keys(providers, keys_dir)
    if args:
        filt = args[0].lower()
        rows = [
            r
            for r in rows
            if filt in r["name"].lower()
            or filt in (r["info"].get("name") or "").lower()
            or any(filt in (a or "").lower() for a in r["info"].get("aliases") or [])
            # Also match env-var names — users often look up providers by the
            # exact API_KEY var they export (e.g. ``MOONSHOT_API_KEY``).
            or any(
                filt in (e or "").lower()
                for e in r["info"].get("env_vars") or []
            )
        ]

    rows.sort(key=lambda r: (r["info"].get("name") or r["name"]).lower())

    keys_dir_display = _display_keys_dir(keys_dir)

    print(f"\n{c('bold', 'AI Providers')}\n")

    # 5-column PROVIDER | ALIASES | ENV VAR | API KEY | URL Table.
    # All five columns ship pre-coloured ANSI strings, so they use
    # ``kind="preformatted"`` + ``trust_cell_width=True`` and are
    # routed through ``cpad`` so each cell visibly matches its column
    # width. PROVIDER is capped at 24 chars so long provider names
    # truncate cleanly without breaking the otherwise-fixed left
    # columns. URL uses fit="content" + kind="preformatted" so the
    # column grows to fit the longest observed URL; since URL is the
    # rightmost column, missing auto-pad on shorter URL cells does
    # not misalign anything downstream.
    #
    # Column widths are pre-measured by walking rows once and tracking
    # parallel text (+ colour) lists, then taking
    # ``max(header_label_len, max(visible_len(cell)))`` for each
    # column. The same exact value flows to both the Table schema
    # ``add_column(width=...)`` AND the per-row ``cpad(..., width=...)``
    # call, so column and cpad agree by construction — no fit="content"
    # dead config, no width-math drift across rows.
    #
    # The description sub-line (when ``--desc`` is passed) is emitted
    # as plain ``print()`` below the rendered row, indented
    # ``HEADER_OUTER_PAD + provider_w + 2`` spaces so it visually
    # aligns under the ALIASES column header. This restores a
    # 2-line-per-provider layout similar to the cmd_skills batch-3
    # pattern.
    HEADER_OUTER_PAD = 2
    PROVIDER_CAP = 24  # visual cap on the PROVIDER column width

    provider_texts: list[str] = []
    aliases_texts: list[str] = []
    env_texts: list[str] = []
    api_key_texts: list[str] = []
    api_key_colors: list[str] = []
    url_texts: list[str] = []

    for row in rows:
        # Truncate at PROVIDER_CAP so the cell arrives at exactly the
        # capped visible width. ``cpad`` doesn't truncate by design
        # (it pads-or-leaves-as-is), so we have to bound the input
        # string before cpad for the cap to be real.
        provider_texts.append(truncate(row["name"], PROVIDER_CAP))

        aliases_plain = ", ".join(
            a for a in (row["info"].get("aliases") or []) if a != row["name"]
        ) or "—"
        aliases_texts.append(aliases_plain)

        env_list = row["info"].get("env_vars") or []
        matched_env = row.get("matched_env")
        if matched_env:
            env_plain = matched_env
        elif env_list:
            env_plain = env_list[0]
            if len(env_list) > 1:
                env_plain += f" (+{len(env_list) - 1})"
        else:
            env_plain = "—"
        env_texts.append(env_plain)

        plain_status = row["masked"]
        api_key_texts.append(plain_status)
        api_key_colors.append("dim" if plain_status == "-" else "green")

        url_short = (row["info"].get("url") or "").replace(
            "https://", ""
        ).replace("http://", "").replace("www.", "")
        url_texts.append(url_short)

    column_widths = {
        "provider": max(
            len("PROVIDER"),
            max((len(t) for t in provider_texts), default=0),
        ),
        "aliases": max(
            len("ALIASES"),
            max((len(t) for t in aliases_texts), default=0),
        ),
        "env_var": max(
            len("ENV VAR"),
            max((len(t) for t in env_texts), default=0),
        ),
        "api_key": max(
            len("API KEY"),
            max((len(t) for t in api_key_texts), default=0),
        ),
        "url": max(
            len("URL"),
            max((len(t) for t in url_texts), default=0),
        ),
    }
    # Cap PROVIDER width at 24 — long provider names truncate cleanly.
    column_widths["provider"] = min(column_widths["provider"], 24)

    table = Table()
    table.add_column(
        "provider", "PROVIDER", width=column_widths["provider"],
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "aliases", "ALIASES", width=column_widths["aliases"],
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "env_var", "ENV VAR", width=column_widths["env_var"],
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "api_key", "API KEY", width=column_widths["api_key"],
        kind="preformatted", trust_cell_width=True,
    )
    table.add_column(
        "url", "URL", width=column_widths["url"],
        kind="preformatted", fit="content",
    )

    for (
        provider_text,
        aliases_text,
        env_text,
        api_key_text,
        api_key_color,
        url_text,
    ) in zip(
        provider_texts,
        aliases_texts,
        env_texts,
        api_key_texts,
        api_key_colors,
        url_texts,
    ):
        table.add_row({
            "provider": cpad(
                "bold", provider_text, column_widths["provider"],
            ),
            "aliases": cpad(
                "dim", aliases_text, column_widths["aliases"],
            ),
            "env_var": cpad(
                "dim", env_text, column_widths["env_var"],
            ),
            "api_key": cpad(
                api_key_color, api_key_text, column_widths["api_key"],
            ),
            "url": c("cyan", url_text),
        })

    # Render once. The header + separator are emitted with the outer
    # 2-space page padding; body rows are emitted 1-to-1 with the
    # `rows` list, with description sub-lines interleaved when
    # ``--desc`` was passed.
    rendered = table.render()
    print(" " * HEADER_OUTER_PAD + rendered[0])
    print(" " * HEADER_OUTER_PAD + rendered[1])

    desc_indent = HEADER_OUTER_PAD + column_widths["provider"] + 2
    body_lines = rendered[2:]
    for row, row_line in zip(rows, body_lines):
        print(" " * HEADER_OUTER_PAD + row_line)
        if show_desc:
            desc = row["info"].get("description") or ""
            if desc:
                print(" " * desc_indent + c("dim", truncate(desc, 90)))

    print()
    n_with = sum(1 for r in rows if r["masked"] != "-")
    no_keys = rows and n_with == 0
    no_dir = not keys_dir.exists()

    if no_dir:
        print(
            c(
                "yellow",
                f"  No keys directory at {keys_dir_display}. "
                f"Create it and add a file per provider holding just the key.",
            )
        )
    if no_keys:
        print(
            c(
                "yellow",
                "  Tip: run `swe providers info <provider>` for instructions on adding a key.",
            )
        )
    if no_dir or no_keys:
        print()

    print(
        c(
            "dim",
            f"  {n_with}/{len(rows)} providers with keys  ·  "
            f"dir: {keys_dir_display}  ·  "
            f"swe providers info <provider>  │  add  │  help",
        )
    )
    print()
    return 0


def cmd_info(args: list[str]) -> int:
    if args and args[0] in ("-h", "--help", "help"):
        print_providers_help()
        return 0
    if not args:
        print(c("red", "Usage: swe providers info <name|alias>"))
        return 1

    api_keys_dir, args = _parse_api_keys_dir(list(args))
    if not args:
        print(c("red", "Usage: swe providers info <name|alias>"))
        return 1

    providers = load_registry()
    name = resolve(providers, args[0])
    if not name:
        print(c("red", f"  Provider '{args[0]}' not found in registry."))
        return 1

    info = providers[name]
    keys_dir = (api_keys_dir or default_keys_dir()).expanduser()
    row = next(
        iter(discover_provider_keys({name: info}, keys_dir)),
        None,
    )

    print(f"\n  {c('bold', info.get('name') or name)}")
    env_list = info.get("env_vars") or []
    matched_env = row.get("matched_env") if row else None
    if matched_env and len(env_list) > 1:
        fallbacks = ", ".join(v for v in env_list if v != matched_env)
        env_display = (
            f"{matched_env} "
            f"{c('dim', f'(matched; fallbacks: {fallbacks})')}"
        )
    elif matched_env:
        env_display = matched_env
    else:
        env_display = ", ".join(env_list) or "—"

    rows_out = [
        ("Slug", name),
        ("URL", info.get("url") or "—"),
        (
            "Aliases",
            ", ".join(
                a for a in (info.get("aliases") or []) if a != name
            )
            or "—",
        ),
        ("Description", info.get("description") or "—"),
        ("Env vars", env_display),
        ("Key filename", info.get("key_filename") or "—"),
        (
            "Key status",
            "-" if not row or row["masked"] == "-" else c("green", row["masked"]),
        ),
    ]
    for label, val in rows_out:
        print(f"  {'  ' + label + ':':<20} {val}")

    if row and row["key_file"]:
        path_display = _display_keys_dir(Path(row["key_file"]))
        print(f"  {'  Key file:':<20} {c('cyan', path_display)}")

    if row and not row.get("raw_key") and row["key_file"]:
        print()
        print(
            c(
                "dim",
                f"  No key found. Drop your key at {_display_keys_dir(Path(row['key_file']))}  (just the key string, no shell quoting).",
            )
        )
    print()
    return 0


def cmd_add(args: list[str]) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_providers_help()
        return 0

    name = args[0]
    description = ""
    url = ""
    key_filename = name
    env_vars: list[str] = []

    i = 1
    while i < len(args):
        a = args[i]
        if a == "--url" and i + 1 < len(args):
            url = args[i + 1]
            i += 2
        elif a == "--env" and i + 1 < len(args):
            env_vars.extend(e.strip() for e in args[i + 1].split(",") if e.strip())
            i += 2
        elif a == "--file" and i + 1 < len(args):
            key_filename = args[i + 1]
            i += 2
        elif a.startswith("--"):
            print(c("red", f"Unknown flag: {a}"))
            return 1
        elif not a.startswith("--"):
            description = a
            i += 1

    providers = load_registry()
    existing = providers.get(name, {})
    action = "Updated" if name in providers else "Added"
    # Note: `name` and `aliases` are NOT persisted here — they are derived
    # from `env_vars[0]` by `load_registry._hydrate` at load time, so the
    # API_KEY env var stays the single source of truth.
    providers[name] = {
        "description": description or existing.get("description", ""),
        "url": url or existing.get("url", ""),
        "key_filename": key_filename,
        "env_vars": env_vars or existing.get("env_vars", []) or [],
        "added": existing.get("added") or datetime.now().isoformat(),
    }
    save_registry(providers)

    parts = [f"key file: {key_filename}"]
    if env_vars:
        parts.append(f"env: {', '.join(env_vars)}")
    if url:
        parts.append(f"url: {url}")
    print(f"  {c('green', '✓')} {action} '{name}' — {', '.join(parts)}")
    print(
        c(
            "dim",
            f"  Drop your key string at {default_keys_dir()}/{key_filename}",
        )
    )
    return 0


def cmd_remove(args: list[str]) -> int:
    if not args:
        print(c("red", "Usage: swe providers remove <name|alias>"))
        return 1
    providers = load_registry(include_removed=True)
    name = resolve(providers, args[0])
    if not name:
        print(c("red", f"  Provider '{args[0]}' not found."))
        return 1

    key_filename = providers[name].get("key_filename") or name
    del providers[name]
    removed_list = providers.setdefault("_removed", [])
    if name not in removed_list:
        removed_list.append(name)
    save_registry(providers)
    print(f"  {c('green', '✓')} Removed '{name}' from registry.")
    print(
        c(
            "dim",
            f"  (Your {default_keys_dir()}/{key_filename} file is untouched — only the registry entry was removed.)",
        )
    )
    print(
        c(
            "dim",
            f"  Removal tracking is on — '{name}' will NOT come back on next upgrade.",
        )
    )
    return 0


def cmd_help(args: list[str]) -> int:
    print_providers_help()
    return 0


PROVIDERS_COMMANDS = {
    "list": cmd_list,
    "ls": cmd_list,
    "info": cmd_info,
    "add": cmd_add,
    "remove": cmd_remove,
    "rm": cmd_remove,
    "help": cmd_help,
}
