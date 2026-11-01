"""Settings: ``GET /v1/app/settings`` and ``POST /v1/app/settings/<action>``.

The Mac app's Settings page, for the machine the hub runs on. Most of the
Mac page configures the Mac app itself (Dock, login item, terminal,
notifications, updates, Keychain services); the web page shows those
greyed out and draws them in the browser. What it reads here is what
belongs to the hub machine:

- ``remote``: the Integrations › Remote card. The hub's status (what
  ``swe remote status --json`` reports, read in process since this is the
  hub), its paired devices with ``this_device`` marking the browser's own
  credential, and the tailnet pair requests waiting for approval (no
  codes or secrets, as ``swe remote devices --requests`` lists them).
- ``providers``: Providers › Inference, as the Mac app's: ``keys``, every
  ``export`` in the API keys file in file order with its ``# Provider``
  heading and the value masked (``APIKeyMask``'s form, never the value),
  and ``catalog``, ``swe providers list``'s providers for the Add key
  picker (name, variables, where to get a key). ``stamp`` fingerprints
  the file as read, so a write over a newer file is refused.
- ``paths``: where the hub's swe, API keys file and MCP servers folder are.
- ``session_search``: Experimental › Session search, the hub's transcript
  index as ``swe session index --status --json`` reports it (whether the
  hub keeps it current, a pass running now, the last pass or its error,
  and the session and message totals).
- ``?part=github``: Integrations › GitHub, ``gh``'s login and the synced
  repo catalog, asked for apart because ``gh auth status`` can be slow.
- ``?part=remote_setup``: the steps from nothing to a paired phone, as
  ``swe remote setup --json`` reports them for this machine, asked for
  apart because it calls tailscale and probes the hub's own https
  address. Read only: the page never runs ``--apply``, which can move the
  very hub serving it.
- ``?part=remote``: just the remote card (devices, pair requests), which
  the section polls while it is open.

Writes, all behind the device bearer and the Origin check:

- ``pair``: mint a one-time pair token (``swe remote pair --json``) and
  return its browser link and ``quiver://pair`` deep link, each with a QR
  code drawn here as SVG (``_qr``), since the web app loads no library.
- ``session_search``: ``{"enabled": bool}`` is ``swe session index
  --on``/``--off``. Turning it on also starts a pass at once on a
  background thread, rather than waiting for the hub loop's next cycle;
  the answer is the new ``session_search`` status, and the page polls
  until ``running`` clears.
- ``sync``: Integrations › GitHub › Sync now, ``swe gh sync --json`` in
  process (``github.manage.sync_catalog``) with the hub's ``gh`` login,
  into the hub's catalog. One runs at a time (a second is a 409). The
  answer is ``{"github", "failed"}``: the GitHub card read again and the
  owners whose listing failed, whose old rows the catalog keeps.
- ``key_set``: ``{"name", "value", "stamp"}`` replaces a key in place,
  or with ``"provider"`` (a catalog id whose variables include ``name``)
  adds one under that provider's heading, as the Mac app's Add key does.
  ``key_remove``: ``{"name", "stamp"}`` deletes one. Both write through
  ``swe.providers.keyfile`` (backup, mode 600, the secrets folder
  sealed) and answer the new ``providers``, never a value.
- ``key_value``: ``{"name"}`` answers ``{"name", "value"}``, the one reply
  that carries a value, for the eye and copy buttons.
- ``revoke``: ``{"device_id"}`` signs one paired device out of the hub
  (``swe remote devices --revoke``), or, for a ``tailnet:<node>`` id
  from ``remote.signins``, stops one of the owner's tailnet devices
  getting in without pairing. The device must be one ``get`` lists.
  Revoking the device that sent the request also needs
  ``"confirm_this_device": true``: without it the answer is 409, so a
  stray click can't lock this browser out.

The caller is either a paired device (``ctx.device_id`` is its id in
devices.json) or the hub owner's own tailnet device, let in without
pairing (``ctx.device_id`` is ``"tailnet:<node id>"``). ``remote.caller``
says which; a tailnet caller has no row in the device list but one in
``remote.signins``, the owner's tailnet devices the hub has let in.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from swe import __version__
from swe.webapp.pages import PageError, home_path, tilde, web_url
from swe.webapp.pages._secrets import mask

ACTIONS = ("pair", "revoke", "session_search", "sync", "key_set", "key_remove", "key_value")
# A pasted key, generously: the longest real ones are a few hundred bytes.
VALUE_MAX = 16384
# The hub's id for the owner's own tailnet device (hub._owner_device).
TAILNET_PREFIX = "tailnet:"

# One catalog sync at a time, held without waiting like the Doctor
# page's Run: a second Sync now while one runs is refused, not queued.
_SYNC_LOCK = threading.Lock()

# Pairing's own file lock already makes each change atomic; this one runs
# the page's writes in turn so a revoke and a list never interleave and
# two mints answer in the order they were asked.
_WRITE_LOCK = threading.Lock()


def get(ctx, args: dict) -> dict:
    part = args.get("part") or ""
    if part == "github":
        return {"github": _github(ctx, refresh=args.get("refresh") == "1")}
    if part == "remote_setup":
        from swe.remote import setup

        return {"remote_setup": setup.evaluate(setup.gather(home=ctx.home))}
    if part == "remote":
        return {"remote": _remote(ctx)}
    if part:
        raise PageError(400, "bad_part", f"unknown part: {part}")
    home = home_path(ctx)
    return {
        "machine": (ctx.machine or {}).get("name"),
        "version": __version__,
        "remote": _remote(ctx),
        "providers": _providers(ctx, home),
        "paths": _paths(ctx, home),
        "session_search": _session_search(home),
    }


def post(ctx, action: str, body: dict) -> dict:
    if action not in ACTIONS:
        raise PageError(404, "unknown_action", f"unknown settings action: {action}")
    if action == "sync":
        # Outside the write lock: a sync lists every owner's repos and can
        # take a minute, and pairing must not wait on it.
        return _sync(ctx)
    with _WRITE_LOCK:
        if action == "pair":
            return {"ticket": _mint(ctx)}
        if action == "session_search":
            return _set_session_search(ctx, body)
        if action in ("key_set", "key_remove", "key_value"):
            return _key_action(ctx, action, body)
        return _revoke(ctx, body)


# -- session search -------------------------------------------------------


def _session_search(home: Path) -> dict:
    from swe.sessions import search_writer

    return search_writer.status(home)


def _set_session_search(ctx, body: dict) -> dict:
    from swe.sessions import search_writer

    home = home_path(ctx)
    if body.get("resync") is True:
        _kick_pass(home)
        return {"session_search": _session_search(home)}
    if body.get("clear") is True:
        try:
            search_writer.clear_index(home)
        except search_writer.IndexBusy:
            raise PageError(409, "busy",
                          "an index pass is running; clear once it finishes")
        # An enabled index rebuilds from scratch, as the Mac's clear does.
        if search_writer.status(home).get("enabled"):
            _kick_pass(home)
        return {"session_search": _session_search(home)}
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise PageError(400, "bad_enabled", "enabled must be true or false")
    search_writer.set_enabled(home, enabled)
    if enabled:
        _kick_pass(home)
    return {"session_search": _session_search(home)}


def _kick_pass(home: Path) -> None:
    from swe.sessions import search_writer

    threading.Thread(target=_first_pass, args=(home,),
                     name="settings-search-index", daemon=True).start()
    # Give the thread the lock before answering, so the page sees
    # ``running`` and starts polling.
    for _ in range(20):
        if search_writer.is_running(home):
            break
        time.sleep(0.01)


def _first_pass(home: Path) -> None:
    from swe.sessions import search_writer

    try:
        search_writer.run_pass(home)
    except Exception:
        # IndexBusy: the hub loop or a CLI pass already has it. Anything
        # else run_pass saved as last_error, which the Settings row shows.
        pass


# -- remote ---------------------------------------------------------------


def _remote(ctx) -> dict:
    from swe.remote import pairing
    from swe.remote import devices as remote_devices

    state = ctx.state
    saved = _hub_config(ctx)
    host, port = saved.get("host"), saved.get("port")
    public_url = str(saved.get("public_url") or "") or None
    hub_addr = f"{host}:{port}" if host and isinstance(port, int) else None
    return {
        "hub": {
            # This page is served by the hub, so the hub is up: the Mac
            # card's "No hub reachable" can't be reached from here.
            "ok": True,
            "version": __version__,
            "uptime_s": round(time.time() - state.started_at),
            "machine": state.machine,
        },
        "caller": _caller(ctx),
        "hub_addr": hub_addr,
        "public_url": public_url,
        "devices": remote_devices.rows(
            ctx.home, ctx.device_id, pairing.load_devices(ctx.home)),
        "signins": remote_devices.signin_rows(ctx.home, ctx.device_id),
        "requests": pairing.list_requests(ctx.home),
        "token_ttl_s": int(pairing.TOKEN_TTL_S),
    }


def _caller(ctx) -> dict:
    """How this browser got in: a paired device, or the hub owner's own
    tailnet device, which has no pairing to show or revoke."""
    did = ctx.device_id or ""
    if did.startswith(TAILNET_PREFIX):
        return {"kind": "tailnet", "node": did[len(TAILNET_PREFIX):]}
    return {"kind": "device" if did else "none", "device_id": did or None}


def _hub_config(ctx) -> dict:
    from swe.remote.hub import load_hub_config

    return load_hub_config(ctx.home)


def _phone_address(ctx) -> tuple[str, int, str]:
    """``(host, port, scheme)`` another device should dial, as ``swe
    remote pair`` picks it: the public URL when the hub runs behind
    ``tailscale serve``, else the address it advertises, else the tailnet
    IP on the saved or default port."""
    saved = _hub_config(ctx)
    base = str(saved.get("public_url") or "") or str(getattr(ctx.state, "base_url", "") or "")
    if base:
        parts = urlsplit(base)
        default = 443 if parts.scheme == "https" else 80
        if parts.hostname:
            return parts.hostname, parts.port or default, parts.scheme or "http"
    from swe.remote import catalog as catalog_mod
    from swe.remote.hub import DEFAULT_PORT

    port = saved.get("port") if isinstance(saved.get("port"), int) else DEFAULT_PORT
    return catalog_mod.tailscale_ip() or "127.0.0.1", port, "http"


def _mint(ctx) -> dict:
    from swe.remote import pairing
    from swe.webapp.pages import _qr

    host, port, scheme = _phone_address(ctx)
    token = pairing.mint_pair_token(ctx.home)
    url = pairing.pair_url(host, port, token, "", scheme)
    deep_link = pairing.pair_payload(host, port, token, "", scheme)
    ttl = int(pairing.TOKEN_TTL_S)
    return {
        "host": host,
        "port": port,
        "token": token,
        "name": "",
        "url": url,
        "deep_link": deep_link,
        "expires_in_s": ttl,
        "expires_at": time.time() + ttl,
        # Error correction M, as the Mac app's CIQRCodeGenerator draws it.
        "qr": {
            "web": _qr.svg(url, "M", label="Pairing QR code for the browser"),
            "app": _qr.svg(deep_link, "M", label="Pairing QR code for the SWE app"),
        },
    }


def _revoke(ctx, body: dict) -> dict:
    """``{"device_id", "confirm_this_device"?}`` signs one paired device
    out of the hub — the same refusal set the remote protocol's
    ``POST /v1/devices/revoke`` applies (``remote/devices.py``)."""
    from swe.remote import devices as remote_devices

    try:
        return remote_devices.revoke(ctx.home, ctx.device_id, body)
    except remote_devices.Refused as exc:
        raise PageError(exc.status, exc.code, exc.message) from exc


# -- providers --------------------------------------------------------------


def _providers(ctx, home: Path) -> dict:
    from swe.paths import provider_keys_path_for
    from swe.providers import keyfile

    path = provider_keys_path_for(ctx.home)
    out = {"path": _display(path, home), "exists": path.exists(), "stamp": None,
           "keys": [], "catalog": [], "error": None}
    set_vars: set[str] | None = None
    try:
        keys, data = keyfile.load(path)
    except keyfile.KeyFileError as exc:
        out["error"] = exc.message
    else:
        out["stamp"] = _stamp(data)
        out["keys"] = [{
            "name": e.name, "provider": e.provider, "has_value": bool(e.value),
            # APIKeyMask's form, never the value.
            "masked": mask(e.value),
        } for e in keys.keys]
        set_vars = {e.name for e in keys.keys if e.value}
    try:
        registry = _provider_registry(ctx)
        out["catalog"] = [_catalog_row(pid, info, set_vars)
                          for pid, info in registry.items()]
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        # A corrupt providers.json, or an entry whose fields have the
        # wrong types (key_filename: 123): say so in this card and keep
        # the rest of Settings, pairing included, working.
        out["error"] = f"Can't read the providers: {exc}"
    return out


def _catalog_row(pid: str, info: dict, set_vars: set[str] | None = None) -> dict:
    env_vars = info.get("env_vars")
    if not isinstance(env_vars, list) or not all(isinstance(e, str) for e in env_vars):
        raise TypeError(f"{pid}: env_vars must be a list of names")
    name = info.get("name")
    if name is not None and not isinstance(name, str):
        raise TypeError(f"{pid}: name must be text")
    return {
        "id": pid,
        "name": name or pid,
        "env_vars": env_vars,
        # The catalogue sheet's "Set" badge: the file covers any variable.
        "has_key": bool(set_vars) and any(v in set_vars for v in env_vars),
        # Only a web link: the page puts it in an href.
        "url": web_url(info.get("url")),
    }


# Keyed per hub process, like the MCP page's plan fingerprint: the stamp
# says whether the file changed without being a hash anyone could test a
# guessed key against.
_STAMP_KEY = os.urandom(32)


def _stamp(data: bytes | None) -> str:
    return hmac.new(_STAMP_KEY, b"none" if data is None else b"file:" + data, hashlib.sha256).hexdigest()


def _key_action(ctx, action: str, body: dict) -> dict:
    from swe.paths import provider_keys_path_for
    from swe.providers import keyfile

    name = body.get("name")
    if not isinstance(name, str) or not keyfile.is_valid_name(name):
        raise PageError(422, "bad_name", "name must be a variable name")
    path = provider_keys_path_for(ctx.home)
    try:
        keys, data = keyfile.load(path)
    except keyfile.KeyFileError as exc:
        raise PageError(409, exc.code, exc.message) from None
    present = {e.name: e for e in keys.keys}
    if action == "key_value":
        if name not in present:
            raise PageError(404, "not_found", f"{name} isn't in the file any more.")
        return {"name": name, "value": present[name].value}
    if body.get("stamp") != _stamp(data):
        raise PageError(409, "changed_on_disk", "The keys file changed since this page read it. Reload, then make the change again.")
    try:
        if action == "key_remove":
            keys.remove(name)
        else:
            keys.set(name, _key_value(body), _key_heading(ctx, body, name, keys, present))
        keyfile.save(path, keys, data, home=ctx.home)
    except keyfile.KeyFileError as exc:
        status = {"not_found": 404, "changed_on_disk": 409, "duplicate": 409,
                  "unreadable": 409, "unwritable": 500}.get(exc.code, 422)
        raise PageError(status, exc.code, exc.message) from None
    return {"providers": _providers(ctx, home_path(ctx))}


def _key_value(body: dict) -> str:
    value = body.get("value")
    if not isinstance(value, str) or len(value) > VALUE_MAX:
        raise PageError(422, "bad_value", "value must be text")
    # A paste often brings a trailing newline; one inside is refused below.
    value = value.strip()
    if not value:
        raise PageError(422, "bad_value", "Paste a key first.")
    return value


def _key_heading(ctx, body: dict, name: str, keys, present: dict) -> str:
    """The heading a set goes under. A key already in the file stays where
    it is; a new one needs the catalog provider it belongs to, and goes
    under ``APIKeyProviders.heading(for:in:)``'s pick."""
    if name in present:
        return present[name].provider
    pid = body.get("provider")
    if not isinstance(pid, str) or not pid:
        raise PageError(422, "bad_provider", "Pick a provider for the new key.")
    try:
        info = _provider_registry(ctx).get(pid)
        row = _catalog_row(pid, info) if isinstance(info, dict) else None
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        raise PageError(409, "bad_registry", f"Can't read the providers: {exc}") from None
    if row is None:
        raise PageError(404, "unknown_provider", f"unknown provider: {pid}")
    if name not in row["env_vars"]:
        raise PageError(422, "bad_name", f"{row['name']} keys are saved as {', '.join(row['env_vars'])}.")
    for entry in keys.keys:
        if entry.name in row["env_vars"]:
            return entry.provider
    wanted = {_fold(row["name"]), _fold(pid)}
    for entry in keys.keys:
        if _fold(entry.provider) in wanted:
            return entry.provider
    return row["name"]


def _fold(text: str) -> str:
    """"Together AI", "together_ai" and "TOGETHER AI" all fold together."""
    return text.lower().replace(" ", "").replace("_", "")


def _provider_registry(ctx) -> dict:
    """``load_registry``'s merge of providers.json and the defaults, read
    without saving: a page read must not seed or rewrite the file."""
    from swe.configuration import read_json_object
    from swe.paths import config_dir_for
    from swe.providers import registry as preg
    from swe.providers.defaults import DEFAULT_PROVIDERS

    path = config_dir_for(ctx.home) / "providers.json"
    user = preg._validated_entries(read_json_object(path), path) if path.exists() else {}
    removed = set(user.get(preg._REMOVED_KEY) or [])
    merged = {
        name: preg._hydrate(dict(info))
        for name, info in user.items()
        if name != preg._REMOVED_KEY and name not in removed
    }
    for name, info in DEFAULT_PROVIDERS.items():
        if name not in removed and name not in merged:
            merged[name] = preg._hydrate(dict(info))
    return merged


# -- github, paths ------------------------------------------------------------


def _github(ctx, refresh: bool = False) -> dict:
    from swe import paths
    from swe.github import catalog
    from swe.github import shared as gh_shared

    env = gh_shared.probe(ctx.home, refresh=refresh)
    return {
        "env": gh_shared.env_payload(env),
        "catalog": catalog.status(paths.github_catalog_for(ctx.home)),
    }


def _sync(ctx) -> dict:
    """Sync now: ``swe gh sync --json`` in process, for the hub's home.
    A missing or signed-out ``gh`` is a 409 in its own setup words. The
    answer is the GitHub card again (``env`` and the new ``catalog``)
    plus ``failed``, the owners that did not list, whose old rows stay."""
    from swe import paths
    from swe.github import catalog, gh, manage
    from swe.github import shared as gh_shared

    if not _SYNC_LOCK.acquire(blocking=False):
        raise PageError(409, "busy", "A sync is already running. Wait for it, then try again.")
    try:
        # Never the cached probe: its login names the account the catalog
        # is saved under, and a gh auth switch seconds ago must count.
        env = gh.probe_env()
        if env.status != "ok":
            raise PageError(409, "gh_" + env.status.replace("-", "_"), gh.env_error(env))
        summary = manage.sync_catalog(env, ctx.home)
        if summary["error"]:
            raise PageError(502, "sync_failed", summary["error"])
        return {"github": {"env": gh_shared.env_payload(env),
                           "catalog": catalog.status(paths.github_catalog_for(ctx.home))},
                "failed": summary["failed"]}
    finally:
        _SYNC_LOCK.release()


def _paths(ctx, home: Path) -> dict:
    from swe.paths import mcp_servers_dir_for, provider_keys_path_for

    swe = shutil.which("swe")
    return {
        "swe": _display(Path(swe), home) if swe else None,
        "api_keys": _display(provider_keys_path_for(ctx.home), home),
        "mcp_servers": _display(mcp_servers_dir_for(ctx.home), home),
    }


def _display(path: Path, home: Path) -> str:
    """``path`` with the hub's home written as ``~``."""
    return tilde(path, home)
