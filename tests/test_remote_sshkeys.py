"""sshkeys.py: phone keys in authorized_keys, host keys, /v1/ssh routes."""

import base64
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from quiver.remote import pairing, sshkeys
from quiver.remote.commands import cmd_remote

try:
    from _tempdir import temp_dir
    from test_remote_hub import _Server, _session
except ImportError:  # run as `python3 -m unittest tests.test_remote_sshkeys`
    from tests._tempdir import temp_dir
    from tests.test_remote_hub import _Server, _session


def _s(data):
    """One SSH wire string."""
    return struct.pack(">I", len(data)) + data


_POINT = {"nistp256": 65, "nistp384": 97, "nistp521": 133}


def _key(kind="ssh-ed25519", body=None, inner=None, fields=None):
    """A made-up OpenSSH public key line of ``kind``: an ed25519 key's 32
    bytes (``body``) or an ecdsa key's curve and point, unless ``fields``
    replaces what follows the type name."""
    if fields is None:
        if kind.startswith("ecdsa-"):
            curve = kind.rsplit("-", 1)[1]
            fields = [curve.encode(), b"\x04" + b"\x02" * (_POINT[curve] - 1)]
        else:
            fields = [body if body is not None else b"\x01" * 32]
    blob = _s((inner or kind).encode("ascii")) + b"".join(_s(f) for f in fields)
    return f"{kind} {base64.b64encode(blob).decode('ascii')}"


def _device(home, name="phone"):
    token = pairing.mint_pair_token(home)
    return pairing.exchange(home, token, name)["device_id"]


class TestNormalize(unittest.TestCase):
    def test_keeps_type_and_blob_drops_comment(self):
        key = _key()
        self.assertEqual(sshkeys.normalize_public_key(key + " me@phone"), key)

    def test_ecdsa_types_pass(self):
        for kind in ("ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384",
                     "ecdsa-sha2-nistp521"):
            self.assertIsNotNone(sshkeys.normalize_public_key(_key(kind)))

    def test_rejects(self):
        for bad in (
            None, 7, "", "ssh-ed25519",
            _key("ssh-rsa"),
            _key("ssh-ed25519", inner="ssh-rsa"),
            "ssh-ed25519 !!!notbase64",
            _key() + "\n" + _key(),
            "ssh-ed25519 " + "A" * 2000,
            # Right type name, wrong or missing key material.
            _key(fields=[]),
            _key(body=b"\x01" * 31),
            _key(fields=[b"\x01" * 32, b"extra"]),
            _key("ecdsa-sha2-nistp256", fields=[]),
            _key("ecdsa-sha2-nistp256", fields=[b"nistp384", b"\x04" + b"\x02" * 64]),
            _key("ecdsa-sha2-nistp256", fields=[b"nistp256", b"\x03" + b"\x02" * 64]),
            _key("ecdsa-sha2-nistp256", fields=[b"nistp256", b"\x04" + b"\x02" * 10]),
        ):
            self.assertIsNone(sshkeys.normalize_public_key(bad), bad)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "needs ssh-keygen")
    def test_fingerprint_matches_ssh_keygen(self):
        tmp = temp_dir(self)
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f",
             str(tmp / "k")], check=True)
        pub = (tmp / "k.pub").read_text().strip()
        out = subprocess.run(
            ["ssh-keygen", "-l", "-E", "sha256", "-f", str(tmp / "k.pub")],
            capture_output=True, text=True, check=True).stdout
        self.assertEqual(sshkeys.fingerprint(pub), out.split()[1])


class TestHostKeys(unittest.TestCase):
    def test_reads_supported_types_only(self):
        tmp = temp_dir(self)
        (tmp / "ssh_host_ed25519_key.pub").write_text(_key() + " root@box\n")
        (tmp / "ssh_host_rsa_key.pub").write_text(_key("ssh-rsa") + "\n")
        (tmp / "ssh_host_ecdsa_key.pub").write_text(
            _key("ecdsa-sha2-nistp256") + "\n")
        (tmp / "ssh_host_ed25519_key").write_text("PRIVATE, never read")
        self.assertEqual(
            sshkeys.host_keys(tmp),
            [_key("ecdsa-sha2-nistp256"), _key()],
        )


class TestSync(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        self.ak = sshkeys.authorized_keys_file(self.home)

    def test_off_by_default_and_creates_nothing(self):
        did = _device(self.home)
        sshkeys.set_device_key(self.home, did, _key())
        self.assertFalse(sshkeys.enabled(self.home))
        self.assertFalse(self.ak.exists())

    def test_on_writes_tagged_line_with_safe_modes(self):
        did = _device(self.home)
        sshkeys.set_device_key(self.home, did, _key())
        result = sshkeys.set_enabled(self.home, True)
        self.assertEqual(result["installed"], [did])
        self.assertEqual(
            self.ak.read_text(),
            f"{sshkeys.OPTIONS} {_key()} {sshkeys.TAG}{did}\n",
        )
        self.assertEqual(stat.S_IMODE(self.ak.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.ak.stat().st_mode), 0o600)

    def test_other_lines_stay_put(self):
        self.ak.parent.mkdir(mode=0o700)
        mine = ["ssh-ed25519 AAAAmine laptop", "# a comment",
                "ecdsa-sha2-nistp256 AAAAother desktop"]
        self.ak.write_text("\n".join(mine) + "\n")
        self.ak.chmod(0o644)
        did = _device(self.home)
        sshkeys.set_device_key(self.home, did, _key())
        sshkeys.set_enabled(self.home, True)
        lines = self.ak.read_text().splitlines()
        self.assertEqual(lines[:3], mine)
        self.assertTrue(lines[3].endswith(f"{sshkeys.TAG}{did}"))
        # An existing file keeps its own mode.
        self.assertEqual(stat.S_IMODE(self.ak.stat().st_mode), 0o644)
        sshkeys.set_enabled(self.home, False)
        self.assertEqual(self.ak.read_text().splitlines(), mine)

    def test_other_lines_keep_their_exact_bytes(self):
        self.ak.parent.mkdir(mode=0o700)
        mine = (b"ssh-ed25519 AAAAmine caf\xe9 latin-1\n"
                b"ssh-ed25519 AAAAcrlf windows\r\n"
                b"ssh-ed25519 AAAAsep a\xe2\x80\xa8b\n")
        self.ak.write_bytes(mine)
        did = _device(self.home)
        sshkeys.set_device_key(self.home, did, _key())
        sshkeys.set_enabled(self.home, True)
        self.assertTrue(self.ak.read_bytes().startswith(mine))
        sshkeys.set_enabled(self.home, False)
        self.assertEqual(self.ak.read_bytes(), mine)

    def test_sync_reads_devices_inside_its_lock(self):
        # A sync that read the keys, then paused, must not write them back
        # over a revoke that finished meanwhile.
        did = _device(self.home)
        sshkeys.set_enabled(self.home, True)
        sshkeys.set_device_key(self.home, did, _key())
        read = threading.Event()
        go = threading.Event()
        real = sshkeys._desired_lines
        calls = []

        def slow(home):
            out = real(home)
            if not calls:
                calls.append(sshkeys._PROCESS_LOCK.locked())
                read.set()
                go.wait(5)
            return out

        def revoked():
            return pairing.load_devices(self.home)[did].get("revoked_at")

        with patch.object(sshkeys, "_desired_lines", slow):
            first = threading.Thread(target=sshkeys.sync, args=(self.home,))
            first.start()
            self.assertTrue(read.wait(5))
            revoke = threading.Thread(
                target=pairing.revoke_device, args=(self.home, did))
            revoke.start()
            # The revoke is saved; its own sync now queues on the lock.
            deadline = time.monotonic() + 5
            while not revoked() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(revoked())
            go.set()
            first.join(5)
            revoke.join(5)
        self.assertFalse(first.is_alive() or revoke.is_alive())
        self.assertEqual(calls, [True])  # the read ran inside the lock
        self.assertNotIn(did, self.ak.read_text())

    def test_installed_means_in_the_file(self):
        did = _device(self.home)
        sshkeys.set_enabled(self.home, True)
        sshkeys.set_device_key(self.home, did, _key())
        self.assertTrue(sshkeys.view(self.home, did)["device"]["installed"])
        with patch.object(sshkeys, "atomic_write_bytes",
                          side_effect=OSError("read-only")):
            with self.assertRaises(OSError):
                sshkeys.set_device_key(self.home, did, _key(body=b"\x09" * 32))
        view = sshkeys.view(self.home, did)
        self.assertEqual(view["device"]["public_key"], _key(body=b"\x09" * 32))
        self.assertFalse(view["device"]["installed"])

    def test_revoke_tells_a_failed_save_from_a_failed_cleanup(self):
        did = _device(self.home)
        sshkeys.set_enabled(self.home, True)
        sshkeys.set_device_key(self.home, did, _key())
        with patch.object(pairing, "_save_devices",
                          side_effect=OSError("disk full")):
            with self.assertRaises(OSError) as caught:
                pairing.revoke_device(self.home, did)
        self.assertNotIsInstance(caught.exception, sshkeys.KeysNotSynced)
        self.assertFalse(pairing.load_devices(self.home)[did].get("revoked_at"))
        with patch.object(sshkeys, "atomic_write_bytes",
                          side_effect=OSError("read-only")):
            with self.assertRaises(sshkeys.KeysNotSynced):
                pairing.revoke_device(self.home, did)
        self.assertTrue(pairing.load_devices(self.home)[did]["revoked_at"])

    def test_untouched_file_keeps_exact_bytes(self):
        self.ak.parent.mkdir(mode=0o700)
        self.ak.write_text("ssh-ed25519 AAAAmine laptop")  # no newline
        result = sshkeys.set_enabled(self.home, True)
        self.assertFalse(result["changed"])
        self.assertEqual(self.ak.read_text(), "ssh-ed25519 AAAAmine laptop")

    def test_revoke_removes_the_line(self):
        a, b = _device(self.home, "a"), _device(self.home, "b")
        sshkeys.set_device_key(self.home, a, _key(body=b"\x0a" * 32))
        sshkeys.set_device_key(self.home, b, _key(body=b"\x0b" * 32))
        sshkeys.set_enabled(self.home, True)
        pairing.revoke_device(self.home, a)
        text = self.ak.read_text()
        self.assertNotIn(f"{sshkeys.TAG}{a}", text)
        self.assertIn(f"{sshkeys.TAG}{b}", text)

    def test_clearing_a_key_removes_the_line(self):
        did = _device(self.home)
        sshkeys.set_enabled(self.home, True)
        sshkeys.set_device_key(self.home, did, _key())
        sshkeys.set_device_key(self.home, did, None)
        self.assertEqual(self.ak.read_text(), "")
        self.assertNotIn("ssh_public_key",
                         pairing.load_devices(self.home)[did])

    def test_unknown_or_revoked_device_refused(self):
        with self.assertRaises(KeyError):
            sshkeys.set_device_key(self.home, "nope", _key())
        did = _device(self.home)
        pairing.revoke_device(self.home, did)
        with self.assertRaises(KeyError):
            sshkeys.set_device_key(self.home, did, _key())


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.srv = _Server(sessions=[_session()])
        self.addCleanup(self.srv.stop)
        cred = self.srv.pair()
        self.did = cred["device_id"]
        self.hdr = {"Authorization": f"Bearer {cred['credential']}"}

    def _view(self):
        status, data = self.srv.request("GET", "/v1/ssh", headers=self.hdr)
        self.assertEqual(status, 200)
        return json.loads(data)

    def test_needs_auth(self):
        self.assertEqual(self.srv.request("GET", "/v1/ssh")[0], 401)
        self.assertEqual(self.srv.request(
            "POST", "/v1/ssh/key", {"public_key": _key()})[0], 401)

    def test_view_shape(self):
        with patch.object(sshkeys, "host_keys", return_value=[_key()]):
            view = self._view()
        self.assertEqual(view["port"], 22)
        self.assertEqual(view["host_keys"], [_key()])
        self.assertFalse(view["enabled"])
        self.assertEqual(view["device"],
                         {"public_key": None, "installed": False})
        self.assertIn("listening", view)
        self.assertTrue(view["user"])

    def test_bad_key_is_422(self):
        status, data = self.srv.request(
            "POST", "/v1/ssh/key", {"public_key": _key("ssh-rsa")},
            headers=self.hdr)
        self.assertEqual(status, 422)
        self.assertEqual(json.loads(data)["error"]["code"], "bad_key")

    def test_register_install_and_clear(self):
        sshkeys.set_enabled(self.srv.home, True)
        status, data = self.srv.request(
            "POST", "/v1/ssh/key", {"public_key": _key() + " phone"},
            headers=self.hdr)
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["device"],
                         {"public_key": _key(), "installed": True})
        ak = sshkeys.authorized_keys_file(self.srv.home)
        self.assertIn(f"{sshkeys.TAG}{self.did}", ak.read_text())
        status, _ = self.srv.request(
            "POST", "/v1/ssh/key", {"public_key": ""}, headers=self.hdr)
        self.assertEqual(status, 200)
        self.assertEqual(ak.read_text(), "")
        self.assertIsNone(self._view()["device"]["public_key"])


class TestCommand(unittest.TestCase):
    def setUp(self):
        self.home = temp_dir(self)
        env = patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def _run(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), \
                patch.object(sshkeys, "sshd_listening", return_value=True), \
                patch.object(sshkeys, "host_keys", return_value=[_key()]):
            rc = cmd_remote(["ssh", *args])
        return rc, out.getvalue()

    def test_on_json_lists_devices(self):
        did = _device(self.home)
        sshkeys.set_device_key(self.home, did, _key())
        rc, out = self._run("--on", "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)["data"]
        self.assertTrue(data["enabled"])
        self.assertEqual(data["host_keys"][0]["fingerprint"],
                         sshkeys.fingerprint(_key()))
        self.assertEqual(data["devices"][0]["id"], did)
        self.assertTrue(data["devices"][0]["installed"])
        self.assertIn(did, sshkeys.authorized_keys_file(self.home).read_text())

    def test_off_and_on_together_refused(self):
        rc, _ = self._run("--on", "--off")
        self.assertEqual(rc, 1)

    def test_plain_status(self):
        rc, out = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("swe remote ssh --on", out)


if __name__ == "__main__":
    unittest.main()
