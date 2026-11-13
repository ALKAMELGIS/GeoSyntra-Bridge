"""CAD geometry clean-up: safe automatic removals, and advanced proposals applied only after review."""
import os
import shutil
import tempfile
import time
import unittest

import ezdxf
from fastapi.testclient import TestClient

from geosyntra_bridge.api.app import create_app
from geosyntra_bridge.cad.reader import read_cad
from geosyntra_bridge.cad import cleanup as CU
from geosyntra_bridge.config import Settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_dxf(path):
    doc = ezdxf.new("R2013"); doc.header["$INSUNITS"] = 6
    m = doc.modelspace()
    for n in ("WALL", "KERB", "PIPE", "BEDS", "TREES", "ANNO"):
        doc.layers.add(n)
    blk = doc.blocks.new("TREE"); blk.add_circle((0, 0), 0.5)
    X, Y = 245000, 2701400
    m.add_line((X, Y), (X + 10, Y), dxfattribs={"layer": "WALL"})
    m.add_line((X + 10, Y), (X, Y), dxfattribs={"layer": "WALL"})                    # exact duplicate, reversed
    m.add_line((X + 0.0004, Y), (X + 10.0003, Y), dxfattribs={"layer": "WALL"})      # duplicate within 1 mm
    m.add_line((X + 3, Y), (X + 6, Y), dxfattribs={"layer": "WALL"})                 # lies on the wall (overlap)
    m.add_line((X, Y + 50), (X, Y + 50), dxfattribs={"layer": "WALL"})              # zero length
    m.add_arc((X + 20, Y), 5, 0, 90, dxfattribs={"layer": "KERB"})
    m.add_arc((X + 20, Y), 5, 0, 90, dxfattribs={"layer": "KERB"})                   # duplicate arc
    m.add_circle((X + 40, Y), 2, dxfattribs={"layer": "BEDS"})
    m.add_circle((X + 40, Y), 2, dxfattribs={"layer": "BEDS"})                       # duplicate circle
    m.add_circle((X + 60, Y), 2, dxfattribs={"layer": "BEDS"})
    m.add_circle((X + 60, Y), 2.5, dxfattribs={"layer": "BEDS"})                     # different size: kept
    m.add_lwpolyline([(X, Y + 20), (X + 5, Y + 20), (X + 5, Y + 20), (X + 9, Y + 22)], dxfattribs={"layer": "PIPE"})  # dup vertex
    m.add_lwpolyline([(X, Y + 20), (X + 5, Y + 20), (X + 9, Y + 22)], dxfattribs={"layer": "PIPE"})                    # = after vertex fix
    m.add_lwpolyline([(X + 9.2, Y + 22), (X + 20, Y + 22)], dxfattribs={"layer": "PIPE"})                              # 0.2 m gap
    m.add_lwpolyline([(X + 30, Y + 30), (X + 40, Y + 30), (X + 40, Y + 40), (X + 30, Y + 40), (X + 30, Y + 30.1)],
                     dxfattribs={"layer": "BEDS"})                                                                     # almost closed
    m.add_lwpolyline([(X + 50, Y + 30), (X + 60, Y + 40), (X + 60, Y + 30), (X + 50, Y + 40)], close=True,
                     dxfattribs={"layer": "BEDS"})                                                                     # bow-tie
    m.add_blockref("TREE", (X + 70, Y), dxfattribs={"layer": "TREES"})
    m.add_blockref("TREE", (X + 70.02, Y), dxfattribs={"layer": "TREES"})           # 20 mm away: near-duplicate (review)
    m.add_blockref("TREE", (X + 75, Y), dxfattribs={"layer": "TREES"})
    m.add_blockref("TREE", (X + 75, Y), dxfattribs={"layer": "ANNO"})               # same tree on another layer (review)
    doc.saveas(path)


def wait(c, jid, states=("ready",), t=60):
    end = time.time() + t
    while time.time() < end:
        s = c.get(f"/api/jobs/{jid}").json()
        if s["status"] in states or s["status"] == "failed":
            return s
        time.sleep(0.3)
    raise AssertionError("timeout")


class Cleanup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_clean_")
        cls.dxf = os.path.join(cls.tmp, "dups.dxf")
        make_dxf(cls.dxf)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_automatic_is_safe(self):
        doc = read_cad(self.dxf, os.path.join(self.tmp, "w1"))
        acts = {c["action"]: c["count"] for c in doc.cleaning}
        txt = " | ".join(f"{k}={v}" for k, v in acts.items())
        self.assertEqual(acts.get("Removed duplicate lines (OVERKILL)"), 2, txt)
        self.assertEqual(acts.get("Removed duplicate arcs (OVERKILL)"), 1, txt)
        self.assertEqual(acts.get("Removed duplicate circles (OVERKILL)"), 1, txt)
        self.assertEqual(acts.get("Removed duplicate polylines (OVERKILL)"), 1, txt)
        self.assertEqual(acts.get("Removed zero-length geometry"), 1, txt)
        self.assertIn("Removed 1 duplicate vertices", acts)
        live = [e for e in doc.entities if e.kind != "artifact"]
        circles = sorted(round(e.radius, 2) for e in live if e.dxftype == "CIRCLE")
        self.assertEqual(circles, [2.0, 2.0, 2.5])                       # the different-size circle is kept
        self.assertEqual(sum(1 for e in live if e.layer == "WALL"), 2)   # wall + the covered piece (review only)
        self.assertEqual(sum(1 for e in live if e.block_name == "TREE"), 4)  # near / cross-layer copies not touched

    def test_advanced_proposals(self):
        doc = read_cad(self.dxf, os.path.join(self.tmp, "w2"))
        kinds = {}
        for p in CU.analyse(doc):
            kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
        for k in ("overlap", "open_gap", "near_closed", "self_intersection", "near_duplicate", "cross_layer_duplicate"):
            self.assertIn(k, kinds, kinds)

    def test_review_and_apply(self):
        app = create_app(Settings(data_dir=os.path.join(self.tmp, "d"), rules_dir=os.path.join(self.tmp, "r")))
        c = TestClient(app)
        try:
            with open(self.dxf, "rb") as f:
                jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("dups.dxf", f, "x")}).json()["job_id"]
            wait(c, jid)
            d = c.get(f"/api/jobs/{jid}/discovery").json()
            self.assertGreater(d["cleanup"]["pending"], 0)
            cl = c.get(f"/api/jobs/{jid}/cleanup").json()
            self.assertTrue(cl["auto"])
            ov = next(p for p in cl["proposals"] if p["kind"] == "overlap")
            nc = next(p for p in cl["proposals"] if p["kind"] == "near_closed")
            gap = next(p for p in cl["proposals"] if p["kind"] == "open_gap")
            r = c.post(f"/api/jobs/{jid}/cleanup", json={"decisions": {ov["id"]: "approved", nc["id"]: "approved", gap["id"]: "rejected"}})
            self.assertEqual(r.status_code, 200, r.text)
            wait(c, jid)
            cl2 = c.get(f"/api/jobs/{jid}/cleanup").json()
            kinds = {x["kind"] for x in cl2["reviewed"]}
            self.assertEqual(kinds, {"overlap", "near_closed"})
            dec = {p["id"]: p["decision"] for p in cl2["proposals"]}
            self.assertEqual(dec.get(gap["id"]), "rejected")
            self.assertEqual(dec.get(ov["id"]), "approved")       # applied, can still be undone
            groups = {g["key"]: g for g in c.get(f"/api/jobs/{jid}/discovery").json()["groups"]}
            self.assertTrue(any(k.startswith("BEDS::polygon") for k in groups))
        finally:
            app.state.jobs.shutdown()


if __name__ == "__main__":
    unittest.main()


class Formats(unittest.TestCase):
    """MLINE / custom objects exploded, DGN V7 read, ZIP packages unpacked into sheets."""

    def test_mline_and_dgn_and_zip(self):
        import warnings
        import zipfile
        import numpy as np
        import pyogrio.raw
        import shapely
        from fastapi.testclient import TestClient
        from geosyntra_bridge.api.app import create_app
        from geosyntra_bridge.cad.reader import read_cad
        from geosyntra_bridge.config import Settings
        warnings.filterwarnings("ignore")
        tmp = tempfile.mkdtemp()
        d = ezdxf.new("R2018")
        d.modelspace().add_mline([(0, 0), (10, 0), (10, 10)], dxfattribs={"layer": "WALL"})
        p = os.path.join(tmp, "m.dxf"); d.saveas(p)
        cad = read_cad(p)
        self.assertEqual(cad.recovered_types.get("MLINE"), 1)
        self.assertTrue(any(e.layer == "WALL" and e.kind == "line" for e in cad.entities))
        g = np.array([shapely.to_wkb(shapely.LineString([(0, 0), (10, 0), (10, 5)])),
                      shapely.to_wkb(shapely.Polygon([(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]))], dtype=object)
        dg = os.path.join(tmp, "t.dgn")
        pyogrio.raw.write(dg, g, [], [], driver="DGN", geometry_type="Unknown")
        cad = read_cad(dg)
        self.assertEqual(sorted(e.kind for e in cad.entities if e.kind != "artifact"), ["line", "polygon"])
        zp = os.path.join(tmp, "pkg.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.write(p, "project/main.dxf")
            z.write(dg, "project/xref/base.dgn")
            z.writestr("../evil.txt", "x")
        app = create_app(Settings(data_dir=os.path.join(tmp, "d"), rules_dir=os.path.join(tmp, "r")))
        c = TestClient(app)
        with open(zp, "rb") as f:
            r = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("pkg.zip", f, "application/zip")})
        self.assertIn(r.status_code, (200, 201, 202), r.text)
        st = r.json()
        names = [st["file"]["name"]] + [x["name"] for x in st.get("extra_files") or []]
        self.assertEqual(sorted(names), ["base.dgn", "main.dxf"])
        app.state.jobs.shutdown()
