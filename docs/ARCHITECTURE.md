# GeoSyntra Bridge — architecture

```
browser (React, web/src)  ──HTTP──▶  api/app.py (FastAPI routes, auth)
                                         │
                                         ▼
                                  jobs/manager.py  JobManager = the parts below, one object per server
                                         │
   ┌──────────────┬──────────────┬───────┴──────┬───────────────┬──────────────┐
 store.py      drawing.py    mapping_ops.py  elevation_ops.py  pipeline.py    editing.py
 folders,      upload, read  layer/field     DEM prepare,      validate,      QA/QC attributes
 state, logs,  DWG/DXF/DGN/  mapping, memory, upload, preview   generate,      (assign.py) and
 locks, expiry ZIP, clean-up required values                    results        Topology geometry
               discovery     reference data                                    (topology_edit.py)
                                         │
                                         ▼
                           engine/conversion.py  run_conversion(doc, schema, mapping)
          cad/ (read, clean, keys, roles) · geometry/ · elevation/ · validation/ · knowledge/ (plant names)
                                         │
                                         ▼
                      gdb/ filegdb_writer (copy of the original template, features appended),
                      row_update (one row by OBJECTID), verify, dmt_qc · reporting/ (Excel, PDF)
```

## Rules the code keeps
* The original ADM template geodatabases are never altered: each delivery starts from a copy.
* The engine is deterministic: the same drawing + mapping + edits give the same result.
  Every user decision is data (`mapping.json`, `feature_edits.json`, `topology_exceptions.json`, `cleanup.json`),
  applied by the engine on each validation — nothing is patched by hand in the output.
* A job lives in `data/jobs/<id>/` (state, mapping, results, preview, work cache, output). JSON files are written
  atomically (temp file + rename); one lock per job serialises edits.
* Long work runs on the job pool (`GSB_WORKERS`); the HTTP layer only reads state.

## Where to change what
| Need | Module |
|---|---|
| a new CAD format / entity type | `cad/reader.py` (`_read_dgn`, `_explode_unhandled`) |
| layer → feature class routing | `mapping/automap.py`, `mapping/asset_category.py`, `cad/roles.py` |
| automatic values / NOT NULL defaults | `validation/attributes.py` (`FIELD_DEFAULTS`, `fill_non_nullable`) |
| element names from the drawing | `knowledge/plants.py` (`fill_names`) |
| elevation | `elevation/engine.py` (drawing levels govern, DEM aligned) |
| topology rules | `validation/topology.py` |
| screens | `web/src/App.tsx` (steps) and `web/src/components/*` |
