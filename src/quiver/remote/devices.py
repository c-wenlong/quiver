"""The paired-device list and revoke, shared by the hub routes and the
web Settings page.

``GET /v1/devices`` (the phone's pair-requests-and-devices screen) and
``GET /v1/app/settings`` serve the same ``devices.json`` view:
``device_view`` rows with ``paired_via`` filled and ``this_device``
marking the caller. ``POST /v1/devices/revoke`` and the Settings
``revoke`` action apply one refusal set, which ``Refused`` carries as an
HTTP status, a wire code and a message a client can show as is — the hub
answers it straight, the page translates it to ``PageError``.
"""

from __future__ import annotations

from pathlib import Path

from quiver.remote import pairing, sshkeys

# ``revoke`` refuses an id far past the real size before looking it up.
DEVICE_ID_MAX = 64

# The hub's id for the owner's own tailnet device (hub._owner_device):
# a tailnet sign-in, not a pairing, so no devices.json row ever has it.
# Its row lives in tailnet_signins.json instead (``signin_rows``).
TAILNET_PREFIX = "tailnet:"


class Refused(Exception):
    """A revoke refusal: an HTTP status, a wire code and the message."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def rows(
    home: Path | str | None,
    caller_device_id: str | None,
    devices: dict | None = None,
) -> list[dict]:
    """The ``devices.json`` list for one caller: ``device_view`` rows
    (everything but the credential, ``id`` and ``revoked`` added) plus
    ``paired_via`` and ``this_device``. A ``tailnet:`` caller marks
    nothing — it has no row."""
    if devices is None:
        devices = pairing.load_devices(home)
    return [
        {
            **pairing.device_view(did, row),
            "paired_via": row.get("paired_via") or "token",
            "this_device": did == caller_device_id,
        }
        for did, row in devices.items()
    ]


def signin_rows(
    home: Path | str | None, caller_device_id: str | None,
) -> list[dict]:
    """The owner's tailnet devices that got in without pairing, newest
    first: ``id`` is the ``tailnet:<node>`` id the hub gives the caller,
    so the same revoke route takes it, and ``this_device`` marks the
    caller's own node."""
    out = [
        {
            "id": TAILNET_PREFIX + node,
            "node_name": row.get("node_name") or "",
            "first_seen_at": row.get("first_seen_at"),
            "last_seen_at": row.get("last_seen_at"),
            "revoked": bool(row.get("revoked_at")),
            "revoked_at": row.get("revoked_at"),
            "this_device": TAILNET_PREFIX + node == caller_device_id,
        }
        for node, row in pairing.load_signins(home).items()
    ]
    out.sort(key=lambda r: -(r["last_seen_at"] or r["revoked_at"] or 0))
    return out


def view(home: Path | str | None, caller_device_id: str | None) -> dict:
    """``GET /v1/devices``: the device list plus the pending pair
    requests the same screen approves from — the rows
    ``GET /v1/pair/requests`` already serves (no codes or secrets) — and
    the owner's tailnet sign-ins (``signin_rows``)."""
    return {
        "devices": rows(home, caller_device_id),
        "requests": pairing.list_requests(home),
        "signins": signin_rows(home, caller_device_id),
    }


def revoke(
    home: Path | str | None, caller_device_id: str | None, body: dict
) -> dict:
    """``POST /v1/devices/revoke`` ``{device_id, confirm_this_device?}``.

    Signs one paired device out (``pairing.revoke_device``, which also
    drops its ``authorized_keys`` line and, for a device paired through a
    tailnet request, its node's sign-in) and answers the refreshed list.
    A ``tailnet:<node>`` id revokes one of the owner's tailnet sign-ins
    (``pairing.revoke_signin``): that node gets no owner identity until a
    pairing from it is approved again, and devices paired from it are
    signed out with it. Refusals raise ``Refused``: ``400 bad_device``,
    ``404 unknown_device``, ``409 already_revoked``, ``409
    confirm_this_device`` for the caller's own row or sign-in without
    ``confirm_this_device: true``, and ``500 ssh_key_left`` when the
    device is out but its key line stayed.
    """
    device_id = (body or {}).get("device_id")
    if (
        not isinstance(device_id, str)
        or not device_id
        or len(device_id) > DEVICE_ID_MAX
    ):
        raise Refused(
            400, "bad_device", "device_id must be a paired device's id")
    if device_id.startswith(TAILNET_PREFIX):
        return _revoke_signin(home, caller_device_id, body, device_id)
    row = pairing.load_devices(home).get(device_id)
    if row is None:
        raise Refused(404, "unknown_device", "No paired device has that id.")
    if row.get("revoked_at"):
        raise Refused(
            409, "already_revoked",
            f"{row.get('name') or 'That device'} is already signed out.")
    # A device paired from a tailnet node takes the node with it, so the
    # caller is hit when it is that node, or another device paired there.
    node = str(row.get("tailscale_node") or "")
    hits = device_id == caller_device_id or (
        bool(node) and _caller_on_node(home, caller_device_id, node))
    if hits and body.get("confirm_this_device") is not True:
        raise Refused(
            409, "confirm_this_device",
            "That's the device you're using. "
            "Confirm to sign it out of the hub.")
    try:
        revoked = pairing.revoke_device(home, device_id)
    except sshkeys.KeysNotSynced as exc:
        raise Refused(
            500, "ssh_key_left",
            f"Signed out, but its SSH key is still in "
            f"~/.ssh/authorized_keys: {exc}") from exc
    if not revoked:
        raise Refused(404, "unknown_device", "No paired device has that id.")
    return _answer(home, caller_device_id, device_id, hits)


def _caller_on_node(home, caller_device_id: str | None, node: str) -> bool:
    """Whether revoking tailnet node ``node`` takes the caller's own
    access: its tailnet sign-in, or a device paired from that node."""
    if not caller_device_id:
        return False
    if caller_device_id == TAILNET_PREFIX + node:
        return True
    row = pairing.load_devices(home).get(caller_device_id) or {}
    return row.get("tailscale_node") == node


def _answer(home, caller_device_id, device_id, hits: bool) -> dict:
    return {
        "revoked": device_id,
        # Whether the caller lost its own access, so a client can sign
        # itself out: not only its exact id, but its node too.
        "this_device": hits,
        "devices": rows(home, caller_device_id),
        "signins": signin_rows(home, caller_device_id),
    }


def _revoke_signin(home, caller_device_id, body: dict, device_id: str) -> dict:
    node = device_id[len(TAILNET_PREFIX):]
    row = pairing.load_signins(home).get(node) if node else None
    if row is None:
        raise Refused(404, "unknown_device",
                      "No device has signed in through Tailscale with that id.")
    name = row.get("node_name") or "That device"
    if row.get("revoked_at"):
        raise Refused(409, "already_revoked", f"{name} is already signed out.")
    hits = _caller_on_node(home, caller_device_id, node)
    if hits and body.get("confirm_this_device") is not True:
        raise Refused(
            409, "confirm_this_device",
            "That's the device you're using. "
            "Confirm to sign it out of the hub.")
    try:
        pairing.revoke_signin(home, node)
    except sshkeys.KeysNotSynced as exc:
        raise Refused(
            500, "ssh_key_left",
            f"Signed out, but a device paired from it still has its SSH key "
            f"in ~/.ssh/authorized_keys: {exc}") from exc
    return _answer(home, caller_device_id, device_id, hits)
