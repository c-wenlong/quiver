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


def view(home: Path | str | None, caller_device_id: str | None) -> dict:
    """``GET /v1/devices``: the device list plus the pending pair
    requests the same screen approves from — the rows
    ``GET /v1/pair/requests`` already serves (no codes or secrets)."""
    return {
        "devices": rows(home, caller_device_id),
        "requests": pairing.list_requests(home),
    }


def revoke(
    home: Path | str | None, caller_device_id: str | None, body: dict
) -> dict:
    """``POST /v1/devices/revoke`` ``{device_id, confirm_this_device?}``.

    Signs one paired device out (``pairing.revoke_device``, which also
    drops its ``authorized_keys`` line) and answers the refreshed list.
    Refusals raise ``Refused``: ``400 bad_device``, ``400 not_paired``
    (a ``tailnet:`` id — a sign-in, not a pairing), ``404
    unknown_device``, ``409 already_revoked``, ``409
    confirm_this_device`` for the caller's own row without
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
        # The owner's tailnet sign-in isn't a pairing: there is no row
        # to revoke, and this must never read as signing anything out.
        raise Refused(
            400, "not_paired",
            "That's a Tailscale sign-in, not a paired device. "
            "There is nothing to revoke.")
    row = pairing.load_devices(home).get(device_id)
    if row is None:
        raise Refused(404, "unknown_device", "No paired device has that id.")
    if row.get("revoked_at"):
        raise Refused(
            409, "already_revoked",
            f"{row.get('name') or 'That device'} is already signed out.")
    if device_id == caller_device_id \
            and body.get("confirm_this_device") is not True:
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
    return {
        "revoked": device_id,
        "this_device": device_id == caller_device_id,
        "devices": rows(home, caller_device_id),
    }
