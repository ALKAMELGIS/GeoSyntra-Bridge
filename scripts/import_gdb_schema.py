#!/usr/bin/env python3
"""
Create / replace a schema definition from an approved template File Geodatabase.

    python scripts/import_gdb_schema.py <template.gdb | template.gdb.zip> <schema_id> "<Display name>" [--discipline X] [--overwrite]

Reads (read-only) feature datasets, feature classes, fields (type/length/alias/nullability/default/domain),
coded + range domains and the spatial reference, and writes schemas/<schema_id>.json. Label rules,
network rules and extra CAD layer-name patterns can then be added to the JSON.
"""
import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from geosyntra_bridge.config import settings  # noqa: E402
from geosyntra_bridge.gdb.schema_import import extract_gdb_zip, schema_from_gdb  # noqa: E402
from geosyntra_bridge.schema.loader import SchemaRegistry  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("gdb")
ap.add_argument("schema_id")
ap.add_argument("display_name")
ap.add_argument("--discipline", default="")
ap.add_argument("--overwrite", action="store_true", help="replace an existing schema file with the same id")
a = ap.parse_args()

src = a.gdb
tmp = None
if src.lower().endswith(".zip"):
    tmp = tempfile.mkdtemp(prefix="gsb_import_")
    src = extract_gdb_zip(src, tmp)
d = schema_from_gdb(src, a.schema_id, a.display_name, a.discipline)
s = SchemaRegistry(settings.schemas_dir).save(d, overwrite=a.overwrite)
print(f"schemas/{s.schema_id}.json: {len(s.layers)} feature classes, {len(s.domains)} domains, "
      f"{len(s.feature_datasets)} feature datasets, EPSG:{s.spatial_reference.epsg}")
