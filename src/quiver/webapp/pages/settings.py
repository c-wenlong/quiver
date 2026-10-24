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
- ``providers``: Providers › Inference, read only. ``swe providers
  list``'s rows: each provider and whether the API keys file sets its
  key, the value masked by ``mask_key``. A raw key never leaves here.
- ``paths``: where the hub's swe, API keys file and MCP servers folder are.
- ``?part=github``: Integrations › GitHub, ``gh``'s login and the synced
  repo catalog, asked for apart because ``gh auth status`` can be slow.

Writes, both behind the device bearer and the Origin check:

- ``pair``: mint a one-time pair token (``swe remote pair --json``) and
  return its browser link and ``quiver://pair`` deep link, each with a QR
  code drawn here as SVG (``_qr``), since the web app loads no library.
- ``revoke``: ``{"device_id"}`` signs one paired device out of the hub
  (``swe remote devices --revoke``). The device must be one ``get``
  lists. Revoking the device that sent the request also needs
  ``"confirm_this_device": true``: without it the answer is 409, so a
  stray click can't lock this browser out.

The caller is either a paired device (``ctx.device_id`` is its id in
devices.json) or the hub owner's own tailnet device, let in without
pairing (``ctx.device_id`` is ``"tailnet:<node id>"``). ``remote.caller``
says which; a tailnet caller has no row in the device list and nothing
of its own to revoke, and an id of that form is refused outright.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from quiver import __version__
from quiver.webapp.pages import PageError

ACTIONS = ("pair", "revoke")
DEVICE_ID_MAX = 64
# The hub's id for the owner's own tailnet device (hub._owner_device).
TAILNET_PREFIX = "tailnet:"

# Pairing's own file lock already makes each change atomic; this one runs
# the page's writes in turn so a revoke and a list never interleave and
# two mints answer in the order they were asked.
_WRITE_LOCK = threading.Lock()


def get(ctx, args: dict) -> dict:
    part = args.get("part") or ""
    if part == "github":
        return {"github": _github(ctx, refresh=args.get("refresh") == "1")}
    if part:
        raise PageError(400, "bad_part", f"unknown part: {part}")
    home = _home(ctx)
    return {
        "machine": (ctx.machine or {}).get("name"),
        "version": __version__,
        "remote": _remote(ctx),
        "providers": _providers(ctx, home),
        "paths": _paths(ctx, home),
    }


def post(ctx, action: str, body: dict) -> dict:
    if action not in ACTIONS:
        raise PageError(404, "unknown_action", f"unknown settings action: {action}")
    with _WRITE_LOCK:
        if action == "pair":
            return {"ticket": _mint(ctx)}
        return _revoke(ctx, body)


# -- remote ---------------------------------------------------------------


def _remote(ctx) -> dict:
    from quiver.remote import pairing

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
        "devices": _devices(ctx, pairing.load_devices(ctx.home)),
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


def _devices(ctx, devices: dict) -> list[dict]:
    """``swe remote status --json``'s device rows, plus who is asking."""
    return [
        {
            "id": did,
            "name": row.get("name"),
            "created_at": row.get("created_at"),
            "last_seen_at": row.get("last_seen_at"),
            "revoked": bool(row.get("revoked_at")),
            "paired_via": row.get("paired_via") or "token",
            "this_device": did == ctx.device_id,
        }
        for did, row in devices.items()
    ]


def _hub_config(ctx) -> dict:
    from quiver.remote.hub import load_hub_config

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
    from quiver.remote import catalog as catalog_mod
    from quiver.remote.hub import DEFAULT_PORT

    port = saved.get("port") if isinstance(saved.get("port"), int) else DEFAULT_PORT
    return catalog_mod.tailscale_ip() or "127.0.0.1", port, "http"


def _mint(ctx) -> dict:
    from quiver.remote import pairing
    from quiver.webapp.pages import _qr

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
            "app": _qr.svg(deep_link, "M", label="Pairing QR code for the Quiver app"),
        },
    }


def _revoke(ctx, body: dict) -> dict:
    from quiver.remote import pairing

    device_id = body.get("device_id")
    if not isinstance(device_id, str) or not device_id or len(device_id) > DEVICE_ID_MAX:
        raise PageError(400, "bad_device", "device_id must be a paired device's id")
    if device_id.startswith(TAILNET_PREFIX):
        # The owner's tailnet sign-in isn't a pairing: there is no row to
        # revoke, and this must never read as signing anything out.
        raise PageError(400, "not_paired",
                        "That's a Tailscale sign-in, not a paired device. There is nothing to revoke.")
    devices = pairing.load_devices(ctx.home)
    row = devices.get(device_id)
    if row is None:
        raise PageError(404, "unknown_device", "No paired device has that id.")
    if row.get("revoked_at"):
        raise PageError(409, "already_revoked", f"{row.get('name') or 'That device'} is already signed out.")
    if device_id == ctx.device_id and body.get("confirm_this_device") is not True:
        raise PageError(409, "confirm_this_device",
                        "That's the device you're using. Confirm to sign it out of the hub.")
    try:
        revoked = pairing.revoke_device(ctx.home, device_id)
    except OSError as exc:
        raise PageError(500, "ssh_key_left",
                        f"Signed out, but its SSH key is still in ~/.ssh/authorized_keys: {exc}") from exc
    if not revoked:
        raise PageError(404, "unknown_device", "No paired device has that id.")
    devices = pairing.load_devices(ctx.home)
    return {
        "revoked": device_id,
        "this_device": device_id == ctx.device_id,
        "devices": _devices(ctx, devices),
    }


# -- providers --------------------------------------------------------------


def _providers(ctx, home: Path) -> dict:
    from quiver.paths import provider_keys_path_for
    from quiver.providers.discover import discover_provider_keys

    keys = provider_keys_path_for(ctx.home)
    out = {"path": _display(keys, home), "exists": keys.exists(), "rows": [], "error": None}
    try:
        registry = _provider_registry(ctx)
        found = discover_provider_keys(registry, keys)
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        # A corrupt providers.json, or an entry whose fields have the
        # wrong types (key_filename: 123): say so in this card and keep
        # the rest of Settings, pairing included, working.
        out["error"] = f"Can't read the providers: {exc}"
        return out
    rows = []
    for r in found:
        info = r["info"]
        env_vars = [str(e) for e in (r.get("env_vars") or []) if isinstance(e, str)]
        has_key = bool(r.get("raw_key"))
        rows.append({
            "id": r["name"],
            "name": str(info.get("name") or r["name"]),
            "env_var": r.get("matched_env") or (env_vars[0] if env_vars else None),
            "env_vars": env_vars,
            "has_key": has_key,
            # mask_key's form, never the value: first 8, last 4 and the length.
            "masked": r["masked"] if has_key else None,
            # Only a web link: the page puts it in an href.
            "url": _web_url(info.get("url")),
            "description": info.get("description") if isinstance(info.get("description"), str) else None,
        })
    rows.sort(key=lambda r: (r["name"] or r["id"]).lower())
    out["rows"] = rows
    return out


def _web_url(value) -> str | None:
    if isinstance(value, str) and value.lower().startswith(("https://", "http://")):
        return value
    return None


def _provider_registry(ctx) -> dict:
    """``load_registry``'s merge of providers.json and the defaults, read
    without saving: a page read must not seed or rewrite the file."""
    from quiver.configuration import read_json_object
    from quiver.paths import config_dir_for
    from quiver.providers import registry as preg
    from quiver.providers.defaults import DEFAULT_PROVIDERS

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
    from quiver import paths
    from quiver.github import catalog
    from quiver.webapp.pages import _github as gh_shared

    env = gh_shared.probe(ctx.home, refresh=refresh)
    return {
        "env": gh_shared.env_payload(env),
        "catalog": catalog.status(paths.github_catalog_for(ctx.home)),
    }


def _paths(ctx, home: Path) -> dict:
    from quiver.paths import mcp_servers_dir_for, provider_keys_path_for

    swe = shutil.which("swe")
    return {
        "swe": _display(Path(swe), home) if swe else None,
        "api_keys": _display(provider_keys_path_for(ctx.home), home),
        "mcp_servers": _display(mcp_servers_dir_for(ctx.home), home),
    }


def _home(ctx) -> Path:
    return Path(ctx.home) if ctx.home is not None else Path.home()


def _display(path: Path, home: Path) -> str:
    """``path`` with the hub's home written as ``~``."""
    s, h = str(path), str(home)
    if s == h:
        return "~"
    if s.startswith(h.rstrip("/") + "/"):
        return "~" + s[len(h.rstrip("/")):]
    return s
