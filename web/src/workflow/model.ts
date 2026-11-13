/**
 * Workspace model of a conversion job, drawn like an ETL workbench canvas:
 * one CAD reader (a feature type per CAD layer) -> layer identification -> geometry classification ->
 * asset classification -> feature-class mapping -> attribute mapping -> geometry transfer ->
 * one geodatabase writer per original template -> quality control.
 *
 * Pure data (no DOM): the same model feeds the in-app canvas and the exported image.
 */
import type { Discovery, GeoJSON, Mapping, Results, SchemaDetail } from "../types";

export type GeomKind = "point" | "line" | "polygon" | "text";

/** Geometry drawn in a 1000-wide local frame: path data for lines, areas and points (points = zero-length strokes). */
export interface WfShape { l: string; a: string; p: string; n: number }
export interface WfAttr { name: string; type: string; src?: string; required?: boolean }

export interface WfLayer {
  name: string;            // CAD layer (and drawing style when several share one layer)
  geometry: GeomKind;
  count: number;
  role: "asset" | "aside";
  asideReason?: string;
  target?: string | null;  // feature class
  template?: string | null;
  attrs?: number;          // attributes filled from the drawing
  attrList?: WfAttr[];     // what the reader exposes for this feature type
  shape?: WfShape;
}
export interface WfFeatureClass {
  name: string; geometry: GeomKind; count: number; ok?: number; failed?: number; fields: number; layers: string[];
  attrList?: WfAttr[];     // template fields, mapped ones first
  shape?: WfShape;
}
export interface WfTemplate {
  id: string; code: string; label: string; gdb: string; version: string;
  classes: WfFeatureClass[]; totalClasses: number; domains: number; features: number;
  ok?: number; failed?: number;
}
export interface WorkflowModel {
  file: string; format: string; entities: number; units: string; epsgIn: number | null; epsgOut: number | null;
  layers: WfLayer[];
  templates: WfTemplate[];
  geometry: Record<GeomKind, number>;
  aside: number;
  stage: "mapped" | "validated" | "delivered";
  elevation: boolean;
  qc?: { passed: boolean; errors: number; domainErrors: number } | null;
  frame?: { h: number; ctx: WfShape } | null;   // drawing extent + everything converted, as context
}

const CODE_LABEL: Record<string, string> = {
  IR: "Irrigation", HL: "Hard Landscaping", RD: "Roads", SL: "Soft Landscaping", SW: "Storm Water",
  STL: "Street Lighting", ITC: "ITC", UT: "Utilities",
};

export function templateCode(id: string): string {
  const m = /^adm_([a-z]+)/i.exec(id || "");
  return (m ? m[1] : id || "").toUpperCase();
}

const asideWhy: Record<string, string> = { annotation: "annotation", basemap: "base map", sheet: "sheet / title block" };

// ── geometry thumbnails ──────────────────────────────────────────────────
type XY = [number, number];
interface Geo { kind: string; layer: string; rings: XY[][] }

function readGeo(cad: GeoJSON | null | undefined): { items: Geo[]; frame: (p: XY) => XY; h: number } | null {
  if (!cad?.features?.length) return null;
  const items: Geo[] = [];
  for (const f of cad.features) {
    const g = f.geometry; if (!g) continue;
    const rings: XY[][] = g.type === "Point" ? [[g.coordinates as XY]]
      : g.type === "LineString" ? [g.coordinates as XY[]]
      : g.type === "Polygon" ? (g.coordinates as XY[][]) : [];
    if (rings.length) items.push({ kind: f.properties?.kind || "", layer: f.properties?.layer || "", rings });
  }
  const xs: number[] = [], ys: number[] = [];
  items.forEach((it) => { if (it.kind === "text") return; it.rings.forEach((r) => r.forEach(([x, y]) => { xs.push(x); ys.push(y); })); });
  if (xs.length < 1) return null;
  xs.sort((a, b) => a - b); ys.sort((a, b) => a - b);
  const q = (a: number[], t: number) => a[Math.min(a.length - 1, Math.max(0, Math.floor(t * (a.length - 1))))];
  const x0 = q(xs, 0.005), x1 = q(xs, 0.995), y0 = q(ys, 0.005), y1 = q(ys, 0.995);
  const geo = !!cad.crs_is_geographic;
  const kx = geo ? Math.cos(((y0 + y1) / 2) * Math.PI / 180) : 1;
  const w = Math.max((x1 - x0) * kx, 1e-9), hh = Math.max(y1 - y0, 1e-9);
  const s = 940 / Math.max(w, hh * 1000 / 640);          // frame 1000 wide, at most 640 high, 30 margin
  const h = Math.max(220, Math.min(640, Math.round(hh * s + 60)));
  const ox = (1000 - w * s) / 2, oy = (h - hh * s) / 2;
  const frame = ([x, y]: XY): XY => [Math.round(ox + (x - x0) * kx * s), Math.round(h - (oy + (y - y0) * s))];
  return { items, frame, h };
}

function shapeOf(items: Geo[], frame: (p: XY) => XY, budget = 5000): WfShape {
  let l = "", a = "", p = "", n = 0;
  const verts = items.reduce((t, it) => t + it.rings.reduce((u, r) => u + r.length, 0), 0);
  const step = Math.max(1, Math.ceil(verts / budget));
  for (const it of items) {
    if (it.kind === "point" || it.kind === "text" || (it.rings.length === 1 && it.rings[0].length === 1)) {
      const [x, y] = frame(it.rings[0][0]); p += `M${x} ${y}h0`; n++; continue;
    }
    for (const r of it.rings) {
      const pts = r.filter((_, i) => i % step === 0 || i === r.length - 1).map(frame);
      if (pts.length < 2) continue;
      const d = "M" + pts.map(([x, y]) => `${x} ${y}`).join("L");
      if (it.kind === "polygon") a += d + "Z"; else l += d;
    }
    n++;
  }
  return { l, a, p, n };
}

const KIND_FOR: Record<string, string[]> = { point: ["point"], line: ["line"], polygon: ["polygon"], text: ["text"] };

const SRC: Record<string, string> = {
  block_attr: "← block attribute", label: "← CAD label", geometry: "← geometry", elevation: "← Z / DEM",
  constant: "= value", computed: "ƒ computed", subtype: "subtype", system: "system", network: "network",
};

/** Build the model from what the job already has (discovery + mapping + schema, results once validated). */
export function buildModel(job: { file: { name: string; format: string } ; status: string },
                           discovery: Discovery, mapping: Mapping, schema: SchemaDetail,
                           results?: Results | null, cad?: GeoJSON | null): WorkflowModel {
  const G = readGeo(cad);
  const byLayer = new Map<string, Geo[]>();
  G?.items.forEach((it) => { const k = `${it.layer}|${it.kind}`; (byLayer.get(k) || byLayer.set(k, []).get(k)!).push(it); });
  const fcOf = new Map(schema.layers.map((l) => [l.name, l]));
  const byFc = new Map<string, { ok: number; failed: number }>();
  for (const f of results?.features || []) {
    const s = byFc.get(f.target) || { ok: 0, failed: 0 };
    if (f.status === "ok") s.ok++; else if (f.status === "failed") s.failed++;
    byFc.set(f.target, s);
  }
  const layers: WfLayer[] = [];
  const geometry: Record<GeomKind, number> = { point: 0, line: 0, polygon: 0, text: 0 };
  let aside = 0;
  for (const g of discovery.groups) {
    const lm = mapping.layers[g.key];
    const geom = (g.geometry as GeomKind) || "point";
    geometry[geom] += g.count;
    const isAside = !lm || lm.role === "ignore" || !lm.target;
    const fc = !isAside ? fcOf.get(lm!.target!) : undefined;
    if (isAside) aside += g.count;
    layers.push({
      name: g.style ? `${g.layer} · ${g.style}` : g.layer, geometry: geom, count: g.count,
      role: isAside ? "aside" : "asset",
      asideReason: isAside ? (asideWhy[g.role || ""] || (lm?.target ? "low confidence" : "no matching class")) : undefined,
      target: isAside ? null : lm!.target, template: fc?.template || (isAside ? null : schema.schema_id),
      attrs: lm?.attributes ? Object.keys(lm.attributes).length : 0,
      attrList: [
        { name: "autocad_layer", type: "char" }, { name: "autocad_entity", type: "char" },
        ...(g.blocks?.length ? [{ name: "autocad_block_name", type: "char", src: g.blocks.slice(0, 2).map((b) => b.name).join(", ") }] : []),
        ...(g.attribute_tags || []).slice(0, 6).map((t) => ({ name: t, type: "char", src: "block attribute" })),
        { name: "autocad_color", type: "int" },
        ...(geom === "line" ? [{ name: "length", type: "real" }] : geom === "polygon" ? [{ name: "area", type: "real" }] : [{ name: "rotation", type: "real" }]),
        { name: "z", type: "real" },
      ],
      shape: G ? shapeOf((KIND_FOR[geom] || [geom]).flatMap((k) => byLayer.get(`${g.layer}|${k}`) || []), G.frame, 2500) : undefined,
    });
  }
  geometry.text = discovery.text_count || 0;

  const tplIds = Array.from(new Set(layers.filter((l) => l.role === "asset").map((l) => l.template || schema.schema_id)));
  const templates: WfTemplate[] = tplIds.map((id) => {
    const all = schema.layers.filter((l) => (l.template || schema.schema_id) === id);
    const used = new Map<string, WfFeatureClass>();
    for (const l of layers) {
      if (l.role !== "asset" || (l.template || schema.schema_id) !== id || !l.target) continue;
      const def = fcOf.get(l.target);
      const c = used.get(l.target) || { name: l.target, geometry: (def?.geometry_type as GeomKind) || l.geometry,
        count: 0, fields: def?.field_count ?? def?.fields?.length ?? 0, layers: [] };
      c.count += l.count; c.layers.push(l.name);
      const s = byFc.get(l.target); if (s) { c.ok = s.ok; c.failed = s.failed; }
      if (!c.attrList && def) {
        const spec = mapping.fields?.[l.target] || {};
        const rows = def.fields.map((f) => {
          const sp = spec[f.name];
          const src = sp && sp.source && sp.source !== "none" ? (sp.source === "constant" && sp.value != null ? `= ${String(sp.value).slice(0, 16)}` : SRC[sp.source] || sp.source) : undefined;
          const ty = (f.type || "").replace(/^esriFieldType/i, "").toLowerCase();
          return { name: f.name, type: f.length && /string|text|char/.test(ty) ? `char(${f.length})` : ty.replace("string", "char").replace("double", "real").replace("integer", "int"),
                   src, required: f.required || f.nullable === false };
        });
        c.attrList = [...rows.filter((r) => r.src), ...rows.filter((r) => !r.src && r.required), ...rows.filter((r) => !r.src && !r.required)];
      }
      used.set(l.target, c);
    }
    const code = templateCode(id);
    const classes = Array.from(used.values()).sort((a, b) => b.count - a.count);
    if (G) classes.forEach((c) => {
      const its = layers.filter((l) => l.target === c.name && l.role === "asset" && l.shape);
      c.shape = { l: its.map((l) => l.shape!.l).join(""), a: its.map((l) => l.shape!.a).join(""), p: its.map((l) => l.shape!.p).join(""), n: its.reduce((t, l) => t + l.shape!.n, 0) };
    });
    const ok = classes.reduce((n, c) => n + (c.ok || 0), 0);
    const failed = classes.reduce((n, c) => n + (c.failed || 0), 0);
    return {
      id, code, label: CODE_LABEL[code] || code, gdb: `ADM_${code}.gdb`,
      version: String(schema.version).split(" + ")[0] || "", classes, totalClasses: all.length || classes.length,
      domains: 0, features: classes.reduce((n, c) => n + c.count, 0),
      ok: results ? ok : undefined, failed: results ? failed : undefined,
    };
  }).sort((a, b) => b.features - a.features);

  const stage = job.status === "completed" ? "delivered" : results ? "validated" : "mapped";
  return {
    file: job.file.name, format: (job.file.format || "dwg").toUpperCase(), entities: discovery.feature_entity_count,
    units: discovery.units, epsgIn: mapping.source_crs, epsgOut: schema.spatial_reference?.epsg ?? null,
    layers, templates, geometry, aside, stage, elevation: !!mapping.elevation?.enabled,
    qc: results ? { passed: !!results.dmt_qc?.passed, errors: results.dmt_qc?.errors?.length || 0,
                    domainErrors: results.summary.domain_errors } : null,
    frame: G ? { h: G.h, ctx: shapeOf(G.items.filter((it) => it.kind !== "text"), G.frame, 6000) } : null,
  };
}
