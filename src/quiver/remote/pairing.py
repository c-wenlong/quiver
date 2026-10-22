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
                 name: str = "") -> str:
    """The QR/URL payload handed to the phone."""
    out = (
        f"quiver://pair?host={quote(host, safe='')}"
        f"&port={port}&token={quote(token, safe='')}"
    )
    if name:
        out += f"&name={quote(name, safe='')}"
    return out


def pair_url(host: str, port: int, token: str, name: str = "") -> str:
    """The https form a phone browser can open directly."""
    out = f"http://{host}:{port}/#pair={quote(token, safe='')}"
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
        device_id = secrets.token_hex(8)
        credential = secrets.token_urlsafe(32)
        devices = load_devices(home)
        devices[device_id] = {
            "name": name or "device",
            "credential_hash": _hash_credential(credential),
            "created_at": time.time(),
            "last_seen_at": None,
            "revoked_at": None,
        }
        _save_devices(home_path, devices)
    return {"device_id": device_id, "credential": credential, "name": name or "device"}


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


def revoke_device(home: Path | str | None, device_id: str) -> bool:
    home_path = Path(home) if home is not None else None
    with _state_lock(home_path):
        devices = load_devices(home_path)
        row = devices.get(device_id)
        if row is None:
            return False
        row["revoked_at"] = time.time()
        _save_devices(home_path, devices)
        return True
