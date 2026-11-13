"""
Object map, classes and the drawing objects of a DWG (R2010+ object framing: separate data / string / handle
streams). Only what GeoSyntra Bridge needs is decoded: layers, block definitions and the geometric entities.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .bits import BitReader, _decode_rgb
from .container import DwgFile, DwgError

# fixed object types
T_TEXT, T_ATTRIB, T_ATTDEF, T_BLOCK, T_ENDBLK, T_SEQEND, T_INSERT, T_MINSERT = 1, 2, 3, 4, 5, 6, 7, 8
T_VERTEX2D, T_VERTEX3D, T_VERTEX_MESH, T_VERTEX_PFACE = 10, 11, 12, 13
T_POLYLINE2D, T_POLYLINE3D, T_ARC, T_CIRCLE, T_LINE = 15, 16, 17, 18, 19
T_DIMS = range(20, 27)
T_POINT, T_3DFACE, T_SOLID, T_TRACE, T_ELLIPSE, T_SPLINE = 27, 28, 31, 32, 35, 36
T_MTEXT, T_BLOCK_HEADER, T_LAYER, T_LWPOLYLINE, T_HATCH = 44, 49, 51, 77, 78


def str_stream(data: bytes, endbit: int) -> Optional[BitReader]:
    r = BitReader(data, endbit)
    if not r.B():
        return None
    length = endbit - 16
    r.pos = length
    size = r.RS()
    if size & 0x8000:
        length -= 16
        r.pos = length
        size = (size & 0x7FFF) + (r.RS() << 15)
    r.pos = length - size
    return r


class _Strings(BitReader):
    """R2007 string stream, positioned once the object's data size is known."""
    def __init__(self, data: bytes):
        super().__init__(data, 0, unicode=True)
        self.empty = True

    def TV(self) -> str:
        return "" if self.empty else super().TV()


class _NoText(BitReader):
    def __init__(self):
        super().__init__(b"")

    def TV(self) -> str:
        return ""


def read_handles(buf: bytes) -> Dict[int, int]:
    out: Dict[int, int] = {}
    p, n = 0, len(buf)
    while p + 2 <= n:
        size = (buf[p] << 8) | buf[p + 1]
        p += 2
        if size == 2:
            break
        end = p + min(size - 2, 2032)
        h = loc = 0
        while p < end:
            v = sh = 0                       # unsigned modular char
            while True:
                b = buf[p]; p += 1
                v |= (b & 0x7F) << sh; sh += 7
                if not b & 0x80:
                    break
            v2 = sh = 0                      # signed modular char
            while True:
                b = buf[p]; p += 1
                if b & 0x80:
                    v2 |= (b & 0x7F) << sh; sh += 7
                else:
                    v2 |= (b & 0x3F) << sh
                    if b & 0x40:
                        v2 = -v2
                    break
            h += v
            loc += v2
            if v > 0:
                out[h] = loc
        p += 2                               # CRC
    return out


def read_classes(f: DwgFile, buf: bytes) -> Dict[int, str]:
    if f.version >= "AC1021":
        r = BitReader(buf, 16 * 8, unicode=True)
        r.RL()
        if f.version > "AC1027" or (f.version >= "AC1024" and f.maint > 3):
            r.RL()
        p0 = r.pos
        flag_pos = p0 + r.RL() - 1
        text = str_stream(buf, flag_pos)
        end = text.pos if text else flag_pos
        text = text or _NoText()
        r.BL(); r.B()
    else:                                             # R2000 / R2004: names inline, end given in bytes
        r = BitReader(buf, 16 * 8, unicode=False)
        size = r.RL()
        end = (20 + size) * 8
        if f.version == "AC1018":
            r.BS(); r.RC(); r.RC(); r.B()
        text = r
    out: Dict[int, str] = {}
    while r.pos < end - 8:
        num = r.BS(); r.BS()
        text.TV(); text.TV()
        name = text.TV()
        r.B(); r.BS()
        if f.version >= "AC1018":
            r.BL(); r.BL(); r.BL(); r.BL(); r.BL()
        out[num] = name
    return out


class Obj(dict):
    """A decoded object: type, handle, layer handle, colour and geometry fields."""


class ObjectReader:
    def __init__(self, f: DwgFile):
        if f.version not in ("AC1015", "AC1018", "AC1021") and f.version < "AC1024":
            raise DwgError(f"DWG {f.version} is not read natively yet: save it as AutoCAD 2000 or newer, or as DXF")
        self.f = f
        v = f.version
        self.r2004 = v >= "AC1018"
        self.r2007 = v >= "AC1021"
        self.r2010 = v >= "AC1024"
        self.r2013 = v >= "AC1027"
        self.r2018 = v >= "AC1032"
        self.data = f.section("AcDb:AcDbObjects")
        self.map = read_handles(f.section("AcDb:Handles"))
        self.classes = read_classes(f, f.section("AcDb:Classes"))
        self.errors: Dict[str, int] = {}
        self._start = 0
        self._h: Optional[BitReader] = None

    # ------------------------------------------------------------ framing
    def _open(self, off: int):
        d = self.data
        r = BitReader(d, off * 8, unicode=self.r2007)
        size = r.MS()
        if self.r2010:
            hsize = r.MC()
            start = r.pos
            hpos = start + size * 8 - hsize
            t = r.BOT()
            h = BitReader(d, hpos)
            s = str_stream(d, hpos - 1) or _NoText()
        else:                                         # handle stream located by the RL in the common data
            start = r.pos
            t = r.BS()
            h = BitReader(d, 0)
            s = _Strings(d) if self.r2007 else r
        self._start, self._h, self._s = start, h, s
        return t, r, s, h

    def type_name(self, t: int) -> str:
        return self.classes.get(t, "") if t >= 500 else ""

    def _common(self, r: BitReader) -> int:
        if not self.r2010:
            end = self._start + r.RL()                # end of the data = start of the handle stream
            self._h.pos = end
            if self.r2007:                            # R2007: strings sit at the end of the data
                ss = str_stream(self.data, end - 1)
                self._s.empty = ss is None
                self._s.pos = ss.pos if ss else 0
        handle = r.H()
        size = r.BS()
        while size:                                   # extended data: skipped
            r.H()
            r.pos += size * 8
            size = r.BS()
        return handle

    def _entity(self, r: BitReader, h: BitReader) -> Obj:
        o = Obj()
        o["handle"] = handle = self._common(r)
        if r.B():                                     # proxy graphics
            n = r.BLL() if self.r2010 else r.RL()
            r.pos += n * 8
        mode = r.BB()
        o["owner"] = h.H(handle) if mode == 0 else None
        o["space"] = mode
        for _ in range(r.BL()):
            h.H(handle)
        if not self.r2004 or not r.B():
            h.H(handle)                               # xdictionary
        if self.r2013:
            r.B()
        if not self.r2004:
            if not r.B():                             # R2000: previous / next entity links
                h.H(handle); h.H(handle)
            aci = r.BS()
            o["color"], o["rgb"] = (aci if 0 <= aci <= 257 else 256), None
        else:
            size = r.BS() & 0xFFFF                    # ENC colour
            aci, rgb, book = 256, None, False
            if size:
                fl = size & 0xFF00
                if fl & 0x4000:                       # colour-book colour: an AcDbColor object holds it
                    aci = 7
                elif fl & 0x8000:
                    aci, rgb = _decode_rgb(r.BL() & 0xFFFFFFFF)
                else:
                    aci = size & 0x0FFF
                if fl & 0x4000:
                    book = True
                if fl & 0x2000:
                    r.BL()
            else:
                aci = 0
            if book:
                h.H(handle)
            o["color"], o["rgb"] = aci, rgb
        r.BD()                                        # linetype scale
        o["layer"] = h.H(handle)
        if r.BB() == 3:
            h.H(handle)
        if self.r2007:
            if r.BB() == 3:                           # material
                h.H(handle)
            r.RC()                                    # shadow
        if r.BB() == 3:                               # plot style
            h.H(handle)
        if self.r2010:
            for _ in range(3):                        # visual styles
                if r.B():
                    h.H(handle)
        o["invisible"] = r.BS() & 1
        r.RC()                                        # lineweight
        return o

    def _nonentity(self, r: BitReader, h: BitReader) -> Obj:
        o = Obj()
        o["handle"] = handle = self._common(r)
        o["owner"] = h.H(handle)
        for _ in range(r.BL()):
            h.H(handle)
        if not self.r2004 or not r.B():
            h.H(handle)
        if self.r2013:
            r.B()
        return o

    def _xref_bits(self, r: BitReader):
        if self.r2007:
            r.BS()
        else:
            r.B(); r.BS(); r.B()

    # ------------------------------------------------------------ objects
    def read(self, handle: int) -> Optional[Obj]:
        off = self.map.get(handle)
        if off is None:
            return None
        t, r, s, h = self._open(off)
        name = self.type_name(t)
        fn = _READERS.get(t) or _CLASS_READERS.get(name)
        if fn is None:
            return None
        o = fn(self, r, s, h)
        if o is not None:
            o["type"] = name or _NAMES.get(t, str(t))
        return o

    def read_all(self) -> List[Obj]:
        out = []
        for hd in self.map:
            try:
                o = self.read(hd)
            except Exception as e:  # noqa: BLE001 - one bad object must not stop the drawing
                k = type(e).__name__
                self.errors[k] = self.errors.get(k, 0) + 1
                continue
            if o is not None:
                out.append(o)
        return out


# ---------------------------------------------------------------- per type
def _line(R: ObjectReader, r, s, h):
    o = R._entity(r, h)
    zz = r.B()
    x1 = r.RD(); x2 = r.DD(x1); y1 = r.RD(); y2 = r.DD(y1)
    z1 = z2 = 0.0
    if not zz:
        z1 = r.RD(); z2 = r.DD(z1)
    o["p"] = [(x1, y1, z1), (x2, y2, z2)]
    r.BT(); o["ext"] = r.BE()
    return o


def _circle(R, r, s, h, arc=False):
    o = R._entity(r, h)
    o["c"] = r.BD3(); o["r"] = r.BD()
    r.BT(); o["ext"] = r.BE()
    if arc:
        o["a0"] = r.BD(); o["a1"] = r.BD()
    return o


def _point(R, r, s, h):
    o = R._entity(r, h)
    o["c"] = r.BD3()
    return o


def _ellipse(R, r, s, h):
    o = R._entity(r, h)
    o["c"] = r.BD3(); o["major"] = r.BD3(); o["ext"] = r.BD3()
    o["ratio"] = min(1.0, max(1e-9, r.BD())); o["a0"] = r.BD(); o["a1"] = r.BD()
    return o


def _lwpline(R, r, s, h):
    o = R._entity(r, h)
    fl = r.BS()
    if fl & 4: r.BD()
    o["elev"] = r.BD() if fl & 8 else 0.0
    if fl & 2: r.BD()
    o["ext"] = r.BD3() if fl & 1 else (0.0, 0.0, 1.0)
    n = r.BL()
    nb = r.BL() if fl & 0x10 else 0
    ni = r.BL() if fl & 0x400 else 0
    nw = r.BL() if fl & 0x20 else 0
    pts = []
    if n > 0:
        x, y = r.RD(), r.RD()
        pts.append((x, y))
        for _ in range(n - 1):
            x = r.DD(x); y = r.DD(y)
            pts.append((x, y))
    o["bulges"] = [r.BD() for _ in range(nb)]
    for _ in range(ni): r.BL()
    o["widths"] = [(r.BD(), r.BD()) for _ in range(nw)]
    o["pts"] = pts
    o["closed"] = bool(fl & 0x200)
    return o


def _pline2d(R, r, s, h):
    o = R._entity(r, h)
    o["flags"] = r.BS(); r.BS(); r.BD(); r.BD(); r.BT()
    o["elev"] = r.BD(); o["ext"] = r.BE()
    n = r.BL() if R.r2004 else 0                      # R2000: vertices are found by their owner
    o["vertices"] = [h.H(o["handle"]) for _ in range(n)]
    o["closed"] = bool(o["flags"] & 1)
    return o


def _pline3d(R, r, s, h):
    o = R._entity(r, h)
    r.RC(); o["closed"] = bool(r.RC() & 1)
    n = r.BL() if R.r2004 else 0
    o["vertices"] = [h.H(o["handle"]) for _ in range(n)]
    return o


def _pface(R, r, s, h):
    o = R._entity(r, h)
    r.BS(); r.BS()
    n = r.BL() if R.r2004 else 0
    o["vertices"] = [h.H(o["handle"]) for _ in range(n)]
    return o


def _pface_face(R, r, s, h):
    o = R._entity(r, h)
    o["idx"] = [r.BS() for _ in range(4)]
    return o


def _vertex2d(R, r, s, h):
    o = R._entity(r, h)
    o["vflags"] = r.RC(); o["c"] = r.BD3()
    w = r.BD()
    if w >= 0:
        r.BD()
    o["bulge"] = r.BD()
    return o


def _vertex3d(R, r, s, h):
    o = R._entity(r, h)
    o["vflags"] = r.RC(); o["c"] = r.BD3()
    return o


def _text_common(R, r, s, h, o):
    fl = r.RC()
    elev = 0.0 if fl & 1 else r.RD()
    x, y = r.RD(), r.RD()
    o["c"] = (x, y, elev)
    o["align"] = None if fl & 2 else (r.DD(x), r.DD(y), elev)
    o["ext"] = r.BE(); r.BT()
    o["oblique"] = 0.0 if fl & 4 else r.RD()
    o["rot"] = 0.0 if fl & 8 else r.RD()
    o["height"] = r.RD()
    o["width"] = 1.0 if fl & 0x10 else r.RD()
    o["text"] = s.TV()
    o["gen"] = 0 if fl & 0x20 else r.BS()
    o["halign"] = 0 if fl & 0x40 else r.BS()
    o["valign"] = 0 if fl & 0x80 else r.BS()
    return o


def _text(R, r, s, h):
    return _text_common(R, r, s, h, R._entity(r, h))


def _attrib(R, r, s, h):
    o = _text_common(R, r, s, h, R._entity(r, h))
    if R.r2010:
        r.RC()                                        # version
    kind = r.RC() if R.r2018 else 1
    o["tag"] = s.TV() if kind in (0, 1) else ""
    return o


def _mtext(R, r, s, h):
    o = R._entity(r, h)
    o["c"] = r.BD3(); o["ext"] = r.BD3(); o["xdir"] = r.BD3()
    o["rect_w"] = r.BD()
    if R.r2007:
        r.BD()
    o["height"] = r.BD(); o["attach"] = r.BS(); r.BS(); r.BD(); r.BD()
    o["text"] = s.TV()
    o["rot"] = math.atan2(o["xdir"][1], o["xdir"][0])
    return o


def _insert(R, r, s, h, minsert=False):
    o = R._entity(r, h)
    o["c"] = r.BD3()
    k = r.BB()
    if k == 0:
        sx = r.RD(); sy = r.DD(sx); sz = r.DD(sx)
    elif k == 1:
        sx = 1.0; sy = r.DD(sx); sz = r.DD(sx)
    elif k == 2:
        sx = sy = sz = r.RD()
    else:
        sx = sy = sz = 1.0
    o["scale"] = (sx, sy, sz)
    o["rot"] = r.BD(); o["ext"] = r.BD3()
    has = r.B()
    n = r.BL() if (has and R.r2004) else 0
    if minsert:
        o["cols"] = r.BS(); o["rows"] = r.BS(); o["cs"] = r.BD(); o["rs"] = r.BD()
    o["block"] = h.H(o["handle"])
    o["attribs"] = [h.H(o["handle"]) for _ in range(n)]
    return o


def _solid(R, r, s, h):
    o = R._entity(r, h)
    r.BT(); z = r.BD()
    o["p"] = [(*r.RD2(), z) for _ in range(4)]
    o["ext"] = r.BE()
    return o


def _face3d(R, r, s, h):
    o = R._entity(r, h)
    nofl = r.B(); zz = r.B()
    x, y = r.RD(), r.RD()
    z = 0.0 if zz else r.RD()
    p = [(x, y, z)]
    for _ in range(3):
        q = p[-1]
        p.append((r.DD(q[0]), r.DD(q[1]), r.DD(q[2])))
    if not nofl:
        r.BS()
    o["p"] = p
    return o


def _spline(R, r, s, h):
    o = R._entity(r, h)
    scen = r.BL()
    if R.r2013:
        f1 = r.BL(); kp = r.BL()
        o["closed"] = bool(f1 & 4)
        scen = 1 if (kp == 15 or not f1 & 8) else 2
    o["degree"] = r.BL()
    nk = nc = nf = 0
    w = False
    if scen == 1:
        o["rational"] = r.B(); o["closed"] = r.B(); o["periodic"] = r.B()
        r.BD(); r.BD()
        nk = r.BL(); nc = r.BL(); w = r.B()
    else:
        r.BD(); r.BD3(); r.BD3()
        nf = r.BL()
    o["knots"] = [r.BD() for _ in range(nk)]
    cps, ws = [], []
    for _ in range(nc):
        cps.append(r.BD3())
        if w:
            ws.append(r.BD())
    o["cps"], o["weights"] = cps, ws
    o["fit"] = [r.BD3() for _ in range(nf)]
    return o


def _hatch(R, r, s, h):
    o = R._entity(r, h)
    if R.r2004:                                       # gradient
        r.BL(); r.BL(); r.BD(); r.BD(); r.BL(); r.BD()
        for _ in range(r.BL()):
            r.BD(); r.CMC(s)
        s.TV()
    o["elev"] = r.BD(); o["ext"] = r.BD3()
    o["pattern"] = s.TV()
    o["solid"] = r.B(); r.B()
    paths = []
    for _ in range(r.BL()):
        fl = r.BL()
        path: Dict[str, Any] = {"flags": fl}
        if not fl & 2:
            edges = []
            for _ in range(r.BL()):
                et = r.RC()
                if et == 1:
                    edges.append(("line", r.RD2(), r.RD2()))
                elif et == 2:
                    edges.append(("arc", r.RD2(), r.BD(), r.BD(), r.BD(), r.B()))
                elif et == 3:
                    edges.append(("ellipse", r.RD2(), r.RD2(), r.BD(), r.BD(), r.BD(), r.B()))
                elif et == 4:
                    deg = r.BL(); rat = r.B(); r.B()
                    nk = r.BL(); nc = r.BL()
                    knots = [r.BD() for _ in range(nk)]
                    cps, ws = [], []
                    for _ in range(nc):
                        cps.append(r.RD2())
                        if rat:
                            ws.append(r.BD())
                    nfit = r.BL() if R.r2010 else 0
                    fit = [r.RD2() for _ in range(nfit)]
                    if nfit:
                        r.RD2(); r.RD2()
                    edges.append(("spline", deg, knots, cps, ws, fit))
                else:
                    raise ValueError(f"hatch edge type {et}")
            path["edges"] = edges
        else:
            bul = r.B(); closed = r.B()
            vs = []
            for _ in range(r.BL()):
                x, y = r.RD2()
                vs.append((x, y, r.BD() if bul else 0.0))
            path["poly"], path["closed"] = vs, closed
        for _ in range(r.BL()):
            h.H(o["handle"])
        paths.append(path)
    o["paths"] = paths
    return o


def _block_header(R, r, s, h):
    o = R._nonentity(r, h)
    o["name"] = s.TV()
    R._xref_bits(r)
    o["anon"] = r.B(); r.B(); o["xref"] = r.B(); o["overlay"] = r.B(); r.B()
    n = 0 if (o["xref"] or o["overlay"] or not R.r2004) else r.BL()
    o["base"] = r.BD3()
    s.TV()
    hd = o["handle"]
    nins = 0
    while r.RC():
        nins += 1
    s.TV()
    npv = r.BL()
    r.pos += npv * 8                                  # preview image
    if R.r2007:
        r.BS(); r.B(); r.RC()
    h.H(hd)                                           # (null)
    o["block_ent"] = h.H(hd)
    o["owned"] = [h.H(hd) for _ in range(n)]
    return o


def _block(R, r, s, h):
    o = R._entity(r, h)
    o["name"] = s.TV()
    return o


def _layer(R, r, s, h):
    o = R._nonentity(r, h)
    o["name"] = s.TV()
    R._xref_bits(r)
    v = r.BS()
    o["frozen"] = bool(v & 1); o["off"] = bool(v & 2); o["locked"] = bool(v & 8)
    if R.r2004:
        o["color"], o["rgb"] = r.CMC(s)
    else:
        c = r.BS()
        o["off"] = o["off"] or c < 0
        o["color"], o["rgb"] = abs(c), None
    return o


def _mleader(R, r, s, h):
    o = R._entity(r, h)
    if R.r2010:
        r.BS()                                        # class version
    heads = []
    for _ in range(r.BL()):                           # leader roots
        r.B(); r.B(); r.BD3(); r.BD3()
        for _ in range(r.BL()):
            r.BD3(); r.BD3()
        r.BL(); r.BD()
        for _ in range(r.BL()):                       # leader lines
            pts = [r.BD3() for _ in range(r.BL())]
            if pts:
                heads.append(pts[0])
            for _ in range(r.BL()):                   # break info
                r.BL()
                for _ in range(r.BL()):
                    r.BD3(); r.BD3()
            r.BL()
            if R.r2010:
                r.BS(); r.CMC(s); h.H(o["handle"]); r.BL(); r.BD(); h.H(o["handle"]); r.BL()
        if R.r2010:
            r.BS()
    r.BD(); o["base"] = r.BD3(); o["height"] = r.BD(); r.BD(); r.BD()
    r.BS(); r.BS(); r.BS(); r.BS()
    if not r.B():
        return None                                   # block content only: no label
    o["text"] = s.TV(); r.BD3(); h.H(o["handle"])
    o["c"] = r.BD3(); d = r.BD3(); o["rot"] = math.atan2(d[1], d[0])
    o["heads"] = heads
    return o


def _mline(R, r, s, h):
    o = R._entity(r, h)
    r.BD(); r.RC(); r.BD3(); o["ext"] = r.BD3()
    o["closed"] = r.BS() == 3
    nl = r.RC()
    verts = []
    for _ in range(r.BS()):
        pos = r.BD3(); r.BD3(); miter = r.BD3()
        offs = []
        for _ in range(nl):
            prm = [r.BD() for _ in range(r.BS())]
            for _ in range(r.BS()):
                r.BD()
            offs.append(prm[0] if prm else 0.0)
        verts.append((pos, miter, offs))
    o["verts"], o["nlines"] = verts, nl
    return o


def _geodata(R, r, s, h):
    o = R._nonentity(r, h)
    ver = r.BL()
    h.H(o["handle"])
    o["ctype"] = r.BS()
    if ver == 1:
        o["ref"] = r.BD3(); r.BL(); o["design"] = r.BD3(); r.BD3(); r.BD3(); r.BD(); r.BD3()
        o["csdef"] = s.TV()
    else:
        o["design"] = r.BD3(); o["ref"] = r.BD3(); r.BD(); r.BL(); r.BD(); r.BL(); r.BD3(); r.RD2()
        r.BL(); r.BD(); r.B(); r.BD(); r.BD()
        o["csdef"] = s.TV()
    return o


_READERS = {
    T_LINE: _line, T_CIRCLE: _circle, T_ARC: lambda R, r, s, h: _circle(R, r, s, h, True), T_POINT: _point,
    T_ELLIPSE: _ellipse, T_LWPOLYLINE: _lwpline, T_POLYLINE2D: _pline2d, T_POLYLINE3D: _pline3d,
    T_VERTEX2D: _vertex2d, T_VERTEX3D: _vertex3d, T_TEXT: _text, T_ATTRIB: _attrib, T_MTEXT: _mtext,
    T_INSERT: _insert, T_MINSERT: lambda R, r, s, h: _insert(R, r, s, h, True), T_SOLID: _solid, T_TRACE: _solid,
    T_3DFACE: _face3d, T_SPLINE: _spline, T_HATCH: _hatch, T_BLOCK_HEADER: _block_header, T_BLOCK: _block,
    T_LAYER: _layer, 47: _mline, 29: _pface, T_VERTEX_PFACE: _vertex3d, 14: _pface_face,
}
_CLASS_READERS = {"LWPOLYLINE": _lwpline, "HATCH": _hatch, "GEODATA": _geodata, "MULTILEADER": _mleader}
_NAMES = {T_LINE: "LINE", T_CIRCLE: "CIRCLE", T_ARC: "ARC", T_POINT: "POINT", T_ELLIPSE: "ELLIPSE",
          T_LWPOLYLINE: "LWPOLYLINE", T_POLYLINE2D: "POLYLINE2D", T_POLYLINE3D: "POLYLINE3D", T_VERTEX2D: "VERTEX2D",
          T_VERTEX3D: "VERTEX3D", T_TEXT: "TEXT", T_ATTRIB: "ATTRIB", T_MTEXT: "MTEXT", T_INSERT: "INSERT",
          T_MINSERT: "MINSERT", T_SOLID: "SOLID", T_TRACE: "TRACE", T_3DFACE: "3DFACE", T_SPLINE: "SPLINE",
          T_HATCH: "HATCH", 47: "MLINE", 29: "PFACE", T_VERTEX_PFACE: "VERTEX_PFACE", 14: "PFACE_FACE", T_BLOCK_HEADER: "BLOCK_HEADER", T_BLOCK: "BLOCK", T_LAYER: "LAYER"}
