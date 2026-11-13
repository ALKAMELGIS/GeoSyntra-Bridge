"""Topology step: error inspector with shapes, exceptions, geometry edits (snapped back to unmoved vertices),
deletions — applied by the engine on the next validation."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from geosyntra_bridge.api.app import create_app  # noqa: E402
from geosyntra_bridge.config import Settings  # noqa: E402
from tests.test_multi_template import wait  # noqa: E402


class TopologyEdit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_topo_")
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_edit_cycle(self):
        c = self.c
        with open(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"), "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("h.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        topo = c.get(f"/api/jobs/{jid}/topology").json()
        self.assertIn("errors", topo)
        pv = c.get(f"/api/jobs/{jid}/preview").json()
        feats = [x for x in pv["features"] if x["properties"].get("fid") is not None]
        line = next(x for x in feats if x["geometry"]["type"] in ("LineString", "MultiLineString"))
        pt = next(x for x in feats if x["geometry"]["type"] == "Point")
        g = line["geometry"]
        coords = g["coordinates"] if g["type"] == "LineString" else g["coordinates"][0]
        before = [list(p) for p in coords]
        moved = [list(p) for p in coords]
        dx = 2.0 if not pv["crs_is_geographic"] else 2e-5
        moved[-1][0] += dx
        newg = {"type": "LineString", "coordinates": [p[:2] for p in moved]}
        # a polygon/line type mismatch is refused
        bad = c.post(f"/api/jobs/{jid}/geometry", json={"edits": [{"fid": line["properties"]["fid"], "geometry": {"type": "Point", "coordinates": moved[0][:2]}}]})
        self.assertEqual(bad.status_code, 422, bad.text)
        r = c.post(f"/api/jobs/{jid}/geometry", json={"edits": [{"fid": line["properties"]["fid"], "geometry": newg},
                                                               {"fid": pt["properties"]["fid"], "delete": True}]})
        self.assertEqual(r.status_code, 200, r.text)
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        pv2 = c.get(f"/api/jobs/{jid}/preview").json()
        l2 = next(x for x in pv2["features"] if x["properties"].get("fid") == line["properties"]["fid"])
        g2 = l2["geometry"]
        c2 = g2["coordinates"] if g2["type"] == "LineString" else g2["coordinates"][0]
        self.assertAlmostEqual(c2[0][0], before[0][0], places=9)        # unmoved vertex: exactly where it was
        self.assertAlmostEqual(c2[-1][0] - before[-1][0], dx, places=6)  # moved vertex: moved
        p2 = next(x for x in pv2["features"] if x["properties"].get("fid") == pt["properties"]["fid"])
        self.assertEqual(p2["properties"]["status"], "skipped")
        d = c.get(f"/api/jobs/{jid}/features/{line['properties']['fid']}").json()
        self.assertIn("geometry edited in the Topology step", d["remarks"])
        # exceptions
        topo = c.get(f"/api/jobs/{jid}/topology").json()
        if topo["errors"]:
            k = topo["errors"][0]["key"]
            self.assertTrue(c.post(f"/api/jobs/{jid}/topology/exception", json={"key": k, "exception": True}).json()["exception"])
            c.post(f"/api/jobs/{jid}/validate")
            wait(c, jid, ("validated",))
            t2 = c.get(f"/api/jobs/{jid}/topology").json()
            self.assertTrue(next(e for e in t2["errors"] if e["key"] == k)["exception"])
        # restore
        c.post(f"/api/jobs/{jid}/geometry/{pt['properties']['fid']}/restore")
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        pv3 = c.get(f"/api/jobs/{jid}/preview").json()
        self.assertNotEqual(next(x for x in pv3["features"] if x["properties"].get("fid") == pt["properties"]["fid"])["properties"]["status"], "skipped")


class AutoFix(unittest.TestCase):
    """One-click corrections: a dangling kerb end is snapped onto the nearby kerb, an overlap is clipped; a lone line
    (nothing within reach) and a large enclosed gap are left to the engineer."""

    @classmethod
    def setUpClass(cls):
        import ezdxf
        cls.tmp = tempfile.mkdtemp(prefix="gsb_fix_")
        d = ezdxf.readfile(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"))
        msp = d.modelspace()
        # a second kerb starting 0.8 m short of the first one (dangle within reach)
        msp.add_lwpolyline([(245040.0, 2701895.8), (245040.0, 2701930.0)], dxfattribs={"layer": "HL-KERB"})
        cls.dxf = os.path.join(cls.tmp, "fix.dxf")
        d.saveas(cls.dxf)
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_autofix(self):
        c = self.c
        with open(self.dxf, "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("fix.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        c.patch(f"/api/jobs/{jid}/mapping", json={"elevation": {"missing_policy": "nodata_value", "nodata_value": -9999}})
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        errs = c.get(f"/api/jobs/{jid}/topology").json()["errors"]
        snap = [e for e in errs if e["code"] == "line_no_dangles" and e["action"] == "Snap"]
        self.assertTrue(snap, [(e["rule"], e["action"], e["no_fix"]) for e in errs])
        gap = next(e for e in errs if e["code"] == "area_no_gaps")
        r = c.post(f"/api/jobs/{jid}/topology/autofix", json={"keys": [e["key"] for e in errs if e["action"]], "bulk": True}).json()
        self.assertTrue(any(x["note"].startswith("end moved") for x in r["fixed"]), r)
        self.assertTrue(any(s["key"] == gap["key"] and "opening" in s["reason"] for s in r["skipped"]), r)
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        after = c.get(f"/api/jobs/{jid}/topology").json()["errors"]
        fixed_fids = {x["fid"] for x in r["fixed"]}
        self.assertFalse([e for e in after if e["code"] == "line_no_dangles" and e["fid"] in fixed_fids and e["action"] == "Snap"])
        # the gap filled on its own request
        r2 = c.post(f"/api/jobs/{jid}/topology/autofix", json={"keys": [gap["key"]]}).json()
        self.assertEqual(len(r2["fixed"]), 1, r2)
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        self.assertFalse([e for e in c.get(f"/api/jobs/{jid}/topology").json()["errors"] if e["code"] == "area_no_gaps"])


class DrawAndSplit(unittest.TestCase):
    """Features drawn on the map are written like CAD ones; a split keeps the CAD source (and so the attributes)
    on both parts; restoring an added feature removes it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_draw_")
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_draw_split_restore(self):
        c = self.c
        with open(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"), "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("d.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        c.patch(f"/api/jobs/{jid}/mapping", json={"elevation": {"missing_policy": "nodata_value", "nodata_value": -9999}})
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        ov = c.get(f"/api/jobs/{jid}/assign").json()
        n0 = len(ov["features"])
        pv = c.get(f"/api/jobs/{jid}/preview").json()
        geo = pv.get("crs_is_geographic")
        kerb = next(r for r in ov["features"] if r["target"] == "HL_CURBSTONE")
        shape = next(f["geometry"] for f in pv["features"] if f["properties"].get("fid") == kerb["fid"])
        cs = shape["coordinates"][0] if shape["type"].startswith("Multi") else shape["coordinates"]
        (x0, y0), (x1, y1) = cs[0][:2], cs[-1][:2]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        d = 0.0002 if geo else 20
        cutter = {"type": "LineString", "coordinates": [[mx, my - d], [mx, my + d]]} if abs(x1 - x0) > abs(y1 - y0) else \
            {"type": "LineString", "coordinates": [[mx - d, my], [mx + d, my]]}
        r = c.post(f"/api/jobs/{jid}/geometry/{kerb['fid']}/split", json={"cutter": cutter})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["parts"], 2)
        # a new bollard drawn next to the first one
        bol = next(r for r in ov["features"] if ov["classes"][r["target"]]["geometry"].lower() == "point")
        p = next(f["geometry"] for f in pv["features"] if f["properties"].get("fid") == bol["fid"])["coordinates"]
        p = p[0] if isinstance(p[0], list) else p
        r = c.post(f"/api/jobs/{jid}/geometry", json={"edits": [{"add": bol["target"], "geometry": {"type": "Point", "coordinates": [p[0] + d / 10, p[1]]}}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["added"], 1)
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        ov2 = c.get(f"/api/jobs/{jid}/assign").json()
        self.assertEqual(len(ov2["features"]), n0 + 2)
        parts = [r for r in ov2["features"] if r["handle"].startswith(kerb["handle"])]
        self.assertEqual(len(parts), 2)
        drawn = next(r for r in ov2["features"] if r["handle"].startswith("new-"))
        # restoring the drawn feature removes it
        c.post(f"/api/jobs/{jid}/geometry/{drawn['fid']}/restore")
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        self.assertEqual(len(c.get(f"/api/jobs/{jid}/assign").json()["features"]), n0 + 1)
        # the delivery carries the new part
        c.post(f"/api/jobs/{jid}/generate")
        st = wait(c, jid, ("completed", "failed"), 300)
        self.assertEqual(st["status"], "completed", st.get("message"))



class MultiEdit(unittest.TestCase):
    """Attributes pane: one value for several features (layer / multi-selection) and the values already in use."""

    def test_many_and_unique(self):
        tmp = tempfile.mkdtemp(prefix="gsb_many_")
        app = create_app(Settings(data_dir=os.path.join(tmp, "d"), rules_dir=os.path.join(tmp, "r")))
        c = TestClient(app)
        try:
            with open(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"), "rb") as f:
                jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("m.dxf", f, "x")}).json()["job_id"]
            wait(c, jid, ("ready",))
            c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
            c.patch(f"/api/jobs/{jid}/mapping", json={"elevation": {"missing_policy": "nodata_value", "nodata_value": -9999}})
            c.post(f"/api/jobs/{jid}/validate")
            wait(c, jid, ("validated",))
            ov = c.get(f"/api/jobs/{jid}/assign").json()
            cls = max(ov["classes"], key=lambda k: sum(1 for r in ov["features"] if r["target"] == k))
            fids = [r["fid"] for r in ov["features"] if r["target"] == cls]
            fld = next(x for x in ov["classes"][cls]["fields"] if not x["read_only"] and x["type"] in ("TEXT", "STRING") and not x["domain"])
            r = c.post(f"/api/jobs/{jid}/assign-many", json={"fids": fids, "fields": {fld["name"]: "BATCH-1"}})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["saved"], len(fids))
            u = c.get(f"/api/jobs/{jid}/assign-values", params={"target": cls, "field": fld["name"]}).json()
            self.assertEqual(u["values"][0], {"value": "BATCH-1", "count": len(fids)})
            d = c.get(f"/api/jobs/{jid}/assign/{fids[-1]}").json()
            self.assertEqual(d["attributes"].get(fld["name"]), "BATCH-1")
        finally:
            app.state.jobs.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)



class AutoFixRun(unittest.TestCase):
    """The full Auto-Fix: fixes, verifies, keeps a ledger, can be undone; preview changes nothing; Merge / Explode."""

    @classmethod
    def setUpClass(cls):
        import ezdxf
        cls.tmp = tempfile.mkdtemp(prefix="gsb_run_")
        d = ezdxf.readfile(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"))
        msp = d.modelspace()
        msp.add_lwpolyline([(245040.0, 2701895.8), (245040.0, 2701930.0)], dxfattribs={"layer": "HL-KERB"})
        cls.dxf = os.path.join(cls.tmp, "run.dxf")
        d.saveas(cls.dxf)
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _job(self):
        c = self.c
        with open(self.dxf, "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("run.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        c.patch(f"/api/jobs/{jid}/mapping", json={"elevation": {"missing_policy": "nodata_value", "nodata_value": -9999}})
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        return jid

    def test_run_undo_preview(self):
        c = self.c
        jid = self._job()
        errs = c.get(f"/api/jobs/{jid}/topology").json()["errors"]
        self.assertTrue(all(e["guide"]["why"] for e in errs))
        snap = next(e for e in errs if e["action"] == "Snap")
        before = c.get(f"/api/jobs/{jid}/assign").json()
        pv = c.get(f"/api/jobs/{jid}/autofix/preview", params={"key": snap["key"]}).json()
        self.assertEqual(pv["action"], "Snap")
        self.assertIn(pv["geometry"]["type"], ("LineString", "MultiLineString"))
        self.assertEqual(c.get(f"/api/jobs/{jid}/assign").json()["counts"], before["counts"])       # preview changed nothing
        c.post(f"/api/jobs/{jid}/autofix/run", json={})
        wait(c, jid, ("validated",), 300)
        rep = c.get(f"/api/jobs/{jid}/autofix/report").json()
        self.assertGreaterEqual(rep["fixed_n"], 1, rep)
        self.assertLess(rep["after"], rep["before"], rep)
        self.assertTrue(rep["undo"])
        # the big gap was left alone and is remembered, so a second run does not touch it again
        self.assertTrue(any("opening" in k for k in rep["skipped"]), rep["skipped"])
        c.post(f"/api/jobs/{jid}/autofix/run", json={})
        wait(c, jid, ("validated",), 300)
        self.assertEqual(c.get(f"/api/jobs/{jid}/autofix/report").json()["fixed_n"], 0)
        # undo the first run: the dangle is back
        r = c.post(f"/api/jobs/{jid}/autofix/undo")
        self.assertEqual(r.status_code, 200, r.text)
        wait(c, jid, ("validated",), 300)
        after = c.get(f"/api/jobs/{jid}/topology").json()["errors"]
        self.assertTrue(any(e["key"] == snap["key"] for e in after))

    def test_merge_explode(self):
        c = self.c
        jid = self._job()
        ov = c.get(f"/api/jobs/{jid}/assign").json()
        kerbs = [r["fid"] for r in ov["features"] if r["target"] == "HL_CURBSTONE"]
        self.assertEqual(len(kerbs), 2)
        r = c.post(f"/api/jobs/{jid}/geometry/merge", json={"fids": kerbs})
        self.assertEqual(r.status_code, 200, r.text)
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        ov2 = c.get(f"/api/jobs/{jid}/assign").json()
        left = [x for x in ov2["features"] if x["target"] == "HL_CURBSTONE" and x["state"] != "skipped"]
        merged = r.json()
        if merged["multipart"]:
            e = c.post(f"/api/jobs/{jid}/geometry/{merged['kept']}/explode")
            self.assertEqual(e.status_code, 200, e.text)
        self.assertTrue(left)
        bad = c.post(f"/api/jobs/{jid}/geometry/merge", json={"fids": [kerbs[0]]})
        self.assertEqual(bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()
