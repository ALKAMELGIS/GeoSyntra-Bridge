export type Geom = "point" | "line" | "polygon";

export interface SchemaSummary {
  schema_id: string;
  display_name: string;
  version: string;
  source_template?: string;
  status: "approved" | "draft" | string;
  discipline: string;
  description: string;
  spatial_reference: { epsg: number | null; name: string; verified: boolean; details?: CrsInfo | null };
  feature_dataset_count: number;
  feature_class_count: number;
  domain_count: number;
  subtype_layers: number;
  label_rule_count: number;
  relationship_count: number;
  network_enabled: boolean;
  network_flow: string;
  geometry_breakdown: Record<Geom, number>;
}

export interface SchemaField {
  name: string; alias?: string | null; type: string; length?: number | null;
  domain?: string | null; required: boolean; nullable?: boolean; default?: unknown;
  description?: string; responsibility?: string; contractor_mandatory?: boolean; allowed_values?: string[];
}
export interface SchemaLayer {
  name: string; alias?: string | null; geometry_type: Geom; feature_dataset?: string | null;
  field_count: number; id_field?: string | null; network_role?: string | null;
  subtype_field?: string | null; subtypes: { code: number; name: string }[];
  description?: string; legacy_names?: string[]; dictionary?: string | null; template?: string | null; asset?: string | null;
  label_fields: string[]; fields: SchemaField[];
}
export interface SchemaDomain {
  name: string; type: string; field_type: string; values: Record<string, string> | null;
  min?: number | null; max?: number | null;
}
export interface SchemaDetail extends SchemaSummary {
  feature_datasets: string[];
  layers: SchemaLayer[];
  domains: SchemaDomain[];
  label_rules: { field: string; pattern: string; targets: string[]; description: string }[];
  network: Record<string, unknown>;
  relationships: Record<string, unknown>[];
  lint: string[];
}

export interface CrsCandidate extends CrsInfo {
  scale: number; center: number[]; footprint: number[][]; size_m: number; region: string; score: number; why: string[];
}
export interface CrsPreset {
  id: string; name: string; created: number; extent: number[] | null;
  source_crs: number | null; unit_scale: number; georef: GeorefFit | null; transform_op: string | null;
}
export interface GeorefPoint { x: number; y: number; E: number; N: number; text?: string; residual?: number }
export interface GeorefFit { scale: number; rotation: number; offset: number[]; residuals?: number[]; rms: number | null }
export interface TransformOption { id: string | null; name: string; accuracy: number | null; available: boolean; best: boolean }
export interface CrsSuggestion {
  v?: number; suggested: number | null; suggested_scale?: number; candidates: (CrsInfo & Partial<CrsCandidate>)[];
  confidence: string; reasons: string[]; extent?: number[]; preset?: { id: string; name: string };
}
export interface CrsInfo { epsg: number; name: string; is_geographic: boolean; units: string; area_of_use: number[] | null }

export interface Capabilities {
  version: string; user: string; admin: boolean; filegdb: boolean;
  gdal: { available: boolean; version?: string; error?: string };
  dwg: { available: boolean; converter: string | null };
  arcpy: boolean; max_upload_mb: number; auth_mode: string; update_pending?: boolean;
}

export type JobStatus = "queued" | "running" | "ready" | "validated" | "completed" | "failed";
export interface JobState {
  job_id: string; owner: string; created_at: string; updated_at: string;
  schema_id: string; schema_name: string;
  file: { name: string; size: number; format: string };
  extra_files?: { name: string; size: number; format: string }[];
  stage: string; status: JobStatus; progress: number; message: string;
  error: { stage: string; message: string } | null;
  outputs: { gdb_zip?: string; xlsx?: string; pdf?: string; qc_csv?: string; package?: string; metadata?: string; boundary_zip?: string };
}

export interface DiscoveryGroup {
  key: string; layer: string; geometry: Geom; count: number;
  entity_types: Record<string, number>; blocks: { name: string; count: number }[];
  attribute_tags: string[]; avg_length: number | null; has_circles: boolean;
  style?: string; role?: LayerRole;
}
export type LayerRole = "asset" | "annotation" | "basemap" | "sheet";
export interface CleaningAction { action: string; count: number; layers: Record<string, number>; handles: string[]; note: string }
export interface RefLayer { name: string; geometry: Geom; count: number; target: string | null; how: string; epsg: number | null }
export interface ReferenceInfo {
  file: string; overlap: boolean; matched_entities: number;
  summary: { source: string; features: number; layers: RefLayer[]; warnings: string[] };
  groups: Record<string, { total: number; classes: Record<string, number> }>;
}
export interface DiscoveryLayer {
  name: string; color: number | null; entity_count: number; kinds: Record<string, number>;
  blocks: { name: string; count: number }[]; attribute_tags: string[]; text_samples: string[]; text_count: number;
  is_off: boolean; is_frozen: boolean; role?: LayerRole; role_reason?: string; artifacts?: number;
}
export interface Discovery {
  source_name: string; dxf_version: string; units: string; insunits: number; converted_from_dwg: boolean;
  entity_count: number; feature_entity_count: number; text_count: number; layer_count: number;
  extent: number[] | null; skipped_types: Record<string, number>; recovered_types?: Record<string, number>; read_warnings: string[];
  geodata_epsg: number | null; layers: DiscoveryLayer[]; groups: DiscoveryGroup[];
  artifacts?: Record<string, number>; paper_entity_count?: number;
  cleaning?: CleaningAction[]; layer_roles?: Record<string, { role: LayerRole; reason: string }>;
  key_info?: { level: string; label: string; entries: number; linked_entries: number;
               tried: { level: string; found: number; sheets?: string[] }[] };
  schema_ranking?: { schema_id: string; display_name: string; coverage: number; mapped_groups: number; groups: number; templates?: Record<string, number> }[];
}

export interface LayerMap {
  target: string | null; role: "feature" | "ignore"; status: string; confidence: string;
  score: number; evidence: string[]; alternatives: { target: string; score: number }[];
  subtype: number | null; convert?: string | null;
  keys?: { code: string; description: string }[];
  attributes?: Record<string, { value: string; label: string; from: string; match: string }>;
  suggested_role?: "ignore"; suggestion_reason?: string; layer_role?: LayerRole;
  twins?: { target: string; convert: string; enabled: boolean; reason: string }[];
}
export interface KeyEntry {
  code: string; description: string; source: string; occurrences: number;
  layers: Record<string, number>; best_layer: string | null;
}
export interface Completeness {
  complete: boolean; validation_current: boolean; blocking_errors: string[];
  open_groups: { group: string; status: string; target: string | null; suggested_role?: string; reason: string }[];
}
export interface FieldSpec { source: string; value?: unknown; fallback?: string | null; user?: boolean }
export interface Mapping {
  schema_id: string; source_crs: number | null; unit_scale: number; drawing_units: string;
  drawing_units_scale_hint: number;
  crs_suggestion: CrsSuggestion;
  georef?: (GeorefFit & { points?: GeorefPoint[] }) | null;
  transform_op?: string | null;
  layers: Record<string, LayerMap>;
  keys?: KeyEntry[];
  reference?: ReferenceInfo;
  elevation?: ElevationOptions;
  dem?: DemMeta;
  project?: Record<string, any>;
  label_layers: string[] | null;
  fields: Record<string, Record<string, FieldSpec>>;
  options: {
    repair: Record<string, boolean | number>; label_search_radius: number;
    snap_tolerance: number | null; duplicate_policy: "flag" | "skip"; non_nullable_policy?: "placeholder" | "reject";
  };
}

export interface Issue {
  id: number; severity: "error" | "warning" | "info"; category: string; code: string;
  message: string; suggestion: string; target_layer: string | null; feature_id: number | null;
  objectid?: number | null; field: string | null;
  source: { handle?: string; layer?: string; type?: string; text?: string } | null;
  location: number[] | null; lonlat?: number[] | null; related: number[];
}
export interface FeatureRow {
  fid: number; objectid: number | null; target: string; subtype: number | null;
  status: "ok" | "failed" | "skipped"; failure: string | null; layer: string; handle: string;
  entity_type: string; labels: string[]; remarks: string[]; issue_count: number;
}
export interface Summary {
  features_total: number; features_imported: number; features_failed: number;
  features_skipped_duplicates: number; features_with_missing_attributes: number;
  duplicates: number; unmatched_labels: number; ambiguous_labels: number; uncertain_mappings: number;
  unmapped_cad_entities: number; geometry_topology_errors: number; network_issues: number; domain_errors: number;
  issues_by_category: Record<string, number>; issues_by_severity: Record<string, number>;
  features_by_class: Record<string, number>; labels: Record<string, number>;
  network: Record<string, unknown>; source_epsg: number | null; target_epsg: number | null;
}
export interface VerificationCheck { check: string; passed: boolean; detail: string; severity: string }
export interface Results {
  job_id: string; generated_at: string; source_file: string;
  schema: { schema_id: string; display_name: string; version: string; status: string };
  summary: Summary; mapping_rows: Record<string, unknown>[]; issues: Issue[]; features: FeatureRow[];
  labels: { stats: Record<string, number>; rows: Record<string, unknown>[]; unrecognised_samples: string[] };
  gdb: { name: string; zip: string; size: number; counts: Record<string, number>; domains: number;
         relationships: string[]; gdal: string; subtypes: { applied: boolean; note?: string } } | null;
  verification: { passed: boolean; checks: VerificationCheck[] } | null;
  elevation?: ElevationReport;
  dmt_qc?: { passed: boolean; template: string; note: string; by_check: Record<string, number>; topology_findings: number;
             errors: { check: string; Dataset: string; FeatureClass: string; Error: string; ObjectID: number | null }[] };
}
export interface FeatureDetail {
  fid: number; target: string; subtype: number | null; status: string; failure: string | null;
  attributes: Record<string, unknown>; remarks: string[]; labels: string[];
  source: Record<string, unknown>; geometry_type: string | null; issues: Issue[];
}
export interface GeoJSON { type: "FeatureCollection"; crs_is_geographic: boolean; truncated: boolean; features: any[] }

export interface SubmissionField { key: string; label: string; kind: string; m: boolean; group: string; help?: string; placeholder?: string; choices?: Record<string, string> }
export interface SubmissionStatus {
  info: Record<string, any>; fields: SubmissionField[]; name: string | null; missing: string[]; errors: string[];
  boundary_candidates: { layer: string; polygons: number; lines: number }[];
}
export interface ElevationOptions {
  enabled?: boolean; source?: string | null; mode?: "fill_missing" | "replace";
  interpolation?: "nearest" | "bilinear" | "bicubic"; vertical_target?: string; vertical_offset?: number;
  cad_zero_is_missing?: boolean; interpolate_gaps?: boolean; missing_policy?: "block" | "exclude" | "nodata_value";
  nodata_value?: number | null; rg_offset?: number | null; dem_accuracy_m?: number | null; fill_fields?: boolean; ground_fields?: boolean; max_slope_pct?: number; cad_dem_tolerance?: number;
}
export interface DemMeta {
  source: string; label: string; provider: string; surface: string; license: string; vertical: string; vertical_label: string;
  crs: string; pixel_size: number[]; resolution_m: number[]; tiles: { tile: string; status: string }[]; bbox_ll: number[];
  min: number | null; max: number | null; mean: number | null; nodata_pct: number | null;
}
export interface DemSourceInfo {
  label: string; provider: string; surface: string; resolution: string; resolution_m: number | null; vertical: string | null;
  vertical_label: string; needs_key: boolean; tiled: boolean; license: string;
}
export interface ElevationReport {
  enabled: boolean; status: string; features: number; by_status: Record<string, number>; vertices: Record<string, number>;
  vertical?: { source: string; result: string; message: string; offset_m: number } | null; interpolation?: string;
  z_range?: { min: number; max: number; mean: number } | null; missing_features?: number; missing_by_class?: Record<string, number>;
  missing_policy?: string; by_class?: Record<string, Record<string, number>>;
  validation?: { spikes: number; out_of_range: number; non_finite: number; cad_dem_difference_features: number };
  ras_ghumays?: boolean | null; accuracy?: { area_type: string; required_std_m: number; dem_std_m: number | null; meets: boolean | null };
  drawing_levels?: { control_points: number; paired_with_dem: number; offset_m: number; spread_m: number; interpolated_vertices: number; radius_m: number };
  dem?: DemMeta | null;
}

export interface TemplateChange { kind: string; object: string; detail: string; severity: "info" | "warning" | "breaking"; layer?: string; field?: string }
export interface TemplateUpdate {
  update_id: string; schema_id: string; status: "pending" | "applied" | "rejected"; uploaded_by: string; uploaded_at: string;
  file: string; template: string; current_template: string; current_version: string; new_version: string;
  summary: Record<string, any>; decided_by?: string; decided_at?: string; archived_as?: string;
  renames_applied?: Record<string, number>;
  diff?: { changes: TemplateChange[]; counts: Record<string, number>; breaking: number; identical: boolean;
           renames: { classes: { old: string; new: string; score: number; shared_fields: number }[];
                      fields: { layer: string; old: string; new: string; name_similarity: number }[] } };
  impact?: { learned_rules: { fingerprint: string; target: string }[]; profiles: { profile: string; classes: string[]; fields: string[] }[];
             jobs: { job_id: string; file: string; status: string }[] };
}
export interface TemplateVersion { version: string; template: string; archived_at: string; archived_by: string; reason: string; folder: string; layers: number; domains: number }

export interface AuthUser {
  id: number | null; email: string; name: string; company?: string; role: "owner" | "admin" | "user"; status: string;
  email_verified?: boolean; avatar_url?: string; credits: number | null; plan_id?: string | null;
  subscription_status?: string | null; current_period_end?: string | null; cancel_at_period_end?: boolean;
  created_at?: string; last_login_at?: string | null; signup_method?: string; has_password?: boolean;
  geodatabases?: number; credits_used?: number;
}
export interface AuthConfig {
  mode: string; providers: Record<string, string>; signup_open: boolean; trial_credits: number; stripe: boolean;
  company_name: string; support_email: string; pricing: { generate_base_credits: number; generate_credits_per_1000_features: number };
}
export interface Plan {
  id: string; name: string; kind: "subscription" | "pack" | "enterprise"; price_amount: number; currency: string; interval: string;
  credits: number; active: boolean; sort: number; highlight: boolean; description: string; features: string[];
  purchasable?: boolean; stripe_price_id?: string | null;
}
export interface LedgerRow { id: number; delta: number; balance_after: number; kind: string; ref: string | null; note: string; created_at: string }
export interface Payment { id: number; user_id: number; stripe_id: string; kind: string; plan_id: string; amount: number; currency: string; description: string; created_at: string; email?: string }
export interface PurchaseRequest {
  id: number; user_id: number; plan_id: string; status: "pending" | "approved" | "rejected" | "cancelled"; reference: string; note: string;
  amount: number; currency: string; created_at: string; decided_at: string | null; decision_note: string; email?: string; name?: string;
}
export interface BillingStatus {
  credits: number; unlimited?: boolean; plan: Plan | null; subscription_status: string | null; current_period_end: string | null; cancel_at_period_end: boolean;
  stripe: boolean; has_customer: boolean; ledger: LedgerRow[]; payments: Payment[];
  pricing: { generate_base_credits: number; generate_credits_per_1000_features: number };
  payment_instructions?: string; support_email?: string;
}

// ---- map-based feature assignment
export type AssignState = "completed" | "incomplete" | "error" | "skipped";
export interface AssignField {
  name: string; alias: string; type: string; length: number | null; required: boolean; not_null: boolean; read_only: boolean;
  description: string; domain: [string, string][] | null; range: [number | null, number | null] | null;
}
export interface AssignRow {
  fid: number; objectid: number | null; target: string; layer: string; handle: string;
  state: AssignState; missing: string[]; edited: boolean; saved_at?: string | null;
}
export interface AssignOverview {
  features: AssignRow[];
  classes: Record<string, { alias: string; geometry: string; dataset: string | null; fields: AssignField[] }>;
  counts: Record<AssignState | "total", number>;
  gdb: { generated: boolean; names: string[] };
}
export interface AssignDetail extends FeatureDetail {
  state: AssignState; missing: string[]; edited: boolean; placeholders: string[]; objectid: number | null;
  gdb: { name: string; objectid: number; values?: Record<string, string | null>; error?: string } | null;
}
export interface AssignSaved {
  ok: boolean; fid: number; objectid: number | null; target: string; values: Record<string, unknown>;
  gdb: { objectid: number; fields: string[]; geometry_unchanged: boolean; gdb: string } | null;
  pending_generate: boolean; state: AssignState; missing: string[]; edited: boolean;
}

// ---- topology step
export interface TopologyError {
  id: number; key: string; rule: string; code: string; severity: string; message: string; class: string | null;
  fid: number | null; handle: string | null; related: number[]; related_class: string | null;
  lonlat: number[] | null; shape: any | null; exception: boolean; standard: boolean; action?: string | null; no_fix?: string | null;
  guide?: { why: string; how: string }; tried?: string | null;
}
export interface AutofixReport {
  at?: string; passes: number; fixed_n: number; reverted_n: number; before: number; after: number; undo: boolean;
  skipped: Record<string, number>; by_rule: Record<string, { before: number; after: number }>;
  fixed: { key: string; fid: number; note: string }[]; reverted: { key: string; fid: number; note: string; why: string[] }[];
}
export interface TopologyErrors { errors: TopologyError[]; exceptions: number; counts: { errors: number; exceptions: number } }
