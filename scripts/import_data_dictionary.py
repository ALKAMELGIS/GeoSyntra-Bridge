#!/usr/bin/env python3
"""Convert DMT data dictionaries (.xlsx) into knowledge/dictionaries/<CODE>.json.

    python scripts/import_data_dictionary.py "C:/.../All DTM"/Data_Dictionary_*.xlsx
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geosyntra_bridge.knowledge.dictionary import knowledge_dir, parse_dictionary  # noqa: E402

for p in sys.argv[1:]:
    d = parse_dictionary(p)
    code = d["code"] or os.path.splitext(os.path.basename(p))[0]
    out = os.path.join(knowledge_dir(), "dictionaries", f"{code}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    n_f = sum(len(c.get("fields", [])) for c in d["classes"].values())
    print(f"{code:4s} {len(d['classes']):3d} classes, {n_f:5d} fields, "
          f"{sum(len(c.get('legacy_names', [])) for c in d['classes'].values()):4d} legacy names  <- {os.path.basename(p)}")
