"""One drawing, several templates: every feature class is delivered in its own original template geodatabase
(copied unchanged) — features from two templates give two geodatabases."""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from geosyntra_bridge.api.app import create_app  # noqa: E402
from geosyntra_bridge.config import Settings  # noqa: E402
from geosyntra_bridge.elevation.dem import write_geotiff  # noqa: E402


def wait(c, jid, statuses, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"/api/jobs/{jid}").json()
        if st["status"] in statuses:
            return st
        if st["status"] == "failed":
            raise AssertionError(st["message"])
        time.sleep(0.2)
    raise AssertionError("timeout")


class MultiTemplate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_multi_")
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_composite_schema(self):
        sc = self.c.get("/api/schemas/adm_all").json()
        self.assertEqual(sc["feature_class_count"], sum(self.c.get(f"/api/schemas/{s}").json()["feature_class_count"] for s in sc["parts"]))
        self.assertEqual(len(sc["parts"]), 7)
        lay = {l["name"]: l for l in sc["layers"]}
        self.assertEqual(lay["IRRIGATION_VALVE"]["asset"], "IR")
        self.assertEqual(lay["BENCH"]["template"], "ADM_HL_Templates_Z40_V10_20012026.gdb")

    def test_two_templates_two_geodatabases(self):
        c = self.c
        with open(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"), "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("h.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        m = c.get(f"/api/jobs/{jid}/mapping").json()
        groups = {k: v for k, v in m["layers"].items() if v.get("role") != "ignore" and v.get("target")}
        disc = {g["key"]: g for g in c.get(f"/api/jobs/{jid}/discovery").json()["groups"]}
        # send one point group of the drawing to an Irrigation class: it must land in the IR template geodatabase
        pt = next(k for k in groups if disc[k]["geometry"] == "point")
        r = c.patch(f"/api/jobs/{jid}/mapping", json={"layers": {pt: {"target": "IRRIGATION_VALVE", "role": "feature"}},
                                                       "fields": {"*": {k: {"source": "constant", "value": v} for k, v in
                                                                        {"DATA_SOURCE": "AS BUILT", "REGION": "ADM"}.items()}}})
        self.assertEqual(r.status_code, 200, r.text)
        # NOT NULL coded fields of the IR class get a value from its own domain (as an engineer would set them)
        irl = next(l for l in c.get("/api/schemas/adm_all").json()["layers"] if l["name"] == "IRRIGATION_VALVE")
        doms = {d["name"]: d for d in c.get("/api/schemas/adm_all").json()["domains"]}
        fix = {f["name"]: {"source": "constant", "value": next(iter(doms[f["domain"]]["values"]))}
               for f in irl["fields"] if not f["nullable"] and f["domain"] and doms.get(f["domain"], {}).get("values")}
        self.assertEqual(c.patch(f"/api/jobs/{jid}/mapping", json={"fields": {"IRRIGATION_VALVE": fix}}).status_code, 200)
        ext = c.get(f"/api/jobs/{jid}/discovery").json()["extent"]
        tif = os.path.join(self.tmp, "dtm.tif")
        write_geotiff(tif, np.full((int(ext[3] - ext[1] + 100), int(ext[2] - ext[0] + 100)), 3.0, dtype=np.float32),
                      (ext[0] - 50, 1.0, 0, ext[3] + 50, 0, -1.0), 32640)
        with open(tif, "rb") as f:
            self.assertEqual(c.post(f"/api/jobs/{jid}/elevation/upload", files={"file": ("d.tif", f, "x")},
                                    data={"vertical": "EPSG:5773"}).status_code, 200)
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        r = c.post(f"/api/jobs/{jid}/generate"); self.assertEqual(r.status_code, 202, r.text[:1500])
        st = wait(c, jid, ("completed", "failed"))
        res = c.get(f"/api/jobs/{jid}/results").json()
        gdbs = res["gdb"]["geodatabases"]
        self.assertEqual(sorted(g["code"] for g in gdbs), ["HL", "IR"])
        for g in gdbs:
            self.assertIn(f"_{g['code']}_", g["name"])               # each named for its own asset type
        ir = next(g for g in gdbs if g["code"] == "IR")
        self.assertEqual([k for k, v in ir["counts"].items() if v], ["IRRIGATION_VALVE"])
        hl = next(g for g in gdbs if g["code"] == "HL")
        self.assertNotIn("IRRIGATION_VALVE", hl["counts"])
        checks = {x["check"]: x for x in res["verification"]["checks"]}
        for code in ("HL", "IR"):                                    # each geodatabase identical to its own template
            self.assertTrue(checks[f"[{code}] DMT QC: schema, domains and topology identical to the template"]["passed"])
        self.assertEqual(st["status"], "completed", st["message"])
        # downloads: one zip per template, the package carries both
        for i in (1, 2):
            self.assertEqual(c.get(f"/api/jobs/{jid}/download/gdb_{i}").status_code, 200)
        import pyogrio
        out = os.path.join(self.tmp, "pkg")
        z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/jobs/{jid}/download/package").content))
        z.extractall(out)
        roots = sorted({n.split("/")[0] for n in z.namelist() if ".gdb/" in n and "PROJECT_BOUND" not in n})
        self.assertEqual(len(roots), 2)
        ir_gdb = next(os.path.join(out, r) for r in roots if "_IR_" in r)
        ir_layers = {n for n, _ in pyogrio.list_layers(ir_gdb)}
        tpl = c.get("/api/schemas/adm_ir_irrigation").json()
        self.assertTrue({l["name"] for l in tpl["layers"]} <= ir_layers)   # the whole template, structure unchanged
        self.assertEqual(pyogrio.read_info(ir_gdb, layer="IRRIGATION_VALVE")["features"], ir["counts"]["IRRIGATION_VALVE"])


if __name__ == "__main__":
    unittest.main()


class Automatic(unittest.TestCase):
    """Upload -> all templates -> confident layers routed and accepted, weak ones set aside -> values the drawing
    lacks asked once per field -> texts mapped to domain codes -> geodatabases; mapping & delivery table."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_auto_")
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_asset_category(self):
        from geosyntra_bridge.mapping.asset_category import categorize, drawing_category
        self.assertEqual(categorize("ISL-ALBT-2025-P02-SS-GC-Pennisetum Rubrum")[0], "SL")
        self.assertEqual(categorize("E-IRR-MAIN-110")[0], "IR")
        self.assertEqual(categorize("HS-PAVERS HATCH")[0], "HL")
        self.assertEqual(categorize("STL-POLE")[0], "STL")
        self.assertEqual(drawing_category("ISL-ALBT-2025-Softscape - 04.dwg"), "SL")

    def test_required_values_and_value_maps(self):
        c = self.c
        with open(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"), "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("h.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        m = c.post(f"/api/jobs/{jid}/mapping/accept-suggestions", params={"min_score": 0.45}).json()
        self.assertTrue(all(v.get("role") == "ignore" or (v.get("score") or 0) >= 0.45 for v in m["layers"].values()))
        pt = next(k for k, v in m["layers"].items() if v.get("target") and c.get(f"/api/jobs/{jid}/discovery").json()
                  and next(g for g in c.get(f"/api/jobs/{jid}/discovery").json()["groups"] if g["key"] == k)["geometry"] == "point")
        c.patch(f"/api/jobs/{jid}/mapping", json={"layers": {pt: {"target": "IRRIGATION_VALVE"}},
                                                   "fields": {"IRRIGATION_VALVE": {"MATERIAL_TYPE": {"source": "constant", "value": "Unknown text"}}}})
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        rv = c.get(f"/api/jobs/{jid}/required-values").json()
        miss = {x["field"]: x for x in rv["missing"]}
        # NOT NULL coded fields are filled automatically (reported for review) — nothing typed is blocking
        self.assertFalse([f for f, x in miss.items() if not x["elevation"]])
        res = c.get(f"/api/jobs/{jid}/results").json()
        self.assertTrue(any(i["code"] == "not_null_placeholder" and i["field"] == "VALVE_OPERATION" for i in res["issues"]))
        # answer once for every class that has the field, then validate again: nothing missing anymore
        self.assertTrue(miss["ZCOORD"]["elevation"])                  # levels: DEM, never a typed constant
        answers = {f: {"source": "constant", "value": next(iter(x["choices"]))} for f, x in miss.items() if x["choices"]}
        self.assertEqual(c.patch(f"/api/jobs/{jid}/mapping", json={"fields": {"*": answers}}).status_code, 200)
        un = [x for x in rv["unmapped_values"] if x["field"] == "MATERIAL_TYPE"]
        self.assertTrue("Unknown text".startswith(un[0]["value"]))     # as validated (truncated to the field length)
        code = next(iter(un[0]["choices"]))
        c.patch(f"/api/jobs/{jid}/mapping", json={"value_maps": {"MATERIAL_TYPE": {un[0]["value"]: code}}})
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        rv2 = c.get(f"/api/jobs/{jid}/required-values").json()
        self.assertFalse({x["field"] for x in rv2["missing"]} & set(answers))
        self.assertFalse([x for x in rv2["unmapped_values"] if x["field"] == "MATERIAL_TYPE"])
        rows = c.get(f"/api/jobs/{jid}/delivery").json()
        ir = next(r for r in rows if r["feature_class"] == "IRRIGATION_VALVE")
        self.assertEqual(ir["geodatabase"], "ADM_IR_Templates_Z40_V10_20012026.gdb")
        self.assertEqual(ir["fc_geometry"], "point")


class ProjectMemory(unittest.TestCase):
    """A value typed once is reused for the next drawings of the same user (and can be forgotten)."""

    def test_memory_reused_on_next_drawing(self):
        tmp = tempfile.mkdtemp(prefix="gsb_mem_")
        app = create_app(Settings(data_dir=os.path.join(tmp, "d"), rules_dir=os.path.join(tmp, "r")))
        c = TestClient(app)
        try:
            def new_job():
                with open(os.path.join(ROOT, "sample_data", "sample_soft_landscaping.dxf"), "rb") as f:
                    jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("s.dxf", f, "x")}).json()["job_id"]
                wait(c, jid, ("ready",))
                return jid, c.post(f"/api/jobs/{jid}/mapping/accept-suggestions", params={"min_score": 0.45}).json()
            j1, m1 = new_job()
            fc = next(v["target"] for v in m1["layers"].values() if v.get("target"))
            sc = c.get("/api/schemas/adm_all").json()
            fields = {f["name"]: f for f in next(l for l in sc["layers"] if l["name"] == fc)["fields"]}
            self.assertIn("DATA_SOURCE", fields)
            dom = next(d for d in sc["domains"] if d["name"] == fields["DATA_SOURCE"]["domain"])
            val = next(iter(dom["values"]))
            self.assertEqual(c.patch(f"/api/jobs/{j1}/mapping", json={"fields": {"*": {"DATA_SOURCE": {"source": "constant", "value": val}}},
                                                                       "value_maps": {"SCIENTIFIC_NAME": {"Wax palm": "X1"}}}).status_code, 200)
            mem = c.get("/api/memory").json()
            self.assertEqual(mem["fields"]["DATA_SOURCE"]["value"], val)
            self.assertEqual(mem["value_maps"]["SCIENTIFIC_NAME"]["WAX PALM"], "X1")
            j2, m2 = new_job()
            spec = m2["fields"][fc]["DATA_SOURCE"]
            self.assertEqual((spec["source"], spec["value"], spec.get("memory")), ("constant", val, True))
            self.assertEqual(m2["value_maps"]["SCIENTIFIC_NAME"]["WAX PALM"], "X1")
            self.assertIn("project memory", c.get(f"/api/jobs/{j2}/log").text)
            c.delete("/api/memory/DATA_SOURCE")
            self.assertNotIn("DATA_SOURCE", c.get("/api/memory").json()["fields"])
        finally:
            app.state.jobs.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)


class MultiSheet(unittest.TestCase):
    """Two sheets of one project in one conversion: layers of both are mapped, decisions are kept."""

    def test_add_second_drawing(self):
        tmp = tempfile.mkdtemp(prefix="gsb_sheets_")
        app = create_app(Settings(data_dir=os.path.join(tmp, "d"), rules_dir=os.path.join(tmp, "r")))
        c = TestClient(app)
        try:
            with open(os.path.join(ROOT, "sample_data", "sample_soft_landscaping.dxf"), "rb") as f:
                jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("soft.dxf", f, "x")}).json()["job_id"]
            wait(c, jid, ("ready",))
            m1 = c.post(f"/api/jobs/{jid}/mapping/accept-suggestions", params={"min_score": 0.45}).json()
            n1 = len(c.get(f"/api/jobs/{jid}/discovery").json()["groups"])
            kept = next(k for k, v in m1["layers"].items() if v.get("target"))
            with open(os.path.join(ROOT, "sample_data", "sample_street_lighting.dxf"), "rb") as f:
                r = c.post(f"/api/jobs/{jid}/drawings", files={"file": ("lights.dxf", f, "x")})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["extra_files"][0]["name"], "lights.dxf")
            wait(c, jid, ("ready",))
            d = c.get(f"/api/jobs/{jid}/discovery").json()
            self.assertGreater(len(d["groups"]), n1)
            m2 = c.get(f"/api/jobs/{jid}/mapping").json()
            self.assertEqual(m2["layers"][kept]["status"], "USER")          # earlier decision kept
            m2 = c.post(f"/api/jobs/{jid}/mapping/accept-suggestions", params={"min_score": 0.45}).json()
            tpls = {next(l for l in c.get("/api/schemas/adm_all").json()["layers"] if l["name"] == v["target"])["asset"]
                    for v in m2["layers"].values() if v.get("target") and v.get("role") != "ignore"}
            self.assertTrue({"SL", "STL"} <= tpls, tpls)
            with open(os.path.join(ROOT, "sample_data", "sample_street_lighting.dxf"), "rb") as f:
                self.assertEqual(c.post(f"/api/jobs/{jid}/drawings", files={"file": ("lights.dxf", f, "x")}).status_code, 400)
        finally:
            app.state.jobs.shutdown()
            shutil.rmtree(tmp, ignore_errors=True)
