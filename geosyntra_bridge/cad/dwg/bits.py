"""Bit-level reader for DWG object data (R2004+ conventions; strings are UTF-16 from R2007 on)."""
from __future__ import annotations

import struct

_D = struct.Struct("<d")


class BitReader:
    __slots__ = ("data", "pos", "unicode")

    def __init__(self, data: bytes, pos_bits: int = 0, unicode: bool = True):
        self.data = data
        self.pos = pos_bits
        self.unicode = unicode

    # ------------------------------------------------------------ raw
    def bits(self, n: int) -> int:
        p = self.pos
        b = p >> 3
        sh = p & 7
        k = (sh + n + 7) >> 3
        v = int.from_bytes(self.data[b:b + k], "big")
        self.pos = p + n
        return (v >> (k * 8 - sh - n)) & ((1 << n) - 1)

    def B(self) -> int:
        p = self.pos
        self.pos = p + 1
        return (self.data[p >> 3] >> (7 - (p & 7))) & 1

    def BB(self) -> int:
        return self.bits(2)

    def RC(self) -> int:
        return self.bits(8)

    def raw(self, n: int) -> bytes:
        if n <= 0:
            return b""
        if self.pos & 7 == 0:
            b = self.pos >> 3
            self.pos += n * 8
            return self.data[b:b + n]
        return self.bits(n * 8).to_bytes(n, "big")

    def RS(self) -> int:
        return int.from_bytes(self.raw(2), "little")

    def RL(self) -> int:
        return int.from_bytes(self.raw(4), "little")

    def RD(self) -> float:
        return _D.unpack(self.raw(8))[0]

    # ------------------------------------------------------------ compressed
    def BS(self) -> int:
        c = self.bits(2)
        if c == 0:
            v = self.RS()
            return v - 0x10000 if v & 0x8000 else v
        if c == 1:
            return self.RC()
        if c == 2:
            return 0
        return 256

    def BL(self) -> int:
        c = self.bits(2)
        if c == 0:
            v = self.RL()
            return v - 0x100000000 if v & 0x80000000 else v
        if c == 1:
            return self.RC()
        return 0

    def BLL(self) -> int:
        n = self.bits(3)
        return int.from_bytes(self.raw(n), "little") if n else 0

    def BD(self) -> float:
        c = self.bits(2)
        if c == 0:
            return self.RD()
        if c == 1:
            return 1.0
        return 0.0

    def DD(self, default: float) -> float:
        c = self.bits(2)
        if c == 0:
            return default
        if c == 3:
            return self.RD()
        arr = bytearray(_D.pack(default))
        if c == 1:
            arr[0:4] = self.raw(4)
        else:
            r = self.raw(6)
            arr[4:6] = r[0:2]
            arr[0:4] = r[2:6]
        return _D.unpack(bytes(arr))[0]

    def BD2(self):
        return (self.BD(), self.BD())

    def BD3(self):
        return (self.BD(), self.BD(), self.BD())

    def RD2(self):
        return (self.RD(), self.RD())

    def RD3(self):
        return (self.RD(), self.RD(), self.RD())

    def BT(self) -> float:        # thickness (R2000+)
        return 0.0 if self.B() else self.BD()

    def BE(self):                 # extrusion (R2000+)
        return (0.0, 0.0, 1.0) if self.B() else self.BD3()

    def MC(self) -> int:          # unsigned modular char
        v, sh = 0, 0
        while True:
            b = self.RC()
            v |= (b & 0x7F) << sh
            sh += 7
            if not b & 0x80:
                return v

    def MS(self) -> int:
        v, sh = 0, 0
        while True:
            w = self.RS()
            v |= (w & 0x7FFF) << sh
            sh += 15
            if not w & 0x8000:
                return v

    def BOT(self) -> int:         # object type (R2010+)
        c = self.bits(2)
        if c == 0:
            return self.RC()
        if c == 1:
            return 0x1F0 + self.RC()
        return self.RS()

    def H(self, ref: int = 0) -> int:
        form = self.RC()
        code, n = form >> 4, form & 0x0F
        v = int.from_bytes(self.raw(n), "big") if n else 0
        if code <= 5:
            return v
        if code == 6:
            return ref + 1
        if code == 8:
            return ref - 1
        if code == 0xA:
            return ref + v
        if code == 0xC:
            return ref - v
        raise ValueError(f"bad handle code {code}")

    def TV(self) -> str:
        n = self.BS()
        if n <= 0:
            return ""
        if self.unicode:
            return self.raw(n * 2).decode("utf-16-le", "replace").replace("\0", "")
        return self.raw(n).decode("cp1252", "replace").replace("\0", "")

    def CMC(self, text: "BitReader"):
        """Colour (R2004+): (aci or None, rgb or None)."""
        self.BS()
        rgb = self.BL() & 0xFFFFFFFF
        flag = self.RC()
        if flag & 1:
            text.TV()
        if flag & 2:
            text.TV()
        return _decode_rgb(rgb)


def _decode_rgb(rgb: int):
    kind = rgb >> 24
    if kind == 0xC0:
        return 256, None          # by layer
    if kind == 0xC1:
        return 0, None            # by block
    if kind == 0xC3:
        return rgb & 0xFF, None
    if kind == 0xC2:
        return None, rgb & 0xFFFFFF
    return (rgb & 0xFF) or 7, None
