import type {
  Capabilities, Completeness, CrsInfo, CrsSuggestion, GeorefFit, GeorefPoint, TransformOption, Discovery, FeatureDetail, GeoJSON, JobState, Mapping, Results, SchemaDetail, SchemaSummary,
  DemSourceInfo, ElevationOptions, DemMeta, SubmissionStatus, TemplateUpdate, TemplateVersion,
  AuthUser, AuthConfig, Plan, BillingStatus, Payment, LedgerRow, AssignOverview, AssignDetail, AssignState, AutofixReport, PurchaseRequest, AssignSaved, TopologyErrors,
} from "./types";

// Relative base keeps the module mountable under any platform path (e.g. /tools/cad2gdb/).
const BASE = "./api";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function authHeaders(): Record<string, string> {
  // Reuses a platform-issued token when the host stores one (GSB_AUTH_MODE=token).
  try {
    const t = window.localStorage.getItem("gsb_token") || window.sessionStorage.getItem("gsb_token");
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch {
    return {};
  }
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
               ...authHeaders(), ...(init.headers || {}) },
    credentials: "same-origin",
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* not JSON */ }
    if (r.status === 401 && !path.startsWith("/auth/") && !window.location.hash.startsWith("#/login")) {
      window.dispatchEvent(new CustomEvent("gsb-unauthorized"));
    }
    throw new ApiError(r.status, msg);
  }
  if (r.status === 204) return undefined as T;
  const ct = r.headers.get("content-type") || "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

const J = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export const auth = {
  config: () => req<AuthConfig>("/auth/config"),
  me: () => req<{ mode: string; user: AuthUser | null }>("/auth/me"),
  login: (email: string, password: string) => req<{ user: AuthUser }>("/auth/login", J({ email, password })),
  signup: (b: { email: string; password: string; name: string; company: string }) => req<{ user: AuthUser }>("/auth/signup", J(b)),
  logout: () => req<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  forgot: (email: string) => req<{ message: string }>("/auth/password/forgot", J({ email })),
  reset: (token: string, password: string) => req<{ user: AuthUser }>("/auth/password/reset", J({ token, password })),
  oauthUrl: (p: string) => `${BASE}/auth/oauth/${p}/start`,
  updateAccount: (b: { name?: string; company?: string }) => req<{ user: AuthUser }>("/account", { method: "PATCH", body: JSON.stringify(b) }),
  changePassword: (current: string, password: string) => req<{ ok: boolean }>("/account/password", J({ current, password })),
  resendVerification: () => req<{ ok: boolean; smtp: boolean }>("/account/resend-verification", { method: "POST" }),
};

export const billing = {
  plans: () => req<Plan[]>("/billing/plans"),
  status: () => req<BillingStatus>("/billing/status"),
  quote: (jobId: string) => req<{ enforced: boolean; features?: number; cost?: number; already_paid?: boolean; balance?: number; enough?: boolean }>(`/billing/quote/${jobId}`),
  checkout: (plan_id: string) => req<{ url: string }>("/billing/checkout", J({ plan_id })),
  portal: () => req<{ url: string }>("/billing/portal", { method: "POST" }),
  request: (plan_id: string, note = "") => req<PurchaseRequest>("/billing/request", J({ plan_id, note })),
  requests: () => req<PurchaseRequest[]>("/billing/requests"),
  receiptUrl: (id: number) => `${BASE}/billing/requests/${id}/receipt.pdf`,
  cancelRequest: (id: number) => req<{ ok: boolean }>(`/billing/requests/${id}/cancel`, { method: "POST" }),
};

export const owner = {
  overview: () => req<any>("/owner/overview"),
  users: (q: Record<string, string>) => req<{ total: number; users: AuthUser[] }>(`/owner/users?${new URLSearchParams(q)}`),
  user: (id: number) => req<any>(`/owner/users/${id}`),
  credits: (id: number, delta: number, note: string) => req<{ balance: number }>(`/owner/users/${id}/credits`, J({ delta, note })),
  updateUser: (id: number, b: Record<string, unknown>) => req<{ user: AuthUser }>(`/owner/users/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
  resetLink: (id: number) => req<{ url: string }>(`/owner/users/${id}/reset-link`, { method: "POST" }),
  logoutUser: (id: number) => req<{ ok: boolean }>(`/owner/users/${id}/logout`, { method: "POST" }),
  deleteUser: (id: number) => req<{ ok: boolean }>(`/owner/users/${id}`, { method: "DELETE" }),
  invite: (b: { email: string; name?: string; company?: string; credits?: number }) => req<{ user: AuthUser; url: string }>("/owner/users", J(b)),
  plans: () => req<Plan[]>("/owner/plans"),
  requests: (status = "") => req<PurchaseRequest[]>(`/owner/requests?status=${status}`),
  decide: (id: number, approve: boolean, note = "") => req<PurchaseRequest>(`/owner/requests/${id}`, J({ approve, note })),
  savePlan: (id: string, b: Partial<Plan>) => req<Plan>(`/owner/plans/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(b) }),
  settings: () => req<{ settings: Record<string, any>; integrations: Record<string, any> }>("/owner/settings"),
  saveSettings: (b: Record<string, unknown>) => req<Record<string, any>>("/owner/settings", { method: "PUT", body: JSON.stringify(b) }),
  payments: () => req<Payment[]>("/owner/payments"),
  audit: () => req<any[]>("/owner/audit"),
};
export type { LedgerRow };

export const api = {
  capabilities: () => req<Capabilities>("/system/capabilities"),
  schemas: () => req<SchemaSummary[]>("/schemas"),
  schema: (id: string) => req<SchemaDetail>(`/schemas/${encodeURIComponent(id)}`),
  profiles: (id: string) => req<{ name: string; saved_at: string; saved_by: string; layer_count: number }[]>(`/schemas/${encodeURIComponent(id)}/profiles`),
  templateUpdates: (id: string) => req<{ current: { version: string; template: string; applied_at?: string; applied_by?: string } | null;
    updates: TemplateUpdate[]; versions: TemplateVersion[] }>(`/schemas/${encodeURIComponent(id)}/template-updates`),
  templateUpdate: (id: string, uid: string) => req<TemplateUpdate>(`/schemas/${encodeURIComponent(id)}/template-updates/${uid}`),
  stageTemplate: (id: string, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return req<TemplateUpdate>(`/schemas/${encodeURIComponent(id)}/template-updates`, { method: "POST", body: fd });
  },
  applyTemplate: (id: string, uid: string, renames: unknown) =>
    req<TemplateUpdate>(`/schemas/${encodeURIComponent(id)}/template-updates/${uid}/apply`, { method: "POST", body: JSON.stringify({ renames }) }),
  rejectTemplate: (id: string, uid: string) => req<TemplateUpdate>(`/schemas/${encodeURIComponent(id)}/template-updates/${uid}/reject`, { method: "POST" }),
  restoreTemplate: (id: string, folder: string) => req<{ restored: string }>(`/schemas/${encodeURIComponent(id)}/versions/${encodeURIComponent(folder)}/restore`, { method: "POST" }),
  crs: (q: string) => req<CrsInfo[]>(`/crs?q=${encodeURIComponent(q)}`),
  jobs: () => req<Partial<JobState>[]>("/jobs"),
  job: (id: string) => req<JobState>(`/jobs/${id}`),
  deleteJob: (id: string) => req<void>(`/jobs/${id}`, { method: "DELETE" }),
  discovery: (id: string) => req<Discovery>(`/jobs/${id}/discovery`),
  mapping: (id: string) => req<Mapping>(`/jobs/${id}/mapping`),
  patchMapping: (id: string, patch: unknown) => req<Mapping>(`/jobs/${id}/mapping`, { method: "PATCH", body: JSON.stringify(patch) }),
  changeSchema: (id: string, schema_id: string) => req<JobState>(`/jobs/${id}/schema`, { method: "PUT", body: JSON.stringify({ schema_id }) }),
  saveProfile: (id: string, name: string) => req<{ profile: string; rule_conflicts: string[] }>(`/jobs/${id}/profiles`, { method: "POST", body: JSON.stringify({ name, update_rules: true }) }),
  applyProfile: (id: string, name: string) => req<Mapping>(`/jobs/${id}/profiles/${encodeURIComponent(name)}/apply`, { method: "POST" }),
  acceptSuggestions: (id: string, minScore?: number) => req<Mapping>(`/jobs/${id}/mapping/accept-suggestions${minScore != null ? `?min_score=${minScore}` : ""}`, { method: "POST" }),
  requiredValues: (id: string) => req<any>(`/jobs/${id}/required-values`),
  delivery: (id: string) => req<any[]>(`/jobs/${id}/delivery`),
  cleanup: (id: string) => req<any>(`/jobs/${id}/cleanup`),
  setCleanup: (id: string, body: unknown) => req<JobState>(`/jobs/${id}/cleanup`, { method: "POST", body: JSON.stringify(body) }),
  memory: () => req<{ fields: Record<string, { value: string }>; value_maps: Record<string, Record<string, string>> }>(`/memory`),
  forget: (field: string) => req<unknown>(`/memory/${encodeURIComponent(field)}`, { method: "DELETE" }),
  attachReference: (id: string, file: File) => {
    const fd = new FormData(); fd.append("file", file);
    return req<Mapping>(`/jobs/${id}/reference`, { method: "POST", body: fd });
  },
  removeReference: (id: string) => req<Mapping>(`/jobs/${id}/reference`, { method: "DELETE" }),
  demSources: () => req<{ sources: Record<string, DemSourceInfo>; vertical_refs: Record<string, string>; defaults: ElevationOptions; opentopography_key_configured: boolean }>("/elevation/sources"),
  prepareDem: (id: string, source: string, api_key?: string) => req<Mapping>(`/jobs/${id}/elevation/prepare`, { method: "POST", body: JSON.stringify({ source, api_key }) }),
  uploadDem: (id: string, file: File, crs: string, vertical: string) => {
    const fd = new FormData(); fd.append("file", file); fd.append("crs", crs); fd.append("vertical", vertical);
    return req<Mapping>(`/jobs/${id}/elevation/upload`, { method: "POST", body: fd });
  },
  disableDem: (id: string) => req<Mapping>(`/jobs/${id}/elevation`, { method: "DELETE" }),
  demPreview: (id: string) => req<GeoJSON & { stats: Record<string, number | null>; dem: DemMeta }>(`/jobs/${id}/elevation/preview`),
  submission: (id: string) => req<SubmissionStatus>(`/jobs/${id}/submission`),
  completeness: (id: string) => req<Completeness>(`/jobs/${id}/completeness`),
  validate: (id: string) => req<JobState>(`/jobs/${id}/validate`, { method: "POST" }),
  generate: (id: string) => req<JobState>(`/jobs/${id}/generate`, { method: "POST" }),
  results: (id: string) => req<Results>(`/jobs/${id}/results`),
  preview: (id: string) => req<GeoJSON>(`/jobs/${id}/preview`),
  cadPreview: (id: string, o?: { epsg: number; scale: number; limit?: number }) =>
    req<GeoJSON>(`/jobs/${id}/cad-preview` + (o ? `?epsg=${o.epsg}&scale=${o.scale}&limit=${o.limit || 4000}` : "")),
  crsDetect: (id: string) => req<CrsSuggestion>(`/jobs/${id}/crs-detect`),
  crsTransforms: (src: number, dst: number) => req<TransformOption[]>(`/crs/transforms?src=${src}&dst=${dst}`),
  crsParse: (text: string) => req<CrsInfo>(`/crs/parse`, { method: "POST", body: JSON.stringify({ text }) }),
  crsCheck: (id: string, x: number, y: number) => req<{ source: number[]; target?: number[] | null; lonlat?: number[]; source_epsg: number | null; target_epsg: number | null }>(
    `/jobs/${id}/crs/check`, { method: "POST", body: JSON.stringify({ x, y }) }),
  georefLabels: (id: string) => req<{ found: number; points: GeorefPoint[]; fit?: GeorefFit; already_real?: boolean; message: string; crs?: CrsSuggestion }>(`/jobs/${id}/georef/labels`),
  georefFit: (points: GeorefPoint[], fixed_scale?: number) => req<GeorefFit>(`/georef/fit`, { method: "POST", body: JSON.stringify({ points, fixed_scale }) }),
  feature: (id: string, fid: number) => req<FeatureDetail>(`/jobs/${id}/features/${fid}`),
  topology: (id: string) => req<TopologyErrors>(`/jobs/${id}/topology`),
  autofixRun: (id: string, codes?: string[]) => req<JobState>(`/jobs/${id}/autofix/run`, { method: "POST", body: JSON.stringify({ codes: codes || null }) }),
  autofixReport: (id: string) => req<AutofixReport>(`/jobs/${id}/autofix/report`),
  autofixUndo: (id: string) => req<JobState>(`/jobs/${id}/autofix/undo`, { method: "POST" }),
  autofixPreview: (id: string, key: string) =>
    req<{ action: string | null; reason?: string; fid?: number; note?: string; delete?: boolean; geometry?: any }>(`/jobs/${id}/autofix/preview?key=${encodeURIComponent(key)}`),
  mergeFeatures: (id: string, fids: number[]) => req<{ kept: number; merged: number; multipart: boolean }>(`/jobs/${id}/geometry/merge`, { method: "POST", body: JSON.stringify({ fids }) }),
  explodeFeature: (id: string, fid: number) => req<{ parts: number }>(`/jobs/${id}/geometry/${fid}/explode`, { method: "POST" }),
  topologyAutofix: (id: string, keys: string[], bulk = false) =>
    req<{ fixed: { key: string; fid: number; note: string }[]; skipped: { key: string; reason: string }[]; validate: boolean }>(
      `/jobs/${id}/topology/autofix`, { method: "POST", body: JSON.stringify({ keys, bulk }) }),
  topologyException: (id: string, key: string, exception: boolean) =>
    req<{ key: string; exception: boolean }>(`/jobs/${id}/topology/exception`, { method: "POST", body: JSON.stringify({ key, exception }) }),
  saveGeometry: (id: string, edits: ({ fid: number; geometry: unknown } | { fid: number; delete: true } | { add: string; geometry: unknown })[]) =>
    req<{ saved: number; geometry: number; deleted: number; added?: number }>(`/jobs/${id}/geometry`, { method: "POST", body: JSON.stringify({ edits }) }),
  splitFeature: (id: string, fid: number, cutter: unknown) =>
    req<{ parts: number }>(`/jobs/${id}/geometry/${fid}/split`, { method: "POST", body: JSON.stringify({ cutter }) }),
  restoreGeometry: (id: string, fid: number) => req<{ restored: number }>(`/jobs/${id}/geometry/${fid}/restore`, { method: "POST" }),
  assign: (id: string) => req<AssignOverview>(`/jobs/${id}/assign`),
  assignMany: (id: string, fids: number[], fields: Record<string, unknown>) =>
    req<{ saved: number; features: { fid: number; state: AssignState; missing: string[] }[]; failed: { fid: number; error: string }[] }>(
      `/jobs/${id}/assign-many`, { method: "POST", body: JSON.stringify({ fids, fields }) }),
  assignValues: (id: string, target: string, field: string) =>
    req<{ field: string; values: { value: string; count: number }[]; distinct: number }>(
      `/jobs/${id}/assign-values?target=${encodeURIComponent(target)}&field=${encodeURIComponent(field)}`),
  assignDetail: (id: string, fid: number) => req<AssignDetail>(`/jobs/${id}/assign/${fid}`),
  assignSave: (id: string, fid: number, fields: Record<string, unknown>, status: "completed" | "skipped" | "draft") =>
    req<AssignSaved>(`/jobs/${id}/assign/${fid}`, { method: "POST", body: JSON.stringify({ fields, status }) }),
  log: (id: string) => req<string>(`/jobs/${id}/log`),
  downloadUrl: (id: string, kind: string) => `${BASE}/jobs/${id}/download/${kind}`,
  upload: (schema_id: string, file: File, onProgress: (f: number) => void): Promise<JobState> =>
    new Promise((resolve, reject) => {
      const fd = new FormData();
      fd.append("schema_id", schema_id);
      fd.append("file", file);
      const x = new XMLHttpRequest();
      x.open("POST", `${BASE}/jobs`);
      Object.entries(authHeaders()).forEach(([k, v]) => x.setRequestHeader(k, v));
      x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
      x.onload = () => {
        let body: any = null;
        try { body = JSON.parse(x.responseText); } catch { /* ignore */ }
        if (x.status >= 200 && x.status < 300) resolve(body);
        else reject(new ApiError(x.status, body?.detail || x.statusText));
      };
      x.onerror = () => reject(new ApiError(0, "network error during upload"));
      x.send(fd);
    }),
  addDrawing: (id: string, file: File): Promise<JobState> => {
    const fd = new FormData(); fd.append("file", file);
    return fetch(`${BASE}/jobs/${id}/drawings`, { method: "POST", body: fd, headers: authHeaders(), credentials: "include" })
      .then(async (r) => { const b = await r.json().catch(() => null); if (!r.ok) throw new ApiError(r.status, b?.detail || r.statusText); return b; });
  },
};
