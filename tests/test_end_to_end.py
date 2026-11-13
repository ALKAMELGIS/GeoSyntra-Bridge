"""
End-to-end tests through the real HTTP API: upload -> discovery -> mapping edit ->
validation -> File Geodatabase generation -> verification -> downloads, for all
seven schemas, plus focused unit tests.

    python -m pytest tests -q          (or)    python -m unittest discover tests -v
"""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from geosyntra_bridge.config import Settings  # noqa: E402
from geosyntra_bridge.api.app import create_app  # noqa: E402

SAMPLES = {
    "wastewater_network": "sample_wastewater.dxf",
    "stormwater_network": "sample_stormwater.dxf",
    "water_distribution": "sample_water.dxf",
    "irrigation_network": "sample_irrigation.dxf",
    "street_lighting": "sample_street_lighting.dxf",
    "adm_sl_softlandscaping": "sample_soft_landscaping.dxf",
    "adm_hl_hardlandscaping": "sample_hard_landscaping.dxf",
}


REQUIRED_CONSTANTS = {"adm_sl_softlandscaping": {
    k: {"source": "constant", "value": v} for k, v in
    {"DATA_SOURCE": "AS BUILT", "REGION": "ADM", "IR_WATER_POINT": "TSE Water", "PLANTTYPE": "Ornamental",
     "GROUNDCOVERTYPE": "Other", "SHRUBTYPE": "Other", "HEDGETYPE": "Other", "PARK_TYPE": "Other"}.items()},
    "adm_hl_hardlandscaping": {k: {"source": "constant", "value": v} for k, v in
                               {"DATA_SOURCE": "AS BUILT", "REGION": "ADM"}.items()}}


def wait(c, job_id, statuses, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"/api/jobs/{job_id}").json()
        if st["status"] in statuses:
            return st
        if st["status"] == "failed":
            raise AssertionError(f"job failed: {st['message']}\n{c.get(f'/api/jobs/{job_id}/log').text}")
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting for {statuses}: {st}")


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_test_")
        s = Settings(data_dir=os.path.join(cls.tmp, "data"), rules_dir=os.path.join(cls.tmp, "rules"),
                     extra_schema_dirs=[os.path.join(ROOT, "schemas", "examples")])
        cls.app = create_app(s)
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _dem_for(self, extent):
        """Synthetic survey DTM (tilted plane, 2 m) covering a drawing extent in UTM 40N."""
        import numpy as np
        from geosyntra_bridge.elevation.dem import write_geotiff
        x0, y1, res = extent[0] - 100, extent[3] + 100, 2.0
        w, h = int((extent[2] - extent[0] + 200) / res) + 1, int((extent[3] - extent[1] + 200) / res) + 1
        X, Y = np.meshgrid(x0 + (np.arange(w) + 0.5) * res, y1 - (np.arange(h) + 0.5) * res)
        path = os.path.join(self.tmp, f"dtm_{int(extent[0])}_{int(extent[1])}.tif")
        write_geotiff(path, (3 + 0.001 * (X - x0) + 0.002 * (Y - extent[1])).astype("float32"), (x0, res, 0, y1, 0, -res), 32640)
        return path

    def test_capabilities_and_schemas(self):
        cap = self.c.get("/api/system/capabilities").json()
        self.assertTrue(cap["filegdb"], cap)
        schemas = self.c.get("/api/schemas").json()
        approved = sorted(sc["schema_id"] for sc in schemas if sc["status"] == "approved")
        self.assertEqual(approved, ["adm_all", "adm_hl_hardlandscaping", "adm_ir_irrigation", "adm_itc", "adm_rd_roads",
                                    "adm_sl_softlandscaping", "adm_stl_streetlighting", "adm_sw_stormwater"])
        for sc in schemas:
            d = self.c.get(f"/api/schemas/{sc['schema_id']}").json()
            self.assertEqual(d["lint"], [], sc["schema_id"])
            self.assertGreater(len(d["layers"]), 0)

    def test_rejects_bad_uploads(self):
        r = self.c.post("/api/jobs", data={"schema_id": "water_distribution"},
                        files={"file": ("x.dxf", io.BytesIO(b"hello world"), "application/octet-stream")})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/jobs", data={"schema_id": "water_distribution"},
                        files={"file": ("x.exe", io.BytesIO(b"MZ..."), "application/octet-stream")})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/jobs", data={"schema_id": "nope"},
                        files={"file": ("x.dxf", io.BytesIO(b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"), "x")})
        self.assertEqual(r.status_code, 400)

    def test_corrupted_dxf_reported(self):
        r = self.c.post("/api/jobs", data={"schema_id": "water_distribution"},
                        files={"file": ("bad.dxf", io.BytesIO(b"0\nSECTION\n2\nHEADER\n9\n$ACADVER\n1\nAC1032\n0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n"), "x")})
        self.assertEqual(r.status_code, 201)
        jid = r.json()["job_id"]
        for _ in range(100):
            st = self.c.get(f"/api/jobs/{jid}").json()
            if st["status"] in ("failed", "ready"):
                break
            time.sleep(0.1)
        # either recovered (ready) or explicitly failed with a message — never silent
        self.assertIn(st["status"], ("failed", "ready"))
        if st["status"] == "failed":
            self.assertTrue(st["message"])

    def test_all_schemas_end_to_end(self):
        for schema_id, sample in SAMPLES.items():
            with self.subTest(schema=schema_id):
                path = os.path.join(ROOT, "sample_data", sample)
                original = open(path, "rb").read()
                with open(path, "rb") as f:
                    r = self.c.post("/api/jobs", data={"schema_id": schema_id}, files={"file": (sample, f, "application/dxf")})
                self.assertEqual(r.status_code, 201, r.text)
                jid = r.json()["job_id"]
                wait(self.c, jid, ("ready",))
                disc = self.c.get(f"/api/jobs/{jid}/discovery").json()
                self.assertGreater(disc["layer_count"], 0)
                m = self.c.get(f"/api/jobs/{jid}/mapping").json()
                self.assertEqual(m["source_crs"], 32640)
                # user accepts every suggestion (targets + proposed ignores) in one action
                self.assertEqual(self.c.post(f"/api/jobs/{jid}/mapping/accept-suggestions").status_code, 200)
                if schema_id in REQUIRED_CONSTANTS:   # NOT NULL fields whose domain has no neutral code
                    fields = {"*": REQUIRED_CONSTANTS[schema_id]}
                    if schema_id == "adm_sl_softlandscaping":
                        fields["NATIVETREE"] = {"UNITTYPE": {"source": "constant", "value": "Ghaf"}}
                        fields["PALMETREE"] = {"SCIENTIFIC_NAME": {"source": "constant", "value": "Phoenix dactylifera"}}
                    r = self.c.patch(f"/api/jobs/{jid}/mapping", json={"fields": fields})
                    self.assertEqual(r.status_code, 200, r.text)
                if schema_id.startswith("adm_"):      # Z-enabled templates (ZCOORD NOT NULL): elevation from a DEM
                    dem = self._dem_for(disc["extent"])
                    with open(dem, "rb") as f:
                        r = self.c.post(f"/api/jobs/{jid}/elevation/upload", files={"file": ("dtm.tif", f, "x")},
                                        data={"vertical": "EPSG:5773"})
                    self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(self.c.post(f"/api/jobs/{jid}/validate").status_code, 202)
                wait(self.c, jid, ("validated",))
                res = self.c.get(f"/api/jobs/{jid}/results").json()
                self.assertGreater(res["summary"]["features_imported"], 0)
                self.assertEqual(res["summary"]["uncertain_mappings"], 0)
                prev = self.c.get(f"/api/jobs/{jid}/preview").json()
                self.assertTrue(prev["crs_is_geographic"])
                lon, lat = _first_coord(prev["features"][0]["geometry"])
                self.assertTrue(54 < lon < 55 and 24 < lat < 25, (lon, lat))
                fid = res["features"][0]["fid"]
                det = self.c.get(f"/api/jobs/{jid}/features/{fid}").json()
                self.assertIn("source", det)
                comp = self.c.get(f"/api/jobs/{jid}/completeness").json()
                self.assertTrue(comp["complete"], comp)
                self.assertEqual(self.c.post(f"/api/jobs/{jid}/generate").status_code, 202)
                st = wait(self.c, jid, ("completed",))
                res = self.c.get(f"/api/jobs/{jid}/results").json()
                self.assertTrue(res["verification"]["passed"], res["verification"])
                if schema_id.startswith("adm_"):       # built on the approved template, checked like DMT does
                    qc = res["dmt_qc"]
                    self.assertEqual(qc["by_check"]["Schema identical to template"], 0, qc["errors"][:3])
                    self.assertEqual(qc["by_check"]["Topology present"], 0, qc["errors"][:3])
                    self.assertEqual(self.c.get(f"/api/jobs/{jid}/download/qc_csv").status_code, 200)
                for kind in ("gdb_zip", "xlsx", "pdf"):
                    d = self.c.get(f"/api/jobs/{jid}/download/{kind}")
                    self.assertEqual(d.status_code, 200, kind)
                    self.assertGreater(len(d.content), 500)
                z = zipfile.ZipFile(io.BytesIO(self.c.get(f"/api/jobs/{jid}/download/gdb_zip").content))
                self.assertTrue(any(n.endswith("/gdb") for n in z.namelist()))
                self.assertEqual(open(path, "rb").read(), original, "source drawing must never change")

    def test_completeness_gate(self):
        path = os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf")
        with open(path, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "adm_hl_hardlandscaping"},
                              files={"file": ("h.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        m = self.c.get(f"/api/jobs/{jid}/mapping").json()["layers"]
        self.assertEqual(m["DIM::line"]["status"], "EXCLUDED")    # sheet furniture never transferred
        # an asset group left without a target class must block generation
        self.c.patch(f"/api/jobs/{jid}/mapping", json={"layers": {"HL-KERB::line": {"target": None, "role": "feature"}}})
        self.c.post(f"/api/jobs/{jid}/validate")
        wait(self.c, jid, ("validated",))
        r = self.c.post(f"/api/jobs/{jid}/generate")
        self.assertEqual(r.status_code, 409)          # undecided layers -> nothing is generated
        self.assertIn("need a decision", r.json()["detail"])

    def test_profiles_and_learning(self):
        path = os.path.join(ROOT, "sample_data", "sample_wastewater.dxf")
        with open(path, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "wastewater_network"},
                              files={"file": ("a.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        self.c.patch(f"/api/jobs/{jid}/mapping", json={"layers": {"L4-SEW::line": {"target": "ForceMain"}}})
        r = self.c.post(f"/api/jobs/{jid}/profiles", json={"name": "Consultant A"}).json()
        self.assertEqual(r["profile"], "Consultant A")
        self.assertEqual(len(self.c.get("/api/schemas/wastewater_network/profiles").json()), 1)
        with open(path, "rb") as f:
            jid2 = self.c.post("/api/jobs", data={"schema_id": "wastewater_network"},
                               files={"file": ("b.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid2, ("ready",))
        m = self.c.get(f"/api/jobs/{jid2}/mapping").json()
        self.assertEqual(m["layers"]["L4-SEW::line"]["target"], "ForceMain")  # learned rule
        self.assertIn("previously approved mapping rule", " ".join(m["layers"]["L4-SEW::line"]["evidence"]))
        m = self.c.post(f"/api/jobs/{jid2}/profiles/Consultant_A/apply").json()
        self.assertEqual(m["layers"]["L4-SEW::line"]["status"], "PROFILE")

    def test_issue_detection_wastewater(self):
        path = os.path.join(ROOT, "sample_data", "sample_wastewater.dxf")
        with open(path, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "wastewater_network"},
                              files={"file": ("w.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        self.c.post(f"/api/jobs/{jid}/validate")
        wait(self.c, jid, ("validated",))
        res = self.c.get(f"/api/jobs/{jid}/results").json()
        codes = {i["code"] for i in res["issues"]}
        for expected in ("cad_cleaning", "label_unmatched", "orphan_junction", "disconnected_subnetwork"):
            self.assertIn(expected, codes)
        cleaning = " ".join(i["message"] for i in res["issues"] if i["code"] == "cad_cleaning")
        self.assertIn("zero-length", cleaning.lower())
        self.assertIn("duplicate", cleaning.lower())
        pipes = {f["fid"]: f for f in res["features"] if f["target"] == "GravitySewer"}
        self.assertTrue(any("reversed" in " ".join(f["remarks"]) for f in pipes.values()))

    def test_engineer_workflow_irrigation(self):
        """Base map / sheet excluded, CAD cleaned, one layer split by drawing style (main vs lateral),
        symbols told apart by position on the network (valve vs controller)."""
        path = os.path.join(ROOT, "sample_data", "sample_irrigation_mixed.dxf")
        with open(path, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "irrigation_network"},
                              files={"file": ("m.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        m = self.c.get(f"/api/jobs/{jid}/mapping").json()["layers"]
        for g in ("EX-ROAD::line", "PLOT-BOUNDARY::polygon", "BUILDING::polygon", "A-ANNO-TTLB::polygon"):
            self.assertEqual(m[g]["status"], "EXCLUDED", g)
        got = {g: (v["status"], v["target"]) for g, v in m.items() if g.startswith("IRR-")}
        self.assertEqual(got["IRR-PIPE::line@Continuous 0.50mm c7"], ("AUTO", "IrrigationMainline"))
        self.assertEqual(got["IRR-PIPE::line@Dashed 0.18mm c7"], ("AUTO", "IrrigationLateral"))
        self.assertEqual(got["IRR-FITTINGS::point@block SYMA"], ("AUTO", "IrrigationValve"))
        self.assertEqual(got["IRR-FITTINGS::point@block SYMB"], ("AUTO", "IrrigationController"))
        disc = self.c.get(f"/api/jobs/{jid}/discovery").json()
        actions = " ".join(c["action"] for c in disc["cleaning"])
        self.assertIn("PEDIT", actions)        # 4 main fragments joined
        self.assertIn("OVERKILL", actions)     # duplicated lateral
        self.assertIn("zero-length", actions)   # zero-length segment
        self.c.post(f"/api/jobs/{jid}/validate")
        wait(self.c, jid, ("validated",))
        res = self.c.get(f"/api/jobs/{jid}/results").json()
        by = res["summary"]["features_by_class"]
        self.assertEqual(by.get("IrrigationMainline"), 1)
        self.assertEqual(by.get("IrrigationLateral"), 4)
        main = next(f for f in res["features"] if f["target"] == "IrrigationMainline")
        detail = self.c.get(f"/api/jobs/{jid}/features/{main['fid']}").json()
        self.assertEqual(detail["attributes"].get("DIAMETER"), 110)

    def test_spatial_join_reference(self):
        """Cryptic layers, no key, no labels: an approved GIS of the same area (WGS 84 GeoPackage) decides."""
        path = os.path.join(ROOT, "sample_data", "sample_irrigation_cryptic.dxf")
        with open(path, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "irrigation_network"},
                              files={"file": ("c.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        m = self.c.get(f"/api/jobs/{jid}/mapping").json()["layers"]
        self.assertTrue(all(v["status"] == "UNRESOLVED" for v in m.values()), m)
        with open(os.path.join(ROOT, "sample_data", "sample_irrigation_reference.gpkg"), "rb") as f:
            r = self.c.post(f"/api/jobs/{jid}/reference", files={"file": ("ref.gpkg", f, "x")})
        self.assertEqual(r.status_code, 200, r.text)
        m = r.json()
        self.assertTrue(m["reference"]["overlap"])
        got = {g: (v["status"], v["target"]) for g, v in m["layers"].items()}
        self.assertEqual(got, {"L-10::line": ("AUTO", "IrrigationMainline"), "L-11::point": ("AUTO", "IrrigationValve"),
                               "L-12::line": ("AUTO", "IrrigationLateral")})
        self.c.post(f"/api/jobs/{jid}/validate")
        wait(self.c, jid, ("validated",))
        res = self.c.get(f"/api/jobs/{jid}/results").json()
        valves = [f for f in res["features"] if f["target"] == "IrrigationValve"]
        self.assertTrue(all("spatial join" in " ".join(f["remarks"]) for f in valves))
        d = self.c.get(f"/api/jobs/{jid}/features/{valves[0]['fid']}").json()
        self.assertTrue(str(d["attributes"].get("VALVE_ID", "")).startswith("SV-"))
        self.assertEqual(self.c.delete(f"/api/jobs/{jid}/reference").status_code, 200)

    def test_geometry_twins(self):
        """BRIDGE (polygon) and BRIDGE_POINT (point) are the same asset required in both geometries:
        geometry routes the CAD group, the twin is derived only when the drawing lacks it."""
        import ezdxf
        for with_cl in (False, True):
            d = ezdxf.new("R2018")
            m = d.modelspace()
            for i in range(2):
                m.add_lwpolyline([(245000 + i * 100, 2701000), (245060 + i * 100, 2701000), (245060 + i * 100, 2701012),
                                  (245000 + i * 100, 2701012)], close=True, dxfattribs={"layer": "BRIDGE"})
            m.add_lwpolyline([(245000, 2701050), (245080, 2701050), (245080, 2701053), (245000, 2701053)], close=True,
                             dxfattribs={"layer": "CYCLE TRACK"})
            if with_cl:
                m.add_lwpolyline([(245000, 2701051.5), (245080, 2701051.5)], dxfattribs={"layer": "CYCLE TRACK CL"})
            path = os.path.join(self.tmp, f"twin{with_cl}.dxf")
            d.saveas(path)
            with open(path, "rb") as f:
                jid = self.c.post("/api/jobs", data={"schema_id": "adm_rd_roads"}, files={"file": ("t.dxf", f, "x")}).json()["job_id"]
            wait(self.c, jid, ("ready",))
            lay = self.c.get(f"/api/jobs/{jid}/mapping").json()["layers"]
            self.assertEqual(lay["BRIDGE::polygon"]["target"], "BRIDGE")
            self.assertEqual(lay["BRIDGE::polygon"]["twins"][0]["target"], "BRIDGE_POINT")
            self.assertEqual(lay["CYCLE TRACK::polygon"]["target"], "RD_TRACK_POLYGON")
            self.assertEqual(lay["CYCLE TRACK::polygon"]["twins"][0]["enabled"], not with_cl)
            self.c.post(f"/api/jobs/{jid}/validate")
            wait(self.c, jid, ("validated",))
            feats = self.c.get(f"/api/jobs/{jid}/results").json()["features"]
            n = {t: sum(1 for f in feats if f["target"] == t) for t in ("BRIDGE", "BRIDGE_POINT", "RD_TRACK_CENTERLINE")}
            self.assertEqual(n, {"BRIDGE": 2, "BRIDGE_POINT": 2, "RD_TRACK_CENTERLINE": 1})

    def test_dmt_spatial_rules_streetlight(self):
        """appconfig.json rules of the DMT QC tool, reported during validation."""
        import ezdxf
        d = ezdxf.new("R2018")
        m = d.modelspace()
        x, y = 245000, 2701000
        for i in range(3):
            m.add_point((x + i * 30, y), dxfattribs={"layer": "STREETLIGHT"})
        m.add_point((x + 200, y), dxfattribs={"layer": "STREETLIGHT"})            # no cable at all
        m.add_lwpolyline([(x, y), (x + 30, y)], dxfattribs={"layer": "LIGHTCABLE"})
        m.add_lwpolyline([(x + 30, y), (x + 60, y)], dxfattribs={"layer": "LIGHTCABLE"})
        m.add_lwpolyline([(x + 60, y), (x + 75, y + 5)], dxfattribs={"layer": "LIGHTCABLE"})   # dangling end
        path = os.path.join(self.tmp, "stl.dxf")
        d.saveas(path)
        with open(path, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "adm_stl_streetlighting"}, files={"file": ("s.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        lay = self.c.get(f"/api/jobs/{jid}/mapping").json()["layers"]
        self.assertEqual(lay["STREETLIGHT::point"]["target"], "STREETLIGHT")
        self.assertEqual(lay["LIGHTCABLE::line"]["target"], "LIGHTCABLE")
        self.c.post(f"/api/jobs/{jid}/validate")
        wait(self.c, jid, ("validated",))
        iss = [i for i in self.c.get(f"/api/jobs/{jid}/results").json()["issues"] if i["code"] == "dmt_spatial_rule"]
        msgs = " ".join(i["message"] for i in iss)
        self.assertIn("Lightcables must either start/end", msgs)
        self.assertIn("Streetlight must be connected", msgs)
        self.assertEqual(sum("Streetlight must" in i["message"] for i in iss), 1)


class DataDictionary(unittest.TestCase):
    """DMT data dictionaries (knowledge/dictionaries) drive class meaning, legacy names and field rules."""

    def setUp(self):
        from geosyntra_bridge.schema.loader import SchemaRegistry
        self.reg = SchemaRegistry(os.path.join(ROOT, "schemas"))

    def test_enrichment(self):
        ir = self.reg.get("adm_ir_irrigation").get_layer("IRRIGATION_VALVE")
        self.assertIn("SOLENOIDVLVASM", ir.legacy_names)
        self.assertIn("VALVE_SIZE", ir.attribute_aliases.get("VALVE_DIAMETER", []))
        st = ir.get_field("STATUS")
        self.assertEqual(st.value_default, "Active")
        self.assertIsNone(st.default)          # the template field definition is never changed
        self.assertTrue(ir.get_field("DATA_SOURCE").contractor_mandatory)
        self.assertFalse(ir.get_field("ROADID").contractor_mandatory)          # municipal engineer / LRS tool
        palm = self.reg.get("adm_sl_softlandscaping").get_layer("PALMETREE")   # dictionary spells PALMTREE
        self.assertEqual(palm.dictionary, "SL")

    def test_legacy_layer_names(self):
        import ezdxf
        from geosyntra_bridge.cad.reader import read_cad
        from geosyntra_bridge.mapping.automap import auto_map
        cases = [("adm_sw_stormwater", "SW_GRATE_CHNL_DRN", "line", "GRATEDCHANNELDRAINAGE"),
                 ("adm_sw_stormwater", "INFRA_MANHOLECHAMBER", "point", "MANHOLECHAMBER"),
                 ("adm_ir_irrigation", "SOLENOIDVLVASM", "point", "IRRIGATION_VALVE"),
                 ("adm_hl_hardlandscaping", "Drinking fountain", "point", "DRINKINGCOOLER")]
        for sid, layer, kind, want in cases:
            with self.subTest(layer=layer):
                doc = ezdxf.new("R2018")
                msp = doc.modelspace()
                for i in range(3):
                    if kind == "line":
                        msp.add_line((245000 + i * 10, 2701000), (245008 + i * 10, 2701000), dxfattribs={"layer": layer})
                    else:
                        msp.add_point((245000 + i * 10, 2701000), dxfattribs={"layer": layer})
                path = os.path.join(tempfile.mkdtemp(), "t.dxf")
                doc.saveas(path)
                m = auto_map(read_cad(path), self.reg.get(sid))
                got = next(iter(m.values()))
                self.assertEqual(got["target"], want, got["evidence"])


class KeyCascade(unittest.TestCase):
    """Layout legend -> drawing-sheet legend -> keynotes -> layers, with cryptic layer names."""

    def _map(self, sample):
        from geosyntra_bridge.cad.reader import read_cad
        from geosyntra_bridge.cad.discovery import discover
        from geosyntra_bridge.schema.loader import load_schema
        from geosyntra_bridge.engine.conversion import build_initial_mapping
        d = read_cad(os.path.join(ROOT, "sample_data", sample))
        s = load_schema(os.path.join(ROOT, "schemas", "adm_hl_hardlandscaping.json"))
        return d, build_initial_mapping(d, s, None, discover(d))

    def _check(self, sample, level):
        d, m = self._map(sample)
        self.assertEqual(d.key_info["level"], level)
        expect = {"L-01::polygon": "STONEGRAVELAREA", "L-02::point": "BENCH", "L-03::line": "EDGES",
                  "L-04::polygon": "HL_CURBSTONE"}
        for g, t in expect.items():
            self.assertEqual(m["layers"][g]["target"], t, g)
            self.assertEqual(m["layers"][g]["status"], "AUTO", g)
        self.assertEqual(m["layers"]["L-04::polygon"]["attributes"]["UNITTYPE"]["value"], "heel")
        self.assertEqual(len([e for e in d.features if e.layer == "L-02"]), 10)   # legend swatch not a bench
        return d

    def test_layout_legend(self):
        self._check("sample_legend_on_layout.dxf", "layout_legend")

    def test_drawing_sheet_legend(self):
        d = self._check("sample_legend_on_drawing.dxf", "drawing_legend")
        self.assertEqual(d.artifacts.get("legend symbols"), 4)

    def test_no_key_falls_back_to_layers(self):
        d, m = self._map("sample_hard_landscaping.dxf")
        self.assertEqual(d.key_info["level"], "layers")
        self.assertEqual(m["layers"]["HL-BOLLARDS::point"]["target"], "HL_BOLLARD")


class Units(unittest.TestCase):
    def test_label_parsing(self):
        from geosyntra_bridge.labels.engine import compile_rules, parse_label
        from geosyntra_bridge.schema.loader import SchemaRegistry
        s = SchemaRegistry(os.path.join(ROOT, "schemas", "examples")).get("wastewater_network")
        rules = compile_rules(s, {"GravitySewer", "SewerManhole"})
        hits = {cr.rule.field: v for cr, v, _ in parse_label("SP-201 300MM UPVC L=45.1m S=0.40%", rules)}
        self.assertEqual(hits["DIAMETER"], 300)
        self.assertEqual(hits["MATERIAL"], "UPVC")
        self.assertEqual(hits["LENGTH"], 45.1)
        self.assertEqual(hits["SLOPE_PCT"], 0.4)
        self.assertEqual(hits["PIPE_ID"], "SP-201")
        hits = {cr.rule.field: v for cr, v, _ in parse_label("SMH-107 CL 5.40 IL 2.70", rules)}
        self.assertEqual((hits["MH_ID"], hits["COVER_LEVEL"], hits["INVERT_LEVEL"]), ("SMH-107", 5.4, 2.7))

    def test_domain_matching(self):
        from geosyntra_bridge.schema.models import DomainDef
        from geosyntra_bridge.validation.attributes import _domain_match
        d = DomainDef("AssetStatus", coded_values={"EXI": "Existing", "PRO": "Proposed"})
        self.assertEqual(_domain_match(d, "existing")[:2], (True, "EXI"))
        ok, _, sugg = _domain_match(d, "Existng")
        self.assertFalse(ok)
        self.assertIn("EXI", sugg)


def _first_coord(g):
    c = g["coordinates"]
    while isinstance(c[0], list):
        c = c[0]
    return c[0], c[1]


if __name__ == "__main__":
    unittest.main()
