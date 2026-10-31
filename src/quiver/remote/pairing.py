"""QR payload, one-time token → device credential.

Tailscale membership is the transport boundary; this file is the app
layer gate on top of it. ``swe remote pair`` mints a one-time,
short-lived token into ``pair_tokens.json`` (which the hub polls), and
``POST /v1/pair`` exchanges it for a device credential stored hashed in
``devices.json`` (0600). Revoking a lost phone is deleting one row, not
touching the tailnet.

The token guards the pairing *window* only: only a tailnet device can
reach ``/v1/pair`` at all, so the QR's real job is carrying the host and
token into the phone's browser without typing.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import quote

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote.protocol import url_host

# A pairing token is worthless within minutes of `swe remote pair`.
TOKEN_TTL_S = 300

# A device row's ``role`` is what its credential may do. ``phone`` is
# the default — and the effective role of every row minted before roles
# existed: session routes only. ``satellite`` may use ``/v1/agent/*``,
# pinned to one ``machine_id`` at its first register. These are the
# roles a ``/v1/pair`` body may claim. (Old hubs also wrote ``actions``
# rows for ntfy buttons; notify.retire_action_credential revokes them.)
PAIR_ROLES = frozenset({"phone", "satellite"})

_DEVICES_BASENAME = "devices.json"
_TOKENS_BASENAME = "pair_tokens.json"
_REQUESTS_BASENAME = "pair_requests.json"
_SETTINGS_BASENAME = "pairing.json"
# The hub owner's tailnet devices that got in without pairing, by node:
# when each was seen, and whether the owner revoked it.
_SIGNINS_BASENAME = "tailnet_signins.json"
# How stale a sign-in's last_seen_at may get before a request rewrites it.
SIGNIN_TOUCH_S = 300

# Tailscale-identity pairing: a phone POSTs /v1/pair/request, shows a
# code, and a second device (or the hub shell) approves it. Codes die
# fast and lock after a handful of wrong guesses.
REQUEST_TTL_S = 300
MAX_PENDING = 10
MAX_CODE_ATTEMPTS = 5
# Hard cap on every live row (pending + resolved), so a spammer can't
# grow the file even as rows churn through states.
MAX_REQUEST_ROWS = 50


class PairBusy(Exception):
    """More than ``MAX_PENDING`` requests are already waiting."""


class SelfApproval(Exception):
    """A device tried to approve the pair request it itself made."""

# Pairing state changes are read-change-save; the lock file makes that
# atomic across the hub process and any `swe remote` CLI running beside
# it, and the threading lock covers concurrent requests in-process.
_PROCESS_LOCK = threading.RLock()


@contextlib.contextmanager
def _state_lock(home: Path | None):
    """Exclusive cross-process section around pairing-state changes."""
    path = remote_dir_for(home)
    path.mkdir(parents=True, exist_ok=True)
    with _PROCESS_LOCK:
        with open(path / "pair.lock", "w") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def devices_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(home) / _DEVICES_BASENAME


def tokens_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(home) / _TOKENS_BASENAME


def requests_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(home) / _REQUESTS_BASENAME


def settings_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(home) / _SETTINGS_BASENAME


def signins_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(home) / _SIGNINS_BASENAME


def _hash_credential(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def _read_json(path: Path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except Exception:
        return default


def load_devices(home: Path | str | None = None) -> dict[str, dict]:
    data = _read_json(devices_file(home), {"devices": {}})
    devices = data.get("devices")
    return devices if isinstance(devices, dict) else {}


def _save_devices(home: Path | None, devices: dict[str, dict]) -> None:
    path = devices_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, json.dumps({"devices": devices}, indent=1), private=True
    )


# -- device metadata --------------------------------------------------------
#
# What a device says about itself when it pairs: a ``kind`` the list
# renders as an icon, a ``model`` where the client knows it (only a
# native app does — browser UAs stopped carrying it years ago), the
# ``client`` name+version and the ``os`` string. ``node_name`` is the
# tailnet node the request came from, which is the closest thing to a
# location Tailscale's CGNAT gives us.
META_KEYS = ("kind", "model", "client", "os")
_META_FIELD_KEYS = META_KEYS + ("node_name",)
_MAX_META_LEN = 120
# A device name the pairing client sends is cut to this, like a rename.
MAX_NAME_LEN = 80

# Darwin major → Apple OS, for CFNetwork UAs that name no platform.
# Both families share one kernel; the product token picks the map.
_DARWIN_IOS = {
    "25": "iOS 26", "24": "iOS 18", "23": "iOS 17",
    "22": "iOS 16", "21": "iOS 15", "20": "iOS 14",
}
_DARWIN_MACOS = {
    "25": "macOS 26", "24": "macOS 15", "23": "macOS 14",
    "22": "macOS 13", "21": "macOS 12", "20": "macOS 11",
}

_PRODUCT_RE = re.compile(r"^([A-Za-z][\w+.-]*)/([\d.]+)")


def client_meta(user_agent: str) -> dict:
    """Best-effort ``{kind, client, os}`` from a User-Agent header.

    The fallback for clients that send no explicit ``device`` map.
    Anything it can't read is simply absent — never guessed.
    """
    # Real UAs are ~200 chars; /v1/pair accepts the header unauthenticated,
    # so bound the text the patterns ever see.
    ua = (user_agent or "").strip()[:1024]
    meta: dict[str, str] = {}
    if not ua:
        return meta
    # kind: the platform token. iPadOS has reported "Macintosh" since
    # 13.x — browsers can only fix that client-side (explicit fields).
    if "iPhone" in ua or "iPod" in ua:
        meta["kind"] = "iphone"
    elif "iPad" in ua:
        meta["kind"] = "ipad"
    elif "Android" in ua:
        meta["kind"] = "android"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        meta["kind"] = "mac"
    elif "Windows" in ua:
        meta["kind"] = "windows"
    elif "Linux" in ua or "X11" in ua:
        meta["kind"] = "linux"
    # os
    m = re.search(r"OS (\d+[._\d]*) like Mac OS X", ua)
    if m:
        family = "iPadOS" if "iPad" in ua else "iOS"
        meta["os"] = f"{family} {m.group(1).replace('_', '.')}"
    elif "Android" in ua:
        m = re.search(r"Android (\d+[\d.]*)", ua)
        meta["os"] = "Android" + (f" {m.group(1)}" if m else "")
    elif "Mac OS X" in ua:
        m = re.search(r"Mac OS X (\d+[._\d]+)", ua)
        meta["os"] = "macOS" + (
            f" {m.group(1).replace('_', '.')}" if m else ""
        )
    elif "Windows NT" in ua:
        m = re.search(r"Windows NT ([\d.]+)", ua)
        meta["os"] = "Windows NT" + (f" {m.group(1)}" if m else "")
    # client: a leading product token (CFNetwork apps, curl, python)
    # wins; else the well-known browser families.
    m = _PRODUCT_RE.match(ua)
    if m and not m.group(1).lower().startswith("mozilla"):
        meta["client"] = f"{m.group(1)} {m.group(2)}"
    else:
        for pat, name in (
            (r"Edg/([\d.]+)", "Edge"),
            (r"Chrome/([\d.]+)", "Chrome"),
            (r"CriOS/([\d.]+)", "Chrome"),
            (r"Firefox/([\d.]+)", "Firefox"),
        ):
            m = re.search(pat, ua)
            if m:
                meta["client"] = f"{name} {m.group(1)}"
                break
        else:
            # Safari's user-facing version lives in Version/x; Safari/x
            # is the WebKit build. Two independent reads — a greedy .*
            # between them turns a crafted header quadratic.
            if "Safari/" in ua:
                m = re.search(r"Version/([\d.]+)", ua) or re.search(
                    r"Safari/([\d.]+)", ua)
                if m:
                    meta["client"] = f"Safari {m.group(1)}"
    # A bare Darwin version says only "some Apple OS". Quiver Remote
    # is the iOS app; the Mac app's own Quiver/x token — and every other
    # Darwin product — reads as macOS.
    if "os" not in meta:
        m = re.search(r"Darwin/(\d+)", ua)
        if m:
            client = meta.get("client", "").lower().replace(" ", "")
            table = (
                _DARWIN_IOS
                if client.startswith("quiverremote")
                else _DARWIN_MACOS
            )
            guess = table.get(m.group(1))
            if guess:
                meta["os"] = guess
    if "kind" not in meta:
        # A Mozilla-prefixed UA with no platform token is some browser
        # we didn't recognise; curl/python-style UAs are just unknown.
        meta["kind"] = "browser" if ua.startswith("Mozilla/") else "unknown"
    return meta


def clean_name(name: object) -> str:
    """A client-sent device name, trimmed and length-capped; "" if none."""
    return str(name or "").strip()[:MAX_NAME_LEN]


def clean_device_meta(fields: dict | None) -> dict:
    """The explicit ``device`` map a client posts: known keys only,
    trimmed and length-capped; ``kind`` becomes a lowercase token so
    renderers can switch on it."""
    if not isinstance(fields, dict):
        return {}
    out: dict[str, str] = {}
    for key in META_KEYS:
        val = fields.get(key)
        if not isinstance(val, str):
            continue
        val = val.strip()[:_MAX_META_LEN]
        if val:
            out[key] = val
    kind = out.get("kind")
    if kind is not None:
        kind = re.sub(r"[^a-z0-9_-]", "", kind.lower())[:32]
        if kind:
            out["kind"] = kind
        else:
            del out["kind"]
    return out


def device_view(device_id: str, row: dict) -> dict:
    """A devices.json row safe to serve: everything but the credential."""
    out = {
        key: val for key, val in row.items() if key != "credential_hash"
    }
    out["id"] = device_id
    out["revoked"] = bool(row.get("revoked_at"))
    return out


def mint_pair_token(home: Path | str | None = None) -> str:
    """Write a one-time, short-lived pairing token the hub will honor."""
    token = secrets.token_urlsafe(24)
    home_path = Path(home) if home is not None else None
    # Locked like exchange: two concurrent mints must not clobber each
    # other's token file update.
    with _state_lock(home_path):
        path = tokens_file(home_path)
        data = _read_json(path, {"tokens": []})
        tokens = data.get("tokens") if isinstance(data, dict) else []
        now = time.time()
        tokens = [
            t
            for t in tokens
            if isinstance(t, dict) and t.get("expires_at", 0) > now
        ]
        tokens.append({"token": token, "expires_at": now + TOKEN_TTL_S})
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path, json.dumps({"tokens": tokens}, indent=1), private=True
        )
    return token


def pair_payload(host: str, port: int, token: str,
                 name: str = "", scheme: str = "http") -> str:
    """The QR/URL payload handed to the phone.

    ``scheme`` rides along only when it isn't plain http (a hub behind
    ``tailscale serve``), so older apps keep reading the links they know.
    """
    out = (
        f"quiver://pair?host={quote(host, safe='')}"
        f"&port={port}&token={quote(token, safe='')}"
    )
    if scheme != "http":
        out += f"&scheme={quote(scheme, safe='')}"
    if name:
        out += f"&name={quote(name, safe='')}"
    return out


def pair_url(host: str, port: int, token: str, name: str = "",
             scheme: str = "http") -> str:
    """The form a phone browser can open directly: the web app's
    ``#pair=`` handler trades the token for a device credential."""
    out = (f"{scheme}://{url_host(host)}:{port}/"
           f"#pair={quote(token, safe='')}")
    if name:
        out += f"&name={quote(name, safe='')}"
    return out


def _consume_token(home: Path | None, token: str) -> bool:
    """True iff ``token`` is a live, unexpired pair token; consumes it."""
    path = tokens_file(home)
    data = _read_json(path, {"tokens": []})
    tokens = data.get("tokens") if isinstance(data, dict) else []
    now = time.time()
    live = [
        t
        for t in tokens
        if isinstance(t, dict) and t.get("expires_at", 0) > now
    ]
    matched = any(
        hmac.compare_digest(str(t.get("token") or ""), token) for t in live
    )
    if not matched:
        # Rewrite anyway when the list shrank by expiry.
        if len(live) != len(tokens):
            atomic_write_text(
                path, json.dumps({"tokens": live}, indent=1), private=True
            )
        return False
    remaining = [
        t
        for t in live
        if not hmac.compare_digest(str(t.get("token") or ""), token)
    ]
    atomic_write_text(
        path, json.dumps({"tokens": remaining}, indent=1), private=True
    )
    return True


def exchange(
    home: Path | str | None, token: str, name: str = "",
    meta: dict | None = None, role: str = "phone",
) -> dict | None:
    """``POST /v1/pair``: token → a fresh device credential, once.

    Returns ``{"device_id", "credential", "name"}`` or None for a bad or
    expired token. Token consumption and the device write happen under
    one lock so two requests can't spend the same token.

    ``role`` is the client's claimed role — ``satellite`` is honored,
    anything else mints a phone row.
    """
    home_path = Path(home) if home is not None else None
    if not token:
        return None
    name = clean_name(name)
    with _state_lock(home_path):
        if not _consume_token(home_path, token):
            return None
        device_id, credential = _mint_device(
            home_path, name,
            extra={
                "paired_via": "token",
                "role": role if role in PAIR_ROLES else "phone",
                **_clean_meta(meta),
            },
        )
    return {"device_id": device_id, "credential": credential, "name": name or "device"}


def _clean_meta(meta: dict | None) -> dict:
    """Device fields bound for the row: known keys, no empties."""
    return {
        key: val for key, val in (meta or {}).items()
        if key in _META_FIELD_KEYS and isinstance(val, str) and val
    }


def _mint_device(
    home_path: Path | None, name: str, extra: dict | None = None
) -> tuple[str, str]:
    """Write one device row; returns ``(device_id, credential)``.

    Caller holds ``_state_lock`` — both pair paths mint inside it.
    """
    device_id = secrets.token_hex(8)
    credential = secrets.token_urlsafe(32)
    devices = load_devices(home_path)
    devices[device_id] = {
        "name": name or "device",
        "credential_hash": _hash_credential(credential),
        "created_at": time.time(),
        "last_seen_at": None,
        "revoked_at": None,
        **(extra or {}),
    }
    _save_devices(home_path, devices)
    return device_id, credential


def device_for_credential(
    home: Path | str | None, credential: str
) -> tuple[str, dict] | None:
    """Resolve a bearer credential to ``(device_id, row)``, else None."""
    if not credential:
        return None
    digest = _hash_credential(credential)
    for device_id, row in load_devices(home).items():
        if row.get("revoked_at"):
            continue
        if hmac.compare_digest(
            str(row.get("credential_hash") or ""), digest
        ):
            return device_id, row
    return None


def touch_device(
    home: Path | str | None, device_id: str, meta: dict | None = None
) -> None:
    """Refresh last_seen_at; cheap best-effort, failures ignored.

    ``meta`` backfills device fields the row still lacks (a device paired
    before they were captured), so an ordinary request gradually fills in
    kind/client/os for old rows. Fields already set are never touched.
    """
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        devices = load_devices(home_path)
        row = devices.get(device_id)
        if row is None:
            return
        row["last_seen_at"] = time.time()
        for key, val in _clean_meta(meta).items():
            if not row.get(key):
                row[key] = val
        _save_devices(home_path, devices)


def update_device(home: Path | str | None, device_id: str, **fields) -> bool:
    """Set fields on a live device's row; False when it is unknown or
    revoked. A None value removes the field."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        devices = load_devices(home_path)
        row = devices.get(device_id)
        if row is None or row.get("revoked_at"):
            return False
        for name, value in fields.items():
            if value is None:
                row.pop(name, None)
            else:
                row[name] = value
        _save_devices(home_path, devices)
        return True


def bind_machine(
    home: Path | str | None, device_id: str, machine_id: str
) -> str | None:
    """Pin a satellite's device row to ``machine_id``; first write wins.

    Returns the machine the row is bound to (the one just written, or
    the earlier binding when a second machine tries). None only when
    the device row is gone.
    """
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        devices = load_devices(home_path)
        row = devices.get(device_id)
        if row is None:
            return None
        bound = row.get("machine_id")
        if bound:
            return str(bound)
        # A machine_id belongs to one live credential: a fresh satellite
        # can't claim a machine another unrevoked device already speaks
        # for. Revoking the old row frees the id — that is the
        # credential-replacement path.
        for other_id, other in devices.items():
            if (
                other_id != device_id
                and str(other.get("machine_id") or "") == machine_id
                and not other.get("revoked_at")
            ):
                return None
        row["machine_id"] = machine_id
        _save_devices(home_path, devices)
        return machine_id


def revoke_device(home: Path | str | None, device_id: str) -> bool:
    """Sign one paired device out. A device paired through a tailnet
    request is a node, so the node goes with it (``_revoke_node``): its
    tailnet sign-in and every other credential paired from it. A lost
    phone on the owner's login would otherwise walk back in through
    Safari, or through a second pairing."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        devices = load_devices(home_path)
        row = devices.get(device_id)
        if row is None:
            return False
        now = time.time()
        row["revoked_at"] = now
        node = str(row.get("tailscale_node") or "")
        if node:
            _revoke_node(home_path, devices, node, now, row.get("node_name") or "")
        else:
            _save_devices(home_path, devices)
    # A lost phone loses its SSH login too. After the lock, since sync
    # takes its own. The revoke is saved by now, so a failed rewrite is
    # KeysNotSynced, never the OSError a failed save raises: the person
    # revoking must hear the device is out but its key line is still there.
    from quiver.remote import sshkeys

    try:
        sshkeys.sync(home_path)
    except OSError as exc:
        raise sshkeys.KeysNotSynced(str(exc)) from exc
    return True


# -- tailnet sign-ins --------------------------------------------------------
#
# The hub owner's own tailnet devices use the hub without pairing (the
# hub's ``_owner_device``). Nothing about them lives in devices.json, so
# this file is what lets the owner see them and revoke a lost one: a
# revoked node gets no owner identity until a pairing from it is approved
# again. Keyed by Tailscale's StableID, which survives renames and IP
# changes but not removing the node from the tailnet.


def _read_signins(home: Path | str | None) -> dict[str, dict] | None:
    """The sign-in rows; ``{}`` when the file doesn't exist yet, None
    when it exists but can't be read or parsed. A damaged file must not
    read as "nothing revoked"."""
    path = signins_file(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    rows = data.get("signins") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        return None
    return {k: v for k, v in rows.items() if isinstance(v, dict)}


def load_signins(home: Path | str | None = None) -> dict[str, dict]:
    """The sign-in rows for listing; a damaged file lists nothing."""
    return _read_signins(home) or {}


def _save_signins(home_path: Path | None, rows: dict[str, dict]) -> None:
    path = signins_file(home_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"signins": rows}, indent=1), private=True)


def signin_revoked(home: Path | str | None, node_id: str) -> bool:
    """True when the owner revoked this tailnet node's sign-in, and for
    every node while the file can't be read: owner devices then pair
    like anyone else rather than a revoked phone getting back in."""
    rows = _read_signins(home)
    return rows is None or bool((rows.get(node_id) or {}).get("revoked_at"))


def _revoke_node(home_path: Path | None, devices: dict, node: str, now: float,
                 node_name: str = "") -> None:
    """Under ``_state_lock``: revoke every device paired from ``node``,
    then its sign-in. Devices are saved first, so a failure between the
    two leaves the sign-in open and a retry does the whole job again."""
    for drow in devices.values():
        if drow.get("tailscale_node") == node and not drow.get("revoked_at"):
            drow["revoked_at"] = now
    _save_devices(home_path, devices)
    rows = _read_signins(home_path)
    if rows is None:
        raise OSError(f"{signins_file(home_path)} is unreadable; not overwriting it")
    entry = rows.setdefault(node, {})
    if not entry.get("revoked_at"):
        entry["revoked_at"] = now
        if node_name:
            entry.setdefault("node_name", node_name)
        _save_signins(home_path, rows)


def note_signin(
    home: Path | str | None, node_id: str, node_name: str = "",
    now: float | None = None,
) -> None:
    """Record that ``node_id`` got in as the owner: first sight, then a
    ``last_seen_at`` refreshed at most every ``SIGNIN_TOUCH_S``. A revoked
    node is left alone; it shouldn't have got this far."""
    if not node_id:
        return
    now = time.time() if now is None else now

    def fresh(row: dict) -> bool:
        seen = row.get("last_seen_at")
        return bool(row.get("revoked_at")) or (
            seen is not None and now - float(seen) < SIGNIN_TOUCH_S)

    rows = _read_signins(home)
    if rows is None or fresh(rows.get(node_id) or {}):
        return
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        rows = _read_signins(home_path)
        if rows is None:
            # Never rewrite a file we couldn't read: it may hold revocations.
            return
        row = rows.setdefault(node_id, {})
        # Re-checked under the lock: a parallel request may have just
        # written it.
        if fresh(row):
            return
        row.setdefault("first_seen_at", now)
        row["last_seen_at"] = now
        if node_name:
            row["node_name"] = node_name
        _save_signins(home_path, rows)


def revoke_signin(home: Path | str | None, node_id: str) -> bool:
    """Revoke a tailnet node's sign-in, and every paired device that
    pairing from that node minted: the owner is signing out a device,
    not one of its credentials. False when the node was never seen."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        rows = _read_signins(home_path)
        if rows is None:
            raise OSError(f"{signins_file(home_path)} is unreadable")
        if node_id not in rows:
            return False
        _revoke_node(home_path, load_devices(home_path), node_id, time.time())
    # Always: a retry after a failed rewrite must still drop the keys.
    from quiver.remote import sshkeys

    try:
        sshkeys.sync(home_path)
    except OSError as exc:
        raise sshkeys.KeysNotSynced(str(exc)) from exc
    return True


# -- tailnet pair requests --------------------------------------------------


def _load_requests(home_path: Path | None) -> list[dict]:
    data = _read_json(requests_file(home_path), {"requests": []})
    rows = data.get("requests")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


def _save_requests(home_path: Path | None, rows: list[dict]) -> None:
    path = requests_file(home_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, json.dumps({"requests": rows}, indent=1), private=True
    )


def _prune_requests(rows: list[dict], now: float) -> list[dict]:
    """Drop expired rows; denied rows stay until the phone polls them."""
    # Only expiry prunes: a denied row must survive until the phone's
    # next poll reads it out, or the denial looks like an expiry.
    return [r for r in rows if r.get("expires_at", 0) > now]


def request_pair(
    home: Path | str | None, peer: dict, name: str = "",
    meta: dict | None = None,
) -> dict:
    """Open a pair request for a tailnet peer; the phone shows ``code``.

    ``meta`` (kind/model/client/os) rides on the request row and is
    minted into the device when it's approved — the requester supplied
    it, not the approver."""
    home_path = Path(home) if home is not None else None
    name = clean_name(name)
    with _state_lock(home_path):
        now = time.time()
        node_id = str(peer.get("node_id") or "")
        rows = [
            r for r in _prune_requests(_load_requests(home_path), now)
            if r.get("node_id") != node_id or r.get("status") != "pending"
        ]
        if len(rows) >= MAX_REQUEST_ROWS or sum(
            r.get("status") == "pending" for r in rows
        ) >= MAX_PENDING:
            raise PairBusy("too many pending pair requests")
        poll_secret = secrets.token_urlsafe(24)
        used = {
            str(r.get("code")) for r in rows if r.get("status") == "pending"
        }
        code = f"{secrets.randbelow(10**6):06d}"
        while code in used:
            code = f"{secrets.randbelow(10**6):06d}"
        row = {
            "request_id": secrets.token_hex(8),
            "code": code,
            "poll_secret_hash": _hash_credential(poll_secret),
            "node_name": str(peer.get("node_name") or ""),
            "node_short": str(peer.get("node_short") or ""),
            "node_id": node_id,
            "login": str(peer.get("login") or ""),
            "peer_ip": str(peer.get("ip") or ""),
            "name": name or str(peer.get("node_short") or "") or "device",
            "device_meta": {
                key: val for key, val in (meta or {}).items()
                if key in META_KEYS and isinstance(val, str) and val
            },
            "created_at": now,
            "expires_at": now + REQUEST_TTL_S,
            "status": "pending",
            "attempts": 0,
        }
        rows.append(row)
        _save_requests(home_path, rows)
    return {
        "request_id": row["request_id"],
        "poll_secret": poll_secret,
        "code": code,
        "expires_in_s": REQUEST_TTL_S,
        "node": {"name": row["node_name"], "login": row["login"]},
    }


def _load_live_requests(home_path: Path | None, now: float) -> list[dict]:
    """The request rows, with expired ones dropped from disk too — an
    approved row's plaintext credential leaves with its row. Caller
    holds ``_state_lock``."""
    rows = _load_requests(home_path)
    kept = _prune_requests(rows, now)
    if len(kept) != len(rows):
        _save_requests(home_path, kept)
    return kept


def prune_requests(home: Path | str | None = None) -> None:
    """Drop expired request rows. The hub runs this on a timer so an
    approved credential the phone never collected doesn't sit in
    pair_requests.json until some other pairing call happens by."""
    home_path = Path(home) if home is not None else None
    if not requests_file(home_path).exists():
        return
    with _state_lock(home_path):
        _load_live_requests(home_path, time.time())


def list_requests(home: Path | str | None = None) -> list[dict]:
    """Pending, unexpired requests — never codes or secrets."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        now = time.time()
        kept = _load_live_requests(home_path, now)
    return [
        {
            "request_id": r["request_id"],
            "name": r["name"],
            "node_name": r["node_name"],
            "login": r["login"],
            "peer_ip": r["peer_ip"],
            "created_at": r["created_at"],
            "expires_in_s": int(r["expires_at"] - now),
        }
        for r in kept if r.get("status") == "pending"
    ]


def approve_request(
    home: Path | str | None,
    code: str,
    request_id: str | None = None,
    approver_node_id: str | None = None,
) -> dict | None:
    """Approve a pending request by code; mints the device credential."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        now = time.time()
        rows = _prune_requests(_load_requests(home_path), now)
        pending = [r for r in rows if r.get("status") == "pending"]
        if request_id is None and pending and not any(
            hmac.compare_digest(str(r.get("code") or ""), str(code))
            for r in pending
        ):
            # A code with no request id is checked against every pending
            # row, so a miss is a strike against each: otherwise guessing
            # without an id would never hit the five-strike limit.
            for row in pending:
                row["attempts"] = row.get("attempts", 0) + 1
                if row["attempts"] >= MAX_CODE_ATTEMPTS:
                    row["status"] = "denied"
            _save_requests(home_path, rows)
            return None
        for row in rows:
            if row.get("status") != "pending":
                continue
            if request_id is not None and row.get("request_id") != request_id:
                continue
            if not hmac.compare_digest(str(row.get("code") or ""), str(code)):
                if request_id is None:
                    continue  # another pending row may hold this code
                row["attempts"] = row.get("attempts", 0) + 1
                if row["attempts"] >= MAX_CODE_ATTEMPTS:
                    row["status"] = "denied"
                _save_requests(home_path, rows)
                return None
            if approver_node_id and approver_node_id == row.get("node_id"):
                raise SelfApproval(row.get("node_id") or "")
            return _approve_row(home_path, rows, row, now, "code")
        _save_requests(home_path, rows)
        return None


def _approve_row(
    home_path: Path | None, rows: list[dict], row: dict, now: float,
    approved_via: str,
) -> dict:
    """Mint the device for one pending request and mark it approved.

    The one approval path: a typed code and an owner auto-approval write
    the same device row, told apart only by ``approved_via``. Caller
    holds ``_state_lock`` and has checked ``row`` is pending.
    """
    device_id, credential = _mint_device(
        home_path, row["name"],
        extra={
            "paired_via": "tailscale",
            "approved_via": approved_via,
            # The tailnet request flow is for phones; satellites
            # pair with a token (agent.pair_agent).
            "role": "phone",
            "tailscale_node": row.get("node_id") or "",
            "tailscale_login": row.get("login") or "",
            "node_name": row.get("node_name") or "",
            **(row.get("device_meta") or {}),
        },
    )
    row["status"] = "approved"
    row["approved_via"] = approved_via
    row["device_id"] = device_id
    if approved_via == "code":
        # The owner just approved a device on this node, so a sign-in
        # revoked earlier (the phone was found) works again. Only a typed
        # code says so: an auto-approval never undoes a revoke.
        node = str(row.get("node_id") or "")
        signins = load_signins(home_path) if node else {}
        if signins.get(node, {}).get("revoked_at"):
            signins[node]["revoked_at"] = None
            _save_signins(home_path, signins)
    # Fresh window: a phone mid-poll at the old deadline must
    # still collect its credential.
    row["expires_at"] = now + REQUEST_TTL_S
    # Kept until the phone polls it out; the file is 0600.
    row["credential"] = credential
    _save_requests(home_path, rows)
    return {
        "device_id": device_id,
        "name": row["name"],
        "node_name": row.get("node_name") or "",
        "login": row.get("login") or "",
    }


# -- owner auto-approve -------------------------------------------------------
#
# Opt-in, off by default: a pair request from the hub owner's own,
# untagged tailnet device is approved without a code. Every fact it
# rests on must be known; anything missing or unexpected refuses.


def auto_approve_owner(home: Path | str | None = None) -> bool:
    """Whether owner auto-approve is on. Only a literal ``true`` in
    ``pairing.json`` counts; a missing, unreadable or odd file is off."""
    data = _read_json(settings_file(home), {})
    return data.get("auto_approve_owner") is True


def set_auto_approve_owner(home: Path | str | None, on: bool) -> bool:
    """Turn owner auto-approve on or off; returns the saved value."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        path = settings_file(home_path)
        data = _read_json(path, {})
        data["auto_approve_owner"] = bool(on)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(data, indent=1), private=True)
    return bool(on)


def owner_auto_approve_refusal(
    peer: dict | None, owner_login: str, enabled: bool,
    own_node_id: str = "",
) -> str | None:
    """Why a request from ``peer`` is NOT auto-approved, or None when it
    is: the setting is on, the hub knows its owner and its own node, and
    the peer is an untagged node of that same login with a node id that
    isn't the hub's own. A request from the hub's own node came from some
    process on that machine, whichever local user runs it, so it proves
    nothing about the owner."""
    if not enabled:
        return "off"
    if not isinstance(owner_login, str) or not owner_login:
        return "no_owner"
    if not isinstance(peer, dict):
        return "no_peer"
    # ``tagged`` must be known and False: a tagged node belongs to no
    # user, and a peer record that doesn't say is not trusted either.
    if peer.get("tagged") is not False:
        return "tagged"
    login = peer.get("login")
    if not isinstance(login, str) or not login:
        return "no_login"
    if login != owner_login:
        return "other_login"
    node = str(peer.get("node_id") or "")
    if not node:
        return "no_node"
    if not isinstance(own_node_id, str) or not own_node_id:
        return "no_own_node"
    if node == own_node_id:
        return "own_node"
    return None


def auto_approve_request(
    home: Path | str | None, request_id: str, peer: dict
) -> dict | None:
    """Approve the pending request ``request_id`` without a code, for a
    peer ``owner_auto_approve_refusal`` already cleared. The row must
    still be pending and come from that peer's node and login, so a
    request swapped in between the two steps is never approved."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        now = time.time()
        rows = _prune_requests(_load_requests(home_path), now)
        for row in rows:
            if row.get("request_id") != request_id:
                continue
            if (
                row.get("status") != "pending"
                or not row.get("node_id")
                or row.get("node_id") != str(peer.get("node_id") or "")
                or row.get("login") != str(peer.get("login") or "")
            ):
                return None
            if node_revoked(home_path, row["node_id"]):
                # A lost phone stays out until the owner types its code.
                return None
            return _approve_row(home_path, rows, row, now, "owner_auto")
        return None


def node_revoked(home: Path | str | None, node_id: str) -> bool:
    """True when the owner revoked this tailnet node: its sign-in is
    revoked (or the sign-ins file can't be read), or the newest device
    paired from it is revoked. A later code approval mints a newer live
    device, which lifts this."""
    if signin_revoked(home, node_id):
        return True
    newest = None
    for row in load_devices(home).values():
        if row.get("tailscale_node") != node_id:
            continue
        if newest is None or (row.get("created_at") or 0) >= (
            newest.get("created_at") or 0
        ):
            newest = row
    return bool(newest and newest.get("revoked_at"))


def deny_request(
    home: Path | str | None,
    code: str,
    request_id: str | None = None,
) -> bool:
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        now = time.time()
        rows = _prune_requests(_load_requests(home_path), now)
        for row in rows:
            if row.get("status") != "pending":
                continue
            if request_id is not None and row.get("request_id") != request_id:
                continue
            if not hmac.compare_digest(str(row.get("code") or ""), str(code)):
                continue
            row["status"] = "denied"
            _save_requests(home_path, rows)
            return True
        return False


def poll_request(
    home: Path | str | None, request_id: str, poll_secret: str
) -> dict | None:
    """The requester's check: pending/denied/approved, credential once."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        now = time.time()
        rows = _load_live_requests(home_path, now)
        for i, row in enumerate(rows):
            if row.get("request_id") != request_id:
                continue
            if row.get("expires_at", 0) <= now or not hmac.compare_digest(
                str(row.get("poll_secret_hash") or ""),
                _hash_credential(str(poll_secret or "")),
            ):
                return None
            status = row.get("status")
            if status == "pending":
                return {
                    "status": "pending",
                    "expires_in_s": int(row["expires_at"] - now),
                }
            if status == "approved":
                out = {
                    "status": "approved",
                    "device_id": row.get("device_id") or "",
                    "credential": row.get("credential") or "",
                    "name": row.get("name") or "",
                }
                del rows[i]
                _save_requests(home_path, rows)
                return out
            # denied: deliver once, then gone
            del rows[i]
            _save_requests(home_path, rows)
            return {"status": "denied"}
        return None
