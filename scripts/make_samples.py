#!/usr/bin/env python3
"""Generates realistic sample DXF drawings (UTM 40N coordinates, Abu Dhabi) for testing every schema.

Each drawing deliberately contains real-world defects: inconsistent layer names, a duplicate entity,
a zero-length line, a polyline drawn against flow, an unmatched label, a domain violation, an
unconnected pipe and labels that sit between two features.
"""
import math
import os
import sys

import ezdxf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "sample_data")
X0, Y0 = 245_000.0, 2_701_000.0   # Abu Dhabi island, UTM 40N


def new_doc():
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    return doc


def block(doc, name, tags=()):
    if name in doc.blocks:
        return
    b = doc.blocks.new(name)
    b.add_circle((0, 0), 0.6)
    for i, t in enumerate(tags):
        b.add_attdef(t, (0.8, -0.5 * i), dxfattribs={"height": 0.35})


def text(msp, s, x, y, layer, rot=0.0, h=0.5):
    msp.add_text(s, dxfattribs={"layer": layer, "height": h, "rotation": rot}).set_placement((x, y))


def sewer(path, storm=False):
    doc = new_doc()
    msp = doc.modelspace()
    pre = "SD" if storm else "S"
    pipe_layer = "C-STORM-PIPE" if storm else "SEW_GRAVITY_MAIN"
    mh_layer = "C-STORM-MH" if storm else "SEW-MH"
    block(doc, "MH", ["MH_NO", "CL", "IL"])
    # 6 manholes along a street, falling invert levels
    mhs = []
    for i in range(6):
        x, y = X0 + i * 45.0, Y0 + (i % 2) * 3.0
        cl, il = 5.80 - i * 0.05, 3.90 - i * 0.18
        ref = msp.add_blockref("MH", (x, y), dxfattribs={"layer": mh_layer})
        ref.add_auto_attribs({"MH_NO": f"{pre}MH-{101 + i}", "CL": f"{cl:.2f}", "IL": f"{il:.2f}"})
        mhs.append((x, y, il))
    for i in range(5):
        (x1, y1, il1), (x2, y2, il2) = mhs[i], mhs[i + 1]
        pts = [(x1, y1), ((x1 + x2) / 2, (y1 + y2) / 2 + 0.4), (x2, y2)]
        if i == 2:
            pts = list(reversed(pts))  # drawn against flow
        msp.add_lwpolyline(pts, dxfattribs={"layer": pipe_layer})
        ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 + 1.2
        dia = [300, 300, 375, 375, 450][i]
        mat = "UPVC" if i < 3 else "GRP"
        text(msp, f"{'SD' if storm else 'SP'}-{201 + i} {dia}MM {mat} L={math.hypot(x2 - x1, y2 - y1):.1f}m S=0.40%",
             mx - 8, my, "TEXT-PIPE", ang)
    # duplicate pipe (copy of first)
    msp.add_lwpolyline([(mhs[0][0], mhs[0][1]), ((mhs[0][0] + mhs[1][0]) / 2, (mhs[0][1] + mhs[1][1]) / 2 + 0.4),
                        (mhs[1][0], mhs[1][1])], dxfattribs={"layer": pipe_layer})
    # zero-length line
    msp.add_line((X0 + 10, Y0 + 10), (X0 + 10, Y0 + 10), dxfattribs={"layer": pipe_layer})
    # disconnected branch on a cryptic layer
    msp.add_line((X0 + 300, Y0 + 80), (X0 + 330, Y0 + 80), dxfattribs={"layer": "L4-SEW"})
    text(msp, "200MM UPVC", X0 + 310, Y0 + 81, "TEXT-PIPE")
    # house connection
    msp.add_line((X0 + 45, Y0 + 3), (X0 + 45, Y0 + 18), dxfattribs={"layer": "HC-LATERAL" if not storm else "C-STORM-PIPE"})
    # manhole without attributes, labelled by text only (on mixed layer "0")
    msp.add_circle((X0 + 270, Y0 + 3), 0.6, dxfattribs={"layer": mh_layer})
    text(msp, f"{pre}MH-107 CL 5.40 IL 2.70", X0 + 271, Y0 + 4.2, "TEXT-MH")
    # unmatched label far from anything
    text(msp, f"{pre}MH-999 CL 6.00", X0 + 600, Y0 + 300, "TEXT-MH")
    # invalid domain value for material
    msp.add_line((X0 + 225, Y0 + 3), (X0 + 225, Y0 + 30), dxfattribs={"layer": pipe_layer})
    text(msp, "150MM PPR L=27.0m", X0 + 226, Y0 + 15, "TEXT-PIPE", 90)
    if storm:
        block(doc, "GULLY", [])
        for k in range(3):
            msp.add_blockref("GULLY", (X0 + 20 + k * 45, Y0 - 6), dxfattribs={"layer": "C-STORM-GULLY"})
        msp.add_lwpolyline([(X0 + 350, Y0 - 40), (X0 + 420, Y0 - 40), (X0 + 420, Y0 - 10), (X0 + 350, Y0 - 10)],
                           close=True, dxfattribs={"layer": "POND"})
    # title block text (unrecognised)
    text(msp, "DRAWING NO. ADM-INF-0042  SHEET 3 OF 9", X0 - 40, Y0 - 60, "TITLE", h=2.0)
    doc.saveas(path)


def water(path):
    doc = new_doc()
    msp = doc.modelspace()
    block(doc, "GV", ["TAG"])
    block(doc, "FH", ["TAG"])
    pts = [(X0 + i * 60, Y0 + 200) for i in range(5)]
    for i in range(4):
        msp.add_line(pts[i], pts[i + 1], dxfattribs={"layer": "WTR-DIST-MAIN"})
        text(msp, f"WM-{11 + i} DN200 DI PN16", pts[i][0] + 20, pts[i][1] + 1.0, "WTR-TXT")
    for i in (1, 3):
        r = msp.add_blockref("GV", pts[i], dxfattribs={"layer": "WTR-VALVE"})
        r.add_auto_attribs({"TAG": f"GV-{i:03d}"})
    r = msp.add_blockref("FH", (pts[4][0], pts[4][1]), dxfattribs={"layer": "WTR-HYDRANT"})
    r.add_auto_attribs({"TAG": "FH-01"})
    msp.add_line(pts[2], (pts[2][0], pts[2][1] + 40), dxfattribs={"layer": "WTR-DIST-MAIN"})
    text(msp, "WM-15 DN110 HDPE", pts[2][0] + 1, pts[2][1] + 20, "WTR-TXT", 90)
    msp.add_line((X0, Y0 + 260), (X0 + 50, Y0 + 260), dxfattribs={"layer": "WTR-SERVICE"})
    text(msp, "DN32 PE", X0 + 25, Y0 + 261, "WTR-TXT")
    doc.saveas(path)


def irrigation(path):
    doc = new_doc()
    msp = doc.modelspace()
    block(doc, "SV", ["VALVE_ID"])
    msp.add_lwpolyline([(X0, Y0 + 400), (X0 + 120, Y0 + 400), (X0 + 120, Y0 + 460)], dxfattribs={"layer": "IRR-MAIN-TSE"})
    text(msp, "HDPE 110MM TSE", X0 + 60, Y0 + 401, "IRR-TEXT")
    for k in range(3):
        y = Y0 + 410 + k * 15
        msp.add_line((X0 + 120, y), (X0 + 170, y), dxfattribs={"layer": "IRR-LATERAL"})
        text(msp, "PE 25MM", X0 + 140, y + 0.6, "IRR-TEXT")
        r = msp.add_blockref("SV", (X0 + 120, y), dxfattribs={"layer": "IRR-VALVE"})
        r.add_auto_attribs({"VALVE_ID": f"SV-{k + 1:02d}"})
    msp.add_lwpolyline([(X0 + 125, Y0 + 405), (X0 + 175, Y0 + 405), (X0 + 175, Y0 + 445), (X0 + 125, Y0 + 445)],
                       close=True, dxfattribs={"layer": "IRR-ZONE"})
    text(msp, "ZONE 3A DRIP", X0 + 150, Y0 + 425, "IRR-TEXT")
    doc.saveas(path)


def lighting(path):
    doc = new_doc()
    msp = doc.modelspace()
    block(doc, "LP", ["POLE_NO", "H"])
    block(doc, "FP", ["ID"])
    r = msp.add_blockref("FP", (X0, Y0 + 600), dxfattribs={"layer": "E-FEEDER-PILLAR"})
    r.add_auto_attribs({"ID": "FP-07"})
    prev = (X0, Y0 + 600)
    for i in range(6):
        p = (X0 + 30 * (i + 1), Y0 + 600)
        r = msp.add_blockref("LP", p, dxfattribs={"layer": "E-STREET-LIGHT"})
        r.add_auto_attribs({"POLE_NO": f"LP-{i + 1:03d}", "H": "10"})
        msp.add_line(prev, p, dxfattribs={"layer": "E-LV-CABLE"})
        text(msp, "4Cx16 SQMM CCT 2", (prev[0] + p[0]) / 2 - 5, p[1] + 0.8, "E-TEXT")
        prev = p
    text(msp, "LED 120W", X0 + 31, Y0 + 602, "E-TEXT")
    doc.saveas(path)


def landscaping(path_soft, path_hard):
    doc = new_doc()
    msp = doc.modelspace()
    block(doc, "PALM", ["ID"])
    block(doc, "TREE-GHAF", ["ID"])
    for i in range(8):
        r = msp.add_blockref("PALM", (X0 + i * 8, Y0 + 800), dxfattribs={"layer": "L-PALM-TREES"})
        r.add_auto_attribs({"ID": f"PT-{i + 1:04d}"})
        text(msp, f"H={4 + i * 0.5:.1f}m", X0 + i * 8 + 0.8, Y0 + 801, "L-TEXT")
    msp.add_blockref("PALM", (X0, Y0 + 800), dxfattribs={"layer": "L-PALM-TREES"})  # duplicate location
    for i in range(3):
        msp.add_blockref("TREE-GHAF", (X0 + i * 12, Y0 + 820), dxfattribs={"layer": "L-NATIVE-TREE"})
    msp.add_lwpolyline([(X0 - 5, Y0 + 790), (X0 + 70, Y0 + 790), (X0 + 70, Y0 + 840), (X0 - 5, Y0 + 840)], close=True,
                       dxfattribs={"layer": "L-LAWN-AREA"})
    msp.add_lwpolyline([(X0 + 80, Y0 + 790), (X0 + 120, Y0 + 790), (X0 + 120, Y0 + 830), (X0 + 100, Y0 + 770), (X0 + 80, Y0 + 830)],
                       close=True, dxfattribs={"layer": "L-GROUNDCOVER"})  # self-intersecting
    msp.add_lwpolyline([(X0 - 10, Y0 + 785), (X0 + 130, Y0 + 785)], dxfattribs={"layer": "L-HEDGE"})
    msp.add_lwpolyline([(X0 - 20, Y0 + 770), (X0 + 140, Y0 + 770), (X0 + 140, Y0 + 860), (X0 - 20, Y0 + 860)], close=True,
                       dxfattribs={"layer": "PARK-BOUNDARY"})
    doc.saveas(path_soft)

    doc = new_doc()
    msp = doc.modelspace()
    block(doc, "BENCH", ["ID"])
    block(doc, "BOLLARD", [])
    block(doc, "LIGHT-POLE", ["ID", "HEIGHT"])
    for i in range(4):
        r = msp.add_blockref("BENCH", (X0 + i * 15, Y0 + 900), dxfattribs={"layer": "HL-FURNITURE-BENCH"})
        r.add_auto_attribs({"ID": f"BN-{i + 1:03d}"})
    for i in range(10):
        msp.add_blockref("BOLLARD", (X0 + i * 2.5, Y0 + 910), dxfattribs={"layer": "HL-BOLLARDS"})
    for i in range(3):
        r = msp.add_blockref("LIGHT-POLE", (X0 + i * 25, Y0 + 920), dxfattribs={"layer": "HL-LIGHTING-POLE"})
        r.add_auto_attribs({"ID": f"LP-{i + 1:04d}", "HEIGHT": "6"})
    msp.add_lwpolyline([(X0 - 5, Y0 + 895), (X0 + 80, Y0 + 895)], dxfattribs={"layer": "HL-KERB"})
    msp.add_lwpolyline([(X0 - 5, Y0 + 925), (X0 + 80, Y0 + 925), (X0 + 80, Y0 + 940), (X0 - 5, Y0 + 940)], close=True,
                       dxfattribs={"layer": "HL-PAVING-INTERLOCK"})
    h = msp.add_hatch(dxfattribs={"layer": "HL-CONCRETE"})
    h.paths.add_polyline_path([(X0 + 90, Y0 + 925), (X0 + 120, Y0 + 925), (X0 + 120, Y0 + 945), (X0 + 90, Y0 + 945)], is_closed=True)
    h.paths.add_polyline_path([(X0 + 100, Y0 + 930), (X0 + 110, Y0 + 930), (X0 + 110, Y0 + 940), (X0 + 100, Y0 + 940)], is_closed=True)
    msp.add_lwpolyline([(X0 - 10, Y0 + 890), (X0 + 130, Y0 + 890)], dxfattribs={"layer": "FENCE-CHAINLINK"})
    msp.add_line((X0, Y0 + 950), (X0 + 20, Y0 + 950), dxfattribs={"layer": "DIM"})  # junk layer
    doc.saveas(path_hard)


def legend_drawing(path, where):
    """Cryptic layer names (L-01..L-04): only the drawing key tells what they are.
    where = 'layout' (legend on a layout sheet) | 'model' (legend drawn on the drawing sheet)."""
    doc = new_doc()
    msp = doc.modelspace()
    b = doc.blocks.new("B1")
    b.add_lwpolyline([(-0.9, -0.25), (0.9, -0.25), (0.9, 0.25), (-0.9, 0.25)], close=True)
    for name in ("L-01", "L-02", "L-03", "L-04"):
        doc.layers.add(name)
    x0, y0 = X0 + 500, Y0 + 1000
    for i in range(6):   # gravel beds
        h = msp.add_hatch(color=40, dxfattribs={"layer": "L-01"})
        h.set_pattern_fill("GRAVEL", scale=0.5)
        h.paths.add_polyline_path([(x0 + i * 12, y0), (x0 + i * 12 + 10, y0), (x0 + i * 12 + 10, y0 + 8), (x0 + i * 12, y0 + 8)], is_closed=True)
    for i in range(10):  # benches
        msp.add_blockref("B1", (x0 + i * 7, y0 + 15), dxfattribs={"layer": "L-02"})
    for i in range(6):   # edgers along the beds
        msp.add_lwpolyline([(x0 + i * 12, y0 - 0.5), (x0 + i * 12 + 10, y0 - 0.5)], dxfattribs={"layer": "L-03"})
    for i in range(3):   # heel kerb, narrow outlines
        msp.add_lwpolyline([(x0, y0 + 25 + i * 5), (x0 + 30, y0 + 25 + i * 5), (x0 + 30, y0 + 25.15 + i * 5), (x0, y0 + 25.15 + i * 5)],
                           close=True, dxfattribs={"layer": "L-04"})
    rows = [("hatch", "L-01", "Natural Crushed Gravel Aggregate"), ("block", "L-02", "Precast Concrete Bench"),
            ("line", "L-03", "Aluminium Edger"), ("kerb", "L-04", "Precast Concrete Heel Kerb")]
    if where == "layout":
        space = doc.layouts.new("A1-LEGEND")
        lx, ly, th = 300, 500, 3.5
    else:
        space = msp
        lx, ly, th = x0 + 150, y0 + 40, 0.8
    space.add_text("HARDSCAPE LEGEND", dxfattribs={"height": th * 1.4}).set_placement((lx, ly))
    for k, (kind, layer, desc) in enumerate(rows):
        y = ly - (k + 1.5) * th * 3
        sx = lx
        if kind == "hatch":
            h = space.add_hatch(color=40, dxfattribs={"layer": layer})
            h.set_pattern_fill("GRAVEL", scale=0.5)
            h.paths.add_polyline_path([(sx, y - th), (sx + 4 * th, y - th), (sx + 4 * th, y + th), (sx, y + th)], is_closed=True)
        elif kind == "block":
            space.add_blockref("B1", (sx + 2 * th, y), dxfattribs={"layer": layer, "xscale": th / 2, "yscale": th / 2})
        elif kind == "line":
            space.add_line((sx, y), (sx + 4 * th, y), dxfattribs={"layer": layer})
        else:
            space.add_lwpolyline([(sx, y - 0.1 * th), (sx + 4 * th, y - 0.1 * th), (sx + 4 * th, y + 0.1 * th), (sx, y + 0.1 * th)],
                                 close=True, dxfattribs={"layer": layer})
        space.add_text(desc, dxfattribs={"height": th}).set_placement((sx + 6 * th, y - th / 2))
    doc.saveas(path)


def irrigation_mixed(path):
    """How consultants really deliver it: one IRR-PIPE layer holding the mainline (continuous, heavy, drawn in
    fragments) and the laterals (dashed, thin); one IRR-FITTINGS layer holding solenoid valves (on the main) and
    a controller (off the network) with neutral block names; base-map, sheet and dimension layers around it;
    a duplicated lateral and a zero-length segment to clean."""
    doc = new_doc()
    msp = doc.modelspace()
    for name, lt, lw in (("IRR-PIPE", "Continuous", 25), ("IRR-FITTINGS", "Continuous", 25), ("IRR-TEXT", "Continuous", 13),
                         ("EX-ROAD", "Continuous", 13), ("PLOT-BOUNDARY", "Continuous", 13), ("BUILDING", "Continuous", 13),
                         ("A-ANNO-TTLB", "Continuous", 35), ("DIM", "Continuous", 13)):
        doc.layers.add(name, linetype=lt, lineweight=lw)
    for nm in ("SYMA", "SYMB"):
        b = doc.blocks.new(nm)
        if nm == "SYMA":
            b.add_lwpolyline([(-0.4, -0.3), (0.4, 0.3), (0.4, -0.3), (-0.4, 0.3)], close=True)
        else:
            b.add_lwpolyline([(-0.8, -0.5), (0.8, -0.5), (0.8, 0.5), (-0.8, 0.5)], close=True)
    x0, y0 = X0 + 1500, Y0 + 400
    # mainline Ø110 drawn as 4 fragments (continuous, 0.50 mm)
    xs = [0, 30, 55, 90, 130]
    for a, b in zip(xs, xs[1:]):
        msp.add_line((x0 + a, y0), (x0 + b, y0), dxfattribs={"layer": "IRR-PIPE", "lineweight": 50, "linetype": "Continuous"})
    text(msp, "Ø110 uPVC TSE", x0 + 42, y0 + 0.6, "IRR-TEXT")
    text(msp, "Ø110 uPVC", x0 + 110, y0 + 0.6, "IRR-TEXT")
    # laterals Ø25 (dashed, 0.18 mm) branching off at the valves
    for k, bx in enumerate((20, 45, 75, 110)):
        msp.add_line((x0 + bx, y0), (x0 + bx, y0 + 35), dxfattribs={"layer": "IRR-PIPE", "lineweight": 18, "linetype": "DASHED"})
        text(msp, "Ø25 PE", x0 + bx + 0.5, y0 + 18, "IRR-TEXT", rot=90)
        r = msp.add_blockref("SYMA", (x0 + bx, y0), dxfattribs={"layer": "IRR-FITTINGS"})
        text(msp, f"SV-{k + 1:02d}", x0 + bx + 1, y0 - 1.5, "IRR-TEXT")
    # duplicated lateral + zero-length segment (to be cleaned)
    msp.add_line((x0 + 45, y0), (x0 + 45, y0 + 35), dxfattribs={"layer": "IRR-PIPE", "lineweight": 18, "linetype": "DASHED"})
    msp.add_line((x0 + 60, y0 + 10), (x0 + 60, y0 + 10), dxfattribs={"layer": "IRR-PIPE", "lineweight": 18, "linetype": "DASHED"})
    # controller away from the pipes
    msp.add_blockref("SYMB", (x0 - 12, y0 + 15), dxfattribs={"layer": "IRR-FITTINGS"})
    text(msp, "CONTROLLER C1 24 STATIONS", x0 - 15, y0 + 17, "IRR-TEXT")
    # base map
    msp.add_lwpolyline([(x0 - 30, y0 - 8), (x0 + 160, y0 - 8)], dxfattribs={"layer": "EX-ROAD"})
    msp.add_lwpolyline([(x0 - 30, y0 - 20), (x0 + 160, y0 - 20)], dxfattribs={"layer": "EX-ROAD"})
    msp.add_lwpolyline([(x0 - 25, y0 - 5), (x0 + 150, y0 - 5), (x0 + 150, y0 + 50), (x0 - 25, y0 + 50)], close=True,
                       dxfattribs={"layer": "PLOT-BOUNDARY"})
    text(msp, "PLOT C-12", x0 + 140, y0 + 45, "PLOT-BOUNDARY")
    msp.add_lwpolyline([(x0 + 60, y0 + 40), (x0 + 80, y0 + 40), (x0 + 80, y0 + 48), (x0 + 60, y0 + 48)], close=True,
                       dxfattribs={"layer": "BUILDING"})
    # sheet furniture
    msp.add_lwpolyline([(x0 - 40, y0 - 30), (x0 + 180, y0 - 30), (x0 + 180, y0 + 70), (x0 - 40, y0 + 70)], close=True,
                       dxfattribs={"layer": "A-ANNO-TTLB"})
    text(msp, "IRRIGATION LAYOUT - SHEET 3 OF 9", x0 + 100, y0 - 27, "A-ANNO-TTLB")
    msp.add_aligned_dim((x0, y0 - 3), (x0 + 130, y0 - 3), distance=1, dxfattribs={"layer": "DIM"}).render()
    doc.saveas(path)


def irrigation_cryptic_with_reference(dxf_path, gpkg_path):
    """Cryptic layer names, no key, no labels: only overlaying the approved GIS (spatial join) tells what they are.
    The reference GeoPackage is in WGS 84 (EPSG:4326) to exercise the coordinate transformation."""
    import numpy as np
    from pyogrio.raw import write
    from pyproj import Transformer
    from shapely import to_wkb
    from shapely.geometry import LineString, Point
    doc = new_doc()
    msp = doc.modelspace()
    x0, y0 = X0 + 2000, Y0 + 600
    mains = [[(x0, y0), (x0 + 80, y0)], [(x0 + 80, y0), (x0 + 80, y0 + 60)]]
    laterals = [[(x0 + 20 * k, y0), (x0 + 20 * k, y0 + 25)] for k in (1, 2, 3)]
    valves = [(x0 + 20 * k, y0) for k in (1, 2, 3)]
    for c in mains:
        msp.add_lwpolyline(c, dxfattribs={"layer": "L-10"})
    for c in laterals:
        msp.add_lwpolyline(c, dxfattribs={"layer": "L-12"})
    b = doc.blocks.new("S1")
    b.add_circle((0, 0), 0.4)
    for v in valves:
        msp.add_blockref("S1", v, dxfattribs={"layer": "L-11"})
    doc.saveas(dxf_path)
    # approved GIS of the same area — digitised slightly differently (0.2 m shift, split differently)
    tr = Transformer.from_crs(32640, 4326, always_xy=True)
    def ll(coords):
        return [tr.transform(x + 0.2, y - 0.15) for x, y in coords]
    if os.path.exists(gpkg_path):
        os.remove(gpkg_path)
    ref_main = [[(x0, y0), (x0 + 80, y0), (x0 + 80, y0 + 60)]]
    write(gpkg_path, np.array([to_wkb(LineString(ll(c))) for c in ref_main], dtype=object),
          [np.array(["HDPE"], dtype=object), np.array([110]), np.array(["IM-001"], dtype=object)],
          fields=["MATERIAL", "DIAMETER", "PIPE_ID"], layer="IrrigationMainline", driver="GPKG",
          geometry_type="LineString", crs="EPSG:4326")
    write(gpkg_path, np.array([to_wkb(LineString(ll(c))) for c in laterals], dtype=object),
          [np.array(["PE"] * 3, dtype=object), np.array([25] * 3)],
          fields=["MATERIAL", "DIAMETER"], layer="Irrigation_Laterals", driver="GPKG",
          geometry_type="LineString", crs="EPSG:4326", append=True)
    write(gpkg_path, np.array([to_wkb(Point(ll([v])[0])) for v in valves], dtype=object),
          [np.array([f"SV-{k:02d}" for k in (1, 2, 3)], dtype=object), np.array(["3A", "3B", "3C"], dtype=object)],
          fields=["VALVE_ID", "ZONE_NO"], layer="IrrigationValve", driver="GPKG",
          geometry_type="Point", crs="EPSG:4326", append=True)


if __name__ == "__main__":
    legend_drawing(os.path.join(OUT, "sample_legend_on_layout.dxf"), "layout")
    legend_drawing(os.path.join(OUT, "sample_legend_on_drawing.dxf"), "model")
    os.makedirs(OUT, exist_ok=True)
    sewer(os.path.join(OUT, "sample_wastewater.dxf"))
    sewer(os.path.join(OUT, "sample_stormwater.dxf"), storm=True)
    water(os.path.join(OUT, "sample_water.dxf"))
    irrigation(os.path.join(OUT, "sample_irrigation.dxf"))
    irrigation_mixed(os.path.join(OUT, "sample_irrigation_mixed.dxf"))
    irrigation_cryptic_with_reference(os.path.join(OUT, "sample_irrigation_cryptic.dxf"),
                                      os.path.join(OUT, "sample_irrigation_reference.gpkg"))
    lighting(os.path.join(OUT, "sample_street_lighting.dxf"))
    landscaping(os.path.join(OUT, "sample_soft_landscaping.dxf"), os.path.join(OUT, "sample_hard_landscaping.dxf"))
    print("samples written to", OUT)
