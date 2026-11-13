# GeoSyntra Bridge — CAD to GDB Schema Converter

A schema-driven web tool that converts AutoCAD drawings (DWG / DXF) into a **new Esri File Geodatabase**
that matches one of the approved schemas. One conversion engine serves every schema; a schema is a JSON
file, so adding the 8th schema is a file drop — no code changes.

```
Upload DWG/DXF ─► Discover layers, blocks, labels ─► Auto-map layers → feature classes (editable, saved as profiles)
   ─► Field mapping (labels / block attributes / constants / geometry) ─► Validate (geometry, domains, labels,
   duplicates, network) ─► Generate new .gdb ─► Re-open & verify ─► Excel + PDF report
```

## Quick start

**Windows:** double-click `start_windows.bat` (first run creates `.venv` and installs the requirements), then the
browser opens at <http://localhost:8600>.
**Linux/macOS:** `./start.sh --open`

Requirements: Python 3.9–3.13. Everything else installs from `requirements.txt` (no ArcGIS licence needed —
GDAL ships inside the `pyogrio` wheel).
**DWG input:** read by the system's own DWG reader (`geosyntra_bridge/cad/dwg`) — AutoCAD 2010 to 2018+
drawings (AC1024 / AC1027 / AC1032), nothing to install. Older drawings: save them as AutoCAD 2010 or newer, or as DXF.
If AutoCAD or Civil 3D is installed on the server, its Core Console is used as a fallback.

## The seven schemas

| schema_id | Name | Source | FCs | Domains |
|---|---|---|---|---|
| `adm_hl_hardlandscaping` | ADM Hard Landscaping (Z40) | approved — extracted from the real ADM template GDB | 58 | 42 |
| `adm_sl_softlandscaping` | ADM Soft Landscaping (Z40) | approved — imported with GDAL from `ADM_SL_Templates_Z40_V10_20012026.gdb` (feature dataset, real domain links, NOT NULL flags, 19 topology rules) | 11 | 27 |
| `water_distribution` | Water Distribution Network | **draft** | 7 | 8 |
| `wastewater_network` | Wastewater (Sewer) Network | **draft** | 6 | 7 |
| `stormwater_network` | Stormwater Drainage Network | **draft** | 7 | 9 |
| `irrigation_network` | Irrigation Network | **draft** | 6 | 7 |
| `street_lighting` | Street Lighting & LV Electrical | **draft** | 5 | 4 |

The two ADM schemas keep their original feature classes and fields exactly; fields were linked to the domain of the
same name, and the bookkeeping columns an earlier script had injected (SOURCE_LAYER, MAPPING_STATUS, CONFIDENCE)
were removed. Their target CRS is set to EPSG:32640 and flagged *unverified* in the UI until confirmed.

The five network schemas are complete working definitions (feature datasets, subtypes, domains, label rules,
network rules, relationships) but are **drafts**: replace each with your approved template GDB:

```
python scripts/import_gdb_schema.py  Approved_Sewer_Template.gdb  wastewater_network  "Wastewater Network" --overwrite
```

The importer reads feature datasets, classes, fields (type, length, alias, nullability, default, domain link), coded
and range domains and the spatial reference. Add label rules / network rules to the JSON afterwards (copy them
from the draft). Admins can also import through the API (`POST /api/schemas/import`, zipped .gdb).
`scripts/build_schemas.py` regenerates the bundled schemas.

## What the engine does

| Stage | Module | Notes |
|---|---|---|
| CAD import | `cad/reader.py` | ezdxf: LINE, (LW)POLYLINE incl. bulges, ARC, CIRCLE, ELLIPSE, SPLINE, POINT, INSERT + ATTRIB, HATCH (+islands), SOLID, 3DFACE, TEXT, MTEXT, MULTILEADER. Corrupt DXF → recover mode or explicit error. Unsupported types are counted, never silently dropped. The upload is stored read-only. |
| Layer discovery & mapping | `cad/discovery.py`, `mapping/automap.py` | Groups by CAD layer × geometry; scores every compatible feature class from layer name, schema regexes, block names, block attribute tags, nearby label keywords/rules, segment length and approved rules. No supporting evidence → *Unmapped*, never guessed. |
| Profiles & learning | `mapping/profiles.py` | Saved per schema; confirmed mappings become approved rules used for future drawings. Overwriting an approved rule needs admin authorisation. |
| Labels | `labels/engine.py` | Regex rules per schema (pipe IDs, DN/Ø diameters, materials, L=, slopes, CL/IL/US IL/DS IL, MH IDs, …). Association by distance + text/line alignment, leader arrow heads respected. Detects unmatched, ambiguous, conflicting and duplicated labels. |
| Geometry & CRS | `geometry/convert.py`, `geometry/crs.py` | Point / polyline / polygon conversion, configurable repair (duplicate vertices, ring closing, make-valid), CRS suggestion from GEODATA/extent, pyproj re-projection, unit scale. |
| Network | `geometry/network.py` | Snapping nodes, T-connections, dangles, orphan junctions, disconnected sub-networks; gravity flow direction from invert levels (reverses pipes drawn against flow), fills US/DS manhole IDs and missing inverts. |
| Validation | `validation/` | Field types/lengths, coded & range domains (code or description, with "did you mean"), subtypes, required fields, duplicate geometry, duplicate IDs, CRS area-of-use. Invalid values are left empty with the original kept in REMARKS. |
| GDB generation | `gdb/filegdb_writer.py` | New .gdb per run via GDAL OpenFileGDB (C API): domains, feature datasets, **all** schema classes, aliases, defaults, domain links, CRS, relationship classes. Subtype definitions are added with `arcpy` when run inside ArcGIS Pro's Python; otherwise subtype codes are written & validated and the report says so. |
| Verification | `gdb/verify.py` | Re-opens the output read-only: classes, geometry types, fields & types, domains & coded values, domain links, CRS, counts, relationships, feature-dataset placement (from the GDB_Items catalog). Success is only reported when all pass. |
| Reports | `reporting/excel.py`, `reporting/pdf.py` | Summary, verification, layer mapping, all issues with suggested corrections, failed features, per-feature REMARKS, label associations, network metrics. |
| Jobs | `jobs/manager.py` | Async thread pool, persisted state/progress, per-job log with tracebacks, temp cleanup (TTL), upload size/type/content checks. |

## The seven approved schemas and the DMT data dictionaries

`schemas/` holds the seven ADM templates (V10, 20-01-2026) imported from the official File Geodatabases:
HL Hard Landscaping, SL Soft Landscaping, IR Irrigation, ITC (ITS), RD Roads, STL Street Lighting, SW Storm Water.
The generic demo network schemas used by the test suite live in `schemas/examples/` (offered only when
`GSB_EXTRA_SCHEMA_DIRS` points there).

`knowledge/dictionaries/<CODE>.json` is generated from the DMT data dictionaries (Excel):

    python scripts/import_data_dictionary.py "C:/.../All DTM"/Data_Dictionary_*.xlsx

The engine uses them as the reference for understanding every layer:
* **ASSET_DESCRIPTION** — definition of each feature class (shown in the UI, supporting meaning for matching);
* **UDM / municipal ASSET_TYPES** — the names the same asset carries in ADM, AAM, DRM and older deliveries
  (`SW_GRATE_CHNL_DRN`, `INFRA_MANHOLECHAMBER`, `SOLENOIDVLVASM`, `Drinking fountain` …). A CAD layer or block with
  one of these names maps to the class directly; a name shared by several classes decides nothing; long plain-word
  lists in one cell (lighting types) are kept as type values, not as class names;
* **one sheet per class** — per field: who fills it (Contractor / Municipal engineer), contractor-mandatory flag,
  description and allowed values, default (`STATUS = Active`) and the field's name in other systems (block-attribute
  aliases: `VALVE_SIZE` → `VALVE_DIAMETER`). Empty contractor-mandatory fields are reported once per class and field
  (Issues, Excel sheet "Contractor Mandatory", PDF); municipal fields are never reported.

**Same asset in two geometries.** HL_TRACK_POLYGON / HL_TRACK_CENTERLINE, RD_TRACK_POLYGON / RD_TRACK_CENTERLINE and
BRIDGE / BRIDGE_POINT share a name but differ in geometry; both are municipal requirements and the templates are
never altered. The drawn geometry routes each CAD group to its class (no ambiguity between twins), and when the
drawing holds the asset in only one geometry the twin is derived from the same objects (polygon → centre point or
centreline), attributes copied, REMARKS "derived from …". If the twin is drawn on its own layer nothing is derived.
Each twin can be switched off per group in Layer Mapping.

ITC has a template but no data dictionary in the delivered set.

## Output = the approved template + DMT Data Quality Control

Every output geodatabase is a **copy of the approved template** (`schemas/templates/*.gdb.zip`, byte-identical to the
templates inside DMT's `ADM_Data_Quality_Control` toolbox) into which the features are appended with GDAL. The
geodatabase topology, every field property (alias, length, nullability, default, domain), editor-tracking fields,
domains, subtypes and relationship classes are therefore the template's own; nothing is recreated or added.
Dictionary defaults (e.g. `STATUS = Active`) fill values only and never change a field definition.

After writing, `gdb/dmt_qc.py` — a GDAL replica of the toolbox's `QCUtility.py` — checks the output the way DMT will:

| DMT QC step | Replica |
|---|---|
| `_compare_schema` — datasets, classes, projection, shape type, fields + properties, domains, topology classes/rules | same comparisons against the template (structural differences fail the verification) |
| `validate_coded_values` | same check and message (ROADCENTRELINE skipped as in the original) |
| `appconfig.json` spatial rules (LIGHTCABLE start/end, STREETLIGHT on a cable end, Solar excepted) | same rules, from `knowledge/qc/appconfig.json`; also run during validation as `dmt_spatial_rule` issues |
| `_validate_geometry` — no curve geometry | same |
| ArcGIS `ValidateTopology` | the template's topology rules are evaluated by GeoSyntra during validation; the final ArcGIS validation remains DMT's |

Errors are listed as in the original (`Dataset | FeatureClass | Error | ObjectID`) in `<name>_QCErrors.csv`, the Excel
sheet "DMT QC" and the PDF.

## DMT As-Built Data Submission Standards V7 — common requirements

| Standard | What the tool does |
|---|---|
| 3.6.1 file naming | Project information (first step) builds `Entity_AssetType_ProjectID_Stage_SubmissionVersion_Date`, e.g. `ADM_HL_241LM2_HO_F001_20261101.gdb`; a re-generation never overwrites an earlier delivery |
| 3.8 metadata | Mandatory metadata and project items (project ID, contract type, names, representatives, dates, costs, contact, usage, security …) → ISO 19115 / 19139 `…_metadata.xml`, with quality (DMT QC, verification) and lineage (CAD source, DEM) |
| 3.8 → PROJECTID | The Project ID fills `PROJECTID` (NOT NULL, contractor-mandatory) in every class where the drawing gives none |
| 3.9 project boundary | `PROJECT_BOUND` polygon(s), topologically clean, from a CAD boundary layer (auto-detected: project boundary, limit of work …) or derived from the delivered features (flagged "verify"); delivered as its own geodatabase because the DMT QC compares the main geodatabase with the template |
| 2.1 / 3.5 Ras Ghumays | Output heights must be Ras Ghumays: accepted from a survey DTM declared in Ras Ghumays, or from global DEM heights plus a benchmark offset you enter; otherwise reported as not compliant (never assumed) |
| 3.4 Table 2 accuracy | The DEM's vertical accuracy (Copernicus ≈ 2.4 m, SRTM ≈ 9.7 m, ALOS ≈ 5 m std. dev.; declared for an uploaded DTM) is compared with the area type's limit (city 0.30 m … desert 2.0 m) and reported |
| Package | `…_submission.zip`: geodatabase, PROJECT_BOUND geodatabase, metadata XML, QCErrors.csv, Excel and PDF reports |

The business tables named in section 4 (ProjectRegistry, ProjectSubmission, AssetRepairInfo, WaterConsumptionREQ) are not
part of the delivered templates and their fields are not defined in the standard; the project information is carried in
the metadata until the official table templates are available.

## Elevation from a DEM — into the template's own elevation fields

CAD → **DEM elevation** → geometry processing → GDB. No field is added and there is no separate screen: the DEM is
chosen in **Field mapping**, above the field table, and its values go into the elevation fields that already exist in
each template class — `ZCOORD` (point Z; line/area mean), `ZCOORD_START` / `ZCOORD_END` (line ends), `GROUND_LEVEL`,
`AVG_GLEVEL` and `COVER_LEVEL` (ground surface). Their source shows as "DEM elevation"; a level written in the drawing
(block attribute / label) always wins over the DEM. `INVERT_LEVEL`, `BOTTOM_LEVEL`, `BASE_LEVEL` and `TOP_LEVEL` are
below-ground or structural levels and never come from a DEM. The Z-enabled geometry of the template classes receives the
same values; the template's geometry definitions are never changed. Validate shows a one-line elevation summary and can
colour the map by elevation.

* **Sources**: Copernicus DEM GLO-30 (AWS open data, EGM2008), NASA SRTM GL1 (AWS terrain tiles, EGM96),
  ALOS AW3D30 (OpenTopography API, free key, EGM96) or an uploaded GeoTIFF (survey DTM / LiDAR; you state its vertical
  reference). Tiles for the drawing extent are downloaded once into `<data>/dem_cache`; `GSB_DEM_URL_<SOURCE>` redirects
  to an internal mirror, `GSB_OPENTOPOGRAPHY_KEY` stores the key. The server needs internet access to these providers.
* **Sampling** at every point, polyline vertex and polygon ring vertex, after transforming to the DEM's CRS; nearest /
  bilinear / bicubic; NoData is detected (interpolation never mixes NoData in silently).
* **CAD Z** is preserved by default (the DEM fills only vertices without CAD Z); *Replace with DEM* is explicit.
  A CAD entity drawn entirely at Z = 0 counts as "no Z".
* **Vertical reference**: DEM heights can be converted (EGM2008 / EGM96 / WGS 84 ellipsoid) with PROJ geoid grids. If the
  grid is not installed the conversion is refused and reported — PROJ would otherwise return the heights unchanged
  without warning. A constant offset (local datum) can be added.
* **Never 0**: vertices without elevation are interpolated along their feature from known vertices; a feature with no
  elevation is blocked (default), excluded, or written with an explicit no-data value you choose (0 is refused). NOT NULL
  elevation fields (ZCOORD …) never receive a placeholder 0.
* **Fields**: ZCOORD (point Z / mean), ZCOORD_START / ZCOORD_END (lines), GROUND_LEVEL / AVG_GLEVEL (DEM ground) — empty
  fields only. Min / max / mean / start / end per feature are in the report.
* **Validation before the DMT QC**: finite Z, slope spikes, values outside the DEM range, CAD-vs-DEM differences; after
  writing, every vertex Z is re-read from the geodatabase and compared with the computed value.
* **Report**: DEM source, resolution, CRS, vertical reference and conversion message, tiles, NoData share, per-feature
  elevation status — UI (Elevation step: source, options, preview map coloured by Z, quality report), Excel sheet
  "Elevation", PDF section.

## Topology rules — standard section 4 vs the template topology

Every rule of the official template topology is evaluated before the geodatabase is written (warnings, with
location), including **Must be covered by endpoint of** (`esriTRTPointCoveredByLineEndpoint`: 3 STL structures on
LIGHTCABLE ends, 14 SW structures on PIPELINE ends). The section-4 tables of the submission standard
(`knowledge/qc/standard_topology.json`) are compared with the template topology for each asset; the report has a
**Topology Rules** sheet/section with both columns and the violations found.

| Asset | Standard says | Template topology has | What the converter does |
|---|---|---|---|
| HL 4.7 | lines: *Must not have dangles* | *Must not intersect* (6 classes) instead | both evaluated; dangles as **information** (standard only) |
| IR 4.5 | lines: *Must not intersect* | *Must not overlap* (1) + *single part* (3) | template rules as warnings; *Must not intersect* as information |
| SW 4.4 | lines: *Must not overlap*; lines covered by endpoint of points | structures covered by PIPELINE endpoints (reverse direction), no line overlap rule | template rules as warnings; line overlap as information |
| STL 4.2 | points covered by line; cable ends on points | points covered by LIGHTCABLE **endpoint** (stricter) | template rule + DMT QC start/end rule (appconfig) |
| ITC 4.3 | polygons closed / edge match / no overlap | no overlap | closed by construction; edge match is between submissions (not evaluated) |
| RD 4.1, SL 4.6 | — | same rule types | template rules |

Standard-only rules never block and never change the template; `options.standard_topology = false` turns them off.
HL_BOLLARD and ROADSIGN have no rule in their templates.

## Feature attributes — the standard's annexes vs the templates

`scripts/import_standard_annex.py` reads the seven annexes of the submission standard (RD, STL, TCS/ITS, SW, IR, SL,
HL — ~9,400 attribute rows: name, type, length, mandatory, description) into `knowledge/annex/<ASSET>.json`.
`scripts/standard_vs_templates.py` writes `docs/Standard_vs_Templates.xlsx`; every job report has the same two sheets
for its schema (*Std Annex - Classes / Fields*), and `GET /api/schemas/{id}/standard-comparison` returns it.

What the comparison shows (template Z40 V10, Jan 2026, is newer than standard V7):
* **Classes** — ITC, STL match 1:1; SW 33/35 (SW_NODE and SPILLWAY differ in geometry); RD 45 matched, 13 only in the
  standard (LRS/roadway network, cycle & jogging tracks…); IR: the standard's 9 valve classes are one IRRIGATION_VALVE
  in the template, VALVECHAMBER is a point in the template and a polygon in the standard; HL 33 matched, 25 newer
  template classes (CCTV, STAGE, WADI…), 9 standard-only (BIN, CYCLETRACK, FOOTPATH…).
* **Fields** — templates add the ADM integration fields (UNITID, COMPKEY, ATSNUMBER, SYNCSTATUS…); the standard lists
  DRAWINGNAME, SEG_RW_ID, LRS_OFFSET, FMEAS, INSPECTED_BY that HL/IR/SL templates no longer have; several text fields
  are shorter in the template (e.g. UNITDESC 300→255, CONDITION 50→10) and ROADID is Number in the standard, Text in
  the template.
* The template is always what is delivered. The annex is used to (a) describe fields that have no dictionary text and
  (b) make annex-mandatory fields contractor-mandatory where no data dictionary says who fills them — this gives ITC,
  which has no dictionary, 246 contractor-mandatory checks; fields the dictionaries give to the municipal engineer
  (ROADID, ROADNAME, LRS…) are never asked of the contractor.

## Delivery: every feature class in its own original geodatabase

The work on a delivery is limited to (1) filling the attribute tables, (2) transferring the CAD geometry into the
corresponding feature classes and (3) placing each feature class in its original geodatabase. Nothing in the
database structure is added, renamed or merged: each geodatabase delivered is a copy of the approved template with
features appended.

* **One asset type** (choose its tile): one geodatabase — the copy of that template.
* **Several asset types** (tile *Several asset types*, schema `adm_all`): map every CAD layer to any feature class of
  the seven ADM templates; at generation the features are grouped by the template each class belongs to and written
  into a copy of **that** template — features from five templates give five geodatabases, each named per standard
  3.6.1 with its own asset code (`ADM_HL_…`, `ADM_IR_…`), each checked on its own against its template by the DMT QC
  replica. Templates that receive no feature are not delivered. The submission package carries all of them; each
  can also be downloaded alone. When a drawing's layers clearly fall into several templates, the upload step
  proposes this option.

## Commercial mode — website, accounts, credits and Stripe

`GSB_AUTH_MODE=accounts` turns the converter into a subscription product:

* **Website** at `/` (English / Arabic): what it does, the standards it follows, plans and prices (read live from
  the plans table), questions. The converter is at `/#/app`.
* **Accounts** — e-mail + password, **Google** and **GitHub** sign-in, password reset by e-mail, sessions in an
  http-only cookie (30 days), login throttling, suspended accounts signed out everywhere. Each customer sees only
  their own jobs.
* **Credits** — every new account gets free trial credits (owner setting, default 50). Uploading, mapping,
  validating and previews are free; a geodatabase costs `10 + 5 × ceil(features / 1000)` credits, charged once per
  job when generation starts; regenerating the same job is free; a failed generation is refunded automatically.
  Insufficient credits → HTTP 402 with a link to top up. Balances only change through the ledger.
* **Stripe** — Checkout for monthly subscriptions (plan credits added on every paid invoice, unused credits carry
  over) and one-off credit packs; the Stripe customer portal for cards, invoices and cancellation; signed webhooks
  (`/api/billing/webhook`, idempotent). Prices shown on the website are set in the owner console; the amount charged is
  the Stripe price whose `price_…` id is attached to the plan.
* **Owner console** (`/#/owner`, owners and administrators): customers (search, credits ±, suspend, role, password
  reset link, sign-out everywhere, delete, invite for enterprise contracts), KPIs (customers, active, subscribers,
  MRR, revenue, geodatabases, credits), plans & prices, payments, activity log, settings (trial credits, prices,
  open/closed sign-up, e-mail confirmation) and the status of every connection with the exact URLs to register.

| Variable | Purpose |
|---|---|
| `GSB_AUTH_MODE=accounts` | enable accounts, credits and billing |
| `GSB_PUBLIC_URL` | public address, e.g. `https://bridge.geosyntra.ae` (OAuth redirects, Stripe return URLs, e-mails) |
| `GSB_OWNER_EMAILS` | e-mails that become owners when they sign up / sign in (or `python scripts/create_owner.py you@x.ae`) |
| `GSB_GOOGLE_CLIENT_ID` / `_SECRET` | Google sign-in — redirect URI `<public>/api/auth/oauth/google/callback` |
| `GSB_GITHUB_CLIENT_ID` / `_SECRET` | GitHub sign-in — callback `<public>/api/auth/oauth/github/callback` |
| `GSB_STRIPE_SECRET_KEY` | `sk_test_…` while testing, `sk_live_…` in production |
| `GSB_STRIPE_WEBHOOK_SECRET` | signing secret of the webhook endpoint (events: checkout.session.completed, invoice.paid, invoice.payment_failed, customer.subscription.created/updated/deleted) |
| `GSB_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `_FROM` | e-mail (reset links, confirmations); without it messages are kept in `data/mail_outbox` |
| `GSB_COOKIE_SECURE=1` | force secure cookies behind a TLS proxy that does not send X-Forwarded-Proto |

All variables with comments and the URLs to register: `deploy/commercial-settings.example`.

Without `accounts` (the default `none`, or `token` / `header` for platform integration) everything works as before
and the converter is the home page.

## When the municipality updates a template

Schema & drawing → **Template versions** (administrators; API `POST /api/schemas/{id}/template-updates`):

1. **Upload** the new template geodatabase (the `.gdb` folder zipped). A candidate schema is built from it and the
   curated CAD knowledge of the current schema is carried over (layer-name / block hints, aliases, label rules,
   validation and repair settings); data dictionary and standard annex enrichment apply automatically.
2. **Review** the change report — classes added / removed, geometry or Z changed, fields added / removed / type /
   length / NOT NULL / domain / default changed, domains and coded values added / removed, topology rules, coordinate
   system — each marked *breaking*, *check* or *info*; **possible renames** (a class or field that disappeared while a
   similar one appeared); and the **impact**: saved mapping profiles and learned CAD rules that use removed
   classes or fields, and open jobs on this schema. Nothing changes at this point.
3. **Apply** (or Reject). The current JSON and template zip are archived in `schemas/_versions/<schema>/<version>/`
   (nothing is deleted); the new template, stored exactly as uploaded, becomes the output container; ticked renames
   are carried into profiles and learned rules (backed up first). The version comes from the template name
   (`…_V11_01112026.gdb` → `11 (01112026)`).
4. Jobs validated on the previous version cannot generate until validated again; CAD layers mapped to a removed class
   are reopened (*TARGET_REMOVED*) for a new target.
5. **Restore** any archived version from the same dialog (the current one is archived first).

## Automatic mapping — the engineers' workflow, step by step

1. **Layer roles** (`cad/roles.py`): every CAD layer is classified as *asset*, *annotation* (text / labels carrying
   attribute values), *base map* (existing roads, plots, buildings, survey, xrefs, switched-off layers) or *sheet*
   (title block, border, viewports, dimensions, grid). Base-map and sheet layers are excluded automatically (recorded
   as `EXCLUDED`, reversible in the UI) unless the drawing key names them; their text is never used as labels.
2. **CAD clean-up before transfer** (`cad/cleaning.py`, `cad/artifacts.py`) — what is done in AutoCAD before moving to
   GIS: zero-length / degenerate objects removed, exact duplicates removed (OVERKILL), line fragments of the same layer
   and style joined into one polyline where no symbol sits on the joint (PEDIT > Join), exploded hatch outlines and
   fragments dropped. Every action is listed in the UI, in the issue log and in the Excel / PDF reports.
3. **Matching by drawing style** (`cad/styles.py`): a layer holding objects drawn differently is split into one mapping
   group per style — lines by linetype + lineweight + colour, points by block symbol, areas by hatch pattern. The
   heavy continuous line of `IRR-PIPE` and its thin dashed lines are mapped separately (mainline vs lateral), the two
   symbols of `IRR-FITTINGS` separately (valve vs controller).
4. **CAD key reader** (`cad/keys.py`), searched in the order engineers use: legend on a layout sheet → key on the
   drawing sheet itself (legend rows, then coded keynotes with leaders) → layers and the drawing. Legend swatches are
   linked to the exact layer *and style* they show.
5. **Reading the drawing like an engineer** (`mapping/automap.py`): besides names and the key, each group is scored on
   - labelled values against the class's typical range (Ø110 → mainline, Ø25 → lateral; schema `value_ranges`),
   - relative line weight / linetype on its layer (schema `line_weight`, `linetype_patterns`),
   - position of symbols on or off the drawn network (valves and manholes sit on the pipes, controllers away from them;
     schema `network_position`),
   - labels that carry the class's asset IDs (`SV-01`, `MH-12`, `CONTROLLER C1`).
6. **Spatial join with existing GIS** (`reference/spatial_join.py`): upload an approved GIS of the same area (zipped
   .gdb or shapefile, GeoPackage, GeoJSON; any CRS — transformed to the drawing). Points within 1 m, lines with ≥ 60 %
   of their length inside a 0.75 m buffer and areas with IoU ≥ 0.5 tell which feature class each CAD group is
   (≥ 80 % of the objects decisive). Attribute values of the matched reference features fill empty fields of the same
   name and are recorded in REMARKS.
7. **Semantic matcher** (`mapping/semantics.py` + editable `knowledge/thesaurus.json`, English + Arabic) maps key
   descriptions, layer and block names to feature classes through concepts, compound names and class domains, and
   fills domain values named in the key (`Precast Concrete Pavers` → `MATERIAL_TYPE = PCC-Pavers`).
8. **Experience**: approved mappings are stored per layer + style *and per key description*.
9. **Completeness gate (the guarantee)**: generation is refused while any asset group is undecided, any NOT NULL field
   blocks features, or validation is out of date.

Measured on the real test drawings: Hardscape-01 — BUILDING (base map) excluded, 11/15 groups automatic, 1 duplicate
and 1 degenerate object removed, 20 fragments joined into 14 polylines, 362 features written, all 11 verification checks
pass; Softscape-04 — 5 duplicates removed, 8/10 groups automatic, 2,050 features written, all checks pass.

## Template-driven rules (learned from real drawings)

* **NOT NULL fields** (from the template) must be filled. Policy `placeholder` (default) writes a neutral domain code
  (N/A, Other, Undefined…), `N/A`, `0` or `1900-01-01` and lists every placeholder in REMARKS and the report;
  domain-refused source values are kept rather than invented. When a coded domain has no neutral code
  (e.g. ADM `DataSource`, `Region`, `IrWaterPoint`, `PlantType`) the feature is blocked until a constant is set in
  Field Mapping (the ✓✓ button applies one value to every mapped class). Policy `reject` blocks instead of placeholders.
* **Topology rules** imported from the template (`topology_rules`) are evaluated during validation: must not overlap,
  must not have gaps, line self-intersection / self-overlap / multipart, point disjoint.
* **Annotation blocks** (block names with *data / tag / label / callout / legend / info*, e.g. `Softscape Data`) carry
  information about features — they are turned into labels, never into assets. Extend with `GSB_ANNOTATION_BLOCKS`.
* **`layer_rules`** extract attributes from CAD layer / block names, e.g. `…-SS-PA-Washingtonia Robusta-Mexican Fan Palm`
  → `SCIENTIFIC_NAME=Washingtonia Robusta`, `COMMON_NAME=Mexican Fan Palm` (layer naming convention SS-PA palms,
  SS-SC shrubs, SS-GC groundcover, SS-TR trees).

## Integration with the platform

* **Mount inside an existing FastAPI app:** `platform_app.mount("/tools/cad2gdb", create_app())`
  (`from geosyntra_bridge.api.app import create_app`). The UI uses relative URLs, so any mount path works;
  or run standalone behind the platform's reverse proxy with `GSB_BASE_PATH=/tools/cad2gdb`.
* **Authentication:** `GSB_AUTH_MODE=header` trusts the user the platform's proxy passes in `X-Forwarded-User`
  (`GSB_USER_HEADER`); `GSB_AUTH_MODE=token` accepts Bearer tokens from `GSB_API_TOKENS="user:token,..."`.
  Admins: `GSB_ADMIN_USERS`. Jobs are visible to their owner only.
* **Storage:** `GSB_DATA_DIR` (jobs), `GSB_SCHEMAS_DIR`, `GSB_RULES_DIR` (approved rules + profiles).
* Other settings: `GSB_MAX_UPLOAD_MB` (500), `GSB_JOB_TTL_HOURS` (72), `GSB_WORKERS` (2), `GSB_CORS_ORIGINS`,
  `GSB_GDAL_LIBRARY` (use a specific GDAL).
* API docs: <http://localhost:8600/api/docs>.

## Headless / batch

```
python -m geosyntra_bridge.cli list-schemas
python -m geosyntra_bridge.cli discover --cad drawing.dxf --schema wastewater_network      # writes editable mapping JSON
python -m geosyntra_bridge.cli convert  --cad drawing.dxf --schema wastewater_network --mapping mapping.json
```

## Development

```
pip install -r requirements.txt pytest
python -m pytest tests -q                  # end-to-end through the HTTP API for all 7 schemas
python scripts/make_samples.py             # regenerate sample drawings (deliberately contain CAD defects)
cd web && npm install && npm run build     # rebuilds the UI into geosyntra_bridge/api/static
```

UI: React 18 + TypeScript + MUI 5 + MUI X DataGrid + OpenLayers (basemap: OpenStreetMap when the CRS is known).

## Known limits

* Subtype *definitions* need arcpy (GDAL cannot create them); attribute rules, topology rules, geometric
  networks / utility networks and annotation classes are not created.
* Many-to-many relationship classes are skipped (reported).
* Output geometry is 2D; elevations go to Z/level fields.
* The template's geodatabase topology object itself is not recreated (GDAL limitation); its rules are checked instead.
* The five network schemas are drafts until replaced by the approved templates; tune label rules on real drawings.
