"""Parse the feature-attribute annexes of DMT_As_Built_Data_Submission_Standards_V7.pdf (pdftotext -layout) into
knowledge/annex/<ASSET>.json: {feature: {geometry, page, fields: [{name, type, length, mandatory, description}]}}."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

ANNEX_RX = re.compile(r'^\s*Annex\s+“([A-Z/]+)”\s*:\s*Feature', re.I)
FEAT_RX = re.compile(r'^\s*Feature\s+Name\s*:\s*([A-Za-z0-9_ ]+?)\s*\(([^)]+)\)')
FIELD_RX = re.compile(r'^([A-Z][A-Z0-9_]{1,})(?:\s{2,}|\s*$)(.*)$')
TYPE_RX = re.compile(r'\b(Text|Double|Long\s*Integer|Long|Short\s*Integer|Short|Integer|Date|Number|Float|GUID|Global\s*ID|Blob|Raster|Object\s*ID)\b\s*(\(\s*[\d, ]+\s*\))?', re.I)
PAGE_RX = re.compile(r'Page\s+(\d+)\s+of\s+\d+')
ASSET = {"RD": "RD", "STL": "STL", "TCS/ITS": "ITC", "SW": "SW", "IR": "IR", "SL": "SL", "HL": "HL"}
SKIP = {"Attribute", "Feature", "Page"}


def norm_type(t: str) -> str:
    t = re.sub(r"\s+", " ", t.strip().lower())
    return {"long integer": "long", "long": "long", "short integer": "short", "short": "short", "integer": "long",
            "number": "double", "float": "double", "double": "double", "text": "string", "date": "date",
            "guid": "guid", "global id": "globalid", "object id": "oid", "blob": "blob", "raster": "raster"}.get(t, t)


def parse(txt: str):
    out, asset, feat, page, last = {}, None, None, None, None
    lines = txt.splitlines()
    started = False
    for i, ln in enumerate(lines):
        m = PAGE_RX.search(ln)
        if m:
            page = int(m.group(1)) + 1
        m = ANNEX_RX.match(ln)
        if m and i > 2000:
            asset, feat, started = ASSET.get(m.group(1).upper()), None, True
            out.setdefault(asset, {})
            continue
        if not started or not asset:
            continue
        m = FEAT_RX.match(ln)
        if m:
            name = m.group(1).strip().replace(" ", "")
            feat = {"geometry": m.group(2).strip().lower(), "page": page, "fields": []}
            key = name if name not in out[asset] else f"{name}#{feat['geometry']}"
            out[asset][key] = feat
            last = None
            continue
        if feat is None:
            continue
        name_m = re.match(r'^ {0,2}([A-Z][A-Z0-9_]*)(?=\s{2,}|\s*$)', ln)
        rest = ln[name_m.end():] if name_m else ln
        mand = re.search(r'(?:^|\s)(Yes|No|YES|NO)(?:\s|$)', rest[:75])
        if mand and mand.start() > 45 and not name_m:
            mand = None
        tm = TYPE_RX.search(rest[: mand.start()] if mand else (rest[:40] if name_m else ln[:48]))
        lm = re.search(r'\(\s*(\d+)\s*(?:,\s*\d+\s*)?\)', rest[: mand.start() if mand else 40])
        if name_m and name_m.group(1) not in SKIP:
            nm = name_m.group(1)
            if len(nm) < 2 and not (last is not None and last.get("_wrap")):
                continue
            if last is not None and last.get("_wrap") and i - last["_line"] <= 3 and not mand:
                last["name"] += nm                       # SECTOR_OR_COMMUNITY_COD / E
                last["_wrap"] = False
                if tm and not last["type"]:
                    last["type"] = norm_type(tm.group(1))
                continue
            if not tm and i > 0 and not re.match(r'^ {0,2}[A-Z][A-Z0-9_]', lines[i - 1]):
                tm = TYPE_RX.search(lines[i - 1][:48])       # "Number" above, "(10,2)" below the name
            last = {"_line": i, "name": nm, "type": norm_type(tm.group(1)) if tm else None,
                    "length": int(lm.group(1)) if lm else None,
                    "mandatory": (mand.group(1).lower() == "yes") if mand else None,
                    "description": rest[mand.end():].strip() if mand else "",
                    "_wrap": not mand and len(nm) >= 18}
            feat["fields"].append(last)
        elif last is not None and (mand or tm) and i - last["_line"] <= 3:
            # type / mandatory written on the line below (or above) the name
            if last["mandatory"] is None and mand:
                last["mandatory"] = mand.group(1).lower() == "yes"
                last["description"] = last["description"] or rest[mand.end():].strip()
                last["_line"] = i
            if not last["type"] and tm:
                last["type"] = norm_type(tm.group(1))
            if not last["length"] and lm:
                last["length"] = int(lm.group(1))
    return out


def main(pdf: str, out_dir: str):
    txt = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True, check=True).stdout
    data = parse(txt)
    for feats in data.values():
        for v in feats.values():
            for f in v["fields"]:
                f.pop("_line", None); f.pop("_wrap", None)
    os.makedirs(out_dir, exist_ok=True)
    for asset, feats in data.items():
        with open(os.path.join(out_dir, f"{asset}.json"), "w", encoding="utf-8") as f:
            json.dump({"_source": os.path.basename(pdf), "asset": asset, "features": feats}, f, indent=1, ensure_ascii=False)
        print(asset, len(feats), "features", sum(len(v["fields"]) for v in feats.values()), "fields")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge", "annex"))
