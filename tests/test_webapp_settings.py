"""The Settings page: its QR encoder, its data, and its pair/revoke writes."""

import json
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from quiver.github import gh
from quiver.remote import pairing
from quiver.remote.hub import save_hub_config
from quiver.webapp.pages import PageContext, PageError, _qr
from quiver.webapp.pages import settings as settings_page

from tests.test_remote_hub import HubBase

# ---------------------------------------------------------------- QR helpers

# "HELLO" at level M, version 1, mask 4: decoded by zbarimg when it was
# recorded, and kept so a change to the encoder shows up without one.
HELLO_M = [
    "#######.##.#..#######",
    "#.....#..##.#.#.....#",
    "#.###.#..####.#.###.#",
    "#.###.#.#..#..#.###.#",
    "#.###.#.#...#.#.###.#",
    "#.....#.#.##..#.....#",
    "#######.#.#.#.#######",
    "........#####........",
    "#...#.######.#####..#",
    "...###..#.###..#.####",
    "#.##..#.#.##..###..#.",
    "###..#...#...##.#....",
    "..#.###..#..###...##.",
    "........###.###..#.##",
    "#######.##..##...#.#.",
    "#.....#....##..#...#.",
    "#.###.#.#..#..###.#.#",
    "#.###.#....##....#.##",
    "#.###.#..###..####...",
    "#.....#..#...##......",
    "#######.#...#####.#.#",
]

PAIR_URL = "https://kai-home-server.taild6a23f.ts.net:8477/#pair=0Ol_Evny-OSYVP3QLNEl6GxapePaqECo"
DEEP_LINK = ("quiver://pair?host=kai-home-server.taild6a23f.ts.net&port=8477"
             "&token=oiXzLUI8gplt6g-ofdf9jvOSUE9C6eZz&scheme=https")

# Format information, by (level, mask), from ISO/IEC 18004 table C.1,
# written out here rather than computed, so the encoder's BCH code is
# checked against the standard's own values.
_FORMAT_LEVEL_BITS = {_qr.LOW: 1, _qr.MEDIUM: 0, _qr.QUARTILE: 3, _qr.HIGH: 2}


def _format_word(ecl, mask):
    data = _FORMAT_LEVEL_BITS[ecl] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


# GF(256) tables built here, apart from the encoder's multiply routine.
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _syndromes(block, nsym):
    """Each syndrome of a received block; all zero for a valid codeword."""
    out = []
    for i in range(nsym):
        acc = 0
        for b in block:
            acc = (_EXP[_LOG[acc] + i] if acc else 0) ^ b
        out.append(acc)
    return out


def _decode(modules):
    """A small reader, independent of the encoder's drawing code: read the
    format bits, unmask, collect codewords, de-interleave the blocks,
    check every block's Reed-Solomon syndromes and return the bytes of a
    single byte-mode segment, with the level and mask it found."""
    size = len(modules)
    version = (size - 17) // 4
    bits = 0
    # First copy of the format word, around the top-left finder.
    coords = [(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(14 - i, 8) for i in range(9, 15)]
    for i, (x, y) in enumerate(coords):
        bits |= int(modules[y][x]) << i
    found = [(e, m) for e in range(4) for m in range(8) if _format_word(e, m) == bits]
    assert len(found) == 1, f"format bits {bits:015b} match nothing"
    ecl, mask = found[0]
    # Second copy must agree.
    bits2 = 0
    coords2 = [(size - 1 - i, 8) for i in range(8)] + [(8, size - 15 + i) for i in range(8, 15)]
    for i, (x, y) in enumerate(coords2):
        bits2 |= int(modules[y][x]) << i
    assert bits2 == bits, "the two format copies differ"

    # Which modules are function patterns: build them on an empty code.
    probe = _qr.QrCode.__new__(_qr.QrCode)
    probe.version, probe.size, probe.ecl = version, size, ecl
    probe.modules = [[False] * size for _ in range(size)]
    probe._isfunction = [[False] * size for _ in range(size)]
    probe._draw_function_patterns()
    func = probe._isfunction

    masks = _qr._MASK_PATTERNS
    raw = []
    for right in range(size - 1, 0, -2):
        if right <= 6:
            right -= 1
        for vert in range(size):
            for j in range(2):
                x = right - j
                y = (size - 1 - vert) if ((right + 1) & 2) == 0 else vert
                if not func[y][x]:
                    raw.append(int(modules[y][x]) ^ int(masks[mask](x, y)))
    codewords = [int("".join(map(str, raw[i:i + 8])), 2) for i in range(0, len(raw) // 8 * 8, 8)]

    nblocks = _qr._NUM_ERROR_CORRECTION_BLOCKS[ecl][version]
    necc = _qr._ECC_CODEWORDS_PER_BLOCK[ecl][version]
    total = _qr._get_num_raw_data_modules(version) // 8
    short = nblocks - total % nblocks
    shortlen = total // nblocks - necc  # data bytes in a short block
    lens = [shortlen + (0 if b < short else 1) for b in range(nblocks)]
    blocks = [[] for _ in range(nblocks)]
    k = 0
    for i in range(max(lens)):
        for b in range(nblocks):
            if i < lens[b]:
                blocks[b].append(codewords[k])
                k += 1
    for i in range(necc):
        for b in range(nblocks):
            blocks[b].append(codewords[k])
            k += 1
    data = []
    for b, blk in enumerate(blocks):
        assert _syndromes(blk, necc) == [0] * necc, f"block {b} fails its RS check"
        data += blk[:lens[b]]
    bitstr = "".join(f"{b:08b}" for b in data)
    assert bitstr[:4] == "0100", "not a byte-mode segment"
    width = 8 if version <= 9 else 16
    n = int(bitstr[4:4 + width], 2)
    start = 4 + width
    payload = bytes(int(bitstr[start + 8 * i:start + 8 * i + 8], 2) for i in range(n))
    return payload, ecl, mask


def _svg_modules(svg):
    """The module grid an SVG from ``to_svg`` draws, read from its path."""
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    dim = int(root.get("viewBox").split()[2])
    grid = [[False] * dim for _ in range(dim)]
    path = root.find(f"{ns}path").get("d")
    for x, y in re.findall(r"M(\d+),(\d+)h1v1h-1z", path):
        grid[int(y)][int(x)] = True
    return grid


def _png(grid, path, scale=4):
    """A greyscale PNG of ``grid`` (border already included), stdlib only."""
    dim = len(grid) * scale
    raw = bytearray()
    for py in range(dim):
        raw.append(0)
        row = grid[py // scale]
        for px in range(dim):
            raw.append(0 if row[px // scale] else 255)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", dim, dim, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b""))


# ---------------------------------------------------------------- QR tests


class TestQrEncoder(unittest.TestCase):
    def test_known_pattern_for_hello(self):
        q = _qr.encode_text("HELLO", _qr.MEDIUM, boost_ecl=False)
        self.assertEqual((q.version, q.mask), (1, 4))
        drawn = ["".join("#" if m else "." for m in row) for row in q.modules]
        self.assertEqual(drawn, HELLO_M)

    def test_finders_timing_and_dark_module(self):
        q = _qr.encode_text(PAIR_URL)
        n = q.size
        finder = ["#######", "#.....#", "#.###.#", "#.###.#", "#.###.#", "#.....#", "#######"]
        for ox, oy in ((0, 0), (n - 7, 0), (0, n - 7)):
            got = ["".join("#" if q.modules[oy + y][ox + x] else "." for x in range(7)) for y in range(7)]
            self.assertEqual(got, finder, (ox, oy))
        for i in range(8, n - 8):
            self.assertEqual(q.modules[6][i], i % 2 == 0)
            self.assertEqual(q.modules[i][6], i % 2 == 0)
        self.assertTrue(q.modules[n - 8][8])

    def test_version_information_on_large_codes(self):
        q = _qr.encode_bytes(b"x" * 300, _qr.MEDIUM)
        self.assertGreaterEqual(q.version, 7)
        bits = 0
        for i in range(18):
            bits |= int(q.modules[i // 3][q.size - 11 + i % 3]) << i
        self.assertEqual(bits >> 12, q.version)
        # The 12 check bits make the 18-bit word divisible by the BCH generator.
        rem = bits
        for shift in range(17, 11, -1):
            if rem >> shift & 1:
                rem ^= 0x1F25 << (shift - 12)
        self.assertEqual(rem, 0)

    def test_reads_back_at_every_level_and_across_versions(self):
        for ecl in (_qr.LOW, _qr.MEDIUM, _qr.QUARTILE, _qr.HIGH):
            for n in (1, 17, 40, 120, 400, 1200):
                data = bytes((i * 37 + n) % 256 for i in range(n))
                try:
                    q = _qr.encode_bytes(data, ecl, boost_ecl=False)
                except _qr.DataTooLong:
                    continue
                payload, got_ecl, got_mask = _decode(q.modules)
                self.assertEqual(payload, data, (ecl, n, q.version))
                self.assertEqual((got_ecl, got_mask), (ecl, q.mask))

    def test_every_mask_reads_back(self):
        for mask in range(8):
            q = _qr.encode_text(DEEP_LINK, mask=mask)
            self.assertEqual(_decode(q.modules)[0], DEEP_LINK.encode())

    def test_level_is_boosted_while_the_version_holds(self):
        q = _qr.encode_text("HELLO", _qr.MEDIUM)
        self.assertEqual((q.version, q.ecl), (1, _qr.HIGH))

    def test_too_long_is_refused(self):
        with self.assertRaises(_qr.DataTooLong):
            _qr.encode_bytes(b"x" * 3000, _qr.HIGH)

    def test_svg_draws_the_modules_with_a_quiet_zone(self):
        q = _qr.encode_text(PAIR_URL)
        grid = _svg_modules(q.to_svg(border=4))
        self.assertEqual(len(grid), q.size + 8)
        self.assertFalse(any(grid[0]) or any(row[0] for row in grid))
        inner = [row[4:-4] for row in grid[4:-4]]
        self.assertEqual(inner, q.modules)
        self.assertEqual(_decode(inner)[0], PAIR_URL.encode())

    def test_svg_label_is_escaped(self):
        svg = _qr.svg("x", label='a "b" <c>')
        self.assertIn('aria-label="a &quot;b&quot; &lt;c&gt;"', svg)
        ET.fromstring(svg)

    @unittest.skipUnless(shutil.which("zbarimg"), "zbarimg is not installed")
    def test_zbarimg_reads_the_svg(self):
        with tempfile.TemporaryDirectory() as tmp:
            for text in (PAIR_URL, DEEP_LINK, "HELLO", "x" * 500):
                png = Path(tmp) / "qr.png"
                _png(_svg_modules(_qr.svg(text)), png)
                out = subprocess.run(["zbarimg", "-q", "--raw", str(png)],
                                     capture_output=True, text=True, timeout=30)
                self.assertEqual(out.stdout.rstrip("\n"), text)


# ---------------------------------------------------------------- page data


class _Settings(HubBase):
    def setUp(self):
        super().setUp()
        self.home = self.srv.home
        patcher = patch("quiver.remote.catalog.tailscale_ip", return_value="100.64.0.7")
        patcher.start()
        self.addCleanup(patcher.stop)

    def ctx(self, device_id=None):
        return PageContext(home=self.home, state=self.srv.state, device_id=device_id)

    def device(self, name):
        token = pairing.mint_pair_token(self.home)
        return pairing.exchange(self.home, token, name)

    def origin(self):
        return f"http://127.0.0.1:{self.srv.port}"

    def post(self, action, body, cred, origin=True):
        headers = {"Authorization": f"Bearer {cred['credential']}", "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = self.origin()
        status, data = self.srv.request("POST", f"/v1/app/settings/{action}", body=body, headers=headers)
        return status, json.loads(data)


class TestGet(_Settings):
    def test_needs_a_device(self):
        self.assertEqual(self.srv.request("GET", "/v1/app/settings")[0], 401)

    def test_remote_lists_devices_and_marks_this_one(self):
        me = self.device("laptop")
        phone = self.device("phone")
        status, data = self.srv.request("GET", "/v1/app/settings",
                                        headers={"Authorization": f"Bearer {me['credential']}"})
        self.assertEqual(status, 200, data)
        body = json.loads(data)
        rows = {d["name"]: d for d in body["remote"]["devices"]}
        self.assertTrue(rows["laptop"]["this_device"])
        self.assertFalse(rows["phone"]["this_device"])
        self.assertEqual(rows["phone"]["id"], phone["device_id"])
        self.assertNotIn("credential_hash", rows["phone"])
        self.assertTrue(body["remote"]["hub"]["ok"])
        self.assertEqual(body["remote"]["token_ttl_s"], pairing.TOKEN_TTL_S)
        self.assertEqual(body["machine"], self.srv.state.machine["name"])

    def test_hub_address_comes_from_the_saved_config(self):
        save_hub_config(self.home, "127.0.0.1", 8477, "https://box.example.ts.net:8477")
        remote = settings_page.get(self.ctx(), {})["remote"]
        self.assertEqual(remote["hub_addr"], "127.0.0.1:8477")
        self.assertEqual(remote["public_url"], "https://box.example.ts.net:8477")

    def test_pending_requests_carry_no_code_or_secret(self):
        pairing.request_pair(self.home, {"node_id": "n1", "node_name": "pixel", "login": "me@x"}, "pixel")
        rows = settings_page.get(self.ctx(), {})["remote"]["requests"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["node_name"], "pixel")
        self.assertFalse({"code", "poll_secret", "poll_secret_hash"} & set(rows[0]))

    def test_providers_are_masked_and_nothing_is_written(self):
        keys = self.home / ".quiver" / "secrets" / "providers" / ".api_keys"
        keys.parent.mkdir(parents=True)
        secret = "sk-proj-SECRETSECRETSECRET9876"
        keys.write_text(f"export OPENAI_API_KEY={secret}\n")
        body = settings_page.get(self.ctx(), {})
        rows = {r["id"]: r for r in body["providers"]["rows"]}
        self.assertTrue(rows["openai"]["has_key"])
        self.assertEqual(rows["openai"]["masked"], "sk-proj-***9876 (30)")
        self.assertFalse(rows["anthropic"]["has_key"])
        self.assertIsNone(rows["anthropic"]["masked"])
        self.assertNotIn("SECRET", json.dumps(body))
        self.assertEqual(body["providers"]["path"], "~/.quiver/secrets/providers/.api_keys")
        self.assertFalse((self.home / ".quiver" / "config" / "providers.json").exists())

    def test_providers_follow_providers_json_and_its_removals(self):
        config = self.home / ".quiver" / "config"
        config.mkdir(parents=True)
        (config / "providers.json").write_text(json.dumps({
            "acme": {"env_vars": ["ACME_API_KEY"], "url": "https://acme.example/keys"},
            "_removed": ["openai"],
        }))
        before = (config / "providers.json").read_text()
        ids = {r["id"] for r in settings_page.get(self.ctx(), {})["providers"]["rows"]}
        self.assertIn("acme", ids)
        self.assertNotIn("openai", ids)
        self.assertIn("anthropic", ids)
        self.assertEqual((config / "providers.json").read_text(), before)

    def test_a_corrupt_providers_json_is_reported_not_raised(self):
        config = self.home / ".quiver" / "config"
        config.mkdir(parents=True)
        (config / "providers.json").write_text("{nope")
        prov = settings_page.get(self.ctx(), {})["providers"]
        self.assertEqual(prov["rows"], [])
        self.assertTrue(prov["error"])

    def test_github_part(self):
        env = gh.GhEnv(installed=True, authenticated=True, login="octo")
        with patch("quiver.webapp.pages._github.probe", return_value=env) as probe:
            body = settings_page.get(self.ctx(), {"part": "github", "refresh": "1"})
        probe.assert_called_once_with(self.home, refresh=True)
        self.assertEqual(body["github"]["env"]["status"], "ok")
        self.assertEqual(body["github"]["env"]["login"], "octo")
        self.assertIsNone(body["github"]["catalog"])

    def test_unknown_part_is_refused(self):
        with self.assertRaises(PageError) as caught:
            settings_page.get(self.ctx(), {"part": "secrets"})
        self.assertEqual(caught.exception.status, 400)

    def test_page_script_is_bundled(self):
        status, data = self.srv.request("GET", "/app/pages.js")
        self.assertEqual(status, 200)
        self.assertIn(b'registerPage("settings"', data)


# ---------------------------------------------------------------- writes


class TestPair(_Settings):
    def test_pair_mints_a_token_that_pairs_a_device(self):
        me = self.device("laptop")
        status, body = self.post("pair", {}, me)
        self.assertEqual(status, 200, body)
        t = body["ticket"]
        self.assertEqual((t["host"], t["port"]), ("100.64.0.7", 8477))
        self.assertEqual(t["url"], f"http://100.64.0.7:8477/#pair={t['token']}")
        self.assertTrue(t["deep_link"].startswith("quiver://pair?host=100.64.0.7&port=8477&token="))
        self.assertEqual(t["expires_in_s"], pairing.TOKEN_TTL_S)
        # The QR codes carry exactly the two links.
        for key, text in (("web", t["url"]), ("app", t["deep_link"])):
            grid = _svg_modules(t["qr"][key])
            self.assertEqual(_decode([row[4:-4] for row in grid[4:-4]])[0], text.encode())
        # And the token is one the hub honours, once.
        status, data = self.srv.request("POST", "/v1/pair", {"token": t["token"], "name": "phone"})
        self.assertEqual(status, 200, data)
        status, _ = self.srv.request("POST", "/v1/pair", {"token": t["token"], "name": "again"})
        self.assertEqual(status, 403)

    def test_pair_links_use_the_public_url_behind_serve(self):
        save_hub_config(self.home, "127.0.0.1", 8477, "https://box.example.ts.net:8477")
        t = settings_page.post(self.ctx(), "pair", {})["ticket"]
        self.assertEqual(t["url"], f"https://box.example.ts.net:8477/#pair={t['token']}")
        q = parse_qs(urlsplit(t["deep_link"]).query)
        self.assertEqual((q["host"], q["port"], q["scheme"]), (["box.example.ts.net"], ["8477"], ["https"]))

    def test_pair_links_use_the_advertised_address(self):
        self.srv.state.base_url = "http://100.64.0.9:9000"
        t = settings_page.post(self.ctx(), "pair", {})["ticket"]
        self.assertEqual(t["url"], f"http://100.64.0.9:9000/#pair={t['token']}")

    def test_cross_site_and_unpaired_posts_are_refused(self):
        me = self.device("laptop")
        headers = {"Authorization": f"Bearer {me['credential']}", "Origin": "https://evil.example",
                   "Content-Type": "application/json"}
        status, _ = self.srv.request("POST", "/v1/app/settings/pair", body={}, headers=headers)
        self.assertEqual(status, 403)
        status, _ = self.srv.request("POST", "/v1/app/settings/pair", body={})
        self.assertEqual(status, 401)
        # No token was minted: the one device() spent is all there was.
        tokens = json.loads(pairing.tokens_file(self.home).read_text())["tokens"]
        self.assertEqual(tokens, [])

    def test_unknown_action(self):
        me = self.device("laptop")
        status, body = self.post("approve", {}, me)
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "unknown_action")


class TestRevoke(_Settings):
    def test_revoke_another_device(self):
        me = self.device("laptop")
        phone = self.device("phone")
        status, body = self.post("revoke", {"device_id": phone["device_id"]}, me)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["revoked"], phone["device_id"])
        self.assertFalse(body["this_device"])
        rows = {d["id"]: d for d in body["devices"]}
        self.assertTrue(rows[phone["device_id"]]["revoked"])
        self.assertTrue(pairing.load_devices(self.home)[phone["device_id"]]["revoked_at"])
        # The phone is signed out; this browser is not.
        status, _ = self.srv.request("GET", "/v1/app/settings",
                                     headers={"Authorization": f"Bearer {phone['credential']}"})
        self.assertEqual(status, 401)
        status, _ = self.srv.request("GET", "/v1/app/settings",
                                     headers={"Authorization": f"Bearer {me['credential']}"})
        self.assertEqual(status, 200)

    def test_this_device_needs_an_explicit_confirm(self):
        me = self.device("laptop")
        for body in ({"device_id": me["device_id"]},
                     {"device_id": me["device_id"], "confirm_this_device": "yes"}):
            status, reply = self.post("revoke", body, me)
            self.assertEqual(status, 409)
            self.assertEqual(reply["error"]["code"], "confirm_this_device")
        self.assertFalse(pairing.load_devices(self.home)[me["device_id"]]["revoked_at"])
        status, reply = self.post("revoke", {"device_id": me["device_id"], "confirm_this_device": True}, me)
        self.assertEqual(status, 200, reply)
        self.assertTrue(reply["this_device"])
        status, _ = self.srv.request("GET", "/v1/app/settings",
                                     headers={"Authorization": f"Bearer {me['credential']}"})
        self.assertEqual(status, 401)

    def test_refusals(self):
        me = self.device("laptop")
        phone = self.device("phone")
        pairing.revoke_device(self.home, phone["device_id"])
        def revoked():
            return {d: bool(r.get("revoked_at")) for d, r in pairing.load_devices(self.home).items()}

        before = revoked()
        for body, code, want in (
            ({}, 400, "bad_device"),
            ({"device_id": 7}, 400, "bad_device"),
            ({"device_id": "x" * 65}, 400, "bad_device"),
            ({"device_id": "0123456789abcdef"}, 404, "unknown_device"),
            ({"device_id": phone["device_id"]}, 409, "already_revoked"),
        ):
            status, reply = self.post("revoke", body, me)
            self.assertEqual((status, reply["error"]["code"]), (code, want), body)
        self.assertEqual(revoked(), before)

    def test_cross_site_revoke_is_refused(self):
        me = self.device("laptop")
        phone = self.device("phone")
        headers = {"Authorization": f"Bearer {me['credential']}", "Origin": "https://evil.example",
                   "Content-Type": "application/json"}
        status, _ = self.srv.request("POST", "/v1/app/settings/revoke",
                                     body={"device_id": phone["device_id"]}, headers=headers)
        self.assertEqual(status, 403)
        self.assertFalse(pairing.load_devices(self.home)[phone["device_id"]]["revoked_at"])


class TestOwnerTailnetCaller(_Settings):
    """The hub owner's own tailnet device, let in without pairing: its
    ``device_id`` is ``tailnet:<node>``, which no devices.json row has."""

    OWNER_IP = "100.64.0.9"

    def setUp(self):
        super().setUp()
        state = self.srv.state
        state.trust_proxy = True
        state.owner_login = "kai@github"
        state.whois = lambda ip: ({"node_id": "n-laptop", "login": "kai@github", "tagged": False}
                                  if ip == self.OWNER_IP else None)

    def headers(self, post=False):
        h = {"X-Forwarded-For": self.OWNER_IP, "X-Forwarded-Proto": "https", "X-Quiver-Pair": "1"}
        if post:
            h.update({"Origin": f"https://127.0.0.1:{self.srv.port}", "Content-Type": "application/json"})
        return h

    def owner_post(self, action, body):
        status, data = self.srv.request("POST", f"/v1/app/settings/{action}", body=body,
                                        headers=self.headers(post=True))
        return status, json.loads(data)

    def test_get_says_tailnet_and_marks_no_device(self):
        self.device("phone")
        status, data = self.srv.request("GET", "/v1/app/settings", headers=self.headers())
        self.assertEqual(status, 200, data)
        remote = json.loads(data)["remote"]
        self.assertEqual(remote["caller"], {"kind": "tailnet", "node": "n-laptop"})
        self.assertFalse(any(d["this_device"] for d in remote["devices"]))

    def test_paired_caller_is_a_device(self):
        me = self.device("laptop")
        caller = settings_page.get(self.ctx(me["device_id"]), {})["remote"]["caller"]
        self.assertEqual(caller, {"kind": "device", "device_id": me["device_id"]})

    def test_cannot_revoke_its_tailnet_identity(self):
        for body in ({"device_id": "tailnet:n-laptop"},
                     {"device_id": "tailnet:n-laptop", "confirm_this_device": True}):
            status, reply = self.owner_post("revoke", body)
            self.assertEqual((status, reply["error"]["code"]), (400, "not_paired"))
        with self.assertRaises(PageError) as caught:
            settings_page.post(self.ctx("tailnet:n-laptop"), "revoke",
                               {"device_id": "tailnet:n-laptop", "confirm_this_device": True})
        self.assertEqual(caught.exception.code, "not_paired")

    def test_owner_can_pair_and_revoke_other_devices(self):
        phone = self.device("phone")
        status, reply = self.owner_post("revoke", {"device_id": phone["device_id"]})
        self.assertEqual(status, 200, reply)
        self.assertFalse(reply["this_device"])
        status, reply = self.owner_post("pair", {})
        self.assertEqual(status, 200, reply)
        self.assertTrue(reply["ticket"]["token"])


if __name__ == "__main__":
    unittest.main()
