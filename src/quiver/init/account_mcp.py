"""The hub's MCP servers merged into Quiver's extra account homes (#888).

``accounts.sync.mcp`` in config.json, off by default. When on, ``swe init``
writes each server in ``~/.quiver/mcp.json`` into every extra account,
rendered the way ``swe mcp sync quiver`` renders it for the default home
(secrets resolved, shared servers as the gateway URL):

- Claude: the top-level ``mcpServers`` member of ``<home>/.claude.json``.
  That file also holds the account's login, so only that member's text is
  replaced and every other byte stays put. A missing file is left missing:
  Claude restores a lost ``.claude.json`` from its backups, which a file
  holding only servers would stop.
- Codex: ``[mcp_servers.<name>]`` tables in ``<home>/config.toml``. Only
  the tables of the servers swe adds or removes change; the rest of the
  file, the account's own servers included, keeps its bytes.

The rules match the link pass in ``accounts.py``:

- A server the account already has under the same name is reported as a
  conflict and left alone.
- Each server swe writes is recorded in ``data/account-mcp.json`` with a
  keyed digest of what it wrote, and is only updated or removed while the
  file still holds exactly that. One edited in the account is no longer
  swe's: it is dropped from the record and left alone. The digests are
  keyed (``secrets/account-mcp.key``) because the entries hold resolved
  secrets and ``data/`` is versioned.
- A file that can't be read or parsed is reported and never written. Each
  write re-parses the result and checks that everything but the servers
  reads back unchanged, keeps a backup in ``~/.quiver/backups``, and goes
  through a temp file and rename, retried when the harness rewrote the file
  in the meantime.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _random
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from quiver import paths as _paths
from quiver.configuration import ConfigurationError, read_json_object
# accounts.py imports this module inside a function, so no cycle.
from quiver.init.accounts import (
    GONE_DETAIL,
    MISSING_HOME_DETAIL,
    OFF_DETAIL,
    OUTSIDE_DETAIL,
    AccountRow,
    AccountSyncUnavailable,
    _plain,
)
from quiver.init.layout import (
    ARCHIVED_DETAIL,
    IGNORED_DETAIL,
    LinkStatus,
    archived_names,
    is_linkignored,
)

PRIMITIVE = "mcp"

# vendor -> (file in the account home, swe mcp format name)
ACCOUNT_FILES: dict[str, tuple[str, str]] = {
    "claude": (".claude.json", "claude"),
    "codex": ("config.toml", "standard"),
}

ATTEMPTS = 3


class _Changed(Exception):
    """The file changed on disk between our read and our rename."""


class _Refused(Exception):
    """The file can't be edited safely; nothing was written."""


def record_file(home: Path) -> Path:
    return _paths.data_dir_for(home) / "account-mcp.json"


def key_file(home: Path) -> Path:
    return _paths.secrets_dir_for(home) / "account-mcp.key"


# --------------------------------------------------------------- record

def load_record(home: Path) -> list[dict]:
    """The servers swe wrote into account homes; raises when unreadable."""

    path = record_file(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError) as exc:
        raise AccountSyncUnavailable(f"cannot read {path}: {exc}") from exc
    servers = data.get("servers") if isinstance(data, dict) else None
    if not isinstance(servers, list) or not all(
            isinstance(e, dict) and all(isinstance(e.get(k), str)
                                        for k in ("account", "path", "name", "digest"))
            for e in servers):
        # Dropping a damaged entry would lose the only note that swe wrote
        # that server, so switching off could never remove it.
        raise AccountSyncUnavailable(f"{path}: a damaged entry, fix or remove the file")
    return servers


def _save_record(home: Path, entries: list[dict]) -> None:
    body = {"version": 1,
            "servers": sorted(entries, key=lambda e: (e["path"], e["name"]))}
    _paths.atomic_write_text(record_file(home), json.dumps(body, indent=2) + "\n")


def _load_key(home: Path, *, create: bool) -> bytes | None:
    path = key_file(home)
    try:
        key = path.read_bytes()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    if not create:
        return None
    from quiver.init.migrate import seal_secrets_dir

    seal_secrets_dir(home)  # mode 700 and its own catch-all .gitignore
    key = _random.token_hex(32).encode("ascii")
    _paths.atomic_write_bytes(path, key, private=True)
    return key


def _digest(key: bytes | None, entry) -> str | None:
    if key is None:
        return None
    body = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str)
    return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


# ------------------------------------------------------------ the source

def hub_entries(home: Path, fmt: str) -> tuple[dict[str, dict], dict[str, str]]:
    """``({name: entry in fmt}, {name: why skipped})`` from the hub.

    The same rendering ``swe mcp sync quiver`` uses for the default home.
    Raises AccountSyncUnavailable when mcp.json can't be read, which must
    not read as "no servers" (that would remove every synced one).
    """
    from quiver.mcp import gateway
    from quiver.mcp.formats import convert_server_between_formats
    from quiver.mcp.secrets import load_secrets, resolve, unresolved_names

    try:
        data = read_json_object(_paths.mcp_source_file_for(home))
    except ConfigurationError as exc:
        raise AccountSyncUnavailable(str(exc)) from exc
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        raise AccountSyncUnavailable("mcp.json: mcpServers is not an object")
    settings = gateway.settings_of(data)
    try:
        store = load_secrets(home)
    except (UnicodeError, ValueError) as exc:
        raise AccountSyncUnavailable(f"cannot read the MCP secrets file: {exc}") from exc
    out: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    for name, raw in servers.items():
        if not isinstance(raw, dict):
            skipped[name] = "not an object in mcp.json"
            continue
        if gateway.runs_on_gateway(raw, settings):
            server = gateway.entry_for(name, settings["port"])
        else:
            missing = unresolved_names(raw, store)
            if missing:
                skipped[name] = f"no value for {', '.join(missing)} (swe mcp secret set)"
                continue
            server = resolve(gateway.without_flags(raw), store)
        entry = convert_server_between_formats(server, "standard", fmt)
        if entry:
            out[name] = entry
        else:
            skipped[name] = "no command or url"
    return out, skipped


# ------------------------------------------------------------ the files

def _parse(vendor: str, text: str) -> tuple[dict, dict]:
    """(whole document, its servers); raises _Refused."""
    if vendor == "claude":
        try:
            doc = json.loads(text)
        except (ValueError, RecursionError) as exc:
            raise _Refused("invalid JSON, not changed") from exc
        if not isinstance(doc, dict):
            raise _Refused("not a JSON object, not changed")
        servers = doc.get("mcpServers", {})
        if not isinstance(servers, dict):
            raise _Refused("mcpServers is not an object, not changed")
        return doc, servers
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise _Refused("invalid TOML, not changed") from exc
    servers = doc.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise _Refused("mcp_servers is not a table, not changed")
    return doc, servers


def _header_path(line: str) -> list[str]:
    """``[mcp_servers.x.env]`` -> ``["mcp_servers", "x", "env"]``."""
    try:
        node = tomllib.loads(line.strip())
    except tomllib.TOMLDecodeError:
        return []
    out: list[str] = []
    while isinstance(node, dict) and len(node) == 1:
        key, node = next(iter(node.items()))
        out.append(key)
        if isinstance(node, list):
            node = node[0] if node else None
    return out


def _edit_toml(text: str, remove: set[str], add: dict[str, dict]) -> str:
    """Drop the tables of ``remove``, append ``add``; every other line kept."""
    from quiver.mcp.codex_io import _table_headers, render_codex_server

    lines = text.splitlines(keepends=True)
    headers = _table_headers(lines)
    drop: set[int] = set()
    for i, h in enumerate(headers):
        path = _header_path(lines[h])
        if len(path) >= 2 and path[0] == "mcp_servers" and path[1] in remove:
            end = headers[i + 1] if i + 1 < len(headers) else len(lines)
            drop.update(range(h, end))
    out = "".join(line for i, line in enumerate(lines) if i not in drop)
    if lines and len(lines) - 1 in drop and out.endswith("\n\n"):
        out = out[:-1]  # the blank line an earlier append put before them
    if add:
        if out and not out.endswith("\n"):
            out += "\n"
        if out.strip():
            out += "\n"
        out += "\n".join(render_codex_server(n, e) for n, e in add.items())
    return out


def _render(vendor: str, text: str, doc: dict, servers: dict,
            remove: set[str], add: dict[str, dict]) -> str:
    """The new file text, checked to change nothing but the servers."""
    new_servers = {k: v for k, v in servers.items() if k not in remove}
    new_servers.update(add)
    if vendor == "claude":
        from quiver.mcp.cli import splice_json_member

        new = splice_json_member(text, "mcpServers", new_servers)
        if new is None:
            raise _Refused("layout not recognised, not changed")
        expected = dict(doc)
        expected["mcpServers"] = new_servers
        try:
            ok = json.loads(new) == expected
        except ValueError:
            ok = False
    else:
        new = _edit_toml(text, remove, add)
        try:
            parsed = tomllib.loads(new)
        except tomllib.TOMLDecodeError:
            parsed = None
        ok = parsed is not None and (
            {k: v for k, v in parsed.items() if k != "mcp_servers"}
            == {k: v for k, v in doc.items() if k != "mcp_servers"}
            and parsed.get("mcp_servers", {}) == new_servers)
    if not ok:
        raise _Refused("could not edit only the servers, not changed")
    return new


def _backup(path: Path, data: bytes, home: Path) -> Path:
    """``data`` into ~/.quiver/backups under ``backup_tree``'s naming, but
    always 0600: these files hold a login and resolved tokens, and the
    backups folder is not private the way an account home is."""
    from datetime import datetime

    folder = _paths.backups_dir_for(home)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        flat = str(path.relative_to(home)).replace("/", "_")
    except ValueError:
        flat = str(path).lstrip("/").replace("/", "_")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest, n = folder / f"{flat}.{stamp}", 1
    while True:
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            dest, n = folder / f"{flat}.{stamp}.{n}", n + 1
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return dest


def _write(path: Path, original: bytes | None, text: str, home: Path) -> None:
    """Temp file + rename, keeping the mode, with a backup of the old file.

    Raises _Changed when the file is no longer ``original`` (Claude
    rewrites .claude.json all the time), so the caller can start over.
    """
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o600
    if original is not None:
        # From the bytes we parsed, before the last check, so nothing slow
        # sits between that check and the rename.
        _backup(path, original, home)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.chmod(tmp, mode)
        try:
            current = path.read_bytes()
        except FileNotFoundError:
            current = None
        if current != original:
            raise _Changed
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- rows

@dataclass
class _Account:
    label: str
    vendor: str
    home: Path
    path: Path


def _row(acct: _Account, state: str, result: str, detail: str, name: str | None):

    why = f"server {name}" if name else ""
    if detail:
        why = f"{why}: {detail}" if why else detail
    return AccountRow(PRIMITIVE, LinkStatus(acct.label, acct.path, state, why), result, name)


def _usable(acct: _Account, home: Path) -> str | None:
    """Why this account's file can't be touched at all, or None."""

    if acct.home.is_symlink() or not acct.home.is_dir():
        return MISSING_HOME_DETAIL
    if not _plain(acct.home, home) or acct.path.is_symlink():
        return OUTSIDE_DETAIL
    if os.path.lexists(acct.path) and not acct.path.is_file():
        return "not a file, left alone"
    return None


def _one_account(acct: _Account, home: Path, on: bool, desired: dict[str, dict],
                 skipped: dict[str, str], mine: dict[str, str], key: bytes | None,
                 check_only: bool) -> tuple[list, dict[str, str], bool]:
    """Rows, the {name: digest} record for this file afterwards, and
    whether the key is needed (something will be written)."""

    why = _usable(acct, home)
    if why is not None:
        state = "skipped" if why == MISSING_HOME_DETAIL else "conflict"
        return [_row(acct, state, state, why, None)], mine, False
    try:
        original: bytes | None = acct.path.read_bytes()
    except FileNotFoundError:
        original = None
    except OSError as exc:
        return [_row(acct, "blocked", "blocked", f"cannot read: {exc.strerror or exc}",
                     None)], mine, False
    if original is None and acct.vendor == "claude":
        if on and desired:
            return [_row(acct, "skipped", "skipped",
                         "no .claude.json yet, sign in first", None)], {}, False
        return [], {}, False
    try:
        text = original.decode("utf-8") if original is not None else ""
        doc, servers = _parse(acct.vendor, text)
    except UnicodeDecodeError:
        return [_row(acct, "blocked", "blocked", "not UTF-8, not changed", None)], mine, False
    except _Refused as exc:
        return [_row(acct, "blocked", "blocked", str(exc), None)], mine, False

    rows = []
    record: dict[str, str] = {}
    remove: set[str] = set()
    add: dict[str, dict] = {}
    for name in sorted(set(mine) | set(desired) | (set(skipped) if on else set())):
        current = servers.get(name)
        ours = name in mine and current is not None and _digest(key, current) == mine[name]
        if on and name in skipped:
            if ours:
                record[name] = mine[name]
            rows.append(_row(acct, "skipped", "skipped", skipped[name], name))
        elif on and name in desired:
            entry = desired[name]
            if ours and current == entry:
                record[name] = mine[name]
                rows.append(_row(acct, "linked", "linked", "", name))
            elif ours or name not in servers:
                state = "relink" if ours else "create"
                if ours:
                    remove.add(name)
                add[name] = entry
                rows.append(_row(acct, state, ("would-" if check_only else "") + state,
                                 "", name))
            else:
                rows.append(_row(acct, "conflict", "conflict",
                                 "the account's own server, left alone", name))
        elif ours:
            remove.add(name)
            rows.append(_row(acct, "unlink", "would-unlink" if check_only else "unlink",
                             OFF_DETAIL if not on else GONE_DETAIL, name))
        # else: recorded but gone or edited in the account: forget it.
    if check_only:
        return rows, mine, False
    if not remove and not add:
        return rows, record, False
    if key is None:
        return rows, record, True  # the caller makes the key and calls again

    new_text = _render(acct.vendor, text, doc, servers, remove, add)
    _write(acct.path, original, new_text, home)
    for row in rows:
        if row.result == "create" or row.result == "relink":
            row.result = "linked"
            record[row.name] = _digest(key, add[row.name])
        elif row.result == "unlink":
            row.result = "unlinked"
    return rows, record, False


def sync_mcp(home: Path, patterns: list[str], registry: dict, on: bool, accounts: list, *,
             check_only: bool) -> tuple[list, list[str]]:
    """Merge the hub's servers into every account home, or take out the
    ones swe added once the switch is off. Returns (rows, notes)."""

    try:
        record = load_record(home)
        if not on and not record:
            return [], []
        sources = {fmt: hub_entries(home, fmt) for _, fmt in ACCOUNT_FILES.values()} \
            if on else {}
    except AccountSyncUnavailable as exc:
        return [], [f"MCP account sync skipped: {exc}"]

    archived = archived_names(registry)
    key = _load_key(home, create=False)
    rows: list = []
    kept: list[dict] = []
    by_file: dict[str, dict[str, str]] = {}
    for entry in record:
        by_file.setdefault(entry["path"], {})[entry["name"]] = entry["digest"]

    for acct in accounts:
        filename, fmt = ACCOUNT_FILES[acct.vendor]
        target = _Account(acct.label, acct.vendor, acct.path, acct.path / filename)
        mine = by_file.pop(str(target.path), {})
        frozen = (IGNORED_DETAIL if is_linkignored(target.path, home, patterns)
                  else ARCHIVED_DETAIL if acct.vendor in archived else None)
        if frozen is not None:
            # .linkignore or an archived harness: touch nothing, forget nothing.
            if on or mine:
                rows.append(_row(target, "ignored", "ignored", frozen, None))
            after = mine
        else:
            desired, skipped = sources.get(fmt, ({}, {}))
            after = mine
            for _attempt in range(ATTEMPTS):
                try:
                    found, after, needs_key = _one_account(
                        target, home, on, desired, skipped, mine, key, check_only)
                    if needs_key:
                        key = _load_key(home, create=True)
                        found, after, _ = _one_account(
                            target, home, on, desired, skipped, mine, key, check_only)
                    rows.extend(found)
                    break
                except _Changed:
                    continue
                except _Refused as exc:
                    rows.append(_row(target, "blocked", "blocked", str(exc), None))
                    after = mine
                    break
                except OSError as exc:
                    rows.append(_row(target, "blocked", "blocked",
                                     f"could not write: {exc.strerror or exc}", None))
                    after = mine
                    break
            else:
                rows.append(_row(target, "blocked", "blocked",
                                 "kept changing while syncing, run swe init again", None))
        kept.extend({"account": acct.label, "path": str(target.path), "name": n, "digest": d}
                    for n, d in after.items())
    # Accounts no longer in profiles.json: keep their entries, so the
    # servers can still be taken out if the account comes back.
    kept.extend(e for e in record if e["path"] in by_file)
    if not check_only:
        new_record = sorted(kept, key=lambda e: (e["path"], e["name"]))
        if new_record != sorted(record, key=lambda e: (e["path"], e["name"])):
            _save_record(home, new_record)
    return rows, []
