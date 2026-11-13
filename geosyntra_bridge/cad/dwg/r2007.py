"""
DWG R2007 (AC1021) container: Reed-Solomon interleaved pages and the R2007 variant of LZ77.
Written from the Open Design Specification for .dwg files (ACadSharp, MIT, used as a reading aid).
"""
from __future__ import annotations

import struct
from typing import Dict, List, Tuple

# literal runs are stored in a shuffled order; (width, src offset, dst offset) per run length
_RUNS = {
    1: [(1, 0, 0)], 2: [(2, 0, 0)], 3: [(3, 0, 0)], 4: [(4, 0, 0)],
    5: [(1, 4, 0), (4, 0, 1)], 6: [(1, 5, 0), (4, 1, 1), (1, 0, 5)], 7: [(2, 5, 0), (4, 1, 2), (1, 0, 6)],
    8: [(8, 0, 0)], 9: [(1, 8, 0), (8, 0, 1)], 10: [(1, 9, 0), (8, 1, 1), (1, 0, 9)],
    11: [(2, 9, 0), (8, 1, 2), (1, 0, 10)], 12: [(4, 8, 0), (8, 0, 4)], 13: [(1, 12, 0), (4, 8, 1), (8, 0, 5)],
    14: [(1, 13, 0), (4, 9, 1), (8, 1, 5), (1, 0, 13)], 15: [(2, 13, 0), (4, 9, 2), (8, 1, 6), (1, 0, 14)],
    16: [(16, 0, 0)], 17: [(8, 9, 0), (1, 8, 8), (8, 0, 9)], 18: [(1, 17, 0), (16, 1, 1), (1, 0, 17)],
    19: [(3, 16, 0), (16, 0, 3)], 20: [(4, 16, 0), (8, 8, 4), (8, 0, 12)],
    21: [(1, 20, 0), (4, 16, 1), (8, 8, 5), (8, 0, 13)], 22: [(2, 20, 0), (4, 16, 2), (8, 8, 6), (8, 0, 14)],
    23: [(3, 20, 0), (4, 16, 3), (8, 8, 7), (8, 0, 15)], 24: [(8, 16, 0), (16, 0, 8)],
    25: [(8, 17, 0), (1, 16, 8), (16, 0, 9)], 26: [(1, 25, 0), (8, 17, 1), (1, 16, 9), (16, 0, 10)],
    27: [(2, 25, 0), (8, 17, 2), (1, 16, 10), (16, 0, 11)],
    28: [(4, 24, 0), (8, 16, 4), (8, 8, 12), (8, 0, 20)],
    29: [(1, 28, 0), (4, 24, 1), (8, 16, 5), (8, 8, 13), (8, 0, 21)],
    30: [(2, 28, 0), (4, 24, 2), (8, 16, 6), (8, 8, 14), (8, 0, 22)],
    31: [(1, 30, 0), (4, 26, 1), (8, 18, 5), (8, 10, 13), (8, 2, 21), (2, 0, 29)],
    32: [(4, 24, 0), (4, 28, 4), (4, 16, 8), (4, 20, 12), (4, 8, 16), (4, 12, 20), (4, 0, 24), (4, 4, 28)],
}


def _unit(width: int, s: int, d: int) -> List[Tuple[int, int]]:
    if width in (1, 4, 8):
        return [(d + i, s + i) for i in range(width)]
    if width == 2:
        return [(d, s + 1), (d + 1, s)]
    if width == 3:
        return [(d, s + 2), (d + 1, s + 1), (d + 2, s)]
    if width == 16:
        return [(d + i, s + 8 + i) for i in range(8)] + [(d + 8 + i, s + i) for i in range(8)]
    raise ValueError(width)


# PERM[n][i] = source index of destination byte i
PERM = {}
for _n, _ops in _RUNS.items():
    _p = [0] * _n
    for _w, _s, _d in _ops:
        for _di, _si in _unit(_w, _s, _d):
            _p[_di] = _si
    PERM[_n] = _p


def decompress21(src: bytes, start: int, length: int, out_size: int) -> bytes:
    dst = bytearray(out_size)
    si = start
    end = start + length
    di = 0
    op = src[si]; si += 1
    ln = 0
    off = 0
    if si >= end:
        return bytes(dst)
    if (op & 0xF0) == 0x20:
        si += 3
        ln = src[si - 1] & 7

    def literal(ln):
        nonlocal si, di
        while ln >= 32:
            p = PERM[32]
            dst[di:di + 32] = bytes(src[si + k] for k in p)
            si += 32; di += 32; ln -= 32
        if ln:
            p = PERM[ln]
            dst[di:di + ln] = bytes(src[si + k] for k in p)
            si += ln; di += ln

    def instructions():
        nonlocal op, ln, off, si
        c = op >> 4
        if c == 0:
            ln = (op & 0xF) + 0x13
            off = src[si]; si += 1
            op = src[si]; si += 1
            ln = ((op >> 3) & 0x10) + ln
            off = ((op & 0x78) << 5) + 1 + off
        elif c == 1:
            ln = (op & 0xF) + 3
            off = src[si]; si += 1
            op = src[si]; si += 1
            off = ((op & 0xF8) << 5) + 1 + off
        elif c == 2:
            off = src[si]; si += 1
            off = ((src[si] << 8) & 0xFF00) | off; si += 1
            ln = op & 7
            if (op & 8) == 0:
                op = src[si]; si += 1
                ln = (op & 0xF8) + ln
            else:
                off += 1
                ln = (src[si] << 3) + ln; si += 1
                op = src[si]; si += 1
                ln = ((op & 0xF8) << 8) + ln + 0x100
        else:
            ln = op >> 4
            off = op & 15
            op = src[si]; si += 1
            off = ((op & 0xF8) << 1) + off + 1

    while si < end:
        if ln == 0:                                   # literal length
            ln = op + 8
            if ln == 0x17:
                n = src[si]; si += 1
                ln += n
                if n == 0xFF:
                    while True:
                        n = src[si] | (src[si + 1] << 8); si += 2
                        ln += n
                        if n != 0xFFFF:
                            break
        literal(ln)
        if si >= end:
            break
        ln = 0                                        # back-references
        op = src[si]; si += 1
        instructions()
        while True:
            a = di - off
            if off >= ln:
                dst[di:di + ln] = dst[a:a + ln]
            else:
                for k in range(ln):
                    dst[di + k] = dst[a + k]
            di += ln
            ln = op & 7
            if ln != 0 or si >= end:
                break
            op = src[si]; si += 1
            if op >> 4 == 0:
                break
            if op >> 4 == 15:
                op &= 15
            instructions()
    return bytes(dst)


def deinterleave(enc: bytes, out_len: int, factor: int, block: int) -> bytes:
    out = bytearray()
    remaining = out_len
    for i in range(factor):
        if i >= len(enc):
            break
        size = min(remaining, block)
        remaining -= size
        out += enc[i:i + factor * size:factor]
    out += bytes(out_len - len(out))
    return bytes(out[:out_len])


class R2007Sections:
    def __init__(self, data: bytes):
        self.data = data
        dec = deinterleave(data[0x80:0x480], 3 * 239, 3, 239)
        (clen,) = struct.unpack_from("<i", dec, 24)
        meta = decompress21(dec, 32, clen, 0x110) if clen > 0 else dec[32:32 + 0x110]
        q = struct.unpack_from("<34Q", meta, 0)
        pm_crc_comp, pm_corr = q[2], q[3]
        pm_offset, pm_size_comp, pm_size_unc = q[7], q[10], q[11]
        sm_size_comp, sm_id, sm_size_unc, sm_corr = q[22], q[24], q[25], q[27]
        pages = self._system_page(pm_offset, pm_size_comp, pm_size_unc, pm_corr, 0xEF)
        self.records: Dict[int, Tuple[int, int]] = {}
        off = 0
        for p in range(0, len(pages) - 15, 16):
            size, pid = struct.unpack_from("<qq", pages, p)
            self.records[abs(pid)] = (off, size)
            off += size
        smap = self._system_page(self.records[sm_id][0], sm_size_comp, sm_size_unc, sm_corr, 239)
        self.sections: Dict[str, dict] = {}
        p = 0
        while p + 64 <= len(smap):
            dsize, maxsize, enc, _hash, nlen, _u, encoding, npages = struct.unpack_from("<QQQQqQQQ", smap, p)
            p += 64
            name = smap[p:p + nlen].decode("utf-16-le", "replace").replace("\0", "") if nlen > 0 else ""
            p += max(nlen, 0)
            pl = []
            for _ in range(npages):
                poff, psize, pid, punc, pcomp, _ck, _crc = struct.unpack_from("<QqqQQQQ", smap, p)
                p += 56
                pl.append((poff, pid, punc, pcomp))
            if name:
                self.sections[name] = {"size": dsize, "encoding": encoding, "pages": pl}

    def _system_page(self, offset, size_comp, size_unc, corr, block) -> bytes:
        v = (size_comp + 7) & ~7
        total = v * corr
        factor = (total + block - 1) // block
        raw = self.data[0x480 + offset:0x480 + offset + factor * 255]
        comp = deinterleave(raw, total, factor, block)
        return decompress21(comp, 0, size_comp, size_unc)

    def section(self, name: str) -> bytes:
        s = self.sections.get(name)
        if not s:
            return b""
        total = max((poff + punc for poff, _pid, punc, _pc in s["pages"]), default=0)
        out = bytearray(total)
        for poff, pid, punc, pcomp in s["pages"]:
            rec = self.records.get(pid)
            if rec is None:
                continue
            raw = self.data[0x480 + rec[0]:0x480 + rec[0] + rec[1]]
            if s["encoding"] == 4:
                n = (((pcomp + 7) & ~7) + 250) // 251
                raw = deinterleave(raw, n * 251, n, 251)
            chunk = decompress21(raw, 0, pcomp, punc) if pcomp != punc else raw[:punc]
            out[poff:poff + punc] = chunk[:punc]
        return bytes(out)
