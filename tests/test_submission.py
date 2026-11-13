"""DMT As-Built Data Submission Standards V7 — common requirements (3.6.1 naming, 3.8 metadata, 3.9 boundary,
2.1/3.5 Ras Ghumays, 3.4 Table 2 accuracy)."""
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from fastapi.testclient import TestClient  # noqa: E402

from geosyntra_bridge.api.app import create_app  # noqa: E402
from geosyntra_bridge.config import Settings  # noqa: E402
from geosyntra_bridge.elevation.dem import write_geotiff  # noqa: E402
from geosyntra_bridge import submission as sub  # noqa: E402

PROJECT = {"entity": "ADM", "asset_type": "HL", "project_id": "241LM2", "project_stage": "HO", "submission_stage": "F",
           "version": 1, "submission_date": "2026-11-01", "project_name": "Al Bateen Park landscaping",
           "contract_type": "New Infra Dev Project", "project_function": "Parks and Landscape",
           "consultant_name": "Consultant LLC", "consultant_rep": "A. Engineer", "contractor_name": "Contractor LLC",
           "contractor_rep": "B. Manager", "start_date": "2025-01-05", "completion_date": "2026-06-30",
           "pac_date": "2026-07-15", "fac_date": "2027-07-15", "operation_cost": 120000, "maintenance_cost": 80000,
           "total_cost": 25000000, "metadata_contact": "GIS team, Contractor LLC, gis@example.com",
           "usage": "Asset handover to ADM", "security": "Restricted — ADM internal use"}


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


class Submission(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_sub_")
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_naming_rules(self):
        self.assertEqual(sub.submission_name({**PROJECT}), "ADM_HL_241LM2_HO_F001_20261101")
        self.assertRegex("ADM_SW_241LM2_HO_F001_20201116", sub.NAME_RX)          # example of the standard
        self.assertIsNone(sub.submission_name({**PROJECT, "project_id": "241_LM2"}))
        self.assertEqual(sub.submission_name({**PROJECT, "project_id": ""}), "ADM_HL_NOPID_HO_F001_20261101")   # optional for now
        missing, errors = sub.check({**PROJECT, "version": 1000, "project_name": ""})
        self.assertIn("Project name (as per contract)", missing)
        self.assertTrue(any("Version" in e for e in errors))

    def _job(self, project, elevation, dem_vertical="EPSG:5773"):
        path = os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf")
        with open(path, "rb") as f:
            jid = self.c.post("/api/jobs", data={"schema_id": "adm_hl_hardlandscaping"}, files={"file": ("h.dxf", f, "x")}).json()["job_id"]
        wait(self.c, jid, ("ready",))
        self.c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        self.c.patch(f"/api/jobs/{jid}/mapping", json={"project": project, "fields": {"*": {
            k: {"source": "constant", "value": v} for k, v in {"DATA_SOURCE": "AS BUILT", "REGION": "ADM"}.items()}}})
        ext = self.c.get(f"/api/jobs/{jid}/discovery").json()["extent"]
        x0, y1, res = ext[0] - 50, ext[3] + 50, 1.0
        w, h = int(ext[2] - ext[0] + 100), int(ext[3] - ext[1] + 100)
        tif = os.path.join(self.tmp, f"dtm_{jid}.tif")
        write_geotiff(tif, np.full((h, w), 3.0, dtype=np.float32), (x0, res, 0, y1, 0, -res), 32640)
        with open(tif, "rb") as f:
            r = self.c.post(f"/api/jobs/{jid}/elevation/upload", files={"file": ("dtm.tif", f, "x")}, data={"vertical": dem_vertical})
        self.assertEqual(r.status_code, 200, r.text)
        self.c.patch(f"/api/jobs/{jid}/mapping", json={"elevation": elevation})
        self.c.post(f"/api/jobs/{jid}/validate")
        wait(self.c, jid, ("validated",))
        return jid

    def test_full_submission_package(self):
        jid = self._job(PROJECT, {"rg_offset": -0.5, "dem_accuracy_m": 0.1})
        st = self.c.get(f"/api/jobs/{jid}/submission").json()
        self.assertEqual((st["name"], st["missing"], st["errors"]), ("ADM_HL_241LM2_HO_F001_20261101", [], []))
        res = self.c.get(f"/api/jobs/{jid}/results").json()
        codes = {i["code"] for i in res["issues"]}
        self.assertNotIn("vertical_datum_not_ras_ghumays", codes)            # offset supplied -> Ras Ghumays
        self.assertNotIn("elevation_accuracy_insufficient", codes)           # 0.1 m < 0.30 m (Area 1)
        self.assertTrue(res["elevation"]["ras_ghumays"])
        self.assertAlmostEqual(res["elevation"]["z_range"]["min"], 2.5, places=3)   # 3.0 + (-0.5)
        self.assertEqual(self.c.post(f"/api/jobs/{jid}/generate").status_code, 202)
        st = wait(self.c, jid, ("completed",))
        self.assertTrue(st["outputs"]["gdb_zip"].startswith("ADM_HL_241LM2_HO_F001_20261101.gdb"))
        res = self.c.get(f"/api/jobs/{jid}/results").json()
        chk = {c["check"]: c for c in res["verification"]["checks"]}
        self.assertTrue(chk["Submission file name (DMT standard 3.6.1)"]["passed"])
        self.assertTrue(chk["Project information & metadata (DMT standard 3.8)"]["passed"])
        self.assertTrue(chk["Project boundary PROJECT_BOUND (DMT standard 3.9)"]["passed"])
        self.assertEqual(res["dmt_qc"]["by_check"]["Schema identical to template"], 0)    # template untouched
        # PROJECTID filled from the project information in every class
        f = next(x for x in res["features"] if x["status"] == "ok")
        self.assertEqual(self.c.get(f"/api/jobs/{jid}/features/{f['fid']}").json()["attributes"]["PROJECTID"], "241LM2")
        # metadata XML: well-formed, mandatory items present
        meta = self.c.get(f"/api/jobs/{jid}/download/metadata").text
        root = ET.fromstring(meta)
        self.assertIn("241LM2", meta)
        self.assertIn("Ras Ghumays", meta)
        self.assertTrue(root.tag.endswith("MD_Metadata"))
        # package
        z = zipfile.ZipFile(io.BytesIO(self.c.get(f"/api/jobs/{jid}/download/package").content))
        names = z.namelist()
        self.assertTrue(any(n.startswith("ADM_HL_241LM2_HO_F001_20261101.gdb/") for n in names))
        self.assertTrue(any("_PROJECT_BOUND.gdb/" in n for n in names))
        for suffix in ("_metadata.xml", "_QCErrors.csv", "_report.xlsx", "_report.pdf"):
            self.assertTrue(any(n.endswith(suffix) for n in names), suffix)
        # PROJECT_BOUND: one clean polygon feature class
        import pyogrio
        bdir = os.path.join(self.tmp, "b")
        z.extractall(bdir)
        bgdb = next(os.path.join(bdir, n.split("/")[0]) for n in names if "_PROJECT_BOUND.gdb/" in n)
        info = pyogrio.read_info(bgdb, layer="PROJECT_BOUND")
        self.assertEqual((info["features"], info["geometry_type"]), (1, "MultiPolygon"))

    def test_not_ras_ghumays_and_accuracy_reported(self):
        jid = self._job({**PROJECT, "project_id": ""}, {"rg_offset": None, "dem_accuracy_m": None}, dem_vertical="EPSG:3855")
        res = self.c.get(f"/api/jobs/{jid}/results").json()
        codes = {i["code"] for i in res["issues"]}
        self.assertIn("vertical_datum_not_ras_ghumays", codes)
        self.assertIn("elevation_accuracy_unknown", codes)
        self.assertFalse(res["elevation"]["ras_ghumays"])
        st = self.c.get(f"/api/jobs/{jid}/submission").json()
        self.assertEqual(st["name"], "ADM_HL_NOPID_HO_F001_20261101")      # Project ID optional: placeholder in the name
        self.assertNotIn("Project ID (optional for now)", st["missing"])


if __name__ == "__main__":
    unittest.main()
