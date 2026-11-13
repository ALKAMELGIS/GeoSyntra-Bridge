"""
DWG file container: R2000 (AC1015, plain section locators), R2004+ (AC1018, AC1024, AC1027, AC1032) and,
through r2007.py, R2007 (AC1021): the encrypted file header, the page map, the section
map and the LZ77-compressed data pages that make up each named section (AcDb:Header, AcDb:Classes, AcDb:Handles,
AcDb:AcDbObjects…).

GeoSyntra Bridge's own implementation, written from the public Open Design Specification for .dwg files; ACadSharp
(MIT licence, © Albert Domenech) was used as a reading aid.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Dict, List

VERSIONS = {"AC1015": 2000, "AC1018": 2004, "AC1021": 2007, "AC1024": 2010, "AC1027": 2013, "AC1032": 2018}
R2000_SECTIONS = {"AcDb:Header": 0, "AcDb:Classes": 1, "AcDb:Handles": 2}


class DwgError(Exception):
    pass


def _magic(n: int = 0x6C) -> bytes:
    out, seed = bytearray(), 1
    for _ in range(n):
        seed = (seed * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        out.append((seed >> 16) & 0xFF)
    return bytes(out)


_MAGIC = _magic()


def decompress18(src: bytes, pos: int, out_size: int) -> bytes:
    """LZ77 variant of R2004+ section pages."""
    dst = bytearray()
    n = len(src)

    def lit_count(code: int) -> int:
        nonlocal pos
        low = code & 0x0F
        if low == 0:
            b = src[pos]; pos += 1
            while b == 0:
                low += 0xFF
                b = src[pos]; pos += 1
            low += 0x0F + b
        return low

    def comp_bytes(op: int, valid: int) -> int:
        nonlocal pos
        c = op & valid
        if c == 0:
            b = src[pos]; pos += 1
            while b == 0:
                c += 0xFF
                b = src[pos]; pos += 1
            c += b + valid
        return c + 2

    def copy(count: int) -> int:
        nonlocal pos
        dst.extend(src[pos:pos + count]); pos += count
        b = src[pos]; pos += 1
        return b

    op = src[pos]; pos += 1
    if op > 0x11:
        op = copy(op - 17)
    if (op & 0xF0) == 0:
        op = copy(lit_count(op) + 3)
    while op != 0x11 and pos < n:
        if op < 0x10 or op >= 0x40:
            cb = (op >> 4) - 1
            op2 = src[pos]; pos += 1
            off = (((op >> 2) & 3) | (op2 << 2)) + 1
        elif op < 0x20:
            cb = comp_bytes(op, 0x07)
            off = (op & 8) << 11
            b1 = src[pos]; b2 = src[pos + 1]; pos += 2
            off = (off | (b1 >> 2) | (b2 << 6)) + 0x4000
            op = b1
        else:
            cb = comp_bytes(op, 0x1F)
            b1 = src[pos]; b2 = src[pos + 1]; pos += 2
            off = ((b1 >> 2) | (b2 << 6)) + 1
            op = b1
        start = len(dst) - off
        if off >= cb:
            dst.extend(dst[start:start + cb])
        else:
            for i in range(cb):
                dst.append(dst[start + i])
        lc = op & 3
        if lc == 0:
            op = src[pos]; pos += 1
            if (op & 0xF0) == 0:
                lc = lit_count(op) + 3
        if lc > 0:
            op = copy(lc)
    if len(dst) < out_size:
        dst.extend(bytes(out_size - len(dst)))
    return bytes(dst[:out_size])


@dataclass
class Section:
    name: str
    size: int
    page_size: int
    compressed: bool
    encrypted: int
    pages: List[tuple] = field(default_factory=list)   # (page number or None for a zero page, data size, start offset)


class DwgFile:
    def __init__(self, data: bytes):
        self.data = data
        self.version = data[:6].decode("ascii", "replace")
        if self.version not in VERSIONS:
            raise DwgError(f"DWG version {self.version} is not supported yet "
                           f"(supported: AutoCAD 2000 to 2018+)")
        self.codepage = struct.unpack_from("<H", data, 0x13)[0]
        self.maint = data[0x0B]
        self.sections: Dict[str, "Section"] = {}
        self.locators: Dict[int, tuple] = {}
        if self.version == "AC1015":                  # R2000: plain section locators, nothing compressed
            (n,) = struct.unpack_from("<I", data, 0x15)
            for i in range(n):
                num, seek, size = struct.unpack_from("<BII", data, 0x19 + i * 9)
                self.locators[num] = (seek, size)
            return
        if self.version == "AC1021":                  # R2007: its own page format
            from .r2007 import R2007Sections
            self.r2007 = R2007Sections(data)
            return
        hdr = bytes(a ^ b for a, b in zip(data[0x80:0x80 + 0x6C], _MAGIC))
        if not hdr.startswith(b"AcFssFcAJMB"):
            raise DwgError("not a valid DWG file (header signature missing)")
        (self.section_page_map_id,) = struct.unpack_from("<I", hdr, 0x50)
        (page_map_addr,) = struct.unpack_from("<Q", hdr, 0x54)
        (self.section_map_id,) = struct.unpack_from("<I", hdr, 0x5C)
        self.pages: Dict[int, int] = {}          # page number -> file offset
        self._read_page_map(page_map_addr + 0x100)
        self._read_section_map()

    def _system_page(self, off: int) -> bytes:
        _typ, dsize, csize, ctype, _crc = struct.unpack_from("<IIIII", self.data, off)
        if ctype == 2:
            return decompress18(self.data, off + 20, dsize)
        return self.data[off + 20:off + 20 + dsize]

    def _read_page_map(self, off: int) -> None:
        buf = self._system_page(off)
        total, p = 0x100, 0
        while p + 8 <= len(buf):
            num, size = struct.unpack_from("<iI", buf, p); p += 8
            if num >= 0:
                self.pages[num] = total
            else:
                p += 16
            total += size

    def _read_section_map(self) -> None:
        buf = self._system_page(self.pages[self.section_map_id])
        (count,) = struct.unpack_from("<I", buf, 0)
        p = 20
        for _ in range(count):
            size, npages, maxsize, _u, comp, _sid, enc = struct.unpack_from("<QIIIIII", buf, p)
            name = buf[p + 32:p + 96].split(b"\0", 1)[0].decode("latin-1")
            p += 96
            s = Section(name, size, maxsize, comp == 2, enc)
            for _ in range(npages):
                pn, dsz, start = struct.unpack_from("<IIQ", buf, p); p += 16
                s.pages.append((pn, dsz, start))
            self.sections[name] = s

    def section(self, name: str) -> bytes:
        if self.version == "AC1015":
            if name == "AcDb:AcDbObjects":
                return self.data                      # object offsets are absolute in R2000 files
            loc = self.locators.get(R2000_SECTIONS.get(name, -1))
            return self.data[loc[0]:loc[0] + loc[1]] if loc else b""
        if self.version == "AC1021":
            return self.r2007.section(name)
        s = self.sections.get(name)
        if s is None:
            return b""
        out = bytearray(s.size)
        for pn, _dsz, start in s.pages:
            off = self.pages.get(pn)
            if off is None:
                continue
            mask = 0x4164536B ^ off
            h = [v ^ mask for v in struct.unpack_from("<8I", self.data, off)]
            csize = h[2]
            body = off + 32
            if s.compressed:
                chunk = decompress18(self.data, body, s.page_size)
            else:
                chunk = self.data[body:body + csize]
            n = min(len(chunk), max(0, s.size - start))
            out[start:start + n] = chunk[:n]
        return bytes(out)
