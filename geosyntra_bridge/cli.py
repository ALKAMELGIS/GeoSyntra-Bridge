#!/usr/bin/env python3
"""
Headless use of the same conversion engine as the web tool.

  python -m geosyntra_bridge.cli list-schemas
  python -m geosyntra_bridge.cli discover --cad drawing.dxf --schema wastewater_network
  python -m geosyntra_bridge.cli convert  --cad drawing.dxf --schema wastewater_network \
        [--source-epsg 32640] [--mapping mapping.json] [--out-dir output]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

from .cad.discovery import discover
from .cad.reader import read_cad
from .config import settings
from .engine.bundle import build_bundle
from .engine.conversion import build_initial_mapping, run_conversion
from .gdb.arcpy_enhancer import apply_subtypes
from .gdb.filegdb_writer import write_filegdb
from .gdb.verify import verify_filegdb
from .reporting.excel import write_excel_report
from .reporting.pdf import write_pdf_report
from .schema.loader import SchemaRegistry, lint_schema


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="geosyntra-bridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-schemas")
    for name in ("discover", "convert"):
        p = sub.add_parser(name)
        p.add_argument("--cad", required=True)
        p.add_argument("--schema", required=True)
        p.add_argument("--source-epsg", type=int)
        p.add_argument("--mapping", help="mapping JSON (as produced by 'discover' / the web tool)")
        p.add_argument("--out-dir", default=os.path.join(settings.root, "output"))
    a = ap.parse_args(argv)
    reg = SchemaRegistry(settings.schemas_dir)

    if a.cmd == "list-schemas":
        for sid, s in sorted(reg.all().items()):
            lint = lint_schema(s)
            print(f"{sid:28s} {s.display_name:40s} {len(s.layers):3d} FCs {len(s.domains):3d} domains "
                  f"[{s.status}]{'  LINT: ' + str(len(lint)) if lint else ''}")
        return 0

    schema = reg.get(a.schema)
    doc = read_cad(a.cad, a.out_dir)
    disc = discover(doc)
    mapping = build_initial_mapping(doc, schema, settings.rules_dir, disc)
    if a.mapping:
        with open(a.mapping, encoding="utf-8") as f:
            user = json.load(f)
        mapping["layers"].update(user.get("layers", {}))
        for fc, flds in user.get("fields", {}).items():
            mapping["fields"].setdefault(fc, {}).update(flds)
        for k in ("label_layers", "source_crs", "unit_scale", "options"):
            if k in user:
                mapping[k] = user[k]
    if a.source_epsg:
        mapping["source_crs"] = a.source_epsg
    os.makedirs(a.out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(a.cad))[0]

    if a.cmd == "discover":
        out = os.path.join(a.out_dir, f"{base}__{schema.schema_id}.mapping.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(mapping, f, indent=2, ensure_ascii=False)
        for k, m in mapping["layers"].items():
            print(f"  {k:40s} -> {str(m['target']):28s} {m['status']:10s} {m['score']}")
        print(f"mapping written to {out} — edit and pass with --mapping")
        return 0

    res = run_conversion(doc, schema, mapping, progress=lambda f, m: print(f"  [{f * 100:5.1f}%] {m}"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    gdb = os.path.join(a.out_dir, f"{base}_{schema.schema_id}_{stamp}.gdb")
    w = write_filegdb(gdb, schema, res.features)
    subs = apply_subtypes(gdb, schema)
    expected = {}
    for f in res.features:
        if not f.failed and f.write and f.geom is not None:
            expected[f.target] = expected.get(f.target, 0) + 1
    ver = verify_filegdb(gdb, schema, expected, w["relationships_created"])
    bundle = build_bundle("cli", os.path.basename(a.cad), res, gdb={"name": os.path.basename(gdb), "subtypes": subs},
                          verification=ver)
    write_excel_report(bundle, gdb[:-4] + "_report.xlsx")
    write_pdf_report(bundle, gdb[:-4] + "_report.pdf")
    s = bundle["summary"]
    print(f"\nGDB: {gdb}\n  imported {s['features_imported']}  failed {s['features_failed']}  issues {len(bundle['issues'])}")
    for c in ver["checks"]:
        print(f"  {'PASS' if c['passed'] else 'FAIL'}  {c['check']}: {c['detail']}")
    return 0 if ver["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
