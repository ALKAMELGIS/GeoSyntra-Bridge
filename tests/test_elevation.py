"""DEM elevation integration: sampling, CAD Z policy, NoData, vertical reference, sources, never-zero rule."""
import functools
import gzip
import http.server
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ezdxf  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from geosyntra_bridge.api.app import create_app  # noqa: E402
from geosyntra_bridge.config import Settings  # noqa: E402
from geosyntra_bridge.elevation.dem import write_geotiff  # noqa: E402
from geosyntra_bridge.elevation.engine import vertical_transformer  # noqa: E402

X0, Y0 = 245000.0, 2701000.0


def plane(x, y):
    return 3 + 0.001 * (x - X0) + 0.002 * (y - Y0)


def wait(c, jid, statuses, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = c.get(f"/api/jobs/{jid}").json()
        if st["status"] in statuses:
            return st
        if st["status"] == "failed":
            raise AssertionError(st["message"])
        time.sleep(0.2)
    raise AssertionError("timeout")


class Elevation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_elev_")
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)
        # drawing: bollards (points), a kerb line crossing a NoData hole, an edge drawn in 3D (CAD levels)
        doc = ezdxf.new("R2018")
        m = doc.modelspace()
        for i in range(4):
            m.add_point((X0 + 10 + i * 10, Y0 + 10, 0), dxfattribs={"layer": "HL-BOLLARDS"})
        m.add_lwpolyline([(X0 + 5, Y0 + 40), (X0 + 50, Y0 + 40), (X0 + 95, Y0 + 40)], dxfattribs={"layer": "HL-KERB"})
        m.add_polyline3d([(X0 + 5, Y0 + 70, 12.5), (X0 + 60, Y0 + 70, 13.0)], dxfattribs={"layer": "HL-EDGES-ALUMINIUM"})
        m.add_point((X0 + 300, Y0 + 300, 0), dxfattribs={"layer": "HL-BOLLARDS"})   # outside the DEM
        cls.dxf = os.path.join(cls.tmp, "elev.dxf")
        doc.saveas(cls.dxf)
        # DEM: tilted plane, 1 m pixels, covering 0..120 m, NoData hole around the middle kerb vertex
        res, w, h = 1.0, 120, 120
        cols = X0 + (np.arange(w) + 0.5) * res
        rows = Y0 + 120 - (np.arange(h) + 0.5) * res
        X, Y = np.meshgrid(cols, rows)
        Z = plane(X, Y).astype(np.float32)
        Z[(np.abs(X - (X0 + 50)) < 4) & (np.abs(Y - (Y0 + 40)) < 4)] = -9999
        cls.tif = os.path.join(cls.tmp, "dtm.tif")
        write_geotiff(cls.tif, Z, (X0, res, 0, Y0 + 120, 0, -res), 32640, -9999)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _job(self, elevation=None, upload=True):
        with open(self.dxf, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "adm_hl_hardlandscaping"},
                              files={"file": ("e.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        lay = self.c.get(f"/api/jobs/{jid}/mapping").json()["layers"]
        patch = {"layers": {}}
        for g, t in (("HL-BOLLARDS::point", "HL_BOLLARD"), ("HL-KERB::line", "HL_CURBSTONE"),
                     ("HL-EDGES-ALUMINIUM::line", "EDGES")):
            if g in lay:
                patch["layers"][g] = {"target": t, "role": "feature"}
        patch["fields"] = {"*": {k: {"source": "constant", "value": v} for k, v in {"DATA_SOURCE": "AS BUILT", "REGION": "ADM"}.items()}}
        self.c.patch(f"/api/jobs/{jid}/mapping", json=patch)
        if upload:
            with open(self.tif, "rb") as f:
                r = self.c.post(f"/api/jobs/{jid}/elevation/upload", files={"file": ("dtm.tif", f, "x")},
                                data={"vertical": "EPSG:5773"})
            self.assertEqual(r.status_code, 200, r.text)
        if elevation:
            self.c.patch(f"/api/jobs/{jid}/mapping", json={"elevation": elevation})
        self.c.post(f"/api/jobs/{jid}/validate")
        wait(self.c, jid, ("validated",))
        return jid, self.c.get(f"/api/jobs/{jid}/results").json()

    def _detail(self, jid, fid):
        return self.c.get(f"/api/jobs/{jid}/features/{fid}").json()

    def test_sampling_exact_and_cad_preserved(self):
        jid, res = self._job({"missing_policy": "exclude"})
        bol = [f for f in res["features"] if f["target"] == "HL_BOLLARD" and f["status"] == "ok"]
        self.assertEqual(len(bol), 4)
        for f in bol:
            d = self._detail(jid, f["fid"])
            x = X0 + 10 + 10 * [b["fid"] for b in bol].index(f["fid"])
            self.assertAlmostEqual(d["attributes"]["ZCOORD"], plane(x, Y0 + 10), places=3)
            self.assertEqual(d["elevation"]["status"], "dem")
        edge = next(f for f in res["features"] if f["target"] == "EDGES")
        self.assertEqual(edge["z"]["status"], "cad")                       # CAD levels preserved by default
        self.assertEqual((edge["z"]["start"], edge["z"]["end"]), (12.5, 13.0))
        # kerb: middle vertex in NoData -> interpolated along the line, never 0
        kerb = next(f for f in res["features"] if f["target"] == "HL_CURBSTONE")
        self.assertEqual(kerb["z"]["n_interpolated"], 1)
        self.assertAlmostEqual(kerb["z"]["start"], plane(X0 + 5, Y0 + 40), places=3)
        # point outside the DEM: excluded (policy), reported, never Z = 0
        out = [f for f in res["features"] if f["target"] == "HL_BOLLARD" and f["status"] == "skipped"]
        self.assertEqual(len(out), 1)
        self.assertIn("elevation_missing_handled", {i["code"] for i in res["issues"]})
        self.assertEqual(res["elevation"]["dem"]["vertical"], "EPSG:5773")
        # generation: Z written as computed, still identical to the template for the DMT QC
        self.assertEqual(self.c.post(f"/api/jobs/{jid}/generate").status_code, 202)
        wait(self.c, jid, ("completed",))
        r = self.c.get(f"/api/jobs/{jid}/results").json()
        chk = {c["check"]: c for c in r["verification"]["checks"]}
        self.assertTrue(chk["Elevation (Z) written as computed"]["passed"], chk["Elevation (Z) written as computed"])
        self.assertEqual(r["dmt_qc"]["by_check"]["Schema identical to template"], 0)

    def test_dem_fills_template_fields_only(self):
        """DEM values go into the elevation fields that already exist in the template class — nothing is added."""
        jid, res = self._job({"missing_policy": "exclude"})
        m = self.c.get(f"/api/jobs/{jid}/mapping").json()
        self.assertEqual(m["fields"]["HL_BOLLARD"]["ZCOORD"], {"source": "elevation", "value": "z"})
        self.assertEqual(m["fields"]["EDGES"]["ZCOORD_START"]["source"], "elevation")
        self.assertEqual(m["fields"]["EDGES"]["ZCOORD_END"]["value"], "end_z")
        from geosyntra_bridge.schema.loader import SchemaRegistry
        tpl = SchemaRegistry(os.path.join(ROOT, "schemas")).get("adm_hl_hardlandscaping")
        for fc, fields in m["fields"].items():
            names = {f.name for f in tpl.get_layer(fc).fields}
            self.assertTrue(set(fields) <= names, set(fields) - names)
        kerb = next(f for f in res["features"] if f["target"] == "HL_CURBSTONE")
        d = self._detail(jid, kerb["fid"])
        self.assertAlmostEqual(d["attributes"]["ZCOORD_START"], plane(X0 + 5, Y0 + 40), places=3)
        self.assertAlmostEqual(d["attributes"]["ZCOORD_END"], plane(X0 + 95, Y0 + 40), places=3)

    def test_replace_mode(self):
        jid, res = self._job({"mode": "replace", "missing_policy": "exclude"})
        edge = next(f for f in res["features"] if f["target"] == "EDGES")
        self.assertEqual(edge["z"]["status"], "dem")
        self.assertAlmostEqual(edge["z"]["start"], plane(X0 + 5, Y0 + 70), places=3)

    def test_missing_blocks_by_default(self):
        jid, res = self._job()
        self.assertIn("elevation_missing", {i["code"] for i in res["issues"]})
        comp = self.c.get(f"/api/jobs/{jid}/completeness").json()
        self.assertFalse(comp["complete"])
        self.assertTrue(any("no elevation" in b for b in comp["blocking_errors"]))
        self.assertEqual(self.c.post(f"/api/jobs/{jid}/generate").status_code, 409)

    def test_explicit_nodata_value(self):
        r = self.c.patch("/api/jobs/x/mapping", json={})   # unknown job -> 404, sanity
        self.assertIn(r.status_code, (403, 404))
        jid, res = self._job({"missing_policy": "nodata_value", "nodata_value": -9999})
        out = next(f for f in res["features"] if f["target"] == "HL_BOLLARD" and "nodata" in (f["z"] or {}).get("status", ""))
        self.assertEqual(self._detail(jid, out["fid"])["attributes"].get("ZCOORD"), -9999)   # the explicit value, never 0
        # zero is refused as a no-data value
        bad = self.c.patch(f"/api/jobs/{jid}/mapping", json={"elevation": {"missing_policy": "nodata_value", "nodata_value": 0}})
        self.assertEqual(bad.status_code, 400)

    def test_disabled_never_zero(self):
        jid, res = self._job(upload=False)
        bol = next(f for f in res["features"] if f["target"] == "HL_BOLLARD")
        self.assertIsNone(self._detail(jid, bol["fid"])["attributes"].get("ZCOORD"))   # was 0 before this module
        # ZCOORD is NOT NULL in the template: without elevation the job is blocked, not filled with 0
        blocked = [i for i in res["issues"] if i["code"] == "not_null_unfilled" and i.get("field") == "ZCOORD"]
        self.assertTrue(blocked and "never written" in blocked[0]["message"])
        edge = next(f for f in res["features"] if f["target"] == "EDGES")
        self.assertEqual(edge["z"]["status"], "cad")                                   # real CAD levels still used
        self.assertEqual(res["elevation"]["status"], "disabled")

    def test_vertical_reference_never_silent(self):
        t, msg = vertical_transformer("EPSG:5773", "EPSG:3855")
        if t is None:                       # grid not installed: refused and explained, not an identity transform
            self.assertIn("unavailable", msg)
        else:
            _, _, h = t.transform(54.4, 24.4, 10.0)
            self.assertNotAlmostEqual(h, 10.0, places=2)
        self.assertIsNone(vertical_transformer("EPSG:5773", "source")[0])

    def test_online_source_via_mirror(self):
        """SRTM tiles fetched by URL template (here a local mirror with the provider's path layout)."""
        mirror = os.path.join(self.tmp, "mirror")
        os.makedirs(os.path.join(mirror, "skadi", "N24"), exist_ok=True)
        n = 3601
        a = np.full((n, n), 7, dtype=">i2")
        with gzip.open(os.path.join(mirror, "skadi", "N24", "N24E054.hgt.gz"), "wb") as f:
            f.write(a.tobytes())
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
            http.server.SimpleHTTPRequestHandler, directory=mirror))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        old = {k: os.environ.get(k) for k in ("GSB_DEM_URL_SRTM_GL1", "GSB_DEM_URL_COPERNICUS_GLO30", "NO_PROXY", "no_proxy")}
        os.environ["GSB_DEM_URL_SRTM_GL1"] = f"http://127.0.0.1:{srv.server_address[1]}/skadi/{{NS}}{{lat:02d}}/{{NS}}{{lat:02d}}{{EW}}{{lon:03d}}.hgt.gz"
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"
        try:
            with open(self.dxf, "rb") as f:
                jid = self.c.post("/api/jobs", data={"schema_id": "adm_hl_hardlandscaping"}, files={"file": ("e.dxf", f, "x")}).json()["job_id"]
            wait(self.c, jid, ("ready",))
            r = self.c.post(f"/api/jobs/{jid}/elevation/prepare", json={"source": "srtm_gl1"})
            self.assertEqual(r.status_code, 200, r.text)
            dem = r.json()["dem"]
            self.assertEqual((dem["vertical"], dem["min"], dem["max"]), ("EPSG:5773", 7.0, 7.0))
            pv = self.c.get(f"/api/jobs/{jid}/elevation/preview").json()
            self.assertGreater(pv["stats"]["with_value"], 0)
            self.assertEqual(pv["stats"]["min"], 7.0)
            # Copernicus GLO-30: COG tile per degree (here a small WGS 84 GeoTIFF at the provider's path layout)
            name = "Copernicus_DSM_COG_10_N24_00_E054_00_DEM"
            os.makedirs(os.path.join(mirror, name), exist_ok=True)
            res = 0.001
            write_geotiff(os.path.join(mirror, name, name + ".tif"), np.full((1000, 1000), 4.5, dtype=np.float32),
                          (54.0, res, 0, 25.0, 0, -res), 4326)
            os.environ["GSB_DEM_URL_COPERNICUS_GLO30"] = (f"http://127.0.0.1:{srv.server_address[1]}/Copernicus_DSM_COG_10_"
                                                          "{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif")
            r = self.c.post(f"/api/jobs/{jid}/elevation/prepare", json={"source": "copernicus_glo30"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual((r.json()["dem"]["vertical"], r.json()["dem"]["min"]), ("EPSG:3855", 4.5))
            # ALOS needs an OpenTopography key: clear message, nothing downloaded
            r = self.c.post(f"/api/jobs/{jid}/elevation/prepare", json={"source": "alos_aw3d30"})
            self.assertEqual(r.status_code, 400)
            self.assertIn("API key", r.json()["detail"])
        finally:
            srv.shutdown()
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
