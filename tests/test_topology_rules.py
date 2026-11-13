"""Template topology rules that need a destination class (esriTRTPointCoveredByLineEndpoint, used 3x in STL and
14x in SW) and the standard's 'Must not have dangles'."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from shapely.geometry import LineString, Point  # noqa: E402

from geosyntra_bridge.cad.model import CadEntity  # noqa: E402
from geosyntra_bridge.engine.model import IssueLog, TargetFeature  # noqa: E402
from geosyntra_bridge.schema.loader import load_schema  # noqa: E402
from geosyntra_bridge.schema.models import TopologyRuleDef  # noqa: E402
from geosyntra_bridge.validation.topology import check_topology  # noqa: E402


def feat(fid, target, geom):
    return TargetFeature(fid=fid, target=target, group_key="g", source=CadEntity(str(fid), "L", "X", "point"), geom=geom)


class TopologyRules(unittest.TestCase):
    def test_sw_point_covered_by_pipeline_endpoint(self):
        schema = load_schema(os.path.join(ROOT, "schemas", "adm_sw_stormwater.json"))
        rules = [r for r in schema.topology_rules if r.origin == "MANHOLECHAMBER"]
        self.assertTrue(any(r.rule == "esriTRTPointCoveredByLineEndpoint" and r.destination == "PIPELINE" for r in rules))
        feats = [feat(1, "PIPELINE", LineString([(0, 0), (50, 0)])),
                 feat(2, "MANHOLECHAMBER", Point(0, 0)),       # on start -> ok
                 feat(3, "MANHOLECHAMBER", Point(50, 0)),      # on end -> ok
                 feat(4, "MANHOLECHAMBER", Point(25, 0)),      # mid-pipe -> error (on line, not on endpoint)
                 feat(5, "MANHOLECHAMBER", Point(10, 7))]      # off the network -> error
        log = IssueLog()
        out = check_topology(schema, feats, log, 0.001)
        bad = sorted(i.feature_id for i in log.items if i.code == "point_covered_by_line_endpoint")
        self.assertEqual(bad, [4, 5])
        self.assertEqual(out["MANHOLECHAMBER: Must be covered by endpoint of PIPELINE"], 2)
        self.assertFalse(any(i.code == "rule_not_evaluated" for i in log.items))

    def test_stl_and_no_destination(self):
        schema = load_schema(os.path.join(ROOT, "schemas", "adm_stl_streetlighting.json"))
        log = IssueLog()
        check_topology(schema, [feat(1, "JUNCTIONBOX", Point(0, 0))], log, 0.001)
        msgs = [i.message for i in log.items if i.code == "point_covered_by_line_endpoint"]
        self.assertEqual(len(msgs), 1)
        self.assertIn("no LIGHTCABLE features delivered", msgs[0])

    def test_dangles_and_point_on_line(self):
        schema = load_schema(os.path.join(ROOT, "schemas", "adm_sw_stormwater.json"))
        schema.topology_rules = [TopologyRuleDef("esriTRTLineNoDangles", "PIPELINE"),
                                 TopologyRuleDef("esriTRTPointCoveredByLine", "INLET", "PIPELINE")]
        feats = [feat(1, "PIPELINE", LineString([(0, 0), (10, 0)])),
                 feat(2, "PIPELINE", LineString([(10, 0), (10, 10)])),
                 feat(3, "INLET", Point(5, 0)), feat(4, "INLET", Point(5, 5))]
        log = IssueLog()
        check_topology(schema, feats, log, 0.001)
        d = sorted(i.location for i in log.items if i.code == "line_no_dangles")
        self.assertEqual(d, [(0.0, 0.0), (10.0, 10.0)])     # the shared vertex (10,0) is connected
        self.assertEqual([i.feature_id for i in log.items if i.code == "point_covered_by_line"], [4])


if __name__ == "__main__":
    unittest.main()
