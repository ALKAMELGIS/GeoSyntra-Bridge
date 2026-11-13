"""Map-based feature assignment: one feature's values saved into its own row of the delivered geodatabase
(by OBJECTID, read back), other rows and the geometry untouched; invalid values refused without writing."""
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
from geosyntra_bridge.elevation.dem import write_geotiff  # noqa: E402
import numpy as np  # noqa: E402


def read(gdb, layer):
    import pyogrio.raw
    meta, fids, geom, cols = pyogrio.raw.read(gdb, layer=layer, return_fids=True)
    return {int(f): ({n: str(cols[j][i]) for j, n in enumerate(meta["fields"])}, bytes(geom[i])) for i, f in enumerate(fids)}


class MapAssign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_assign_")
        cls.app = create_app(Settings(data_dir=os.path.join(cls.tmp, "d"), rules_dir=os.path.join(cls.tmp, "r")))
        cls.c = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_edit_one_feature_in_gdb(self):
        c = self.c
        with open(os.path.join(ROOT, "sample_data", "sample_hard_landscaping.dxf"), "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": "adm_all"}, files={"file": ("h.dxf", f, "x")}).json()["job_id"]
        wait(c, jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        c.patch(f"/api/jobs/{jid}/mapping", json={"fields": {"*": {k: {"source": "constant", "value": v} for k, v in
                                                                   {"DATA_SOURCE": "AS BUILT", "REGION": "ADM"}.items()}}})
        ext = c.get(f"/api/jobs/{jid}/discovery").json()["extent"]
        tif = os.path.join(self.tmp, "dtm.tif")
        write_geotiff(tif, np.full((int(ext[3] - ext[1] + 100), int(ext[2] - ext[0] + 100)), 3.0, dtype=np.float32),
                      (ext[0] - 50, 1.0, 0, ext[3] + 50, 0, -1.0), 32640)
        with open(tif, "rb") as f:
            c.post(f"/api/jobs/{jid}/elevation/upload", files={"file": ("d.tif", f, "x")}, data={"vertical": "EPSG:5773"})
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        # before generation: saved as an edit, written on Generate
        ov = c.get(f"/api/jobs/{jid}/assign").json()
        self.assertGreater(ov["counts"]["total"], 1)
        self.assertFalse(ov["gdb"]["generated"])
        c.post(f"/api/jobs/{jid}/generate")
        wait(c, jid, ("completed", "failed"))
        ov = c.get(f"/api/jobs/{jid}/assign").json()
        self.assertTrue(ov["gdb"]["generated"])
        rows = [r for r in ov["features"] if r["objectid"]]
        from collections import Counter
        big = Counter(r["target"] for r in rows).most_common(1)[0][0]
        a, b = [r for r in rows if r["target"] == big][:2]
        spec = ov["classes"][a["target"]]["fields"]
        txt = next(f for f in spec if f["type"] == "TEXT" and not f["read_only"] and not f["domain"] and (f["length"] or 50) >= 8)
        d = c.get(f"/api/jobs/{jid}/assign/{a['fid']}").json()
        gdb = os.path.join(self.tmp, "d", "jobs", jid, "output", d["gdb"]["name"])
        before = read(gdb, a["target"])
        self.assertEqual(d["gdb"]["objectid"], a["objectid"])
        r = c.post(f"/api/jobs/{jid}/assign/{a['fid']}", json={"fields": {txt["name"]: "MAPEDIT1"}, "status": "draft"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["gdb"]["objectid"], a["objectid"])
        self.assertTrue(r.json()["gdb"]["geometry_unchanged"])

        after = read(gdb, a["target"])
        self.assertEqual(after[a["objectid"]][0][txt["name"]], "MAPEDIT1")
        self.assertEqual(after[b["objectid"]][0], before[b["objectid"]][0])                 # other row untouched
        self.assertEqual(after[a["objectid"]][1], before[a["objectid"]][1])                 # geometry untouched
        self.assertEqual(len(after), len(before))
        self.assertEqual(c.get(f"/api/jobs/{jid}/assign/{a['fid']}").json()["gdb"]["values"][txt["name"]], "MAPEDIT1")

        # a value outside the domain is refused and nothing is written
        dom = next((f for f in spec if f["domain"] and not f["read_only"]), None)
        if dom:
            r = c.post(f"/api/jobs/{jid}/assign/{a['fid']}", json={"fields": {dom["name"]: "NOT-A-CODE-XYZ"}})
            self.assertEqual(r.status_code, 422, r.text)
            again = read(gdb, a["target"])
            self.assertEqual(again[a["objectid"]], after[a["objectid"]])
        # skip -> orange state; the edit survives a re-validation (engine applies it)
        r = c.post(f"/api/jobs/{jid}/assign/{b['fid']}", json={"fields": {}, "status": "skipped"})
        self.assertEqual(r.json()["state"], "skipped")
        c.post(f"/api/jobs/{jid}/validate")
        wait(c, jid, ("validated",))
        d = c.get(f"/api/jobs/{jid}/features/{a['fid']}").json()
        self.assertEqual(d["attributes"][txt["name"]], "MAPEDIT1")
        self.assertIn("edited on map", d["remarks"])
        st = {x["fid"]: x["state"] for x in c.get(f"/api/jobs/{jid}/assign").json()["features"]}
        self.assertEqual(st[b["fid"]], "skipped")


if __name__ == "__main__":
    unittest.main()


class PlantNames(unittest.TestCase):
    def test_species_from_layer_name(self):
        from geosyntra_bridge.knowledge.plants import find_species
        self.assertEqual(find_species("ISL-ALBT-2025-P02-SS-PA-Washingtonia Robusta-Mexican Fan Palm")[0], "Washingtonia robusta")
        self.assertEqual(find_species("SS-GC-Pennisetum Rubrum-Red Fountain Grass")[0], "Pennisetum setaceum 'Rubrum'")
        self.assertEqual(find_species("SS-GC-Pennisetum Setaceum-Fountain Grass")[1], "Fountain Grass")
        self.assertIsNone(find_species("HL-BOLLARDS"))


class CadNames(unittest.TestCase):
    def test_names_as_written(self):
        from geosyntra_bridge.knowledge.plants import names_from_text
        self.assertEqual(names_from_text("ISL-ALBT-2025-P02-SS-PA-Washingtonia Robusta-Mexican Fan Palm"),
                         ["Washingtonia Robusta", "Mexican Fan Palm"])
        self.assertEqual(names_from_text("SS-PA-COPERNICIA (WAX Palm )"), ["COPERNICIA", "WAX Palm"])

    def test_name_outside_list_is_kept(self):
        from geosyntra_bridge.config import Settings
        from geosyntra_bridge.engine.model import IssueLog, TargetFeature
        from geosyntra_bridge.cad.model import CadEntity
        from geosyntra_bridge.schema.loader import SchemaRegistry
        from geosyntra_bridge.validation.attributes import coerce_and_validate
        s = Settings()
        schema = SchemaRegistry(getattr(s, "schemas_dir", "schemas")).get("adm_all")
        layer = schema.get_layer("PALMETREE")
        ent = CadEntity(handle="1", layer="SS-PA-Washingtonia Robusta", kind="point", dxftype="INSERT", coords=[(0.0, 0.0)])
        f = TargetFeature(fid=1, target="PALMETREE", group_key="g", source=ent, attrs={"SCIENTIFIC_NAME": "Washingtonia Robusta"})
        log = IssueLog()
        coerce_and_validate(f, layer, schema, log)
        self.assertEqual(f.attrs["SCIENTIFIC_NAME"], "Washingtonia Robusta")
        self.assertFalse([i for i in log.items if i.severity == "error"])
