"""ntfy fan-out for ``notify`` and ``approval.requested`` events.

The hub watches its own event bus; when a session flips to needs_you /
finished / error / limit, or a tmux pane shows a permission menu, one
POST lands on the configured ntfy topic. Self-hosted ntfy + the phone's
ntfy subscription is the whole push path — no app server, no APNs code
here (iOS background delivery still rides ntfy's upstream relay; the
doc records that trade-off).

Approval notifications carry ntfy ``http`` action buttons that POST
``/v1/sessions/{key}/approval`` straight back to the hub over the
tailnet — approve/deny from the lock screen, no app code. The action
credential is a normal device row (``name: ntfy-actions``) minted once
and stored in notify.json, so revoking it works like revoking a phone.

Config lives at ``remote/notify.json`` (0600):

    {"enabled": true, "url": "https://ntfy.example.com",
     "topic": "quiver-alice", "ntfy_token": null,
     "action_device_id": "...", "action_credential": "..."}
"""

from __future__ import annotations

import json
import queue
import secrets
import time
import urllib.parse
import urllib.request
from pathlib import Path

from quiver.paths import atomic_write_text, remote_dir_for
from quiver.remote import pairing

CONFIG_FILE = "notify.json"
_NOTIFY_KIND_TITLES = {
    "needs_you": "Needs you",
    "finished": "Finished",
    "error": "Errored",
    "limit": "Limit hit",
}
_POST_TIMEOUT_S = 5.0


def _config_path(home: Path | None) -> Path:
    return remote_dir_for(home) / CONFIG_FILE


def load_config(home: Path | str | None = None) -> dict:
    """notify.json contents; {} when unset or unreadable."""
    home_path = Path(home) if home is not None else None
    try:
        data = json.loads(_config_path(home_path).read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(home: Path | str | None, cfg: dict) -> None:
    home_path = Path(home) if home is not None else None
    path = _config_path(home_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(cfg, indent=1), private=True)


def configure(
    home: Path | str | None,
    *,
    url: str | None = None,
    topic: str | None = None,
    ntfy_token: str | None = None,
    enabled: bool | None = None,
) -> dict:
    """Merge a partial update into notify.json; returns the result."""
    cfg = load_config(home)
    if url is not None:
        cfg["url"] = url.rstrip("/")
    if topic is not None:
        cfg["topic"] = topic
    if ntfy_token is not None:
        cfg["ntfy_token"] = ntfy_token or None
    if enabled is not None:
        cfg["enabled"] = enabled
    save_config(home, cfg)
    return cfg


def _action_credential(home: Path | None, cfg: dict) -> str:
    """The credential ntfy action buttons send, minted as a device row.

    The raw credential only lives in notify.json (0600) and the POST the
    phone's ntfy app makes back to the hub — devices.json keeps the
    hash. The "ntfy-actions" device shows up in `swe remote devices` and
    revokes like any phone.
    """
    cred = str(cfg.get("action_credential") or "")
    device_id = str(cfg.get("action_device_id") or "")
    if cred and device_id:
        row = pairing.load_devices(home).get(device_id)
        if row and not row.get("revoked_at"):
            return cred
    # Mint (or re-mint after revocation) without burning a pair token —
    # this is a local device row, same shape exchange() writes, under
    # the same lock.
    device_id = secrets.token_hex(8)
    cred = secrets.token_urlsafe(32)
    with pairing._state_lock(home):
        devices = pairing.load_devices(home)
        devices[device_id] = {
            "name": "ntfy-actions",
            "credential_hash": pairing._hash_credential(cred),
            "created_at": time.time(),
            "last_seen_at": None,
            "revoked_at": None,
        }
        pairing._save_devices(home, devices)
    cfg["action_device_id"] = device_id
    cfg["action_credential"] = cred
    save_config(home, cfg)
    return cred


class Notifier:
    """Bus subscriber → ntfy POSTs. Best-effort; failures drop silently.

    ``base_url`` is the hub's own reachable base (its Tailscale bind) —
    action buttons point back at it so a tap on the phone hits the same
    host the app talks to.
    """

    def __init__(self, state, base_url: str, *, subscribe_key: str) -> None:
        self._state = state
        self._base = base_url.rstrip("/")
        self._subscribe_key = subscribe_key
        self._stop = state._stop
        self._q: queue.Queue | None = None

    def subscribe(self) -> None:
        """Claim the bus queue. HubState.start() calls this *before*
        spawning the producer threads so an approval already on screen
        at boot lands in the queue instead of racing the subscribe."""
        if self._q is None:
            self._q = self._state.bus.subscribe(self._subscribe_key)

    def run(self) -> None:
        cfg = load_config(self._state.home)
        if not cfg.get("enabled") or not cfg.get("url") or \
                not cfg.get("topic"):
            return
        cred = _action_credential(self._state.home, cfg)
        self.subscribe()
        q = self._q
        try:
            while not self._stop.is_set():
                try:
                    ev = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    self._handle(ev, cfg, cred)
                except Exception:
                    continue
        finally:
            self._state.bus.unsubscribe(q)

    def _handle(self, ev: dict, cfg: dict, cred: str) -> None:
        etype = ev.get("type")
        data = ev.get("data") or {}
        key = str(ev.get("session") or "")
        title = self._session_title(key)
        if etype == "approval.requested":
            rid = str(data.get("request_id") or "")
            prompt = str(data.get("prompt") or "Approval requested")
            actions = [
                self._action(key, rid, "allow", "Allow", cred),
                self._action(key, rid, "deny", "Deny", cred),
                self._action(key, rid, "always", "Always", cred),
            ]
            self._post(cfg, {
                "topic": cfg["topic"],
                "title": f"{title}: approval needed",
                "message": prompt,
                "priority": 5,
                "tags": ["lock"],
                "actions": actions,
            })
            return
        if etype != "notify":
            return
        kind = str(data.get("kind") or "")
        label = _NOTIFY_KIND_TITLES.get(kind)
        if label is None:
            return
        priority = 4 if kind in ("needs_you", "error") else 3
        self._post(cfg, {
            "topic": cfg["topic"],
            "title": f"{title}: {label.lower()}",
            "message": "",
            "priority": priority,
            "tags": ["rotating_light" if kind == "error" else "bell"],
        })

    def _session_title(self, key: str) -> str:
        row = self._state.catalog.row_for(key) if key else None
        if row:
            title = str(row.get("title") or "").strip()
            if title:
                return title[:60]
            cwd = str(row.get("cwd") or "")
            if cwd:
                return cwd.rstrip("/").rsplit("/", 1)[-1] or cwd
        return "Quiver"

    def _action(
        self, key: str, rid: str, decision: str, label: str, cred: str
    ) -> dict:
        return {
            "action": "http",
            "label": label,
            "method": "POST",
            "url": (f"{self._base}/v1/sessions/"
                    f"{urllib.parse.quote(key, safe='')}/approval"),
            "headers": {
                "Authorization": f"Bearer {cred}",
                "Content-Type": "application/json",
            },
            "body": json.dumps(
                {"request_id": rid, "decision": decision}
            ),
        }

    def _post(self, cfg: dict, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            str(cfg["url"]), data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        token = cfg.get("ntfy_token")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=_POST_TIMEOUT_S):
            pass
