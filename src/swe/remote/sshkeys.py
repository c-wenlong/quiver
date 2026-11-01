"""Phone SSH keys in ``~/.ssh/authorized_keys``, and the host keys a
phone pins.

The phone's SSH console dials this machine's own sshd over the tailnet,
never the hub, so it still works when the hub is the thing that broke.
The hub's part is the setup: a paired phone posts the public half of a
key it keeps in its Secure Enclave, the hub stores it on the device's
``devices.json`` row, and ``sync`` writes one line per device into
``authorized_keys``. The phone pins the host keys ``GET /v1/ssh``
returns, so it never has to trust the first key sshd shows it.

Off until ``swe remote ssh --on``: writing ``authorized_keys`` changes
who can log in, so pairing alone never does it. The managed lines carry
a ``quiver-remote:<device id>`` comment and are rebuilt from
``devices.json`` on every change, so revoking a device (from any path)
or turning the feature off removes its line and leaves every other
line in the file alone.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import fcntl
import getpass
import hashlib
import json
import os
import socket
import struct
import threading
from pathlib import Path

from swe.paths import atomic_write_bytes, atomic_write_text, remote_dir_for
from swe.remote import pairing

SSH_PORT = 22
HOST_KEY_DIR = Path("/etc/ssh")
# The key types SwiftNIO SSH speaks; RSA is out on both ends.
KEY_TYPES = (
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
)
TAG = "quiver-remote:"
# A debugging shell needs a pty and nothing it could hand on.
OPTIONS = "no-agent-forwarding,no-X11-forwarding"
# An encoded P-521 key is ~170 characters; anything near this is junk.
_MAX_KEY_LEN = 1024

_CONFIG_BASENAME = "ssh.json"
_PROCESS_LOCK = threading.Lock()


@contextlib.contextmanager
def _lock(home: Path | None):
    """One ``authorized_keys`` rewrite at a time, across processes.

    Its own lock file, not pairing's: ``revoke_device`` calls ``sync``
    after releasing that one, and flock on a second descriptor of the
    same file would block this process against itself.
    """
    path = remote_dir_for(home)
    path.mkdir(parents=True, exist_ok=True)
    with _PROCESS_LOCK:
        with open(path / "ssh.lock", "w") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _home(home: Path | str | None) -> Path | None:
    return Path(home) if home is not None else None


def config_file(home: Path | str | None = None) -> Path:
    return remote_dir_for(_home(home)) / _CONFIG_BASENAME


def authorized_keys_file(home: Path | str | None = None) -> Path:
    return (_home(home) or Path.home()) / ".ssh" / "authorized_keys"


def enabled(home: Path | str | None = None) -> bool:
    try:
        data = json.loads(config_file(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("enabled") is True


def set_enabled(home: Path | str | None, on: bool) -> dict:
    """Turn the feature on or off, then bring ``authorized_keys`` in line."""
    atomic_write_text(
        config_file(home), json.dumps({"enabled": bool(on)}, indent=1),
        private=True,
    )
    return sync(home)


def _read_string(blob: bytes, at: int) -> tuple[bytes, int] | None:
    """One SSH wire ``string`` (uint32 length, then bytes) at ``at``."""
    if at + 4 > len(blob):
        return None
    (size,) = struct.unpack(">I", blob[at:at + 4])
    end = at + 4 + size
    if end > len(blob):
        return None
    return blob[at + 4:end], end


# An ecdsa key's curve name and uncompressed point (0x04, X, Y) size.
_ECDSA_CURVES = {
    "ecdsa-sha2-nistp256": (b"nistp256", 65),
    "ecdsa-sha2-nistp384": (b"nistp384", 97),
    "ecdsa-sha2-nistp521": (b"nistp521", 133),
}


def _blob_ok(kind: str, blob: bytes) -> bool:
    """True when ``blob`` is a whole ``kind`` public key and nothing else."""
    fields = []
    at = 0
    while at < len(blob):
        got = _read_string(blob, at)
        if got is None:
            return False
        value, at = got
        fields.append(value)
    if not fields or fields[0] != kind.encode("ascii"):
        return False
    if kind == "ssh-ed25519":
        return len(fields) == 2 and len(fields[1]) == 32
    curve, point_len = _ECDSA_CURVES[kind]
    return (len(fields) == 3 and fields[1] == curve
            and len(fields[2]) == point_len and fields[2][:1] == b"\x04")


def normalize_public_key(text: object) -> str | None:
    """``"<type> <base64>"`` for a well-formed public key, else None.

    The comment is dropped (the hub writes its own). The blob must be a
    whole key of the type it claims: its own type name, then the ed25519
    key's 32 bytes or the ecdsa curve and point, and nothing after.
    """
    if not isinstance(text, str) or len(text) > _MAX_KEY_LEN:
        return None
    if "\n" in text or "\r" in text:
        return None
    parts = text.split()
    if len(parts) < 2 or parts[0] not in KEY_TYPES:
        return None
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except (binascii.Error, ValueError):
        return None
    if not _blob_ok(parts[0], blob):
        return None
    return f"{parts[0]} {parts[1]}"


def fingerprint(public_key: str) -> str:
    """OpenSSH's ``SHA256:...`` fingerprint, as ``ssh-keygen -l`` prints."""
    blob = base64.b64decode(public_key.split()[1])
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii")
    return "SHA256:" + digest.rstrip("=")


def host_keys(key_dir: Path | None = None) -> list[str]:
    """This machine's sshd host public keys, in a type the phone speaks."""
    out = []
    for path in sorted((key_dir or HOST_KEY_DIR).glob("ssh_host_*_key.pub")):
        try:
            key = normalize_public_key(path.read_text(encoding="utf-8").strip())
        except OSError:
            continue
        if key and key not in out:
            out.append(key)
    return out


def sshd_listening(port: int = SSH_PORT, timeout: float = 1.0) -> bool:
    """True when something accepts TCP on this machine's SSH port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class KeysNotSynced(Exception):
    """``authorized_keys`` couldn't be rewritten after a change that was
    already saved (a revoke, say). The cause is ``__cause__``."""


_TAG_BYTES = TAG.encode("ascii")


def _managed(line: bytes) -> bool:
    return any(part.startswith(_TAG_BYTES) for part in line.split())


def _desired_lines(home: Path | None) -> dict[str, str]:
    """``{device_id: line}`` for every live device with a key."""
    out = {}
    for device_id, row in pairing.load_devices(home).items():
        key = normalize_public_key(row.get("ssh_public_key"))
        if row.get("revoked_at") or not key:
            continue
        out[device_id] = f"{OPTIONS} {key} {TAG}{device_id}"
    return out


def _read_lines(path: Path) -> list[bytes]:
    """The file's lines as raw bytes, split where sshd splits: at ``\\n``.

    Bytes, not text, so a key comment in any encoding (or a ``\\r``, or
    a Unicode line separator) comes back out exactly as it went in.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def sync(home: Path | str | None = None) -> dict:
    """Rewrite the managed lines of ``authorized_keys`` from ``devices.json``.

    Every line without the tag stays exactly as it was, in its place.
    The wanted state is read inside the lock: a sync that read it before
    a concurrent revoke and wrote after would put the revoked key back.
    Returns ``{"enabled", "installed": [device ids], "changed"}``.
    """
    home_path = _home(home)
    path = authorized_keys_file(home_path)
    with _lock(home_path):
        on = enabled(home_path)
        desired = _desired_lines(home_path) if on else {}
        old = _read_lines(path)
        lines = [ln for ln in old if not _managed(ln)]
        lines += [ln.encode("ascii") for ln in desired.values()]
        # Compared by line, so a file this never manages keeps its exact
        # bytes (a missing final newline included).
        changed = lines != old
        if changed:
            ssh_dir = path.parent
            if not ssh_dir.exists():
                # sshd's StrictModes refuses a group- or world-writable
                # ~/.ssh, so a new one starts at 0700.
                ssh_dir.mkdir(mode=0o700, parents=True)
            data = b"\n".join(lines) + b"\n" if lines else b""
            atomic_write_bytes(path, data, private=True)
    return {"enabled": on, "installed": sorted(desired), "changed": changed}


def installed_line(home: Path | str | None, device_id: str) -> bool:
    """True when the device's line is in ``authorized_keys`` right now."""
    row = pairing.load_devices(_home(home)).get(device_id) or {}
    key = normalize_public_key(row.get("ssh_public_key"))
    if not key or row.get("revoked_at"):
        return False
    want = f"{OPTIONS} {key} {TAG}{device_id}".encode("ascii")
    return want in _read_lines(authorized_keys_file(_home(home)))


def set_device_key(
    home: Path | str | None, device_id: str, public_key: str | None
) -> dict:
    """Store (or with None, clear) a device's key, then ``sync``.

    Raises ``KeyError`` for a device that isn't paired or was revoked.
    """
    home_path = _home(home)
    if not pairing.update_device(
        home_path, device_id, ssh_public_key=public_key
    ):
        raise KeyError(device_id)
    return sync(home_path)


def view(home: Path | str | None, device_id: str | None) -> dict:
    """``GET /v1/ssh``: what the phone needs to dial and pin this machine."""
    home_path = _home(home)
    on = enabled(home_path)
    row = pairing.load_devices(home_path).get(device_id or "") or {}
    key = normalize_public_key(row.get("ssh_public_key"))
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = os.environ.get("USER", "")
    return {
        "enabled": on,
        "user": user,
        "port": SSH_PORT,
        "listening": sshd_listening(),
        "host_keys": host_keys(),
        "device": {
            "public_key": key,
            # What sshd will read, not what was asked for: a rewrite
            # that failed leaves this false.
            "installed": bool(on and device_id
                              and installed_line(home_path, device_id)),
        },
    }
