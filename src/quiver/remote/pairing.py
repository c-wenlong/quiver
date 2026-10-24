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
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import quote

from quiver.paths import atomic_write_text, remote_dir_for

# A pairing token is worthless within minutes of `swe remote pair`.
TOKEN_TTL_S = 300

_DEVICES_BASENAME = "devices.json"
_TOKENS_BASENAME = "pair_tokens.json"
_REQUESTS_BASENAME = "pair_requests.json"

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
    """The form a phone browser can open directly."""
    out = f"{scheme}://{host}:{port}/#pair={quote(token, safe='')}"
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


def exchange(home: Path | str | None, token: str, name: str = "") -> dict | None:
    """``POST /v1/pair``: token → a fresh device credential, once.

    Returns ``{"device_id", "credential", "name"}`` or None for a bad or
    expired token. Token consumption and the device write happen under
    one lock so two requests can't spend the same token.
    """
    home_path = Path(home) if home is not None else None
    if not token:
        return None
    with _state_lock(home_path):
        if not _consume_token(home_path, token):
            return None
        device_id, credential = _mint_device(home_path, name)
    return {"device_id": device_id, "credential": credential, "name": name or "device"}


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


def touch_device(home: Path | str | None, device_id: str) -> None:
    """Refresh last_seen_at; cheap best-effort, failures ignored."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        devices = load_devices(home_path)
        if device_id not in devices:
            return
        devices[device_id]["last_seen_at"] = time.time()
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


def revoke_device(home: Path | str | None, device_id: str) -> bool:
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        devices = load_devices(home_path)
        row = devices.get(device_id)
        if row is None:
            return False
        row["revoked_at"] = time.time()
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


def request_pair(home: Path | str | None, peer: dict, name: str = "") -> dict:
    """Open a pair request for a tailnet peer; the phone shows ``code``."""
    home_path = Path(home) if home is not None else None
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


def list_requests(home: Path | str | None = None) -> list[dict]:
    """Pending, unexpired requests — never codes or secrets."""
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        now = time.time()
        rows = _load_requests(home_path)
        kept = _prune_requests(rows, now)
        if len(kept) != len(rows):
            _save_requests(home_path, kept)
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
            device_id, credential = _mint_device(
                home_path, row["name"],
                extra={
                    "paired_via": "tailscale",
                    "tailscale_node": row.get("node_id") or "",
                    "tailscale_login": row.get("login") or "",
                },
            )
            row["status"] = "approved"
            row["device_id"] = device_id
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
            }
        _save_requests(home_path, rows)
        return None


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
        rows = _load_requests(home_path)
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
