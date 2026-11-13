"""Municipality publishes a new template: upload -> change report & impact -> apply (archived) -> jobs revalidate
-> restore. Runs on a copy of the schemas folder; the shipped templates are never touched."""
import copy
import json
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

from geosyntra_bridge.api.app import create_app  # noqa: E402
from geosyntra_bridge.config import Settings  # noqa: E402
from geosyntra_bridge.gdb.filegdb_writer import write_filegdb  # noqa: E402
from geosyntra_bridge.schema.loader import schema_from_dict  # noqa: E402

SID = "adm_stl_streetlighting"


def make_v11(tmp: str) -> str:
    """A 'new template' built from the STL definition with typical municipal changes."""
    with open(os.path.join(ROOT, "schemas", f"{SID}.json"), encoding="utf-8") as f:
        d = json.load(f)
    d = copy.deepcopy(d)
    d.pop("source_template", None)
    d["topology_rules"] = []
    layers = {l["name"]: l for l in d["layers"]}
    d["layers"] = [l for l in d["layers"] if l["name"] != "EARTHING"]                      # class removed
    jb = layers["JUNCTIONBOX"]
    jb["name"] = "JUNCTION_BOX"                                                             # class renamed
    sl = layers["STREETLIGHT"]
    sl["fields"].append({"name": "SMART_NODE_ID", "field_type": "TEXT", "length": 50})     # field added
    sl["fields"] = [x for x in sl["fields"] if x["name"] != "ADVERTISESIGN"]                # field removed
    d["layers"].append({"name": "EV_CHARGER", "geometry_type": "point", "feature_dataset": sl.get("feature_dataset"),
                        "fields": [{"name": "UNITID", "field_type": "TEXT", "length": 50},
                                   {"name": "POWER_KW", "field_type": "DOUBLE"}]})          # class added
    dom = next(x for x in d["domains"] if x.get("domain_type") == "coded" and x.get("coded_values"))
    dom["coded_values"]["NEW_CODE"] = "New value from V11"                                  # domain value added
    gdb = os.path.join(tmp, "src", "ADM_STL_Templates_Z40_V11_01112026.gdb")
    write_filegdb(gdb, schema_from_dict(d), [])
    zp = os.path.join(tmp, "ADM_STL_Templates_Z40_V11_01112026.gdb.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(gdb):
            for fn in files:
                p = os.path.join(root, fn)
                z.write(p, os.path.relpath(p, os.path.dirname(gdb)))
    return zp, dom["name"]


class TemplateUpdate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gsb_tu_")
        sd = os.path.join(cls.tmp, "schemas")
        os.makedirs(os.path.join(sd, "templates"))
        shutil.copy(os.path.join(ROOT, "schemas", f"{SID}.json"), sd)
        cur = json.load(open(os.path.join(sd, f"{SID}.json")))
        shutil.copy(os.path.join(ROOT, "schemas", "templates", cur["source_template"] + ".zip"), os.path.join(sd, "templates"))
        rules = os.path.join(cls.tmp, "rules")
        os.makedirs(os.path.join(rules, "profiles", SID))
        with open(os.path.join(rules, f"{SID}.rules.json"), "w") as f:
            json.dump({"schema_id": SID, "rules": [{"fingerprint": "abc", "target_layer": "JUNCTIONBOX",
                                                     "match_type": "fingerprint", "approved_by": "t"}]}, f)
        with open(os.path.join(rules, "profiles", SID, "p1.json"), "w") as f:
            json.dump({"name": "p1", "layers": {"E-JB|point": {"target": "JUNCTIONBOX"}},
                       "fields": {"JUNCTIONBOX": {"UNITID": {"source": "label"}}}}, f)
        cls.app = create_app(Settings(schemas_dir=sd, extra_schema_dirs=[], data_dir=os.path.join(cls.tmp, "d"), rules_dir=rules))
        cls.c = TestClient(cls.app)
        cls.zip, cls.domain = make_v11(cls.tmp)
        cls.sd, cls.rules = sd, rules

    @classmethod
    def tearDownClass(cls):
        cls.app.state.jobs.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_update_cycle(self):
        c = self.c
        before = c.get(f"/api/schemas/{SID}").json()
        with open(os.path.join(ROOT, "sample_data", "sample_street_lighting.dxf"), "rb") as f:
            jid = c.post("/api/jobs", data={"schema_id": SID}, files={"file": ("sl.dxf", f, "x")}).json()["job_id"]
        self._wait(jid, ("ready",))
        c.post(f"/api/jobs/{jid}/mapping/accept-suggestions")
        c.post(f"/api/jobs/{jid}/validate")
        self._wait(jid, ("validated",))
        targets = {v.get("target") for v in c.get(f"/api/jobs/{jid}/mapping").json()["layers"].values()}
        with open(self.zip, "rb") as f:
            r = c.post(f"/api/schemas/{SID}/template-updates", files={"file": (os.path.basename(self.zip), f, "application/zip")})
        self.assertEqual(r.status_code, 200, r.text)
        u = r.json()
        kinds = {(x["kind"], x["object"]) for x in u["diff"]["changes"]}
        self.assertIn(("class_removed", "EARTHING"), kinds)
        self.assertIn(("class_added", "EV_CHARGER"), kinds)
        self.assertIn(("field_added", "STREETLIGHT.SMART_NODE_ID"), kinds)
        self.assertIn(("field_removed", "STREETLIGHT.ADVERTISESIGN"), kinds)
        self.assertIn(("values_added", self.domain), kinds)
        self.assertEqual(u["new_version"], "11 (01112026)")
        self.assertEqual(u["diff"]["renames"]["classes"][0]["old"], "JUNCTIONBOX")
        self.assertEqual(u["diff"]["renames"]["classes"][0]["new"], "JUNCTION_BOX")
        self.assertEqual(u["impact"]["learned_rules"][0]["target"], "JUNCTIONBOX")
        self.assertEqual(u["impact"]["profiles"][0]["classes"], ["JUNCTIONBOX"])
        # nothing changed yet
        self.assertEqual(c.get(f"/api/schemas/{SID}").json()["version"], before["version"])

        r = c.post(f"/api/schemas/{SID}/template-updates/{u['update_id']}/apply",
                   json={"renames": {"classes": {"JUNCTIONBOX": "JUNCTION_BOX"}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["renames_applied"], {"learned_rules": 1, "profiles": 1})
        comp = c.get(f"/api/jobs/{jid}/completeness").json()               # validated on V10 -> must revalidate
        self.assertFalse(comp["complete"])
        self.assertFalse(comp["validation_current"])
        self.assertTrue(any("template was updated" in b for b in comp["blocking_errors"]))
        if "EARTHING" in targets:
            self.assertIn("TARGET_REMOVED", {g["status"] for g in comp["open_groups"]})
        self.assertEqual(c.post(f"/api/jobs/{jid}/generate").status_code // 100, 4)
        after = c.get(f"/api/schemas/{SID}").json()
        self.assertEqual(after["version"], "11 (01112026)")
        self.assertIn("V11", after["display_name"])
        names = {l["name"]: l for l in after["layers"]}
        self.assertIn("EV_CHARGER", names)
        self.assertNotIn("EARTHING", names)
        # curated CAD knowledge carried over, geometry hints follow the new template
        cur = json.load(open(os.path.join(self.sd, f"{SID}.json")))
        sl = next(l for l in cur["layers"] if l["name"] == "STREETLIGHT")
        self.assertIn("classification_hints", sl)
        self.assertTrue(os.path.isfile(os.path.join(self.sd, "templates", "ADM_STL_Templates_Z40_V11_01112026.gdb.zip")))
        prof = json.load(open(os.path.join(self.rules, "profiles", SID, "p1.json")))
        self.assertEqual(prof["layers"]["E-JB|point"]["target"], "JUNCTION_BOX")
        self.assertIn("JUNCTION_BOX", prof["fields"])
        lst = c.get(f"/api/schemas/{SID}/template-updates").json()
        self.assertEqual(lst["current"]["version"], "11 (01112026)")
        self.assertEqual(len(lst["versions"]), 1)                      # V10 archived with its template
        folder = lst["versions"][0]["folder"]
        self.assertTrue(any(n.endswith(".zip") for n in os.listdir(os.path.join(self.sd, "_versions", SID, folder))))
        # a second apply of the same update is refused
        self.assertEqual(c.post(f"/api/schemas/{SID}/template-updates/{u['update_id']}/apply").status_code, 409)
        # restore V10
        r = c.post(f"/api/schemas/{SID}/versions/{folder}/restore")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get(f"/api/schemas/{SID}").json()["version"], before["version"])
        self.assertEqual(len(c.get(f"/api/schemas/{SID}/template-updates").json()["versions"]), 2)

    def _wait(self, jid, statuses, timeout=120):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.c.get(f"/api/jobs/{jid}").json()
            if st["status"] in statuses:
                return st
            if st["status"] == "failed":
                raise AssertionError(st["message"])
            time.sleep(0.2)
        raise AssertionError("timeout")

    def test_identical_template_reports_no_change(self):
        cur = json.load(open(os.path.join(self.sd, f"{SID}.json")))
        zp = os.path.join(self.sd, "templates", cur["source_template"] + ".zip")
        with open(zp, "rb") as f:
            u = self.c.post(f"/api/schemas/{SID}/template-updates", files={"file": (os.path.basename(zp), f, "x")}).json()
        self.assertTrue(u["summary"]["identical"], u["diff"]["changes"][:5])
        self.c.post(f"/api/schemas/{SID}/template-updates/{u['update_id']}/reject")
        self.assertEqual(self.c.get(f"/api/schemas/{SID}/template-updates/{u['update_id']}").json()["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
