import math
import unittest

from geosyntra_bridge.geometry.convert import pipeline_for
from geosyntra_bridge.geometry.crs import detect_source_crs, epsg_from_text, fit_similarity


class Georef(unittest.TestCase):
    def test_fit_recovers_rotation_shift(self):
        th = math.radians(15)
        real = lambda x, y: (x * math.cos(th) - y * math.sin(th) + 236900, x * math.sin(th) + y * math.cos(th) + 2704700)
        pts = [(x, y, *real(x, y)) for x, y in [(10, 10), (60, 10), (110, 70)]]
        f = fit_similarity(pts)
        self.assertAlmostEqual(f["rotation"], 15, 6)
        self.assertAlmostEqual(f["scale"], 1, 9)
        self.assertLess(f["rms"], 1e-6)
        cp = pipeline_for({"source_crs": 32640, "georef": f}, None)
        e, n = cp.point((10, 10))
        self.assertAlmostEqual(e, real(10, 10)[0], 4)
        self.assertAlmostEqual(n, real(10, 10)[1], 4)

    def test_detect_and_parse(self):
        r = detect_source_crs([234000e3, 2701000e3, 235200e3, 2702000e3], None, None, 32640, 0.001)
        self.assertEqual((r["suggested"], r["suggested_scale"]), (32640, 0.001))
        self.assertIsNone(detect_source_crs([100, 100, 500, 400], None, None, 32640, 1.0)["suggested"])
        self.assertEqual(epsg_from_text('<Alias id="UTM84-39N" type="CoordinateSystem">'), 32639)


if __name__ == "__main__":
    unittest.main()
