#!/usr/bin/env python3
"""
Check a folder of official templates (as published by the municipality) against the templates in use, and stage an
update for every one that changed:

    python scripts/sync_templates.py "C:/Users/<you>/Downloads/All DTM/ADM"  [--apply]

Each zip is matched to its schema by the asset code in its name (ADM_<HL|SL|IR|ITC|RD|STL|SW>_Templates_...).
Identical files (same bytes) are reported and skipped. A changed template is staged exactly like an upload in
Schema & drawing -> Template versions: the change report is printed and the update waits for approval in the app
(or is applied here with --apply).
"""
import argparse
import glob
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from geosyntra_bridge.config import settings  # noqa: E402
from geosyntra_bridge.schema.loader import SchemaRegistry  # noqa: E402
from geosyntra_bridge.schema.template_update import TemplateUpdates  # noqa: E402
from geosyntra_bridge.validation.topology import asset_code  # noqa: E402


QC_NAMES = {"hardlandscaping": "HL", "softlandscaping": "SL", "irrigation": "IR", "itc": "ITC", "roadinfra": "RD",
            "stormwater": "SW", "streetlight": "STL"}


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--apply", action="store_true", help="apply staged updates immediately (admin decision)")
    a = ap.parse_args()
    reg = SchemaRegistry(settings.schemas_dir)
    by_code = {asset_code(s): s for s in reg.all().values() if asset_code(s)}
    tu = TemplateUpdates(reg, settings.data_dir, settings.rules_dir, settings.jobs_dir)
    for z in sorted(glob.glob(os.path.join(a.folder, "*.zip"))):
        name = os.path.basename(z)
        m = re.match(r"^ADM_(HL|SL|IR|ITC|RD|STL|SW)_", name, re.I)
        q = re.match(r"^ADM_([A-Za-z]+)\.zip$", name)          # names used inside the DMT QC tool's templates folder
        code = m.group(1).upper() if m else QC_NAMES.get(q.group(1).lower()) if q else None
        if not code:
            print(f"? {name}: no asset code in the name — skipped")
            continue
        sc = by_code.get(code)
        if not sc:
            print(f"? {name}: no schema for {code}")
            continue
        cur = sc.template_path()
        if cur and md5(cur) == md5(z):
            print(f"= {name}: identical to the template in use ({sc.schema_id} v{sc.version})")
            continue
        u = tu.stage(sc.schema_id, z, name, "sync_templates")
        s = u["summary"]
        if s["identical"]:
            print(f"= {name}: same schema as {sc.schema_id} v{sc.version} (file differs) — staged {u['update_id']}, nothing to apply")
            continue
        print(f"* {name}: {sc.schema_id} v{u['current_version']} -> v{u['new_version']}  classes {s['classes']} "
              f"(+{s['classes_added']}/-{s['classes_removed']}), fields +{s['fields_added']}/-{s['fields_removed']}, "
              f"{s['fields_changed']} changed, values +{s['domain_values_added']}/-{s['domain_values_removed']}, "
              f"{s['breaking']} breaking — update {u['update_id']}")
        for c in u["diff"]["changes"][:40]:
            print(f"     [{c['severity']}] {c['kind']}: {c['object']} — {c['detail']}")
        if a.apply:
            tu.apply(sc.schema_id, u["update_id"], "sync_templates")
            print("     applied (previous version archived)")


if __name__ == "__main__":
    main()
