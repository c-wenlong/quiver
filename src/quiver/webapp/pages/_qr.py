"""A QR code encoder, stdlib only, for the Settings page's pair codes.

The web app has no QR library and loads nothing from a CDN, so the hub
draws the code itself and sends it as SVG. This is a port of the byte
mode of Project Nayuki's QR Code generator library (Python edition),
reduced to what a pair link needs: one byte segment, versions 1 to 40,
every error correction level, and the automatic mask choice. The
algorithm, tables and penalty rules are Nayuki's; the names are kept so
the two can be read side by side.

Original licence (MIT), kept as its terms require:

    QR Code generator library (Python)

    Copyright (c) Project Nayuki. (MIT License)
    https://www.nayuki.io/page/qr-code-generator-library

    Permission is hereby granted, free of charge, to any person obtaining
    a copy of this software and associated documentation files (the
    "Software"), to deal in the Software without restriction, including
    without limitation the rights to use, copy, modify, merge, publish,
    distribute, sublicense, and/or sell copies of the Software, and to
    permit persons to whom the Software is furnished to do so, subject to
    the following conditions:
    - The above copyright notice and this permission notice shall be
      included in all copies or substantial portions of the Software.
    - The Software is provided "as is", without warranty of any kind,
      express or implied, including but not limited to the warranties of
      merchantability, fitness for a particular purpose and
      noninfringement. In no event shall the authors or copyright holders
      be liable for any claim, damages or other liability, whether in an
      action of contract, tort or otherwise, arising from, out of or in
      connection with the Software or the use or other dealings in the
      Software.

A leading underscore keeps this module off ``/v1/app/<page>``.
"""

from __future__ import annotations

import collections
import itertools

# Error correction levels: index into the tables, and the two format bits.
LOW, MEDIUM, QUARTILE, HIGH = 0, 1, 2, 3
_FORMAT_BITS = (1, 0, 3, 2)
LEVELS = {"L": LOW, "M": MEDIUM, "Q": QUARTILE, "H": HIGH}

MIN_VERSION, MAX_VERSION = 1, 40

_PENALTY_N1 = 3
_PENALTY_N2 = 3
_PENALTY_N3 = 40
_PENALTY_N4 = 10

# Index 0 is padding: there is no version 0.
_ECC_CODEWORDS_PER_BLOCK = (
    # 0,  1,  2,  3,  4,  5,  6,  7,  8,  9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40
    (-1,  7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Low
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),  # Medium
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # Quartile
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),  # High
)

_NUM_ERROR_CORRECTION_BLOCKS = (
    # 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4,  4,  4,  4,  4,  6,  6,  6,  6,  7,  8,  8,  9,  9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),  # Low
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5,  5,  8,  9,  9, 10, 10, 11, 13, 14, 16, 17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),  # Medium
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8,  8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),  # Quartile
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),  # High
)


class DataTooLong(ValueError):
    """The text does not fit in a version 40 code at this level."""


def _get_bit(x: int, i: int) -> bool:
    return (x >> i) & 1 != 0


def _byte_count_bits(version: int) -> int:
    """Width of the byte segment's character count field."""
    return 8 if version <= 9 else 16


def _get_num_raw_data_modules(ver: int) -> int:
    """Modules free for data and ECC once the function patterns are drawn."""
    result = (16 * ver + 128) * ver + 64
    if ver >= 2:
        numalign = ver // 7 + 2
        result -= (25 * numalign - 10) * numalign - 55
        if ver >= 7:
            result -= 36
    return result


def _get_num_data_codewords(ver: int, ecl: int) -> int:
    return (_get_num_raw_data_modules(ver) // 8
            - _ECC_CODEWORDS_PER_BLOCK[ecl][ver]
            * _NUM_ERROR_CORRECTION_BLOCKS[ecl][ver])


def _reed_solomon_multiply(x: int, y: int) -> int:
    """Product in GF(2^8/0x11D)."""
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _reed_solomon_compute_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _reed_solomon_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _reed_solomon_multiply(root, 0x02)
    return result


def _reed_solomon_compute_remainder(data, divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _reed_solomon_multiply(coef, factor)
    return result


class QrCode:
    """A drawn code: ``modules[y][x]`` is True for a dark module."""

    def __init__(self, version: int, ecl: int, datacodewords: bytes, mask: int = -1):
        if not MIN_VERSION <= version <= MAX_VERSION:
            raise ValueError("version out of range")
        if not -1 <= mask <= 7:
            raise ValueError("mask out of range")
        self.version = version
        self.size = version * 4 + 17
        self.ecl = ecl
        self.modules = [[False] * self.size for _ in range(self.size)]
        self._isfunction = [[False] * self.size for _ in range(self.size)]
        self._draw_function_patterns()
        self._draw_codewords(self._add_ecc_and_interleave(bytearray(datacodewords)))
        if mask == -1:
            minpenalty = 1 << 32
            for i in range(8):
                self._apply_mask(i)
                self._draw_format_bits(i)
                penalty = self._get_penalty_score()
                if penalty < minpenalty:
                    mask = i
                    minpenalty = penalty
                self._apply_mask(i)  # XOR again undoes it
        self.mask = mask
        self._apply_mask(mask)
        self._draw_format_bits(mask)
        del self._isfunction

    # -- function patterns ------------------------------------------------

    def _draw_function_patterns(self) -> None:
        for i in range(self.size):
            self._set_function_module(6, i, i % 2 == 0)
            self._set_function_module(i, 6, i % 2 == 0)
        self._draw_finder_pattern(3, 3)
        self._draw_finder_pattern(self.size - 4, 3)
        self._draw_finder_pattern(3, self.size - 4)
        alignpatpos = self._get_alignment_pattern_positions()
        numalign = len(alignpatpos)
        skips = ((0, 0), (0, numalign - 1), (numalign - 1, 0))
        for i in range(numalign):
            for j in range(numalign):
                if (i, j) not in skips:
                    self._draw_alignment_pattern(alignpatpos[i], alignpatpos[j])
        self._draw_format_bits(0)  # a placeholder until the mask is chosen
        self._draw_version()

    def _draw_format_bits(self, mask: int) -> None:
        data = _FORMAT_BITS[self.ecl] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        # First copy, around the top left finder.
        for i in range(0, 6):
            self._set_function_module(8, i, _get_bit(bits, i))
        self._set_function_module(8, 7, _get_bit(bits, 6))
        self._set_function_module(8, 8, _get_bit(bits, 7))
        self._set_function_module(7, 8, _get_bit(bits, 8))
        for i in range(9, 15):
            self._set_function_module(14 - i, 8, _get_bit(bits, i))
        # Second copy, split between the other two finders.
        for i in range(0, 8):
            self._set_function_module(self.size - 1 - i, 8, _get_bit(bits, i))
        for i in range(8, 15):
            self._set_function_module(8, self.size - 15 + i, _get_bit(bits, i))
        self._set_function_module(8, self.size - 8, True)  # always dark

    def _draw_version(self) -> None:
        if self.version < 7:
            return
        rem = self.version
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = self.version << 12 | rem
        for i in range(18):
            bit = _get_bit(bits, i)
            a = self.size - 11 + i % 3
            b = i // 3
            self._set_function_module(a, b, bit)
            self._set_function_module(b, a, bit)

    def _draw_finder_pattern(self, x: int, y: int) -> None:
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                xx, yy = x + dx, y + dy
                if 0 <= xx < self.size and 0 <= yy < self.size:
                    self._set_function_module(xx, yy, max(abs(dx), abs(dy)) not in (2, 4))

    def _draw_alignment_pattern(self, x: int, y: int) -> None:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set_function_module(x + dx, y + dy, max(abs(dx), abs(dy)) != 1)

    def _set_function_module(self, x: int, y: int, isdark: bool) -> None:
        self.modules[y][x] = isdark
        self._isfunction[y][x] = True

    def _get_alignment_pattern_positions(self) -> list[int]:
        ver = self.version
        if ver == 1:
            return []
        numalign = ver // 7 + 2
        step = (ver * 8 + numalign * 3 + 5) // (numalign * 4 - 4) * 2
        result = [(self.size - 7 - i * step) for i in range(numalign - 1)] + [6]
        return list(reversed(result))

    # -- codewords --------------------------------------------------------

    def _add_ecc_and_interleave(self, data: bytearray) -> bytes:
        ver, ecl = self.version, self.ecl
        if len(data) != _get_num_data_codewords(ver, ecl):
            raise ValueError("wrong number of data codewords")
        numblocks = _NUM_ERROR_CORRECTION_BLOCKS[ecl][ver]
        blockecclen = _ECC_CODEWORDS_PER_BLOCK[ecl][ver]
        rawcodewords = _get_num_raw_data_modules(ver) // 8
        numshortblocks = numblocks - rawcodewords % numblocks
        shortblocklen = rawcodewords // numblocks
        blocks = []
        rsdiv = _reed_solomon_compute_divisor(blockecclen)
        k = 0
        for i in range(numblocks):
            dat = data[k: k + shortblocklen - blockecclen + (0 if i < numshortblocks else 1)]
            k += len(dat)
            ecc = _reed_solomon_compute_remainder(dat, rsdiv)
            if i < numshortblocks:
                dat.append(0)
            blocks.append(dat + bytes(ecc))
        result = bytearray()
        for i in range(len(blocks[0])):
            for j, blk in enumerate(blocks):
                # The padding byte of a short block is skipped.
                if i != shortblocklen - blockecclen or j >= numshortblocks:
                    result.append(blk[i])
        return bytes(result)

    def _draw_codewords(self, data: bytes) -> None:
        i = 0
        # Two-module columns, right to left, zigzagging up and down.
        for right in range(self.size - 1, 0, -2):
            if right <= 6:
                right -= 1  # skip the vertical timing column
            for vert in range(self.size):
                for j in range(2):
                    x = right - j
                    upward = (right + 1) & 2 == 0
                    y = (self.size - 1 - vert) if upward else vert
                    if not self._isfunction[y][x] and i < len(data) * 8:
                        self.modules[y][x] = _get_bit(data[i >> 3], 7 - (i & 7))
                        i += 1
        # Any remainder bits (0 to 7) stay light, as initialised.

    def _apply_mask(self, mask: int) -> None:
        masker = _MASK_PATTERNS[mask]
        for y in range(self.size):
            row, func = self.modules[y], self._isfunction[y]
            for x in range(self.size):
                if not func[x] and masker(x, y):
                    row[x] = not row[x]

    # -- mask penalty -----------------------------------------------------

    def _get_penalty_score(self) -> int:
        result = 0
        size = self.size
        modules = self.modules
        for y in range(size):
            runcolor = False
            runx = 0
            runhistory = collections.deque([0] * 7, 7)
            for x in range(size):
                if modules[y][x] == runcolor:
                    runx += 1
                    if runx == 5:
                        result += _PENALTY_N1
                    elif runx > 5:
                        result += 1
                else:
                    self._finder_penalty_add_history(runx, runhistory)
                    if not runcolor:
                        result += self._finder_penalty_count_patterns(runhistory) * _PENALTY_N3
                    runcolor = modules[y][x]
                    runx = 1
            result += self._finder_penalty_terminate_and_count(runcolor, runx, runhistory) * _PENALTY_N3
        for x in range(size):
            runcolor = False
            runy = 0
            runhistory = collections.deque([0] * 7, 7)
            for y in range(size):
                if modules[y][x] == runcolor:
                    runy += 1
                    if runy == 5:
                        result += _PENALTY_N1
                    elif runy > 5:
                        result += 1
                else:
                    self._finder_penalty_add_history(runy, runhistory)
                    if not runcolor:
                        result += self._finder_penalty_count_patterns(runhistory) * _PENALTY_N3
                    runcolor = modules[y][x]
                    runy = 1
            result += self._finder_penalty_terminate_and_count(runcolor, runy, runhistory) * _PENALTY_N3
        for y in range(size - 1):
            for x in range(size - 1):
                if modules[y][x] == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                    result += _PENALTY_N2
        dark = sum(row.count(True) for row in modules)
        total = size ** 2
        # The smallest k with (45-5k)% <= dark <= (55+5k)%.
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        result += k * _PENALTY_N4
        return result

    def _finder_penalty_count_patterns(self, runhistory) -> int:
        n = runhistory[1]
        core = (n > 0 and runhistory[2] == runhistory[4] == runhistory[5] == n
                and runhistory[3] == n * 3)
        return ((1 if (core and runhistory[0] >= n * 4 and runhistory[6] >= n) else 0)
                + (1 if (core and runhistory[6] >= n * 4 and runhistory[0] >= n) else 0))

    def _finder_penalty_terminate_and_count(self, currentruncolor: bool,
                                            currentrunlength: int, runhistory) -> int:
        if currentruncolor:  # end the dark run
            self._finder_penalty_add_history(currentrunlength, runhistory)
            currentrunlength = 0
        currentrunlength += self.size  # the light border after the last run
        self._finder_penalty_add_history(currentrunlength, runhistory)
        return self._finder_penalty_count_patterns(runhistory)

    def _finder_penalty_add_history(self, currentrunlength: int, runhistory) -> None:
        if runhistory[0] == 0:
            currentrunlength += self.size  # the light border before the first run
        runhistory.appendleft(currentrunlength)

    # -- output -----------------------------------------------------------

    def to_svg(self, border: int = 4, label: str = "QR code") -> str:
        """The code as a standalone SVG: dark modules on a white square
        with a quiet zone of ``border`` modules, scaled by its container."""
        if border < 0:
            raise ValueError("border must not be negative")
        dim = self.size + border * 2
        parts = []
        for y in range(self.size):
            row = self.modules[y]
            for x in range(self.size):
                if row[x]:
                    parts.append(f"M{x + border},{y + border}h1v1h-1z")
        title = (label.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" '
            f'shape-rendering="crispEdges" role="img" aria-label="{title}">'
            f'<rect width="{dim}" height="{dim}" fill="#fff"/>'
            f'<path d="{"".join(parts)}" fill="#000"/></svg>'
        )


_MASK_PATTERNS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


def encode_bytes(data: bytes, ecl: int = MEDIUM, *, mask: int = -1,
                 boost_ecl: bool = True, min_version: int = MIN_VERSION,
                 max_version: int = MAX_VERSION) -> QrCode:
    """``data`` as one byte-mode segment in the smallest version that
    holds it; with ``boost_ecl`` the level rises while it still fits."""
    data = bytes(data)
    if ecl not in (LOW, MEDIUM, QUARTILE, HIGH):
        raise ValueError("unknown error correction level")
    if not MIN_VERSION <= min_version <= max_version <= MAX_VERSION:
        raise ValueError("bad version range")
    for version in range(min_version, max_version + 1):
        countbits = _byte_count_bits(version)
        usedbits = 4 + countbits + len(data) * 8
        if len(data) < (1 << countbits) and usedbits <= _get_num_data_codewords(version, ecl) * 8:
            break
        if version >= max_version:
            raise DataTooLong(f"{len(data)} bytes do not fit in a QR code at this level")
    for newecl in (MEDIUM, QUARTILE, HIGH):
        if boost_ecl and newecl > ecl and usedbits <= _get_num_data_codewords(version, newecl) * 8:
            ecl = newecl

    bits: list[int] = []

    def append_bits(val: int, n: int) -> None:
        bits.extend((val >> i) & 1 for i in reversed(range(n)))

    append_bits(0x4, 4)  # byte mode
    append_bits(len(data), countbits)
    for b in data:
        append_bits(b, 8)
    capacity = _get_num_data_codewords(version, ecl) * 8
    append_bits(0, min(4, capacity - len(bits)))  # terminator
    append_bits(0, -len(bits) % 8)  # to a byte boundary
    for pad in itertools.cycle((0xEC, 0x11)):
        if len(bits) >= capacity:
            break
        append_bits(pad, 8)
    codewords = bytearray(len(bits) // 8)
    for i, bit in enumerate(bits):
        codewords[i >> 3] |= bit << (7 - (i & 7))
    return QrCode(version, ecl, bytes(codewords), mask)


def encode_text(text: str, ecl: int = MEDIUM, **kwargs) -> QrCode:
    """``text`` as UTF-8 bytes. A pair link is plain ASCII, which every
    reader takes as itself."""
    return encode_bytes(text.encode("utf-8"), ecl, **kwargs)


def svg(text: str, level: str = "M", border: int = 4, label: str = "QR code") -> str:
    """``text`` as a QR code SVG, at error correction ``level`` (L/M/Q/H)."""
    return encode_text(text, LEVELS[level]).to_svg(border=border, label=label)
