"""Turn the decoded DWG objects into an ezdxf document (written as DXF for the rest of the pipeline)."""
from __future__ import annotations

import math
from typing import Dict, List

import ezdxf

from .container import DwgFile
from .objects import ObjectReader, Obj

DEG = 180.0 / math.pi


def _attribs(o: Obj) -> dict:
    a = {}
    c = o.get("color", 256)
    if c is not None and c != 256:
        a["color"] = c
    if o.get("rgb") is not None:
        a["true_color"] = o["rgb"]
    ext = o.get("ext")
    if ext and tuple(round(v, 12) for v in ext) != (0.0, 0.0, 1.0) and any(ext):
        a["extrusion"] = ext
    return a


class Builder:
    def __init__(self, objs: List[Obj]):
        self.by_handle: Dict[int, Obj] = {o["handle"]: o for o in objs}
        self.objs = objs
        self.doc = ezdxf.new("R2018", setup=False)
        self.layers: Dict[int, str] = {}
        self.blocks: Dict[int, str] = {}     # block header handle -> name in the new document
        self.skipped: Dict[str, int] = {}
        self.children: Dict[int, List[int]] = {}   # owner -> owned entities (R2000 has no owned lists)
        self.model_free: List[int] = []
        for o in objs:
            if "space" not in o:
                continue
            if o.get("owner") is not None:
                self.children.setdefault(o["owner"], []).append(o["handle"])
            elif o.get("space") == 2:
                self.model_free.append(o["handle"])

    # ------------------------------------------------------------ tables
    def build_layers(self):
        for o in self.objs:
            if o["type"] != "LAYER":
                continue
            name = o.get("name") or "0"
            self.layers[o["handle"]] = name
            if name == "0":
                lay = self.doc.layers.get("0")
            elif name in self.doc.layers:
                continue
            else:
                lay = self.doc.layers.add(name)
            c = o.get("color")
            if c and 0 < c < 256:
                lay.color = c
            if o.get("rgb") is not None:
                lay.rgb = ((o["rgb"] >> 16) & 255, (o["rgb"] >> 8) & 255, o["rgb"] & 255)
            if o.get("off"):
                lay.off()
            if o.get("frozen"):
                lay.freeze()

    def build_blocks(self):
        heads = [o for o in self.objs if o["type"] == "BLOCK_HEADER"]
        for o in heads:
            name = o.get("name") or ""
            low = name.lower()
            if low == "*model_space":
                self.blocks[o["handle"]] = "*Model_Space"
                continue
            if low.startswith("*paper_space"):
                continue
            if o.get("xref") or o.get("overlay"):
                continue
            if name.startswith("*"):
                blk = self.doc.blocks.new_anonymous_block(type_char=name[1:2].upper() or "U",
                                                          base_point=o.get("base", (0, 0, 0)))
            else:
                if name in self.doc.blocks:
                    continue
                blk = self.doc.blocks.new(name, base_point=o.get("base", (0, 0, 0)))
            self.blocks[o["handle"]] = blk.name
        # fill every block (model space included) from the owned-object lists
        for o in heads:
            target = self.blocks.get(o["handle"])
            if not target:
                continue
            layout = self.doc.modelspace() if target == "*Model_Space" else self.doc.blocks.get(target)
            owned = o.get("owned") or (self.children.get(o["handle"], []) +
                                        (self.model_free if target == "*Model_Space" else []))
            for hd in owned:
                e = self.by_handle.get(hd)
                if e is not None and not e.get("invisible"):
                    self.add(layout, e)

    def build_geodata(self):
        g = next((o for o in self.objs if o["type"] == "GEODATA" and o.get("csdef")), None)
        if g is None:
            return
        try:
            gd = self.doc.modelspace().new_geodata()
            gd.coordinate_system_definition = g["csdef"]
            gd.dxf.design_point = g.get("design", (0, 0, 0))
            gd.dxf.reference_point = g.get("ref", (0, 0, 0))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ entities
    def layer(self, e: Obj) -> str:
        return self.layers.get(e.get("layer"), "0")

    def add(self, L, e: Obj):
        t = e["type"]
        a = {"layer": self.layer(e), **_attribs(e)}
        try:
            fn = getattr(self, "_" + t.lower(), None)
            if fn is None:
                self.skipped[t] = self.skipped.get(t, 0) + 1
                return
            fn(L, e, a)
        except Exception:  # noqa: BLE001 - one bad entity must not stop the drawing
            self.skipped[t + " (invalid)"] = self.skipped.get(t + " (invalid)", 0) + 1

    def _line(self, L, e, a):
        a.pop("extrusion", None)
        L.add_line(e["p"][0], e["p"][1], dxfattribs=a)

    def _circle(self, L, e, a):
        L.add_circle(e["c"], max(e["r"], 1e-9), dxfattribs=a)

    def _arc(self, L, e, a):
        L.add_arc(e["c"], max(e["r"], 1e-9), e["a0"] * DEG, e["a1"] * DEG, dxfattribs=a)

    def _point(self, L, e, a):
        L.add_point(e["c"], dxfattribs=a)

    def _ellipse(self, L, e, a):
        ext = a.pop("extrusion", (0, 0, 1))
        L.add_ellipse(e["c"], major_axis=e["major"], ratio=e["ratio"], start_param=e["a0"], end_param=e["a1"],
                      dxfattribs={**a, "extrusion": ext})

    def _lwpolyline(self, L, e, a):
        pts = e["pts"]
        if len(pts) < 1:
            return
        b = e.get("bulges") or []
        w = e.get("widths") or []
        rows = []
        for i, (x, y) in enumerate(pts):
            sw, ew = w[i] if i < len(w) else (0.0, 0.0)
            rows.append((x, y, sw, ew, b[i] if i < len(b) else 0.0))
        a["elevation"] = e.get("elev", 0.0)
        L.add_lwpolyline(rows, format="xyseb", close=e.get("closed", False), dxfattribs=a)

    def _verts(self, e):
        out = []
        for hd in e.get("vertices") or self.children.get(e["handle"], []):
            v = self.by_handle.get(hd)
            if v is not None and v["type"] in ("VERTEX2D", "VERTEX3D"):
                if v.get("vflags", 0) & 16:          # spline frame control point
                    continue
                out.append(v)
        return out

    def _polyline2d(self, L, e, a):
        vs = self._verts(e)
        if not vs:
            return
        a["elevation"] = e.get("elev", 0.0)
        L.add_lwpolyline([(v["c"][0], v["c"][1], 0, 0, v.get("bulge", 0.0)) for v in vs], format="xyseb",
                         close=e.get("closed", False), dxfattribs=a)

    def _polyline3d(self, L, e, a):
        vs = self._verts(e)
        if not vs:
            return
        a.pop("extrusion", None)
        L.add_polyline3d([v["c"] for v in vs], close=e.get("closed", False), dxfattribs=a)

    def _text(self, L, e, a):
        a.update(height=e["height"] or 1.0, rotation=e["rot"] * DEG, width=e.get("width", 1.0) or 1.0,
                 oblique=e.get("oblique", 0.0) * DEG, insert=e["c"], halign=e.get("halign", 0),
                 valign=e.get("valign", 0))
        if e.get("align") is not None:
            a["align_point"] = e["align"]
        L.add_text(e["text"], dxfattribs=a)

    def _mtext(self, L, e, a):
        a.update(insert=e["c"], char_height=e["height"] or 1.0, rotation=e["rot"] * DEG,
                 attachment_point=e.get("attach") or 1)
        if e.get("rect_w"):
            a["width"] = e["rect_w"]
        L.add_mtext(e["text"], dxfattribs=a)

    def _insert(self, L, e, a):
        name = self.blocks.get(e.get("block"))
        if not name or name == "*Model_Space":
            return
        sx, sy, sz = e["scale"]
        a.update(xscale=sx, yscale=sy, zscale=sz, rotation=e["rot"] * DEG)
        ins = L.add_blockref(name, e["c"], dxfattribs=a)
        for hd in e.get("attribs") or self.children.get(e["handle"], []):
            at = self.by_handle.get(hd)
            if at is None or at["type"] != "ATTRIB" or not at.get("tag"):
                continue
            aa = {"layer": self.layer(at), **_attribs(at), "height": at["height"] or 1.0,
                  "rotation": at["rot"] * DEG, "halign": at.get("halign", 0), "valign": at.get("valign", 0)}
            if at.get("align") is not None:
                aa["align_point"] = at["align"]
            ins.add_attrib(at["tag"], at["text"], at["c"], dxfattribs=aa)

    def _minsert(self, L, e, a):
        self._insert(L, e, a)                                 # first cell; arrays are rare in survey drawings

    def _multileader(self, L, e, a):
        from ezdxf.render.mleader import ConnectionSide
        a.pop("extrusion", None)
        ml = L.add_multileader_mtext("Standard", dxfattribs=a)
        ml.set_content(e["text"], char_height=e.get("height") or 1.0)
        from ezdxf.math import Vec2
        for hd in e.get("heads", [])[:4]:
            ml.add_leader_line(ConnectionSide.left, [Vec2(hd[0], hd[1])])
        ml.build(insert=Vec2(e["c"][0], e["c"][1]), rotation=e.get("rot", 0.0) * DEG)

    def _pface(self, L, e, a):
        a.pop("extrusion", None)
        kids = [self.by_handle.get(hd) for hd in (e.get("vertices") or self.children.get(e["handle"], []))]
        pts = [k["c"] for k in kids if k is not None and k["type"] == "VERTEX_PFACE"]
        faces = []
        for k in kids:
            if k is not None and k["type"] == "PFACE_FACE":
                f = [pts[abs(i) - 1] for i in k["idx"] if i and abs(i) <= len(pts)]
                if len(f) >= 3:
                    faces.append(f)
        if faces:
            L.add_polyface(dxfattribs=a).append_faces(faces)

    def _mline(self, L, e, a):
        """A multiline becomes one polyline per element (offset along each vertex's miter direction)."""
        a.pop("extrusion", None)
        vs = e.get("verts") or []
        for j in range(e.get("nlines", 0)):
            pts = [(p[0] + m[0] * o[j], p[1] + m[1] * o[j], p[2] + m[2] * o[j]) for p, m, o in vs if j < len(o)]
            if len(pts) < 2:
                continue
            if len({round(q[2], 9) for q in pts}) == 1:
                L.add_lwpolyline([q[:2] for q in pts], close=e.get("closed", False),
                                 dxfattribs={**a, "elevation": pts[0][2]})
            else:
                L.add_polyline3d(pts, close=e.get("closed", False), dxfattribs=a)

    def _solid(self, L, e, a):
        L.add_solid(e["p"], dxfattribs=a)

    def _trace(self, L, e, a):
        L.add_trace(e["p"], dxfattribs=a)

    def _3dface(self, L, e, a):
        L.add_3dface(e["p"], dxfattribs=a)

    def _spline(self, L, e, a):
        a.pop("extrusion", None)
        if e.get("cps"):
            sp = L.add_spline(dxfattribs={**a, "degree": e["degree"]})
            sp.control_points = e["cps"]
            sp.knots = e["knots"]
            if e.get("weights"):
                sp.weights = e["weights"]
            if e.get("closed"):
                sp.closed = True
        elif e.get("fit"):
            L.add_spline(e["fit"], degree=e["degree"] or 3, dxfattribs=a)

    def _hatch(self, L, e, a):
        a["elevation"] = (0, 0, e.get("elev", 0.0))
        h = L.add_hatch(dxfattribs=a)
        if e.get("solid"):
            h.set_solid_fill(color=a.get("color", 256) if a.get("color") is not None else 256)
        else:
            h.set_pattern_fill(e.get("pattern") or "ANSI31", color=a.get("color", 256))
        for p in e["paths"]:
            fl = p["flags"]
            if "poly" in p:
                h.paths.add_polyline_path([(x, y, b) for x, y, b in p["poly"]], is_closed=p.get("closed", True),
                                          flags=fl)
                continue
            ep = h.paths.add_edge_path(flags=fl)
            for ed in p["edges"]:
                k = ed[0]
                if k == "line":
                    ep.add_line(ed[1], ed[2])
                elif k == "arc":       # clockwise edges are stored as complementary angles (as in DXF)
                    a0, a1 = ed[3] * DEG, ed[4] * DEG
                    if not ed[5]:
                        a0, a1 = 360.0 - a1, 360.0 - a0
                    ep.add_arc(ed[1], ed[2], a0, a1, ccw=bool(ed[5]))
                elif k == "ellipse":
                    a0, a1 = ed[4] * DEG, ed[5] * DEG
                    if not ed[6]:
                        a0, a1 = 360.0 - a1, 360.0 - a0
                    ep.add_ellipse(ed[1], ed[2], ed[3], a0, a1, ccw=bool(ed[6]))
                elif k == "spline":
                    _deg, knots, cps, ws, fit = ed[1], ed[2], ed[3], ed[4], ed[5]
                    ep.add_spline(fit_points=fit or None, control_points=cps or None, knot_values=knots or None,
                                  weights=ws or None, degree=_deg)


def read_dwg_doc(path: str):
    """Read a DWG with the built-in reader into an ezdxf document (kept in memory)."""
    with open(path, "rb") as fh:
        data = fh.read()
    R = ObjectReader(DwgFile(data))
    B = Builder(R.read_all())
    B.build_layers()
    B.build_blocks()
    B.build_geodata()
    return B.doc


def dwg_to_dxf(path: str, out: str) -> dict:
    """Read a DWG with the built-in reader and write it as DXF. Returns a small report."""
    with open(path, "rb") as fh:
        data = fh.read()
    f = DwgFile(data)
    R = ObjectReader(f)
    objs = R.read_all()
    B = Builder(objs)
    B.build_layers()
    B.build_blocks()
    B.build_geodata()
    B.doc.saveas(out)
    return {"version": f.version, "objects": len(objs), "unread": R.errors, "skipped": B.skipped,
            "layers": len(B.layers), "blocks": len(B.blocks)}
