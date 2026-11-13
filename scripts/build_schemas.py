#!/usr/bin/env python3
"""
Builds the seven schema definitions shipped with the converter.

  * adm_hl_hardlandscaping / adm_sl_softlandscaping
        upgraded in place from the JSON that was extracted from the real ADM
        template GDBs: original feature classes and fields are preserved
        exactly; only the bookkeeping columns the earlier converter injected
        (SOURCE_LAYER / MAPPING_STATUS / CONFIDENCE) are removed, fields are
        linked to the domain of the same name, and CAD mapping/label rules
        are added.
  * five utility-network schemas (water, wastewater, stormwater, irrigation,
    street lighting) marked status="draft": complete, working definitions
    that should be replaced by the approved template GDB of each discipline
    through  Schema Manager -> Import from GDB  (or scripts/import_gdb_schema.py).

Re-running this script is idempotent.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMAS = os.path.join(ROOT, "schemas", "examples")   # generic demo schemas; the approved ones are imported templates
INJECTED = {"SOURCE_LAYER", "MAPPING_STATUS", "CONFIDENCE"}

# ---------------------------------------------------------------- helpers
def F(name, ftype="TEXT", alias=None, length=None, domain=None, required=False, default=None):
    d = {"name": name, "field_type": ftype}
    if alias: d["alias"] = alias
    if length: d["length"] = length
    if domain: d["domain"] = domain
    if required: d["required"] = True
    if default is not None: d["default"] = default
    return d


def coded(name, values, ftype="TEXT", description=""):
    if isinstance(values, list):
        values = {v: v for v in values}
    return {"name": name, "domain_type": "coded", "field_type": ftype,
            "description": description, "coded_values": {str(k): v for k, v in values.items()}}


def rng(name, lo, hi, ftype="DOUBLE", description=""):
    return {"name": name, "domain_type": "range", "field_type": ftype,
            "description": description, "min_value": lo, "max_value": hi}


COMMON_TAIL = [
    F("STATUS", length=20, domain="AssetStatus"),
    F("CONDITION", length=20, domain="AssetCondition"),
    F("INSTALL_DATE", "DATE", "Installation Date"),
    F("CONTRACTOR", length=100),
    F("PROJECT_NO", length=50, alias="Project Number"),
    F("DATA_SOURCE", length=30, default="CAD Conversion"),
    F("REMARKS", length=1000),
]

COMMON_DOMAINS = [
    coded("AssetStatus", {"PRO": "Proposed", "UC": "Under Construction", "EXI": "Existing",
                          "ABD": "Abandoned", "REM": "Removed", "UNK": "Unknown"}),
    coded("AssetCondition", ["Very Good", "Good", "Fair", "Poor", "Very Poor", "Unknown"]),
    coded("FlowDirection", {"1": "With digitized", "2": "Against digitized", "0": "Undetermined"}, "SMALLINTEGER"),
]

# reusable label regexes
RX_DIA = r"(?:DN|Ø|⌀|%%C|DIA\.?|D\s*=)\s*(?P<v>\d{2,4})(?:\s*MM)?\b|\b(?P<v2>\d{2,4})\s*MM\b"
RX_LEN = r"\bL\s*[=:]\s*(?P<v>\d+(?:\.\d+)?)\s*M?\b"
RX_SLOPE = r"\b(?:S|SLOPE|GR)\s*[=:]?\s*(?P<v>\d+(?:\.\d+)?)\s*%"
RX_CL = r"\b(?:CL|C\.L\.|COVER\s*LEVEL|RL|TL)\s*[=:]?\s*(?P<v>-?\d+\.\d+)"
RX_IL = r"\b(?:IL|I\.L\.|INV(?:ERT)?\s*(?:LEVEL)?)\s*[=:]?\s*(?P<v>-?\d+\.\d+)"
RX_US_IL = r"\b(?:US|U/S|UP)\s*\.?\s*IL\s*[=:]?\s*(?P<v>-?\d+\.\d+)"
RX_DS_IL = r"\b(?:DS|D/S|DN)\s*\.?\s*IL\s*[=:]?\s*(?P<v>-?\d+\.\d+)"
RX_DEPTH = r"\b(?:DEPTH|D)\s*[=:]\s*(?P<v>\d+(?:\.\d+)?)\s*M?\b"


def material_rule(field, domain_values, targets):
    alts = "|".join(sorted((re.escape(k) for k in domain_values), key=len, reverse=True))
    return {"field": field, "pattern": rf"\b(?P<v>{alts})\b", "targets": targets,
            "value_type": "upper", "description": "pipe material code in label"}


def save(schema):
    path = os.path.join(SCHEMAS, f"{schema['schema_id']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    print(f"  wrote {os.path.relpath(path, ROOT)}  ({len(schema['layers'])} FCs, {len(schema['domains'])} domains)")


def tokens(name):
    s = re.sub(r"^(HL|SL)_", "", name)
    s = re.sub(r"[_\-]+", " ", s)
    return [w.lower() for w in s.split() if len(w) > 1]


# ------------------------------------------------- ADM real schemas upgrade
ADM_SYNONYMS = {
    # extra CAD layer-name evidence for ADM classes (common consultant naming)
    "PALMETREE": [r"palm", r"date[\s_-]*palm", r"nakheel"],
    "TREE": [r"\btree", r"\btr[_-]"],
    "NATIVETREE": [r"native", r"ghaf", r"sidr"],
    "SHRUB": [r"shrub"],
    "HEDGE": [r"hedge"],
    "LAWN": [r"lawn", r"grass", r"turf"],
    "GROUNDCOVER": [r"ground[\s_-]*cover", r"\bgc\b"],
    "PLANTS": [r"plant(?!er)"],
    "SEASONALFLOWER": [r"flower", r"season"],
    "FORESTAREA": [r"forest", r"woodland"],
    "PARK": [r"\bpark\b", r"park[_-]?bnd", r"park[_-]?boundary"],
    "HL_CURBSTONE": [r"curb", r"kerb"],
    "HL_BOLLARD": [r"bollard"],
    "HL_LIGHTING": [r"light", r"lamp", r"lux"],
    "HL_FENCE": [r"fence"],
    "BOUNDARYWALL": [r"boundary[\s_-]*wall", r"bnd[\s_-]*wall"],
    "HL_RETAININGWALL": [r"retaining", r"\brw\b"],
    "BENCH": [r"bench", r"seat"],
    "LITTERBIN": [r"litter", r"\bbin\b", r"waste[\s_-]*bin"],
    "HL_PEDESTRIANAREA": [r"pedestrian", r"walkway", r"footpath", r"paving"],
    "CONCRETESURFACE": [r"concrete", r"conc\b"],
    "TILES": [r"tile", r"interlock", r"paver"],
    "SHADE": [r"shade", r"canopy", r"pergola"],
    "PLAYGROUNDAREA": [r"playground", r"play[\s_-]*area"],
    "PLAY_EQUIPMENT": [r"play[\s_-]*equip"],
    "HL_SIGNBOARD": [r"sign"],
    "CCTV": [r"cctv", r"camera"],
    "GATE": [r"\bgate"],
    "HANDRAIL": [r"handrail", r"railing"],
    "EDGES": [r"edge", r"edging"],
    "SANDYAREA": [r"sand"],
    "STONEGRAVELAREA": [r"gravel", r"stone"],
    "LAKE": [r"lake", r"pond"],
}

ADM_LABEL_RULES = {
    "adm_sl_softlandscaping": [
        {"field": "TREE_HEIGHT", "pattern": r"\b(?:H|HT|HEIGHT)\s*[=:]?\s*(?P<v>\d+(?:\.\d+)?)\s*M?\b",
         "value_type": "float", "description": "tree height label, e.g. H=3.5m"},
        {"field": "UNITID", "pattern": r"\b(?P<v>(?:PT|TR|NT|SH|PL)[-_]?\d{2,6})\b", "value_type": "upper",
         "description": "asset tag e.g. PT-0012"},
    ],
    "adm_hl_hardlandscaping": [
        {"field": "UNITID", "pattern": r"\b(?P<v>(?:BL|BN|LB|LP|SB|CC|GT)[-_]?\d{2,6})\b", "value_type": "upper",
         "description": "asset tag e.g. LP-0101"},
        {"field": "HEIGHT", "pattern": r"\b(?:H|HT|HEIGHT)\s*[=:]?\s*(?P<v>\d+(?:\.\d+)?)\s*M?\b",
         "value_type": "float", "description": "height label"},
    ],
}


def upgrade_adm(path, discipline, label_text_rules):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("source") == "template_gdb":
        print(f"  {os.path.basename(path)} was imported from the approved template GDB — left unchanged")
        return
    if d.get("format") == 2:
        print(f"  {os.path.basename(path)} already v2 — re-linking only")
    dom_by_upper = {x["name"].upper(): x["name"] for x in d["domains"]}
    for x in d["domains"]:
        x.setdefault("field_type", "TEXT")
    for layer in d["layers"]:
        layer["fields"] = [fl for fl in layer["fields"] if fl["name"].upper() not in INJECTED]
        names = {fl["name"].upper() for fl in layer["fields"]}
        for fl in layer["fields"]:
            if not fl.get("domain") and fl["name"].upper() in dom_by_upper and fl["field_type"] == "TEXT":
                fl["domain"] = dom_by_upper[fl["name"].upper()]
        if "REMARKS" not in names:
            layer["fields"].append(F("REMARKS", length=1000))
        layer["category"] = "asset"
        hints = layer.setdefault("classification_hints", {})
        base = [re.escape(layer["name"]), re.escape(re.sub(r"^(HL|SL)_", "", layer["name"]))]
        hints["layer_name_patterns"] = sorted(set(base + ADM_SYNONYMS.get(layer["name"], [])))
        hints["block_name_patterns"] = sorted(set(base + ADM_SYNONYMS.get(layer["name"], [])))
        hints["text_keywords"] = sorted(set(tokens(layer["name"])))
        hints["geometry_types"] = [layer["geometry_type"]]
        if "UNITID" in names:
            layer["id_field"] = "UNITID"
        layer["attribute_aliases"] = {"UNITID": ["ID", "TAG", "ASSET_ID", "ASSETID", "UNIT_ID"],
                                      "UNITDESC": ["DESC", "DESCRIPTION"],
                                      "ZCOORD": ["Z", "LEVEL", "ELEV"]}
    d["format"] = 2
    d["status"] = "approved"
    d["discipline"] = discipline
    d["spatial_reference"] = {"epsg": 32640, "name": "WGS 84 / UTM zone 40N", "verified": False}
    d["xy_tolerance"] = 0.001
    d.setdefault("feature_datasets", [])
    d["label_rules"] = label_text_rules
    d["label_search_radius"] = 4.0
    d["network"] = {"enabled": False}
    d["validation"] = {"min_line_length": 0.01, "min_polygon_area": 0.05, "duplicate_tolerance": 0.001,
                       "unique_fields": {l["name"]: ["UNITID"] for l in d["layers"]
                                         if any(f["name"] == "UNITID" for f in l["fields"])}}
    d["repair"] = {"remove_duplicate_vertices": True, "close_near_closed_rings": True,
                   "ring_closure_tolerance": 0.05, "make_valid_polygons": True}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    print(f"  upgraded {os.path.basename(path)}: {len(d['layers'])} FCs, {len(d['domains'])} domains, "
          f"{sum(1 for l in d['layers'] for fl in l['fields'] if fl.get('domain'))} domain-linked fields")


# --------------------------------------------------- draft network schemas
def layer(name, geom, fds, fields, alias=None, layer_patterns=(), block_patterns=(), keywords=(),
          subtype_field=None, subtypes=None, id_field=None, role=None, aliases=None, length_range=None,
          default_subtype=None, value_ranges=None, weight=None, linetypes=None, position=None):
    out = {"name": name, "alias": alias or name, "geometry_type": geom, "feature_dataset": fds,
           "category": role or "asset", "fields": fields + COMMON_TAIL,
           "classification_hints": {"layer_name_patterns": list(layer_patterns),
                                    "block_name_patterns": list(block_patterns),
                                    "text_keywords": list(keywords), "geometry_types": [geom]}}
    if length_range: out["classification_hints"]["typical_length_range"] = length_range
    if value_ranges: out["classification_hints"]["value_ranges"] = value_ranges
    if weight: out["classification_hints"]["line_weight"] = weight
    if linetypes: out["classification_hints"]["linetype_patterns"] = list(linetypes)
    if position: out["classification_hints"]["network_position"] = position
    elif role == "junction" and geom == "point": out["classification_hints"]["network_position"] = "on_network"
    if subtype_field:
        out["subtype_field"] = subtype_field
        out["subtypes"] = subtypes or []
        out["default_subtype"] = default_subtype
    if id_field: out["id_field"] = id_field
    if role: out["network_role"] = role
    if aliases: out["attribute_aliases"] = aliases
    return out


def base_schema(sid, name, discipline, desc, fds, layers, domains, label_rules, network, relationships,
                unique, radius=6.0):
    return {
        "format": 2, "schema_id": sid, "display_name": name, "version": "1.0-draft",
        "status": "draft", "discipline": discipline, "description": desc,
        "spatial_reference": {"epsg": 32640, "name": "WGS 84 / UTM zone 40N", "verified": False},
        "xy_tolerance": 0.001,
        "feature_datasets": [{"name": n} for n in fds],
        "domains": COMMON_DOMAINS + domains,
        "layers": layers, "label_rules": label_rules, "label_search_radius": radius,
        "network": network, "relationships": relationships,
        "validation": {"min_line_length": 0.05, "min_polygon_area": 0.1, "duplicate_tolerance": 0.001,
                       "unique_fields": unique},
        "repair": {"remove_duplicate_vertices": True, "close_near_closed_rings": True,
                   "ring_closure_tolerance": 0.05, "make_valid_polygons": True,
                   "snap_line_endpoints": True},
    }


PIPE_MAT = {"PVC": "PVC", "UPVC": "uPVC", "HDPE": "HDPE", "PE": "Polyethylene", "DI": "Ductile Iron",
            "GRP": "Glass Reinforced Plastic", "ST": "Steel", "CI": "Cast Iron", "AC": "Asbestos Cement",
            "RCP": "Reinforced Concrete", "VC": "Vitrified Clay", "CU": "Copper"}


def water():
    fds = "WaterNetwork"
    pipe_fields = [F("SUBTYPE", "SMALLINTEGER", "Main Type"), F("PIPE_ID", length=30, alias="Pipe ID"),
                   F("DIAMETER", "INTEGER", "Diameter (mm)", domain="WaterDiameter"),
                   F("MATERIAL", length=10, domain="PipeMaterial"), F("PN_RATING", "INTEGER", "Pressure Rating (bar)"),
                   F("LENGTH", "DOUBLE", "Length (m)"), F("DEPTH", "DOUBLE", "Cover Depth (m)"),
                   F("FROM_NODE", length=30), F("TO_NODE", length=30)]
    return base_schema(
        "water_distribution", "Water Distribution Network", "Water",
        "Potable water transmission/distribution network (pressure system).", [fds],
        [layer("WaterMain", "line", fds, pipe_fields, "Water Main",
               [r"\bw(?:tr|ater)?[\s_-]*(?:main|pipe|pp|line)", r"\bwm\b", r"potable"], [],
               ["water", "wm", "potable", "dn"], "SUBTYPE",
               [{"code": 1, "name": "Transmission", "cad_patterns": [r"trans", r"trunk"]},
                {"code": 2, "name": "Distribution", "cad_patterns": [r"dist"]},
                {"code": 3, "name": "Service", "cad_patterns": [r"serv", r"house[\s_-]*conn"]}],
               "PIPE_ID", "edge", {"PIPE_ID": ["ID", "PIPEID", "TAG"], "DIAMETER": ["DIA", "DN", "SIZE"],
                                    "MATERIAL": ["MAT", "MATL"]}, [1, 2000], default_subtype=2,
               value_ranges={"DIAMETER": [50, 2000]}),
         layer("Valve", "point", fds,
               [F("SUBTYPE", "SMALLINTEGER", "Valve Type"), F("VALVE_ID", length=30), F("DIAMETER", "INTEGER", domain="WaterDiameter"),
                F("NORMAL_POS", length=10, domain="ValvePosition", alias="Normal Position"), F("ELEVATION", "DOUBLE")],
               "Valve", [r"valve", r"\bv(?:lv|alv)?\b", r"\bgv\b", r"\bbfv\b"], [r"valve", r"\bgv", r"bfv", r"\bav\b", r"\bwo\b", r"prv"],
               ["valve", "gv", "bfv", "av", "prv", "wo"], "SUBTYPE",
               [{"code": 1, "name": "Gate Valve", "cad_patterns": [r"\bgv", r"gate"]},
                {"code": 2, "name": "Butterfly Valve", "cad_patterns": [r"bfv", r"butter"]},
                {"code": 3, "name": "Air Valve", "cad_patterns": [r"\bav\b", r"air"]},
                {"code": 4, "name": "Washout", "cad_patterns": [r"\bwo\b", r"wash"]},
                {"code": 5, "name": "PRV", "cad_patterns": [r"prv", r"reduc"]}],
               "VALVE_ID", "junction", {"VALVE_ID": ["ID", "TAG", "VALVEID", "NO"], "DIAMETER": ["DIA", "DN", "SIZE"]},
               default_subtype=1),
         layer("Hydrant", "point", fds, [F("HYDRANT_ID", length=30), F("DIAMETER", "INTEGER", domain="WaterDiameter"),
                                         F("HYD_TYPE", length=20, domain="HydrantType"), F("ELEVATION", "DOUBLE")],
               "Fire Hydrant", [r"hydrant", r"\bfh\b", r"\bhyd"], [r"hydrant", r"\bfh", r"hyd"], ["hydrant", "fh"],
               id_field="HYDRANT_ID", role="junction", aliases={"HYDRANT_ID": ["ID", "TAG", "NO"]}),
         layer("Fitting", "point", fds, [F("FITTING_ID", length=30), F("FIT_TYPE", length=20, domain="FittingType"),
                                         F("DIAMETER", "INTEGER", domain="WaterDiameter")],
               "Fitting", [r"fitting", r"\bfit"], [r"tee", r"bend", r"reducer", r"cap", r"\bfit"], ["tee", "bend", "reducer"],
               id_field="FITTING_ID", role="junction"),
         layer("WaterMeter", "point", fds, [F("METER_ID", length=30), F("SIZE_MM", "INTEGER"), F("ACCOUNT_NO", length=30)],
               "Water Meter", [r"meter", r"\bwmr?\b"], [r"meter", r"\bmtr"], ["meter"], id_field="METER_ID", role="junction"),
         layer("ValveChamber", "polygon", fds, [F("CHAMBER_ID", length=30), F("COVER_LEVEL", "DOUBLE")],
               "Valve Chamber", [r"chamber", r"\bvc\b"], [], ["chamber"], id_field="CHAMBER_ID"),
         layer("PumpStation", "point", fds, [F("STATION_ID", length=30), F("STATION_NAME", length=100), F("CAPACITY_M3H", "DOUBLE")],
               "Pump Station", [r"pump", r"\bps\b"], [r"pump"], ["pump station"], id_field="STATION_ID", role="junction")],
        [coded("PipeMaterial", PIPE_MAT), coded("WaterDiameter", {str(v): f"{v} mm" for v in
             (20, 25, 32, 50, 63, 75, 90, 100, 110, 150, 160, 200, 225, 250, 300, 315, 350, 400, 450, 500, 600, 700, 800, 900, 1000, 1200, 1400, 1600)}, "INTEGER"),
         coded("ValvePosition", {"OPEN": "Open", "CLOSED": "Closed", "PART": "Partially Open"}),
         coded("HydrantType", {"PILLAR": "Pillar", "UG": "Underground", "WALL": "Wall"}),
         coded("FittingType", {"TEE": "Tee", "BEND": "Bend", "RED": "Reducer", "CAP": "End Cap", "CROSS": "Cross", "COUP": "Coupling"})],
        [{"field": "DIAMETER", "pattern": RX_DIA, "value_type": "int", "targets": ["WaterMain", "Valve", "Hydrant", "Fitting"]},
         material_rule("MATERIAL", PIPE_MAT, ["WaterMain"]),
         {"field": "LENGTH", "pattern": RX_LEN, "value_type": "float", "targets": ["WaterMain"]},
         {"field": "PN_RATING", "pattern": r"\bPN\s*(?P<v>\d{1,2})\b", "value_type": "int", "targets": ["WaterMain"]},
         {"field": "PIPE_ID", "pattern": r"\b(?P<v>WM[-_]?\d{1,6})\b", "value_type": "upper", "targets": ["WaterMain"]},
         {"field": "VALVE_ID", "pattern": r"\b(?P<v>(?:GV|BFV|AV|WO|PRV|V)[-_]?\d{1,6})\b", "value_type": "upper", "targets": ["Valve"]},
         {"field": "HYDRANT_ID", "pattern": r"\b(?P<v>(?:FH|HYD)[-_]?\d{1,6})\b", "value_type": "upper", "targets": ["Hydrant"]},
         {"field": "ELEVATION", "pattern": r"\b(?:EL|ELEV|RL)\s*[=:]?\s*(?P<v>-?\d+\.\d+)", "value_type": "float", "targets": ["Valve", "Hydrant"]}],
        {"enabled": True, "flow": "pressure", "edges": ["WaterMain"],
         "junctions": ["Valve", "Hydrant", "Fitting", "WaterMeter", "PumpStation"], "snap_tolerance": 0.10,
         "upstream_node_field": "FROM_NODE", "downstream_node_field": "TO_NODE"},
        [],
        {"WaterMain": ["PIPE_ID"], "Valve": ["VALVE_ID"], "Hydrant": ["HYDRANT_ID"]})


def _gravity_computed(schema, pipe, mh):
    _computed(schema, pipe, {"SLOPE_PCT": "(US_IL - DS_IL) / LENGTH * 100"})
    _computed(schema, mh, {"DEPTH": "COVER_LEVEL - INVERT_LEVEL"})
    return schema


def gravity_schema(sid, name, discipline, desc, fds, pipe, mh, extra_layers, extra_domains, mh_prefix, pipe_prefix, rel_name):
    pipe_fields = [F("PIPE_ID", length=30, alias="Pipe ID"), F("DIAMETER", "INTEGER", "Diameter (mm)", domain="GravityDiameter"),
                   F("MATERIAL", length=10, domain="PipeMaterial"), F("LENGTH", "DOUBLE", "Length (m)"),
                   F("US_IL", "DOUBLE", "Upstream Invert Level"), F("DS_IL", "DOUBLE", "Downstream Invert Level"),
                   F("SLOPE_PCT", "DOUBLE", "Slope (%)", domain="SlopeRange"), F("US_MH_ID", length=30, alias="Upstream MH"),
                   F("DS_MH_ID", length=30, alias="Downstream MH"), F("FLOWDIR", "SMALLINTEGER", "Flow Direction", domain="FlowDirection")]
    mh_fields = [F("MH_ID", length=30, alias="Manhole ID", required=True), F("COVER_LEVEL", "DOUBLE", "Cover Level"),
                 F("INVERT_LEVEL", "DOUBLE", "Invert Level"), F("DEPTH", "DOUBLE", "Depth (m)"),
                 F("MH_DIA", "INTEGER", "Manhole Diameter (mm)"), F("COVER_TYPE", length=20, domain="CoverType")]
    return base_schema(
        sid, name, discipline, desc, [fds],
        [layer(pipe[0], "line", fds, pipe_fields, pipe[1], pipe[2], [], pipe[3], id_field="PIPE_ID", role="edge",
               aliases={"PIPE_ID": ["ID", "TAG", "PIPEID"], "DIAMETER": ["DIA", "DN", "SIZE"], "US_IL": ["USIL", "UIL"], "DS_IL": ["DSIL", "DIL"]},
               length_range=[1, 300], value_ranges={"DIAMETER": [150, 3600]}, weight="heavy"),
         layer(mh[0], "point", fds, mh_fields, mh[1], mh[2], mh[3], mh[4], id_field="MH_ID", role="junction",
               aliases={"MH_ID": ["ID", "TAG", "MHNO", "MH_NO", "NO", "NAME"], "COVER_LEVEL": ["CL", "COVER", "TL", "RL"],
                        "INVERT_LEVEL": ["IL", "INVERT", "INV"], "DEPTH": ["D", "DEP"]})] + extra_layers,
        [coded("PipeMaterial", PIPE_MAT),
         coded("GravityDiameter", {str(v): f"{v} mm" for v in (100, 150, 160, 200, 225, 250, 300, 315, 375, 400, 450, 500, 600, 700, 750, 800, 900, 1000, 1050, 1200, 1350, 1500, 1800, 2000, 2400, 3000)}, "INTEGER"),
         rng("SlopeRange", 0.0, 30.0), coded("CoverType", {"DI": "Ductile Iron", "CONC": "Concrete", "COMP": "Composite"})] + extra_domains,
        [{"field": "DIAMETER", "pattern": RX_DIA, "value_type": "int", "targets": [pipe[0]]},
         material_rule("MATERIAL", PIPE_MAT, [pipe[0]]),
         {"field": "LENGTH", "pattern": RX_LEN, "value_type": "float", "targets": [pipe[0]]},
         {"field": "SLOPE_PCT", "pattern": RX_SLOPE, "value_type": "float", "targets": [pipe[0]]},
         {"field": "US_IL", "pattern": RX_US_IL, "value_type": "float", "targets": [pipe[0]], "priority": 5},
         {"field": "DS_IL", "pattern": RX_DS_IL, "value_type": "float", "targets": [pipe[0]], "priority": 5},
         {"field": "PIPE_ID", "pattern": rf"\b(?P<v>{pipe_prefix}[-_]?\d{{1,6}})\b", "value_type": "upper", "targets": [pipe[0]]},
         {"field": "MH_ID", "pattern": rf"\b(?P<v>{mh_prefix}[-_ ]?\d{{1,6}}[A-Z]?)\b", "value_type": "upper", "targets": [mh[0]]},
         {"field": "COVER_LEVEL", "pattern": RX_CL, "value_type": "float", "targets": [mh[0]]},
         {"field": "INVERT_LEVEL", "pattern": RX_IL, "value_type": "float", "targets": [mh[0]]},
         {"field": "DEPTH", "pattern": RX_DEPTH, "value_type": "float", "targets": [mh[0]]}],
        {"enabled": True, "flow": "gravity", "edges": [pipe[0]], "junctions": [mh[0]] + [l["name"] for l in extra_layers if l.get("network_role") == "junction"],
         "snap_tolerance": 0.10, "upstream_level_field": "US_IL", "downstream_level_field": "DS_IL",
         "junction_level_field": "INVERT_LEVEL", "direction_field": "FLOWDIR",
         "upstream_node_field": "US_MH_ID", "downstream_node_field": "DS_MH_ID", "require_junction_at_ends": True},
        [{"name": rel_name, "origin": mh[0], "destination": pipe[0], "origin_primary_key": "MH_ID",
          "origin_foreign_key": "US_MH_ID", "cardinality": "one_to_many", "forward_label": "Outgoing pipes", "backward_label": "Upstream manhole"}],
        {pipe[0]: ["PIPE_ID"], mh[0]: ["MH_ID"]})


def _computed(schema, layer_name, exprs):
    for l in schema["layers"]:
        if l["name"] == layer_name:
            l["computed_fields"] = exprs
    return schema


def wastewater():
    fds = "SewerNetwork"
    extra = [
        layer("ForceMain", "line", fds, [F("PIPE_ID", length=30), F("DIAMETER", "INTEGER", domain="GravityDiameter"),
                                         F("MATERIAL", length=10, domain="PipeMaterial"), F("LENGTH", "DOUBLE")],
              "Rising / Force Main", [r"force", r"rising", r"\bfm\b", r"\brm\b"], [], ["force main", "rising main"],
              id_field="PIPE_ID"),
        layer("InspectionChamber", "point", fds, [F("IC_ID", length=30), F("COVER_LEVEL", "DOUBLE"), F("INVERT_LEVEL", "DOUBLE")],
              "Inspection Chamber", [r"inspection", r"\bic\b"], [r"\bic\b", r"inspect"], ["ic", "inspection"],
              id_field="IC_ID", role="junction", aliases={"IC_ID": ["ID", "TAG", "NO"]}),
        layer("LiftStation", "point", fds, [F("STATION_ID", length=30), F("STATION_NAME", length=100), F("WET_WELL_LEVEL", "DOUBLE")],
              "Lift / Pumping Station", [r"lift", r"pump", r"\bps\b", r"\bls\b"], [r"pump", r"lift"], ["pumping station"],
              id_field="STATION_ID", role="junction"),
        layer("ServiceConnection", "line", fds, [F("CONN_ID", length=30), F("DIAMETER", "INTEGER", domain="GravityDiameter"),
                                                 F("PLOT_NO", length=30)],
              "House Connection", [r"conn", r"\bhc\b", r"lateral"], [], ["hc", "connection"], id_field="CONN_ID",
              length_range=[0.5, 40], value_ranges={"DIAMETER": [100, 160]}, weight="light"),
    ]
    return _gravity_computed(gravity_schema("wastewater_network", "Wastewater (Sewer) Network", "Wastewater",
                          "Gravity sewer network with manholes, force mains and lift stations.", fds,
                          ("GravitySewer", "Gravity Sewer", [r"sew", r"\bss\b", r"foul", r"\bww\b", r"waste", r"gravity"], ["sewer", "foul", "gravity"]),
                          ("SewerManhole", "Sewer Manhole", [r"\bmh\b", r"manhole", r"s[\s_-]*mh"], [r"\bmh", r"manhole"], ["mh", "manhole"]),
                          extra, [], "(?:SMH|MH|S)", "(?:SP|SW|GS)", "SewerManhole_GravitySewer"), "GravitySewer", "SewerManhole")


def stormwater():
    fds = "StormwaterNetwork"
    extra = [
        layer("Gully", "point", fds, [F("GULLY_ID", length=30), F("GRATE_LEVEL", "DOUBLE"), F("INLET_TYPE", length=20, domain="InletType")],
              "Gully / Inlet", [r"gull", r"inlet", r"catch"], [r"gull", r"inlet", r"\bcb\b", r"catch"], ["gully", "inlet", "cb"],
              id_field="GULLY_ID", role="junction", aliases={"GULLY_ID": ["ID", "TAG", "NO"]}),
        layer("Outfall", "point", fds, [F("OUTFALL_ID", length=30), F("INVERT_LEVEL", "DOUBLE"), F("RECEIVING_BODY", length=100)],
              "Outfall", [r"outfall", r"\bof\b", r"headwall"], [r"outfall", r"headwall"], ["outfall", "headwall"],
              id_field="OUTFALL_ID", role="junction"),
        layer("Culvert", "line", fds, [F("CULVERT_ID", length=30), F("SPAN_MM", "INTEGER"), F("RISE_MM", "INTEGER"), F("LENGTH", "DOUBLE")],
              "Culvert", [r"culvert", r"box"], [], ["culvert"], id_field="CULVERT_ID"),
        layer("OpenChannel", "line", fds, [F("CHANNEL_ID", length=30), F("LINING", length=20, domain="ChannelLining")],
              "Open Channel / Swale", [r"channel", r"swale", r"ditch", r"wadi"], [], ["channel", "swale"], id_field="CHANNEL_ID"),
        layer("RetentionPond", "polygon", fds, [F("POND_ID", length=30), F("CAPACITY_M3", "DOUBLE")],
              "Retention / Detention Pond", [r"pond", r"retention", r"detention", r"basin"], [], ["pond"], id_field="POND_ID"),
    ]
    return _gravity_computed(gravity_schema("stormwater_network", "Stormwater Drainage Network", "Stormwater",
                          "Gravity storm-drainage network with manholes, gullies, culverts and outfalls.", fds,
                          ("StormPipe", "Storm Pipe", [r"storm", r"\bsd\b", r"\bsw\b", r"drain", r"\bstm"], ["storm", "drain", "sd"]),
                          ("StormManhole", "Storm Manhole", [r"\bmh\b", r"manhole", r"sd[\s_-]*mh", r"stm[\s_-]*mh"], [r"\bmh", r"manhole"], ["mh", "manhole"]),
                          extra, [coded("InletType", {"KERB": "Kerb Inlet", "GRATE": "Grated", "COMB": "Combination", "SLOT": "Slot Drain"}),
                                  coded("ChannelLining", {"UNL": "Unlined", "CONC": "Concrete", "RIP": "Rip-rap", "GRASS": "Grass"})],
                          "(?:SDMH|SMH|DMH|MH|D)", "(?:SD|STP|SW)", "StormManhole_StormPipe"), "StormPipe", "StormManhole")


def irrigation():
    fds = "IrrigationNetwork"
    pipe_common = [F("PIPE_ID", length=30), F("DIAMETER", "INTEGER", "Diameter (mm)", domain="IrrDiameter"),
                   F("MATERIAL", length=10, domain="PipeMaterial"), F("LENGTH", "DOUBLE"),
                   F("WATER_SOURCE", length=20, domain="IrrWaterSource"), F("FROM_NODE", length=30), F("TO_NODE", length=30)]
    return base_schema(
        "irrigation_network", "Irrigation Network", "Irrigation",
        "TSE / potable irrigation mainlines, laterals, valves, controllers and zones.", [fds],
        [layer("IrrigationMainline", "line", fds, pipe_common, "Irrigation Mainline",
               [r"irr\w*[\s_-]*main", r"\bim\b", r"main[\s_-]*line", r"\btse\b"], [], ["mainline", "tse"], id_field="PIPE_ID", role="edge",
               aliases={"DIAMETER": ["DIA", "DN", "SIZE"]}, value_ranges={"DIAMETER": [50, 400]}, weight="heavy",
               linetypes=[r"contin", r"bylayer"]),
         layer("IrrigationLateral", "line", fds, pipe_common, "Lateral / Drip Line",
               [r"lateral", r"drip", r"\blat\b", r"sub[\s_-]*main"], [], ["lateral", "drip"], id_field="PIPE_ID", role="edge",
               length_range=[0.5, 200], value_ranges={"DIAMETER": [12, 50]}, weight="light",
               linetypes=[r"dash", r"hidden", r"dot", r"drip", r"center"]),
         layer("IrrigationValve", "point", fds, [F("SUBTYPE", "SMALLINTEGER", "Valve Type"), F("VALVE_ID", length=30),
                                                 F("DIAMETER", "INTEGER", domain="IrrDiameter"), F("ZONE_NO", length=20)],
               "Irrigation Valve", [r"valve", r"\bsv\b", r"\bqcv\b", r"\biv\b"], [r"valve", r"\bsv", r"qcv", r"\biv"], ["valve", "sv", "qcv"],
               "SUBTYPE", [{"code": 1, "name": "Solenoid Valve", "cad_patterns": [r"\bsv", r"solen"]},
                           {"code": 2, "name": "Isolation Valve", "cad_patterns": [r"\biv", r"isol", r"gate"]},
                           {"code": 3, "name": "Quick Coupling Valve", "cad_patterns": [r"qcv", r"quick"]},
                           {"code": 4, "name": "Air Release Valve", "cad_patterns": [r"\barv", r"air"]}],
               "VALVE_ID", "junction", {"VALVE_ID": ["ID", "TAG", "NO"]}, default_subtype=1),
         layer("IrrigationController", "point", fds, [F("CONTROLLER_ID", length=30), F("STATIONS", "INTEGER"), F("POWER_SOURCE", length=20)],
               "Controller", [r"controller", r"\bctrl\b", r"\bclr\b"], [r"control", r"ctrl"], ["controller"], id_field="CONTROLLER_ID",
               position="off_network"),
         layer("IrrigationTank", "point", fds, [F("TANK_ID", length=30), F("CAPACITY_M3", "DOUBLE"), F("WATER_SOURCE", length=20, domain="IrrWaterSource")],
               "Irrigation Tank", [r"tank"], [r"tank"], ["tank"], id_field="TANK_ID", role="junction"),
         layer("IrrigationZone", "polygon", fds, [F("ZONE_NO", length=20), F("IRR_METHOD", length=20, domain="IrrMethod"), F("AREA_M2", "DOUBLE")],
               "Irrigation Zone", [r"zone", r"hydrozone"], [], ["zone"], id_field="ZONE_NO")],
        [coded("PipeMaterial", PIPE_MAT),
         coded("IrrDiameter", {str(v): f"{v} mm" for v in (16, 20, 25, 32, 40, 50, 63, 75, 90, 110, 125, 160, 200, 250, 315)}, "INTEGER"),
         coded("IrrWaterSource", {"TSE": "TSE Water", "POT": "Potable", "MIX": "Mixed", "GW": "Ground Water"}),
         coded("IrrMethod", {"DRIP": "Drip", "SPRAY": "Spray", "ROTOR": "Rotor", "BUBB": "Bubbler", "FLOOD": "Flood"})],
        [{"field": "DIAMETER", "pattern": RX_DIA, "value_type": "int", "targets": ["IrrigationMainline", "IrrigationLateral", "IrrigationValve"]},
         {"field": "DIAMETER", "pattern": r"\b(?:Ø|PE|HDPE)?\s*(?P<v>\d{2,3})\s*(?:MM|Ø)\b", "value_type": "int", "targets": ["IrrigationLateral"]},
         material_rule("MATERIAL", PIPE_MAT, ["IrrigationMainline", "IrrigationLateral"]),
         {"field": "LENGTH", "pattern": RX_LEN, "value_type": "float", "targets": ["IrrigationMainline", "IrrigationLateral"]},
         {"field": "WATER_SOURCE", "pattern": r"\b(?P<v>TSE|POT|MIX|GW)\b", "value_type": "upper", "targets": ["IrrigationMainline", "IrrigationLateral", "IrrigationTank"]},
         {"field": "VALVE_ID", "pattern": r"\b(?P<v>(?:SV|IV|QCV|ARV)[-_]?\d{1,5})\b", "value_type": "upper", "targets": ["IrrigationValve"]},
         {"field": "ZONE_NO", "pattern": r"\bZ(?:ONE)?\s*[-_#]?\s*(?P<v>\d{1,4}[A-Z]?)\b", "value_type": "upper", "targets": ["IrrigationValve", "IrrigationZone"]},
         {"field": "CONTROLLER_ID", "pattern": r"\b(?:CONTROLLER|CTRL|CLR)\s*[-_#]?\s*(?P<v>[A-Z]?\d{1,3})\b", "value_type": "upper", "targets": ["IrrigationController"]},
         {"field": "STATIONS", "pattern": r"\b(?P<v>\d{1,3})\s*(?:ST|STN|STATIONS?)\b", "value_type": "int", "targets": ["IrrigationController"]}],
        {"enabled": True, "flow": "pressure", "edges": ["IrrigationMainline", "IrrigationLateral"],
         "junctions": ["IrrigationValve", "IrrigationTank"], "snap_tolerance": 0.10,
         "upstream_node_field": "FROM_NODE", "downstream_node_field": "TO_NODE"},
        [], {"IrrigationValve": ["VALVE_ID"]}, radius=4.0)


def street_lighting():
    fds = "StreetLighting"
    return base_schema(
        "street_lighting", "Street Lighting & LV Electrical", "Electrical",
        "Street-lighting poles, feeder pillars, LV cables and ducts.", [fds],
        [layer("LightPole", "point", fds, [F("SUBTYPE", "SMALLINTEGER", "Pole Type"), F("POLE_ID", length=30, required=True),
                                           F("POLE_HEIGHT", "DOUBLE", "Height (m)"), F("LUMINAIRE", length=30, domain="LuminaireType"),
                                           F("WATTAGE", "INTEGER", "Wattage (W)"), F("CIRCUIT_NO", length=20), F("FP_ID", length=30, alias="Feeder Pillar")],
               "Light Pole", [r"light", r"lamp", r"\bsl\b", r"pole", r"\blp\b"], [r"light", r"lamp", r"pole", r"\blp"], ["pole", "lp", "light"],
               "SUBTYPE", [{"code": 1, "name": "Single Arm", "cad_patterns": [r"single", r"\b1[\s_-]*arm"]},
                           {"code": 2, "name": "Double Arm", "cad_patterns": [r"double", r"\b2[\s_-]*arm"]},
                           {"code": 3, "name": "High Mast", "cad_patterns": [r"mast", r"\bhm\b"]},
                           {"code": 4, "name": "Bollard Light", "cad_patterns": [r"bollard"]}],
               "POLE_ID", "junction", {"POLE_ID": ["ID", "TAG", "NO", "POLE_NO"], "POLE_HEIGHT": ["H", "HT", "HEIGHT"], "WATTAGE": ["W", "WATT"]},
               default_subtype=1),
         layer("FeederPillar", "point", fds, [F("FP_ID", length=30, required=True), F("RATING_A", "INTEGER", "Rating (A)"), F("SUBSTATION", length=50)],
               "Feeder Pillar", [r"feeder", r"pillar", r"\bfp\b", r"\bdb\b"], [r"feeder", r"pillar", r"\bfp"], ["fp", "feeder pillar"],
               id_field="FP_ID", role="junction", aliases={"FP_ID": ["ID", "TAG", "NO"]}),
         layer("LVCable", "line", fds, [F("CABLE_ID", length=30), F("CABLE_SIZE", length=30, alias="Cable Size"), F("CORES", "SMALLINTEGER"),
                                        F("CIRCUIT_NO", length=20), F("LENGTH", "DOUBLE"), F("FROM_NODE", length=30), F("TO_NODE", length=30)],
               "LV Cable", [r"cable", r"\blv\b", r"\bcbl\b", r"elec"], [], ["cable", "lv", "xlpe", "sqmm"], id_field="CABLE_ID", role="edge"),
         layer("Duct", "line", fds, [F("DUCT_ID", length=30), F("DUCT_DIA", "INTEGER", "Duct Diameter (mm)"), F("WAYS", "SMALLINTEGER")],
               "Duct", [r"duct", r"sleeve", r"conduit"], [], ["duct", "sleeve"], id_field="DUCT_ID"),
         layer("HandHole", "point", fds, [F("HH_ID", length=30), F("COVER_TYPE", length=20)],
               "Hand Hole / Pull Box", [r"hand[\s_-]*hole", r"\bhh\b", r"pull[\s_-]*box", r"jb"], [r"\bhh", r"hand", r"pull"], ["hh", "handhole"],
               id_field="HH_ID", role="junction")],
        [coded("LuminaireType", {"LED": "LED", "HPS": "High Pressure Sodium", "MH": "Metal Halide", "IND": "Induction"})],
        [{"field": "POLE_ID", "pattern": r"\b(?P<v>(?:LP|SL|P)[-_]?\d{1,6}[A-Z]?)\b", "value_type": "upper", "targets": ["LightPole"]},
         {"field": "FP_ID", "pattern": r"\b(?P<v>FP[-_]?\d{1,5})\b", "value_type": "upper", "targets": ["FeederPillar", "LightPole"]},
         {"field": "POLE_HEIGHT", "pattern": r"\b(?:H|HT)\s*[=:]?\s*(?P<v>\d{1,2}(?:\.\d+)?)\s*M\b", "value_type": "float", "targets": ["LightPole"]},
         {"field": "WATTAGE", "pattern": r"\b(?P<v>\d{2,4})\s*W\b", "value_type": "int", "targets": ["LightPole"]},
         {"field": "LUMINAIRE", "pattern": r"\b(?P<v>LED|HPS|MH)\b", "value_type": "upper", "targets": ["LightPole"]},
         {"field": "CABLE_SIZE", "pattern": r"\b(?P<v>\d\s*[Cx×]\s*\d{1,3}(?:\.\d)?\s*(?:SQ\.?\s*MM|MM2|MM²))", "value_type": "upper", "targets": ["LVCable"]},
         {"field": "CIRCUIT_NO", "pattern": r"\b(?:CCT|CIRCUIT|C)\s*[-_#]?\s*(?P<v>\d{1,3}[A-Z]?)\b", "value_type": "upper", "targets": ["LVCable", "LightPole"]},
         {"field": "LENGTH", "pattern": RX_LEN, "value_type": "float", "targets": ["LVCable"]}],
        {"enabled": True, "flow": "none", "edges": ["LVCable"], "junctions": ["LightPole", "FeederPillar", "HandHole"],
         "snap_tolerance": 0.25, "upstream_node_field": "FROM_NODE", "downstream_node_field": "TO_NODE"},
        [{"name": "FeederPillar_LightPole", "origin": "FeederPillar", "destination": "LightPole", "origin_primary_key": "FP_ID",
          "origin_foreign_key": "FP_ID", "cardinality": "one_to_many", "forward_label": "Poles", "backward_label": "Feeder pillar"}],
        {"LightPole": ["POLE_ID"], "FeederPillar": ["FP_ID"]}, radius=5.0)


if __name__ == "__main__":
    os.makedirs(SCHEMAS, exist_ok=True)
    print("Upgrading approved ADM schemas:")
    upgrade_adm(os.path.join(ROOT, "schemas", "adm_hl_hardlandscaping.json"), "Hard Landscaping", ADM_LABEL_RULES["adm_hl_hardlandscaping"])
    upgrade_adm(os.path.join(ROOT, "schemas", "adm_sl_softlandscaping.json"), "Soft Landscaping", ADM_LABEL_RULES["adm_sl_softlandscaping"])
    print("Writing network schemas (draft):")
    for fn in (water, wastewater, stormwater, irrigation, street_lighting):
        save(fn())
    sys.path.insert(0, ROOT)
    from geosyntra_bridge.schema.loader import SchemaRegistry, lint_schema
    reg = SchemaRegistry(SCHEMAS)
    bad = 0
    for sid, s in reg.all().items():
        p = lint_schema(s)
        if p:
            bad += 1
            print(f"  LINT {sid}: {p[:5]}")
    print(f"{len(reg.all())} schemas loaded, {bad} with lint problems")
