"""
Which ADM asset template a CAD layer belongs to, from the words in its name (and its blocks), with the drawing's
file name as a weaker hint. Used when one drawing is mapped against all templates at once: an irrigation layer is
routed to the Irrigation geodatabase only, a planting layer to Soft Landscaping, and so on.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, Optional, Tuple

# word -> (asset code, weight); strong words decide alone, weak ones only add up
WORDS: Dict[str, Tuple[str, float]] = {}


def _add(code: str, strong: Iterable[str], weak: Iterable[str] = ()):
    for w in strong:
        WORDS[w] = (code, 1.0)
    for w in weak:
        WORDS.setdefault(w, (code, 0.4))


_add("IR", ["IRR", "IRRI", "IRRIG", "IRRIGATION", "DRIP", "DRIPLINE", "SPRINKLER", "SPRINKLERS", "BUBBLER", "BUBBLERS",
            "EMITTER", "EMITTERS", "SOLENOID", "RTU", "IRRIGATIONVALVE", "IRMAIN", "IRLAT"],
     ["IR", "LATERAL", "LATERALS", "MAINLINE", "CONTROLLER", "QCV", "ARV", "FLUSH"])
_add("SW", ["STORM", "STORMWATER", "SWD", "DRAINAGE", "GULLY", "GULLIES", "CATCHBASIN", "SOAKAWAY", "OUTFALL",
            "CULVERT", "INLET", "INLETS"],
     ["SW", "DRAIN", "DR", "HEADWALL"])
_add("STL", ["STL", "STREETLIGHT", "STREETLIGHTING", "LIGHTING", "LUMINAIRE", "LAMP", "LAMPS", "FEEDERPILLAR",
             "LIGHTCABLE", "LIGHTPOLE"],
     ["LIGHT", "LIGHTS", "LT", "POLE", "POLES", "ELEC", "CABLE"])
_add("SL", ["SS", "SOFTSCAPE", "SOFTSCAPING", "PLANT", "PLANTS", "PLANTING", "TREE", "TREES", "SHRUB", "SHRUBS",
            "PALM", "PALMS", "GRASS", "GROUNDCOVER", "GC", "TURF", "LAWN", "HEDGE", "CLIMBER", "CLIMBERS", "SUCCULENT"],
     ["SL", "LANDSCAPE", "GREEN", "SOIL"])
_add("HL", ["HS", "HARDSCAPE", "HARDSCAPING", "PAVING", "PAVER", "PAVERS", "BENCH", "BENCHES", "BOLLARD", "BOLLARDS",
            "PERGOLA", "SHADE", "PLAYGROUND", "KERB", "CURB", "EDGING"],
     ["HL", "BIN", "BINS", "FENCE", "WALL", "STEP", "STEPS", "TILE", "TILES", "SEAT"])
_add("RD", ["RD", "ROAD", "ROADS", "CARRIAGEWAY", "ROADMARKING", "MARKING", "MARKINGS", "GUARDRAIL", "ASPHALT",
            "MEDIAN", "SIGNAGE"],
     ["LANE", "SIGN", "SIGNS", "BARRIER"])
_add("ITC", ["ITC", "ITS", "TRAFFIC", "SIGNAL", "SIGNALS", "VMS", "DETECTOR", "TCS"],
     ["LOOP", "CAMERA"])

CODE_OF = {"adm_hl_hardlandscaping": "HL", "adm_sl_softlandscaping": "SL", "adm_ir_irrigation": "IR",
           "adm_rd_roads": "RD", "adm_stl_streetlighting": "STL", "adm_sw_stormwater": "SW", "adm_itc": "ITC"}


def _words(s: str):
    return [w for w in re.split(r"[^A-Z0-9]+", (s or "").upper()) if w and not w.isdigit()]


def categorize(*names: str) -> Tuple[Optional[str], float, Dict[str, float]]:
    """-> (asset code or None, confidence 0..1, scores by code)."""
    sc: Counter = Counter()
    for n in names:
        ws = _words(n)
        for i, w in enumerate(ws + [a + b for a, b in zip(ws, ws[1:])]):     # "STREET LIGHT" -> STREETLIGHT
            hit = WORDS.get(w)
            if hit:
                # CAD layer names lead with the discipline (RD-BOLLARD, IRR-VALVE): the first word counts double
                sc[hit[0]] += hit[1] * (2.0 if i == 0 and len(ws) > 1 else 1.0)
    if not sc:
        return None, 0.0, {}
    (best, b), *rest = sc.most_common()
    second = rest[0][1] if rest else 0.0
    conf = 0.0 if b - second < 0.4 else min(1.0, (b - second) / 1.0)
    return (best if conf > 0 else None), conf, dict(sc)


def drawing_category(file_name: str, layer_cats: Iterable[Optional[str]] = ()) -> Optional[str]:
    """The drawing's own asset type: its file name first (…Softscape…, …IRR…), else the layers' majority."""
    code, conf, _ = categorize(file_name)
    if code and conf >= 0.6:
        return code
    c = Counter(x for x in layer_cats if x)
    if c:
        (best, n), *rest = c.most_common()
        if n >= 2 and (not rest or n >= 2 * rest[0][1]):
            return best
    return None
