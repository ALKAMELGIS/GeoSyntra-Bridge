"""Annexes of the DMT As-Built Data Submission Standards V7 (feature attributes) vs the official templates."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from geosyntra_bridge.knowledge.annex import compare, load_annex  # noqa: E402
from geosyntra_bridge.schema.loader import load_schema  # noqa: E402


def S(name):
    return load_schema(os.path.join(ROOT, "schemas", f"{name}.json"))


class Annex(unittest.TestCase):
    def test_parsed_rows(self):
        bench = {f["name"]: f for f in load_annex("HL")["BENCH"]["fields"]}
        self.assertEqual((bench["PROJECTID"]["type"], bench["PROJECTID"]["length"], bench["PROJECTID"]["mandatory"]), ("string", 30, True))
        self.assertIn("SECTOR_OR_COMMUNITY_CODE", bench)          # name wrapped over two lines in the PDF
        self.assertEqual(bench["ROADID"]["type"], "double")       # "Number" above, "(10,2)" below the name
        self.assertFalse(bench["REMARKS"]["mandatory"])
        self.assertEqual(len(load_annex("ITC")), 30)

    def test_compare_hl(self):
        c = compare(S("adm_hl_hardlandscaping"), "HL")
        st = {x["template"]: x["status"] for x in c["classes"] if x["template"]}
        self.assertEqual(st["HL_BOLLARD"], "matched (name differs)")             # BOLLARDS in the standard
        self.assertEqual(st["BENCH"], "matched")
        self.assertIn("CYCLETRACK", [x["annex"] for x in c["classes"] if x["status"] == "standard only"])
        f = [x for x in c["fields"] if x["template"] == "BENCH" and x["field"] == "UNITDESC"]
        self.assertEqual(f[0]["issue"], "text length shorter in template")      # 300 in the standard, 255 in the template

    def test_geometry_difference_and_itc_mandatory(self):
        c = compare(S("adm_sw_stormwater"), "SW")
        self.assertIn("geometry differs", {x["template"]: x["status"] for x in c["classes"] if x["template"]}["SPILLWAY"])
        itc = S("adm_itc")                                                   # no data dictionary for ITC -> annex
        pole = {f.name: f for f in itc.get_layer("TRPOLE").fields}
        self.assertTrue(pole["POLE_TYPE"].contractor_mandatory)
        self.assertEqual(pole["POLE_TYPE"].responsibility, "CONTRACTOR (standard annex)")
        self.assertFalse(pole["OBJECTID"].contractor_mandatory if "OBJECTID" in pole else False)
        hl = S("adm_hl_hardlandscaping")                                    # the dictionary keeps priority
        self.assertEqual(hl.get_layer("BENCH").get_field("ROADID").responsibility, "MUNICIPAL ENGINEER")
        self.assertFalse(hl.get_layer("BENCH").get_field("ROADID").contractor_mandatory)


if __name__ == "__main__":
    unittest.main()
