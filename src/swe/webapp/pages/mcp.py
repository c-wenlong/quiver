"""The MCP servers page: the Mac app's McpServersPage, read only.

``GET /v1/app/mcp`` is ``swe mcp list --json`` built in process (the same
``server_inventory`` rows), plus the managed servers folder the Mac app
scans beside it (``McpServerRepo``: which checkouts sit in
``~/.quiver/mcp/servers``, which servers run from them, their git state).
``GET /v1/app/mcp?health=1`` adds ``swe mcp status``'s health probe per
server, run in parallel under one deadline, and only when asked.

Server definitions never carry a secret here. swe already sends env and header names only
and scrubs known credentials out of commands, arguments and URLs; the
row's command, args and url are then replaced by ``summary``, the one line
the Mac app shows, redacted again the way ``McpRedaction`` does it, so a
token swe does not recognise still never reaches the browser.

``POST /v1/app/mcp/preview`` and ``/sync`` are the sheet's Sync, as the
Mac app runs it: ``swe mcp sync <source> <targets> --only=<name>
--no-interactive``, once without ``--force`` for the harnesses that lack
the server and once with it for those whose own definition it replaces,
through ``mcp/cli.py``'s ``_sync`` in process. Preview is that dry run plus
what each harness config gains, changes or loses (names only), and a
fingerprint of what it read; Sync recomputes that fingerprint, refuses
with 409 when anything it depends on changed since the preview, and then
writes the checked snapshot with ``_sync``'s own per-target steps, so it
writes exactly the plan that was shown.

Each hub server's row carries ``secrets``: every ``${NAME}`` its hub
definition refers to, ``{name, set, masked}``, masked as the Mac app
masks a key (``_secrets.mask``). ``POST /v1/app/mcp/secret_set``
(``{"name", "value"}``) is the sheet's Save or Replace,
``swe mcp secret set NAME`` (``set_secret``); ``POST
/v1/app/mcp/secret_value`` (``{"name"}``) answers one value for Reveal or
Copy, the only reply that ever holds one. Both take only a name some hub
server refers to.

``POST /v1/app/mcp/remove`` is the sheet's Remove: ``swe mcp remove
<name> --from=<places>`` (``remove_places`` + ``remove_server``) for the
places the confirm named, each of which must still hold the server.

``POST /v1/app/mcp/add`` is the page's Add server, the Mac app's
``McpAddServerSheet``: ``swe mcp add`` in process (``add_plan`` +
``add_server``). A variable marked secret is stored with ``set_secret``
under ``SERVER_KEY`` and the configs get ``${SERVER_KEY}``; nothing is
stored for an add the hub would refuse. The reply never holds a value.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import unquote, urlsplit

from swe.mcp.secrets import _SECRET_FLAG_RE
from swe.webapp.pages import CONFIG_LOCK, PageError, tilde

# A health probe HEADs each URL with its own five-second timeout; the whole
# check gets this long before a server still being probed reads "unknown".
HEALTH_DEADLINE_S = 30.0
HEALTH_WORKERS = 8
# McpServerRepoModel.gitBatch, and McpServerRepo.gitState's timeout.
GIT_WORKERS = 4
GIT_TIMEOUT_S = 5.0


def get(ctx, args: dict) -> dict:
    health = str(args.get("health", "")) in {"1", "true"}
    harnesses: list[str] = []
    pairs: list[tuple[dict, object]] = []
    error = None
    try:
        harnesses, pairs = inventory()
    except (OSError, ValueError) as exc:
        # The Mac app shows the error and still lists the folder rows.
        error = str(exc) or exc.__class__.__name__
    if health and pairs:
        for (row, _), (state, detail) in zip(pairs, probe_all(pairs)):
            row["health"], row["detail"] = state, detail
    folders = folder_scan(_servers_root(ctx), [row for row, _ in pairs],
                          home=ctx.home)
    servers = [page_row(row) for row, _ in pairs]
    _add_secrets(servers)
    return {
        "harnesses": harnesses,
        "servers": servers,
        "caps": _caps(harnesses),
        "health": bool(health and pairs),
        "error": error,
        "folders": folders,
        "machine": (ctx.machine or {}).get("name"),
    }


def _caps(harnesses: list[str]) -> dict:
    """What each harness's config can hold: a server at a URL at all
    (Claude Desktop launches local ones only) and an SSE one (opencode and
    Codex have no marker for it), as ``swe mcp add`` judges."""
    from swe.mcp import cli as mcp_cli

    def sse(h: str) -> bool:
        return not (mcp_cli.get_tool_format(h) in ("opencode",)
                    or mcp_cli.get_tool_config(h).get("io_type") == "toml_region")

    return {h: {"remote": mcp_cli.takes_remote(h), "sse": mcp_cli.takes_remote(h) and sse(h)}
            for h in harnesses}


# ---------------------------------------------------------------- inventory


def inventory() -> tuple[list[str], list[tuple[dict, object]]]:
    """``swe mcp list --json`` for every harness, as ``(harnesses, [(row,
    first_raw_config)])``. The registry is read without the legacy
    migration write, so loading the page never changes a file."""
    from swe.harness.registry import load_registry_readonly
    from swe.mcp import cli as mcp_cli
    from swe.mcp.inventory import server_inventory

    tools = mcp_cli._display_tools(mcp_cli.get_mcp_tools(load_registry_readonly()))
    tool_data = {name: mcp_cli.get_tool_servers(name) for name in tools}
    return list(tool_data), server_inventory(tool_data, include_hub=True)


def page_row(row: dict) -> dict:
    """The swe row without its command line or URL, which become the
    redacted ``summary``; env and header values were never in it."""
    out = {k: v for k, v in row.items() if k not in {"command", "args", "url"}}
    out["summary"] = summary(row.get("command"), row.get("args") or [], row.get("url"))
    return out


def probe_all(pairs: list[tuple[dict, object]],
              deadline: float = HEALTH_DEADLINE_S) -> list[tuple[str, str | None]]:
    """``swe mcp status``'s probe for every server, in parallel. A command
    is looked up on PATH, never started; a URL gets one HEAD request. A
    probe still running at the deadline reads unknown."""
    from swe.mcp.cli import probe_server_health

    def one(row: dict, cfg) -> tuple[str, str | None]:
        if not isinstance(cfg, dict):
            return "unknown", "no command or url"
        try:
            return probe_server_health(row["name"], cfg)
        except Exception as exc:  # a malformed entry must not sink the page
            return "error", f"check failed: {exc.__class__.__name__}"

    pool = ThreadPoolExecutor(max_workers=HEALTH_WORKERS, thread_name_prefix="mcp-health")
    try:
        futures = [pool.submit(one, row, cfg) for row, cfg in pairs]
        wait(futures, timeout=deadline)
        return [f.result() if f.done() else ("unknown", "check timed out")
                for f in futures]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


# ---------------------------------------------------------------- sync
# The sheet's Sync and its Preview (McpServersPage's ServerSheet, and
# McpModel.sync/targets/sources): one server, copied from one source to the
# harnesses picked, through the same ``_sync`` that ``swe mcp sync`` runs.

ACTIONS = ("preview", "sync", "remove", "secret_set", "secret_value", "add")
HUB = "quiver"
NAME_MAX = 200

# Every write here reads harness configs, changes one server and writes them
# back; two at once could lose one, and the dry run and the write must see
# the same files. ``_sync`` also prints as it goes, which is captured while
# this is held (the hub itself never writes to stdout).
_WRITE_LOCK = CONFIG_LOCK  # shared with Skills and Setup
# Fingerprints are keyed per hub process: the material holds the configs,
# resolved secrets included, so a bare hash of it could be guessed at
# offline. A hub restart between Preview and Sync just asks for a new
# preview.
_FINGERPRINT_KEY = secrets.token_bytes(32)


def post(ctx, action: str, body: dict) -> dict:
    if action not in ACTIONS:
        raise PageError(404, "unknown_action", f"unknown MCP action: {action}")
    if action == "remove":
        return _remove(ctx, body)
    if action == "add":
        return _add(ctx, body)
    if action in ("secret_set", "secret_value"):
        return _secret(ctx, action, body)
    request = _sync_request(body, action == "sync")
    with _WRITE_LOCK:
        plan = _plan(request)
        if action == "preview":
            report, error = _run(plan, dry_run=True)
            if error is not None and not report["targets"]:
                raise PageError(409, "sync_refused", error)
            return _plan_reply(plan, report, error)
        if not hmac.compare_digest(request["fingerprint"].encode("utf-8"),
                                   plan["fingerprint"].encode("utf-8")):
            raise PageError(409, "plan_changed",
                            "The configs changed since the preview. Preview again before syncing.")
        report, error = _write(plan)
        if error is not None and not report["targets"]:
            raise PageError(409, "sync_refused", error)
    reply = _plan_reply(plan, report, error)
    # The page repaints from this: the list as it is now, after the write.
    reply["page"] = get(ctx, {})
    return reply


# ---------------------------------------------------------------- secrets


def _hub_refs() -> dict[str, list[str]]:
    """Each hub server's ``${NAME}`` references."""
    from swe.mcp.cli import get_hub_servers
    from swe.mcp.secrets import referenced_names

    return {name: referenced_names(raw) for name, raw in get_hub_servers().items()}


def _add_secrets(servers: list[dict]) -> None:
    """``secrets`` on each hub row: name, whether the store sets it, and its
    masked value. Never the value itself."""
    from swe.mcp.secrets import load_secrets
    from swe.webapp.pages._secrets import mask

    try:
        refs, store = _hub_refs(), load_secrets()
    except (OSError, ValueError):
        refs, store = {}, {}
    for row in servers:
        names = refs.get(row["name"], []) if row.get("in_hub") else []
        row["secrets"] = [{"name": n, "set": bool(store.get(n)), "masked": mask(store.get(n) or "") or None}
                          for n in names]


def _secret(ctx, action: str, body: dict) -> dict:
    from swe.mcp.secrets import load_secrets, set_secret

    if not isinstance(body, dict):
        raise PageError(422, "bad_body", "expected a JSON object")
    name = body.get("name")
    if not isinstance(name, str) or not name or len(name) > NAME_MAX:
        raise PageError(422, "bad_name", "name must be a secret a server refers to")
    with _WRITE_LOCK:
        if not any(name in names for names in _hub_refs().values()):
            raise PageError(404, "unknown_secret", f"No hub server refers to ${{{name}}}.")
        if action == "secret_value":
            value = load_secrets().get(name)
            if not value:
                raise PageError(404, "secret_not_set", f"{name} has no value on the hub.")
            return {"name": name, "value": value}
        value = body.get("value")
        if not isinstance(value, str) or len(value) > 16384:
            raise PageError(422, "bad_value", "value must be the secret, as text")
        if not value.strip():
            raise PageError(422, "bad_value", "Paste a value first.")
        try:
            set_secret(name, value.strip())
        except ValueError as exc:
            # set_secret's reasons name the variable, never the value.
            raise PageError(422, "bad_value", str(exc)) from exc
    return {"name": name, "page": get(ctx, {})}


def _remove(ctx, body: dict) -> dict:
    """Remove: ``{"server", "from": [places]}``, the places the confirm
    listed ("quiver" for the hub). Every one must hold the server now; a
    place it has since spread to is left alone, since the confirm didn't
    name it. The reply is ``{"removed", "absent", "page"}``."""
    from swe.harness.registry import load_registry_readonly
    from swe.mcp.cli import RemoveRefused, remove_places, remove_server

    if not isinstance(body, dict):
        raise PageError(422, "bad_body", "expected a JSON object")
    name, places = body.get("server"), body.get("from")
    if not isinstance(name, str) or not name or len(name) > NAME_MAX:
        raise PageError(422, "bad_server", "server must be a server name")
    if (not isinstance(places, list) or not places or len(places) > 100
            or not all(isinstance(p, str) and 0 < len(p) <= NAME_MAX for p in places)):
        raise PageError(422, "bad_from", "from must list the places to remove the server from")
    with _WRITE_LOCK:
        _, pairs = inventory()
        row = next((r for r, _ in pairs if r["name"] == name), None)
        if row is None:
            raise PageError(404, "unknown_server", f"No server named {name}. Refresh and try again.")
        holds = set(row.get("harnesses") or []) | ({HUB} if row.get("in_hub") else set())
        gone = [p for p in places if p not in holds]
        if gone:
            raise PageError(409, "state_changed",
                            f"{name} is no longer in {', '.join(gone)}. Refresh and try again.")
        try:
            targets = remove_places(load_registry_readonly(), name, list(dict.fromkeys(places)))
            removed, absent = remove_server(name, targets)
        except RemoveRefused as exc:
            raise PageError(422, "remove_refused", str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise PageError(409, "remove_failed", str(exc) or exc.__class__.__name__) from exc
    return {"server": name, "removed": removed, "absent": absent, "page": get(ctx, {})}


# ------------------------------------------------------------------- add

ADD_WORDS_MAX = 200
ADD_VARS_MAX = 100
TEXT_MAX = 4096
VALUE_MAX = 16384


_SECRET_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def secret_name(server: str, key: str) -> str:
    """``mcpSecretName``: the ``${NAME}`` a secret env var or header is
    stored under, ``SERVER_KEY`` in caps with every other run of
    characters one ``_``, and ``MCP_`` in front of a leading digit."""
    name = "".join(ch.upper() if ch.isascii() and ch.isalnum() else "_" for ch in f"{server}_{key}")
    name = re.sub(r"_+", "_", name).strip("_")
    return "MCP_" + name if name[:1].isdigit() else name


def _add_request(body: dict) -> dict:
    """The body's fields, type-checked; swe judges the rest."""
    if not isinstance(body, dict):
        raise PageError(422, "bad_body", "expected a JSON object")
    from swe.mcp.cli import _SERVER_NAME_RE

    name = body.get("name")
    if not isinstance(name, str) or len(name) > NAME_MAX or not _SERVER_NAME_RE.match(name):
        raise PageError(422, "bad_name",
                        "A name is letters, digits, . _ @ - and can't start with _ . @ -.")
    kind = body.get("kind")
    if kind not in ("command", "url"):
        raise PageError(422, "bad_kind", "kind must be command or url")
    command, url, transport = None, None, None
    if kind == "command":
        command = body.get("command")
        if (not isinstance(command, list) or not command or len(command) > ADD_WORDS_MAX
                or not all(isinstance(w, str) and len(w) <= TEXT_MAX for w in command)
                or not command[0]):
            raise PageError(422, "bad_command", "Enter the command that starts the server.")
    else:
        url = body.get("url")
        if not isinstance(url, str) or not url.strip() or len(url) > TEXT_MAX:
            raise PageError(422, "bad_url", "Enter the server's URL.")
        url = url.strip()
        transport = body.get("transport", "http")
        if transport not in ("http", "sse"):
            raise PageError(422, "bad_transport", "transport must be http or sse")
    variables = body.get("vars", [])
    if not isinstance(variables, list) or len(variables) > ADD_VARS_MAX:
        raise PageError(422, "bad_vars", "vars must be a list")
    pairs = []
    for var in variables:
        if not isinstance(var, dict):
            raise PageError(422, "bad_vars", "each var is {key, value, secret}")
        key, value, secret = var.get("key", ""), var.get("value", ""), var.get("secret", False)
        if (not isinstance(key, str) or len(key) > NAME_MAX or not isinstance(value, str)
                or len(value) > VALUE_MAX or not isinstance(secret, bool)):
            raise PageError(422, "bad_vars", "each var is {key, value, secret}")
        if key.strip():
            pairs.append((key.strip(), value, secret))
    sign_in = body.get("sign_in", "none")
    if sign_in not in ("none", "bearer", "oauth") or (sign_in != "none" and kind != "url"):
        raise PageError(422, "bad_sign_in", "sign_in is none, bearer or oauth, for a URL server")
    token = body.get("token", "")
    if sign_in == "bearer":
        if not isinstance(token, str) or not token.strip() or len(token) > VALUE_MAX:
            raise PageError(422, "bad_value", "Paste the bearer token.")
        if any(k.lower() == "authorization" for k, _, _ in pairs):
            raise PageError(422, "bad_vars", "Give the token once: as the bearer token or an Authorization header.")
    targets = body.get("targets", [])
    if (not isinstance(targets, list) or len(targets) > 100
            or not all(isinstance(t, str) and 0 < len(t) <= NAME_MAX for t in targets)):
        raise PageError(422, "bad_targets", "targets must list harness names")
    force = body.get("force", False)
    if not isinstance(force, bool):
        raise PageError(422, "bad_force", "force must be true or false")
    return {"name": name, "command": command, "url": url, "transport": transport,
            "pairs": pairs, "targets": list(dict.fromkeys(targets)), "force": force,
            "sign_in": sign_in, "token": token.strip() if sign_in == "bearer" else ""}


def _add(ctx, body: dict) -> dict:
    """Add server: ``{"name", "kind", "command": [words] | "url" +
    "transport", "vars": [{key, value, secret}], "targets", "force"}``.
    Secret values are stored only once swe would take the add, and every
    refusal names variables, never their values."""
    from swe.harness.registry import load_registry_readonly
    from swe.mcp.cli import AddRefused, add_conflicts, add_plan, add_server, get_hub_servers
    from swe.mcp.secrets import load_secrets, render_secret_line, set_secret, unresolved_names

    req = _add_request(body)
    name = req["name"]
    values, stored = {}, {}
    for key, value, secret in req["pairs"]:
        if secret and not value.startswith("${"):
            ref = secret_name(name, key)
            if not value.strip():
                raise PageError(422, "bad_value", f"{key} is marked secret but has no value.")
            if ref in stored:
                raise PageError(422, "secret_clash",
                                f"Two keys would both be saved as {ref}. Rename one of them.")
            stored[ref] = value.strip()
            value = "${" + ref + "}"
        values[key] = value
    if req["sign_in"] == "bearer":
        # The token is a secret like any other: stored, and the hub keeps
        # Authorization: Bearer ${SERVER_TOKEN}.
        ref = secret_name(name, "TOKEN")
        if ref in stored:
            raise PageError(422, "secret_clash", f"Two keys would both be saved as {ref}. Rename one of them.")
        stored[ref] = req["token"]
        values["Authorization"] = "Bearer ${" + ref + "}"
    # Every value must be storable before any is stored.
    for ref, value in stored.items():
        if not _SECRET_NAME_RE.fullmatch(ref):
            raise PageError(422, "bad_value", f"{ref} isn't a name a secret can be saved under. Rename the key.")
        if "\n" in value or "\r" in value:
            raise PageError(422, "bad_value", f"{ref}: a value can't span more than one line")
        try:
            render_secret_line(ref, value)
        except ValueError as exc:
            # Its reasons name the variable, never the value.
            raise PageError(422, "bad_value", f"{ref}: {exc}") from exc
    env = values if req["command"] is not None else {}
    headers = values if req["url"] is not None else {}
    with _WRITE_LOCK:
        try:
            canonical, targets = add_plan(
                load_registry_readonly(), url=req["url"], transport=req["transport"],
                command=req["command"], env=env, headers=headers, to_names=req["targets"])
        except AddRefused as exc:
            raise PageError(422, "add_refused", str(exc)) from exc
        clash = add_conflicts(name, targets)
        if clash and not req["force"]:
            raise PageError(409, "exists",
                            f"A server named {name} already exists in {', '.join(clash)}. "
                            "Turn on Replace to overwrite it.")
        # A saved name another hub server refers to is that server's value:
        # overwriting it would change what that server gets.
        for ref in stored:
            users = sorted(other for other, refs in _hub_refs().items() if ref in refs and other != name)
            if users:
                raise PageError(409, "secret_taken",
                                f"{ref} is already {', '.join(users)}'s secret. "
                                "Rename the server or the key so they don't share it.")
        # swe refuses a ${NAME} the store can't fill once a harness is
        # picked; check it with the values this add would store, first.
        if targets:
            missing = unresolved_names(canonical, {**load_secrets(), **stored})
            if missing:
                raise PageError(409, "add_refused", "\n".join(
                    f"Not adding: no value for {var} (used by {name}). Set it first, or mark it secret here."
                    for var in missing))
        # What each name held before, so a failed add puts the store back.
        before, hub_before = load_secrets(), get_hub_servers().get(name)
        try:
            for ref, value in stored.items():
                try:
                    set_secret(ref, value)
                except ValueError as exc:
                    raise PageError(422, "bad_value", f"{ref}: {exc}") from exc
                except OSError as exc:
                    raise PageError(409, "add_failed", str(exc) or exc.__class__.__name__) from exc
            try:
                report = add_server(name, canonical, targets, force=req["force"])
            except AddRefused as exc:
                raise PageError(409, "add_refused", str(exc)) from exc
            except (OSError, ValueError) as exc:
                raise PageError(409, "add_failed", str(exc) or exc.__class__.__name__) from exc
        except PageError:
            # add_server saves the hub before the harness files, so a failed
            # harness write leaves a hub entry that needs these secrets.
            if get_hub_servers().get(name) == hub_before:
                _restore_secrets(stored, before)
            raise
    return {**report, "page": get(ctx, {})}


def _restore_secrets(stored: dict[str, str], before: dict[str, str]) -> None:
    """Put each name in ``stored`` back to its value in ``before``, or
    remove it when it had none, so a failed add leaves no secret behind.
    A name whose value is no longer the one this add wrote changed since,
    and is left alone."""
    from swe.mcp.secrets import load_secrets, remove_secret, set_secret
    now = load_secrets()
    for ref, value in stored.items():
        if now.get(ref) != value:
            continue
        try:
            if ref in before:
                set_secret(ref, before[ref])
            else:
                remove_secret(ref)
        except (OSError, ValueError):
            pass


def _sync_request(body: dict, needs_fingerprint: bool) -> dict:
    """The body's fields, type-checked; membership is checked by _plan."""
    if not isinstance(body, dict):
        raise PageError(422, "bad_body", "expected a JSON object")
    name, source = body.get("server"), body.get("source")
    if not isinstance(name, str) or not name or len(name) > NAME_MAX:
        raise PageError(422, "bad_server", "server must be a server name")
    if not isinstance(source, str) or not source or len(source) > NAME_MAX:
        raise PageError(422, "bad_source", "source must be a harness name or quiver")

    def names(key: str) -> list[str]:
        value = body.get(key, [])
        if (not isinstance(value, list) or len(value) > 100
                or not all(isinstance(v, str) and 0 < len(v) <= NAME_MAX for v in value)):
            raise PageError(422, f"bad_{key}", f"{key} must be a list of harness names")
        return list(dict.fromkeys(value))

    targets, replace = names("targets"), names("replace")
    if not targets:
        raise PageError(422, "no_targets", "Pick at least one harness to sync to.")
    if not set(replace) <= set(targets):
        raise PageError(422, "bad_replace", "replace must name only picked targets")
    fingerprint = body.get("fingerprint")
    if needs_fingerprint and (not isinstance(fingerprint, str) or not fingerprint):
        raise PageError(422, "no_fingerprint", "Sync needs the fingerprint of a preview.")
    return {"server": name, "source": source, "targets": targets,
            "replace": set(replace), "fingerprint": fingerprint or ""}


def _groups(row: dict) -> list[list[str]]:
    """McpServer.groups: the harnesses grouped by identical definition."""
    if row.get("variants") is not None:
        return row["variants"]
    return [[h] for h in row["harnesses"]] if row.get("conflict") else [row["harnesses"]]


def sources(row: dict) -> list[str]:
    """McpModel.sources: each definition's first harness, then the hub."""
    out = [g[0] for g in _groups(row) if g]
    if row.get("in_hub") and HUB not in out:
        out.append(HUB)
    return out


def source_is_remote(row: dict, source: str) -> bool:
    """Whether the definition a sync copies, ``source``'s, is reached at a
    URL. ``transport`` is only the first definition's; a server can be
    local in one harness and remote in another."""
    if "remote_in" in row:
        return source in row["remote_in"]
    return row.get("transport") in ("http", "sse")


def sync_targets(row: dict, source: str, harnesses: list[str]) -> tuple[list[str], list[str]]:
    """McpModel.targets: ``(missing, differing)``. Missing lacks the server;
    differing has its own definition, which a sync would replace. The hub
    is no column, so from it every harness with the server differs."""
    from swe.mcp.cli import takes_remote

    if source_is_remote(row, source):
        # A config that launches local servers only can't take a URL one.
        harnesses = [h for h in harnesses if takes_remote(h)]
    groups = _groups(row)
    group_of = {h: i for i, g in enumerate(groups) for h in g}
    if source == HUB:
        return ([h for h in harnesses if h not in row["harnesses"]],
                [h for h in harnesses if h in row["harnesses"]])
    mine = group_of.get(source)
    missing = [h for h in harnesses if h != source and h not in group_of]
    differing = [h for h in harnesses if h != source and h in group_of and group_of[h] != mine]
    return missing, differing


def only_pattern(name: str) -> str:
    """McpServer.onlyPattern: the name as an fnmatch glob matching only itself."""
    return "".join({"[": "[[]", "*": "[*]", "?": "[?]"}.get(ch, ch) for ch in name)


def _plan(request: dict) -> dict:
    """What a sync would do, read fresh from disk, or a refusal. Each
    target is classified now, and must still be what the page showed:
    a harness picked to gain the server must still lack it, and one picked
    to have its definition replaced must still have its own."""
    from swe.harness.registry import load_registry_readonly
    from swe.mcp import cli as mcp_cli
    from swe.mcp.inventory import server_inventory
    from swe.mcp.secrets import load_secrets

    tools = mcp_cli._display_tools(mcp_cli.get_mcp_tools(load_registry_readonly()))
    harnesses = list(tools)
    tool_data = {t: mcp_cli.get_tool_servers(t) for t in harnesses}
    name, source = request["server"], request["source"]
    row = next((r for r, _ in server_inventory(tool_data, include_hub=True)
                if r["name"] == name), None)
    if row is None:
        raise PageError(404, "unknown_server", f"No server named {name} in any harness or the hub.")
    if "," in name:
        raise PageError(422, "comma_name",
                        "swe can't sync a server whose name has a comma on its own. "
                        "Use swe mcp sync on the hub.")
    if source not in sources(row):
        raise PageError(422, "bad_source", f"{name} can't be copied from {source}.")
    missing, differing = sync_targets(row, source, harnesses)
    remote = source_is_remote(row, source)
    plan_targets = []
    for target in request["targets"]:
        if target not in harnesses:
            raise PageError(422, "unknown_harness", f"No harness named {target} reads MCP servers here.")
        if remote and not mcp_cli.takes_remote(target):
            raise PageError(422, "local_only",
                            f"{mcp_cli.get_tool_config(target).get('label', target)} {mcp_cli.LOCAL_ONLY_NOTE}.")
        replacing = target in request["replace"]
        if target in missing and not replacing:
            action = "add"
        elif target in differing and replacing:
            action = "replace"
        elif target in missing:
            raise PageError(409, "target_changed",
                            f"{target} no longer has its own {name}. Refresh and pick again.")
        elif target in differing:
            raise PageError(409, "target_changed",
                            f"{target} now has its own definition of {name}. Refresh and pick again.")
        else:
            raise PageError(409, "target_changed",
                            f"{target} already has this definition of {name}. Refresh and pick again.")
        plan_targets.append((target, action))
    # Inventory order, as McpModel.sync passes them.
    order = {h: i for i, h in enumerate(harnesses)}
    plan_targets.sort(key=lambda pair: order[pair[0]])

    if source == HUB and row.get("secrets_missing"):
        # _sync refuses this too (a ${NAME} the store can't fill would reach
        # the server as a literal string); saying so here names them.
        raise PageError(409, "secrets_missing", " ".join(
            f"Not syncing: no value for {var} (used by {name}). "
            f"Set {var} in this server's sheet, or on the hub with: swe mcp secret set {var}"
            for var in row["secrets_missing"]))

    store = load_secrets()
    src_cfg = mcp_cli.servers_for_source(source).get(name)
    if not isinstance(src_cfg, dict):
        raise PageError(409, "bad_config",
                        f"{source}'s {name} isn't a server definition swe can copy.")
    shown_src = mcp_cli.get_hub_servers().get(name) if source == HUB else src_cfg
    entries = []
    for target, action in plan_targets:
        cfg = mcp_cli.get_tool_config(target)
        before = tool_data[target].get(name)
        converted = mcp_cli.convert_server_for_target(src_cfg, source, target)
        after = (mcp_cli._merge_format_keys(before, converted) if action == "replace"
                 else converted)
        entries.append({
            "harness": target, "action": action, "path": cfg["path"],
            "before": before, "after": after,
        })
    material = json.dumps({
        "server": name, "source": source, "config": src_cfg,
        "targets": [[e["harness"], e["action"], str(e["path"]), e["before"]] for e in entries],
    }, sort_keys=True, default=str)
    fingerprint = hmac.new(_FINGERPRINT_KEY, material.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"server": name, "source": source, "row": row, "entries": entries,
            "source_described": _describe(shown_src, source, store),
            "store": store, "fingerprint": fingerprint}


def _run(plan: dict, dry_run: bool) -> tuple[dict, str | None]:
    """``_sync`` in process, as McpModel.sync runs swe: one call for the
    harnesses that gain the server, one with ``--force`` for those whose
    definition it replaces. Preview runs it with ``--dry-run``, so swe's
    own checks and report stand behind the plan; the write is ``_write``.
    Returns the merged McpSyncReport and, when a call failed, its
    message."""
    from swe.console import strip_ansi
    from swe.mcp import cli as mcp_cli

    merged = {"source": plan["source"], "dry_run": dry_run, "selected": [], "targets": []}
    runs = [([e["harness"] for e in plan["entries"] if e["action"] == action], force)
            for action, force in (("add", False), ("replace", True))]
    for targets, force in runs:
        if not targets:
            continue
        part = {"source": None, "dry_run": dry_run, "selected": [], "targets": []}
        args = [plan["source"], *targets, f"--only={only_pattern(plan['server'])}", "--no-interactive"]
        if force:
            args.append("--force")
        if dry_run:
            args.append("--dry-run")
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = mcp_cli._sync(args, part)
        except Exception as exc:  # a config that won't parse or save
            return merged, f"Sync stopped: {exc}" if str(exc) else f"Sync stopped: {exc.__class__.__name__}"
        if code:
            lines = [ln.strip() for ln in strip_ansi(out.getvalue()).splitlines() if ln.strip()]
            return merged, lines[-1] if lines else "swe mcp sync refused."
        merged["selected"] = merged["selected"] or part["selected"]
        merged["targets"] += part["targets"]
    return merged, None


def _write(plan: dict) -> tuple[dict, str | None]:
    """The sync itself: writes the plan that was fingerprinted, never a
    fresh read of the source. ``_sync`` would load the source again (twice,
    once per call), so an edit landing after the check could reach a target
    the user never previewed; this does ``_sync``'s per-target steps on the
    checked snapshot instead. ``after`` is ``convert_server_for_target``'s
    copy, merged over the target's own keys with ``_merge_format_keys``
    for a replace, saved through ``get_tool_saver``: what ``_sync --force``
    writes. Adds go first, then replacements, as McpModel.sync runs them.

    Each target is read again just before its save, and the sync stops if
    its entry no longer matches the preview. The report (McpSyncReport)
    lists only the targets that were saved, so a failure part way shows
    what already changed."""
    from swe.mcp import cli as mcp_cli

    name = plan["server"]
    report = {"source": plan["source"], "dry_run": False, "selected": [name], "targets": []}
    entries = sorted(plan["entries"], key=lambda e: e["action"] != "add")
    for entry in entries:
        target = entry["harness"]
        try:
            servers = mcp_cli.get_tool_servers(target)
            if servers.get(name) != entry["before"]:
                return report, (f"{target}'s {name} changed while syncing, so it was left alone. "
                                "Preview again.")
            servers[name] = entry["after"]
            mcp_cli.get_tool_saver(target)(servers, mcp_cli.get_tool_config(target)["path"])
        except Exception as exc:  # a config that won't parse or save
            return report, f"Sync stopped at {target}: {str(exc) or exc.__class__.__name__}"
        added = entry["action"] == "add"
        report["targets"].append({"name": target, "added": [name] if added else [],
                                  "updated": [] if added else [name], "skipped": [], "pruned": [], "unsupported": []})
    return report, None


def _describe(cfg, tool: str, store: dict) -> dict | None:
    """A definition as the page may show it: transport, the redacted
    summary, and env and header names. Known secret values are swapped
    back to ``${NAME}`` first, as swe's inventory does."""
    if not isinstance(cfg, dict):
        return None
    from swe.mcp import cli as mcp_cli
    from swe.mcp.inventory import transport_of
    from swe.mcp.secrets import redact as redact_secrets, scrub_args, scrub_url

    canonical = mcp_cli.get_mcp_handler(tool).parse(cfg)
    command = canonical.get("command")
    url = canonical.get("url")
    return {
        "transport": transport_of(cfg, canonical),
        "summary": summary(redact_secrets(str(command), store) if command else None,
                           scrub_args(canonical.get("args") or [], store),
                           scrub_url(str(url), store) if url else None),
        "env_keys": sorted(str(k) for k in canonical.get("env") or {}),
        "header_keys": sorted(str(k) for k in canonical.get("headers") or {}),
    }


def _key_diff(before: dict, after: dict) -> dict:
    """Names gained, lost and whose value changes; values never leave."""
    return {
        "added": sorted(str(k) for k in after if k not in before),
        "removed": sorted(str(k) for k in before if k not in after),
        "changed": sorted(str(k) for k in after if k in before and after[k] != before[k]),
    }


def _plan_reply(plan: dict, report: dict, error: str | None) -> dict:
    """The plan per harness, by swe's own report: what each config gains,
    changes or loses, and the file it is in."""
    from swe.mcp import cli as mcp_cli

    store = plan["store"]
    name = plan["server"]
    done = {t["name"]: t for t in report["targets"]}
    harnesses = []
    for entry in plan["entries"]:
        target = entry["harness"]
        swe = done.get(target)
        if swe is None:
            outcome = "not_run"
        elif name in swe["added"]:
            outcome = "add"
        elif name in swe["updated"]:
            outcome = "replace"
        else:
            outcome = "skip"
        handler = mcp_cli.get_mcp_handler(target)
        before = handler.parse(entry["before"]) if isinstance(entry["before"], dict) else {}
        after = handler.parse(entry["after"]) if isinstance(entry["after"], dict) else {}
        path = entry["path"]
        kept = []
        if entry["action"] == "replace" and isinstance(entry["before"], dict):
            kept = sorted(str(k) for k in entry["before"]
                          if k not in mcp_cli._CANONICAL_SERVER_KEYS and k in entry["after"])
        was = _describe(entry["before"], target, store)
        now = _describe(entry["after"], target, store)
        harnesses.append({
            "harness": target,
            "action": entry["action"],
            "outcome": outcome,
            "path": _display_path(Path(path)),
            "creates": not Path(path).exists() if report["dry_run"] else False,
            "unverified": bool((mcp_cli.get_tool_config(target) or {}).get("unverified")),
            "before": was,
            "after": now,
            "command_changes": bool(was and now and (was["summary"], was["transport"])
                                    != (now["summary"], now["transport"])),
            "env": _key_diff(before.get("env") or {}, after.get("env") or {}),
            "headers": _key_diff(before.get("headers") or {}, after.get("headers") or {}),
            "kept": kept,
        })
    return {
        "server": name,
        "source": plan["source"],
        "from": plan["source_described"],
        "fingerprint": plan["fingerprint"],
        "report": report,
        "harnesses": harnesses,
        "files": [h["path"] for h in harnesses if h["outcome"] in {"add", "replace"}],
        "error": error,
    }


# ---------------------------------------------------------------- redaction
# A port of QuiverCore's McpRedaction and SecretText.maskingKeys: the parts
# of a command line or URL that may hold a secret, hidden as "…".

_SECRET_FLAG_ENDINGS = ("token", "key", "secret", "password", "passwd", "credential",
                        "credentials", "auth", "authorization", "header", "headers")
_HEADER_FLAG_ENDINGS = ("header", "headers")
_KEY_PREFIXES = ("sk-", "sk_", "pk_", "rk_", "ghp_", "gho_", "ghu_", "ghs_", "ghr_",
                 "github_pat_", "glpat-", "xoxa-", "xoxb-", "xoxp-", "xoxs-", "xapp-",
                 "AIza", "AKIA", "ASIA", "hf_", "gsk_", "xai-", "pplx-", "r8_", "npm_")
_KEY_RUN = re.compile(r"[A-Za-z0-9_\-]{16,}")
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_URL_START = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")
_QUOTES = "\"'"


def summary(command: str | None, args: list, url: str | None) -> str:
    """The command line or URL on one line, with anything that may be a
    secret hidden (``McpServer.summary``)."""
    if url:
        return redact_url(str(url))
    words = command_words(str(command or ""), [str(a) for a in args])
    return " ".join(redact_args(words)).strip()


def looks_like_key(text: str) -> bool:
    """16+ characters with a vendor prefix, mixed case, or digits among
    letters; an upper-case NAME_WITH_UNDERSCORES is a variable name."""
    if len(text) < 16 or not any(ch.isalpha() for ch in text):
        return False
    if text.startswith(_KEY_PREFIXES):
        return True
    lower = any(ch.islower() for ch in text)
    upper = any(ch.isupper() for ch in text)
    digit = any(ch.isdigit() for ch in text)
    return (lower and upper) or (lower and digit) or (digit and "_" not in text)


def masking_keys(text: str) -> str:
    return _KEY_RUN.sub(lambda m: "…" if looks_like_key(m.group(0)) else m.group(0), text)


def _bare(flag: str) -> str:
    return "".join(ch for ch in flag if ch not in "\"'\\")


def is_secret_flag(flag: str) -> bool:
    flag = _bare(flag)
    if not flag.startswith("-"):
        return False
    if flag == "-H":
        return True
    name = "".join(ch for ch in flag if ch not in "-_").lower()
    # The app's endings, and anywhere in the name swe's own credential
    # words (cookie, signature, bearer, passw...), whichever hides more.
    return bool(name) and (name.endswith(_SECRET_FLAG_ENDINGS)
                           or _SECRET_FLAG_RE.search(name) is not None)


def shell_words(line: str) -> list[str]:
    """Words split at unquoted whitespace, quotes and escapes kept; with an
    unclosed quote every space and quote splits, so no word hides a flag."""
    words: list[str] = []
    word = ""
    quote = None
    escaped = False
    for ch in line:
        if escaped:
            word += ch
            escaped = False
        elif ch == "\\" and quote != "'":
            word += ch
            escaped = True
        elif quote is not None:
            word += ch
            if ch == quote:
                quote = None
        elif ch in _QUOTES:
            word += ch
            quote = ch
        elif ch.isspace():
            if word:
                words.append(word)
            word = ""
        else:
            word += ch
    if quote is not None:
        return loose_words(line)
    if word:
        words.append(word)
    return words


def loose_words(line: str) -> list[str]:
    return [w for w in re.split(r"[\s\"']+", line) if w]


def command_words(command: str, args: list[str]) -> list[str]:
    """The command split like a shell, then each argument; an argument
    holding a flag and its value (``--token abc``) becomes two words."""
    words = shell_words(command)
    for arg in args:
        space = next((i for i, ch in enumerate(arg) if ch.isspace()), None)
        if arg.startswith("-") and space is not None and "=" not in arg[:space]:
            words.append(arg[:space])
            words.append(arg[space:].strip())
        else:
            words.append(arg)
    return words


def _secret_value(value: str, flag: str) -> str:
    flag = _bare(flag)
    header = flag == "-H" or flag.lower().endswith(_HEADER_FLAG_ENDINGS)
    if header and ":" in value:
        return value[:value.index(":") + 1] + "…"
    return "…"


def _quoted(arg: str) -> bool:
    return len(arg) >= 2 and arg[0] in _QUOTES and arg[-1] == arg[0]


def _url_word(arg: str) -> str | None:
    quote, inner = "", arg
    if _quoted(arg):
        quote, inner = arg[0], arg[1:-1]
    if not _URL_START.match(inner):
        return None
    space = next((i for i, ch in enumerate(inner) if ch.isspace()), None)
    if space is None:
        return quote + redact_url(inner) + quote
    return quote + redact_url(inner[:space]) + " …" + quote


def _line(text: str) -> str:
    words = shell_words(text)
    if words == [text]:
        words = loose_words(text)
    return " ".join(redact_args(words))


def redact_args(args: list[str]) -> list[str]:
    out: list[str] = []
    hide_next = None
    for arg in args:
        if hide_next is not None:
            out.append(_secret_value(arg, hide_next))
            hide_next = None
        elif "=" in arg and is_secret_flag(arg[:arg.index("=")]):
            flag = arg[:arg.index("=")]
            out.append(flag + "=" + _secret_value(arg[arg.index("=") + 1:], flag))
        elif is_secret_flag(arg):
            out.append(arg)
            hide_next = arg
        elif (assign := _ENV_ASSIGN.match(arg)) is not None:
            # An environment assignment, as `env NAME=value cmd` takes.
            out.append(assign.group(0) + "…")
        elif (redacted := _url_word(arg)) is not None:
            out.append(redacted)
        elif _quoted(arg):
            # A quoted word may be a whole command, as `bash -c '…'` takes.
            out.append(arg[0] + _line(arg[1:-1]) + arg[0])
        elif any(ch.isspace() for ch in arg):
            out.append(_line(arg))
        elif "://" in arg:
            out.append(redact_url(arg))
        else:
            out.append(masking_keys(arg))
    return out


def redact_url(url: str) -> str:
    """The URL with userinfo, query values and fragment hidden, and any
    key-like path segment too."""
    rest, out = url, ""
    if "://" in rest:
        cut = rest.index("://") + 3
        out, rest = rest[:cut], rest[cut:]
    end = next((i for i, ch in enumerate(rest) if ch in "/?#"), len(rest))
    authority, rest = rest[:end], rest[end:]
    if "@" in authority:
        out += "…@"
        authority = authority[authority.rindex("@") + 1:]
    out += authority
    fragment = None
    if "#" in rest:
        rest, fragment = rest.split("#", 1)
    query = None
    if "?" in rest:
        rest, query = rest.split("?", 1)

    def segment(seg: str) -> str:
        decoded = unquote(seg)
        if decoded != seg and masking_keys(decoded) != decoded:
            return "…"
        return masking_keys(seg)

    out += "/".join(segment(s) for s in rest.split("/"))
    if query is not None:
        out += "?" + "&".join(
            "" if not pair else (pair[:pair.index("=") + 1] + "…" if "=" in pair else "…")
            for pair in query.split("&"))
    if fragment:
        out += "#…"
    return out


# ---------------------------------------------------------------- folders
# A port of QuiverCore's McpServerRepo: the server checkouts in the managed
# folder, which inventory servers run from each, and each one's git state.

SERVER_MARKERS = (".git", "package.json", "pyproject.toml", "go.mod", "Cargo.toml",
                  "Dockerfile", "setup.py", "requirements.txt", "server.py", "main.py",
                  "index.js")


def _servers_root(ctx) -> Path:
    from swe.paths import mcp_servers_dir_for

    return mcp_servers_dir_for(getattr(ctx, "home", None))


def _display_path(path: Path, home: Path | None = None) -> str:
    """``path`` with ``home`` as ``~``. Harness configs live in the
    process's home (``mcp_cli`` expands ``~``), so that is the default;
    the servers folder lives in the hub's own."""
    return tilde(path, home)


def _child_folders(path: Path) -> list[Path]:
    """The non-hidden folders right inside ``path``. A symlink is judged by
    itself, not its target, as the Mac app's directory listing does."""
    out = []
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    out.append(Path(entry.path))
            except OSError:
                continue
    return out


def scan(root: Path) -> list[dict]:
    """The server folders under ``root``: a child with no marker file is a
    group folder whose children are the servers; any other child is a
    server. Ungrouped first, then groups, names case-insensitively.
    A missing root is empty; a file, or a listing that fails, raises."""
    if not root.exists():
        return []
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is a file, not a folder.")
    try:
        children = _child_folders(root)
    except OSError as exc:
        raise OSError(f"Couldn't read {root}: {exc.strerror or exc}.") from exc
    found = []
    for child in children:
        if not any((child / marker).exists() for marker in SERVER_MARKERS):
            try:
                members = _child_folders(child)
            except OSError:
                members = []  # an unreadable group just shows no servers
            found += [{"name": m.name, "group": child.name, "path": m} for m in members]
        else:
            found.append({"name": child.name, "group": None, "path": child})
    found.sort(key=lambda f: (f["group"] is not None, (f["group"] or "").casefold(),
                              f["name"].casefold()))
    return found


def _unquote_word(word: str) -> str:
    """A word as exec receives it: quotes removed, backslash escapes undone
    (literal inside single quotes)."""
    out, quote, escaped = "", None, False
    for ch in word:
        if escaped:
            out += ch
            escaped = False
        elif ch == "\\" and quote != "'":
            escaped = True
        elif quote is not None:
            if ch == quote:
                quote = None
            else:
                out += ch
        elif ch in _QUOTES:
            quote = ch
        else:
            out += ch
    if escaped:
        out += "\\"
    return out


def _inside(word: str, base: str) -> bool:
    if word.startswith("file://"):
        try:
            word = unquote(urlsplit(word).path)
        except ValueError:
            return False
    if not word.startswith("/"):
        return False
    path = os.path.normpath(word)
    return path == base or path.startswith(base.rstrip("/") + "/")


def hub_usage(folders: list[dict], servers: list[dict]) -> dict[str, list[str]]:
    """Each folder path -> the servers whose command or an argument is an
    absolute path at or under it, so ``node …/linear-mcp/build/index.js``
    matches while ``mcp-hnx`` never answers for ``mcp-hn``."""
    usage: dict[str, list[str]] = {}
    for folder in folders:
        base = os.path.normpath(str(folder["path"]))
        for server in servers:
            words = [str(a) for a in server.get("args") or []]
            command = server.get("command")
            if command:
                words.append(str(command))
                words += [_unquote_word(w) for w in shell_words(str(command))]
            if any(_inside(w, base) for w in words):
                usage.setdefault(base, []).append(server["name"])
    return usage


def parse_git(porcelain: str) -> dict:
    """``git status --porcelain=v1 --branch`` boiled down to a row's needs."""
    lines = [ln for ln in porcelain.split("\n") if ln]
    if not lines or not lines[0].startswith("## "):
        return git_state(is_repo=False)
    head = lines[0][3:]
    ahead = behind = 0
    if head.endswith("]") and " [" in head:
        open_at = head.rindex(" [")
        for part in head[open_at + 2:-1].split(","):
            part = part.strip()
            if part.startswith("ahead "):
                ahead = _int(part[6:])
            if part.startswith("behind "):
                behind = _int(part[7:])
        head = head[:open_at]
    changes = len(lines) > 1
    if head.startswith("HEAD (no branch)"):
        return git_state(None, ahead, behind, changes)
    if head.startswith("No commits yet on "):
        return git_state(head[18:], ahead, behind, changes)
    if "..." in head:
        head = head[:head.index("...")]
    return git_state(head, ahead, behind, changes)


def _int(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def git_state(branch: str | None = None, ahead: int = 0, behind: int = 0,
              has_changes: bool = False, is_repo: bool = True) -> dict:
    return {"branch": branch, "ahead": ahead, "behind": behind,
            "has_changes": has_changes, "is_repo": is_repo}


def read_git(path: Path) -> dict:
    """The folder's git state. Only a folder with its own ``.git`` counts,
    or ``git status`` would answer for an enclosing repository. Optional
    locks are off, so the read never rewrites the index. Anything off
    (git missing, an error, five seconds gone) is not a repo."""
    if not (path / ".git").exists():
        return git_state(is_repo=False)
    git = shutil.which("git")
    if not git:
        return git_state(is_repo=False)
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        done = subprocess.run(
            [git, "-C", str(path), "status", "--porcelain=v1", "--branch"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_S, env=env,
            stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return git_state(is_repo=False)
    if done.returncode != 0 or "not a git repository" in done.stderr:
        return git_state(is_repo=False)
    return parse_git(done.stdout)


def folder_scan(root: Path, servers: list[dict],
                home: Path | None = None) -> dict:
    """The servers folder as the page shows it: each checkout with its git
    state and the servers that run from it (``used_by``), or why it could
    not be read. ``servers`` is the whole inventory, so a server the page
    filters out still marks its folder as used."""
    out = {"root": _display_path(root, home), "exists": root.exists(), "error": None,
           "items": []}
    try:
        found = scan(root)
    except OSError as exc:
        out["error"] = str(exc)
        return out
    usage = hub_usage(found, servers)
    with ThreadPoolExecutor(max_workers=GIT_WORKERS, thread_name_prefix="mcp-git") as pool:
        states = list(pool.map(lambda f: read_git(f["path"]), found))
    for folder, git in zip(found, states):
        path = os.path.normpath(str(folder["path"]))
        out["items"].append({
            "name": folder["name"],
            "group": folder["group"],
            "path": _display_path(Path(path), home),
            "git": git,
            "used_by": usage.get(path, []),
        })
    return out
