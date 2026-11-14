/**
 * Lays a WorkflowModel out as a workbench canvas and returns standalone SVG markup:
 * bookmarks (coloured regions), reader / transformer / writer nodes with input and output ports,
 * connection lines with feature counts, and sticky annotations.
 */
import type { GeomKind, WfAttr, WfShape, WfTemplate, WorkflowModel } from "./model";

type Kind = "reader" | "ft" | "xf" | "writer" | "wft" | "sink" | "qc";
interface Port { id: string; label: string; count?: number | string; reject?: boolean }
interface Node {
  id: string; kind: Kind; x: number; y: number; w: number; title: string; icon: string;
  inputs: Port[]; outputs: Port[]; params: string[]; geom?: GeomKind;
  thumb?: WfShape | null; attrs?: WfAttr[]; attrMore?: number; tag?: string;
}
interface Edge { from: [string, string]; to: [string, string]; count?: number | string; reject?: boolean }
interface Bookmark { title: string; ids: string[]; tint: string; pad?: number }
interface Note { x: number; y: number; w: number; text: string[] }

const HEAD = 28, ROW = 20, PARAM = 15;
const FONT = "'Geist','Segoe UI',Arial,sans-serif";
const MONO = "'IBM Plex Mono',Consolas,monospace";

const PAL: Record<Kind, { head: string; headText: string; body: string; line: string }> = {
  reader: { head: "#3F7F43", headText: "#FFFFFF", body: "#EDF6EA", line: "#6FA36A" },
  ft: { head: "#D6EBD0", headText: "#1F4A22", body: "#F5FBF3", line: "#7FB278" },
  xf: { head: "#D9E5F4", headText: "#1D3557", body: "#FFFFFF", line: "#8EA9CC" },
  writer: { head: "#E9B77A", headText: "#4A2604", body: "#FFF8EF", line: "#C98A42" },
  wft: { head: "#F8E2C6", headText: "#6B3A06", body: "#FFFCF7", line: "#D49A55" },
  sink: { head: "#E6E7EA", headText: "#3B4250", body: "#FAFAFB", line: "#A5ABB5" },
  qc: { head: "#E3DAF5", headText: "#3D2A7A", body: "#FFFFFF", line: "#9C8BD0" },
};

const esc = (s: unknown) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const fmt = (n: number | string | undefined) => (typeof n === "number" ? n.toLocaleString("en-US") : n ?? "");
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const GCOL: Record<GeomKind, { stroke: string; fill: string }> = {
  point: { stroke: "#0E7490", fill: "#0E749033" }, line: { stroke: "#C2410C", fill: "none" },
  polygon: { stroke: "#4D7C0F", fill: "#65A30D40" }, text: { stroke: "#6B7280", fill: "none" },
};
const GEO_LABEL: Record<GeomKind, string> = { point: "Point", line: "Line", polygon: "Area", text: "Text" };

const AROW = 14;
let FRAME_H = 0;   // height of the shared geometry frame (1000 wide); 0 = no geometry available
const SHOW_THUMBS = false;   // map pictures inside the nodes are off: the workspace shows the flow, the map is in QA / QC
function thumbH(n: Node) {
  if (!SHOW_THUMBS) return 0;
  return n.thumb && FRAME_H ? Math.max(70, Math.min(n.kind === "ft" ? 100 : 150, Math.round((n.w - 16) * FRAME_H / 1000))) : 0;
}
function bodyTop(n: Node) { return n.y + HEAD + 4 + Math.max(n.inputs.length, n.outputs.length, 1) * ROW; }
function height(n: Node) {
  const th = thumbH(n);
  const at = n.attrs?.length ? (n.attrs.length + (n.attrMore ? 1 : 0)) * AROW + 10 : 0;
  return HEAD + Math.max(n.inputs.length, n.outputs.length, 1) * ROW + (th ? th + 8 : 0) + at + n.params.length * PARAM + 12;
}
function portY(n: Node, i: number) { return n.y + HEAD + 4 + i * ROW + ROW / 2; }

// ── icons (16×16, stroke) ───────────────────────────────────────────────
const ICON: Record<string, string> = {
  db: '<ellipse cx="8" cy="3.5" rx="5.5" ry="2"/><path d="M2.5 3.5v9c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2v-9"/><path d="M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2"/>',
  layer: '<path d="M8 2 1.5 5.5 8 9l6.5-3.5z"/><path d="m1.5 9 6.5 3.5L14.5 9"/>',
  funnel: '<path d="M2 3h12l-4.5 5.5V13l-3 1.5V8.5z"/>',
  gear: '<circle cx="8" cy="8" r="2.4"/><path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4"/>',
  map: '<path d="M2 5h9M8 2l3 3-3 3M14 11H5M8 8l-3 3 3 3"/>',
  test: '<path d="M2.5 8.5 6 12l7.5-8"/>',
  fan: '<path d="M2 8h4M6 8l7-5M6 8h7M6 8l7 5"/><circle cx="2" cy="8" r="1"/>',
  globe: '<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2 2 2 10 0 12M8 2c-2 2-2 10 0 12"/>',
  expose: '<path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/>',
  log: '<path d="M3 2h7l3 3v9H3z"/><path d="M5.5 7h5M5.5 10h5"/>',
  shield: '<path d="M8 1.5 2.5 3.5v4c0 3.4 2.4 5.9 5.5 7 3.1-1.1 5.5-3.6 5.5-7v-4z"/><path d="m5.5 8 1.8 1.8L10.8 6"/>',
  geom: '<path d="M2 13 6 4l4 6 4-7"/><circle cx="2" cy="13" r="1.2"/><circle cx="6" cy="4" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="14" cy="3" r="1.2"/>',
};
const geomIcon = (g?: GeomKind) => (g === "line" ? '<path d="M2 12 6 5l4 5 4-7"/>'
  : g === "polygon" ? '<path d="M2.5 4 9 2.5l4.5 4-2 7H4z"/>'
  : g === "text" ? '<path d="M3 3.5h10M8 3.5v10"/>'
  : '<circle cx="8" cy="8" r="3"/>');

// ── layout ──────────────────────────────────────────────────────────────
function layout(m: WorkflowModel) {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const marks: Bookmark[] = [];
  const notes: Note[] = [];
  const add = (n: Node) => { nodes.push(n); return n; };
  const top = 170;

  // columns
  const X = { ft: 70, expose: 430, role: 730, geo: 1020, asset: 1310, route: 1620,
              fc: 1920, attr: 2220, dom: 2520, geot: 2810, fan: 3110, wr: 3390, qc: 0, rep: 0 };
  const FTW = 290, WFW = 310, WGAP = 24, WCOLS = 3;
  X.qc = X.wr + WCOLS * WFW + (WCOLS - 1) * WGAP + 110; X.rep = X.qc + 300;

  // 1 · reader + one feature type per CAD layer
  const reader = add({ id: "reader", kind: "reader", x: X.ft, y: top, w: FTW, title: "AutoCAD DWG/DXF Reader", icon: "db",
    inputs: [], outputs: [], params: [`Dataset: ${clip(m.file, 30)}`, `Format: ${m.format} · units ${m.units || "—"}`,
      `Entities: ${fmt(m.entities)} · layers: ${m.layers.length}`,
      `Coord. system: ${m.epsgIn ? "EPSG:" + m.epsgIn : "from drawing"}`] });
  let y = reader.y + height(reader) + 26;
  const MAXFT = 12;
  const ordered = [...m.layers.filter((l) => l.role === "asset"), ...m.layers.filter((l) => l.role !== "asset")];
  const shown = ordered.slice(0, ordered.length > MAXFT ? MAXFT - 1 : MAXFT);
  const rest = ordered.slice(shown.length);
  const ftIds: string[] = [];
  shown.forEach((l, i) => {
    const at = l.attrList || [];
    const n = add({ id: `ft${i}`, kind: "ft", x: X.ft, y, w: FTW, title: clip(l.name, 30), icon: "layer", geom: l.geometry,
      tag: `${GEO_LABEL[l.geometry]} · ${fmt(l.count)}`,
      inputs: [], outputs: [{ id: "o", label: l.role === "aside" ? `Output — set aside (${l.asideReason})` : `Output → ${clip(l.target || "", 20)}`, count: l.count, reject: l.role === "aside" }], params: [],
      thumb: l.shape && l.shape.n ? l.shape : null, attrs: at.slice(0, 5), attrMore: Math.max(0, at.length - 5) });
    ftIds.push(n.id); y += height(n) + 10;
  });
  if (rest.length) {
    const n = add({ id: "ftmore", kind: "ft", x: X.ft, y, w: FTW, title: `+ ${rest.length} more feature types`, icon: "layer",
      inputs: [], outputs: [{ id: "o", label: "Output", count: rest.reduce((s, l) => s + l.count, 0) }], params: [] });
    ftIds.push(n.id); y += height(n) + 10;
  }
  const readerBottom = y;
  marks.push({ title: "1 · Source CAD — Reader & Feature Types (one per CAD layer)", ids: ["reader", ...ftIds], tint: "#E8F3E5" });

  // 2 · layer identification + geometry classification
  const assetCount = m.layers.filter((l) => l.role === "asset").reduce((s, l) => s + l.count, 0);
  add({ id: "expose", kind: "xf", x: X.expose, y: top + 120, w: 220, title: "AttributeExposer", icon: "expose",
    inputs: [{ id: "i", label: "Input" }], outputs: [{ id: "o", label: "Output", count: assetCount + m.aside }],
    params: ["fme_feature_type (CAD layer)", "autocad_block_name", "autocad_entity · colour", "text / block attributes"] });
  const role = add({ id: "role", kind: "xf", x: X.role, y: top + 120, w: 210, title: "Tester — CAD role", icon: "test",
    inputs: [{ id: "i", label: "Input" }],
    outputs: [{ id: "p", label: "Passed (asset)", count: assetCount }, { id: "f", label: "Failed", count: m.aside, reject: true }],
    params: ["layer role ≠ annotation", "layer role ≠ base map / sheet"] });
  const geo = add({ id: "geo", kind: "xf", x: X.geo, y: top + 120, w: 210, title: "GeometryFilter", icon: "geom",
    inputs: [{ id: "i", label: "Input" }],
    outputs: (["point", "line", "polygon"] as GeomKind[]).map((g) => ({ id: g, label: GEO_LABEL[g],
      count: m.layers.filter((l) => l.role === "asset" && l.geometry === g).reduce((s, l) => s + l.count, 0) })),
    params: ["closed polyline/hatch → Area", "INSERT block → Point"] });
  const aside = add({ id: "aside", kind: "sink", x: X.role, y: role.y + height(role) + 70, w: 210, title: "Set aside (not converted)", icon: "log",
    inputs: [{ id: "i", label: "Input" }], outputs: [],
    params: Array.from(new Set(m.layers.filter((l) => l.role === "aside").map((l) => `${clip(l.name, 22)} — ${l.asideReason}`))).slice(0, 5)
      .concat(m.layers.filter((l) => l.role === "aside").length > 5 ? ["…"] : []) });
  ftIds.forEach((id) => edges.push({ from: [id, "o"], to: ["expose", "i"] }));
  edges.push({ from: ["expose", "o"], to: ["role", "i"], count: assetCount + m.aside });
  edges.push({ from: ["role", "p"], to: ["geo", "i"], count: assetCount });
  edges.push({ from: ["role", "f"], to: ["aside", "i"], count: m.aside, reject: true });
  marks.push({ title: "2 · Layer Identification & Geometry Classification", ids: ["expose", "role", "geo", "aside"], tint: "#E7EEF8" });

  // 3 · asset classification + routing to templates
  const tpls = m.templates;
  add({ id: "asset", kind: "xf", x: X.asset, y: top + 120, w: 230, title: "AttributeValueMapper", icon: "map",
    inputs: [{ id: "i", label: "Input" }], outputs: [{ id: "o", label: "Output", count: assetCount }],
    params: ["source: layer name + block + style", "→ asset_type (IR · HL · RD · SL", "   SW · STL · ITC)", "dictionary + learned rules"] });
  const route = add({ id: "route", kind: "xf", x: X.route, y: top + 120, w: 220, title: "TestFilter — asset_type", icon: "funnel",
    inputs: [{ id: "i", label: "Input" }],
    outputs: tpls.map((t): Port => ({ id: t.code, label: `${t.code} — ${clip(t.label, 16)}`, count: t.features }))
      .concat([{ id: "none", label: "<Unfiltered>", count: 0, reject: true } as Port]),
    params: [] });
  geo.outputs.forEach((o) => edges.push({ from: ["geo", o.id], to: ["asset", "i"], count: o.count }));
  edges.push({ from: ["asset", "o"], to: ["route", "i"], count: assetCount });
  marks.push({ title: "3 · Asset Classification & Dynamic Routing", ids: ["asset", "route"], tint: "#F1EAF8" });
  notes.push({ x: X.asset, y: route.y + height(route) + 40, w: 500, text: [
    "Routing key = CAD layer name + block name + geometry type.",
    "Each asset type is sent to its own original template —",
    "no layer is forced into a template it does not belong to." ] });

  // 4–6 · one lane per template
  let laneY = top + 60;
  const laneIds: string[][] = [];
  const writers: Node[] = [];
  tpls.forEach((t: WfTemplate) => {
    const MAXFC = 8;
    const fcs = t.classes.slice(0, t.classes.length > MAXFC ? MAXFC - 1 : MAXFC);
    const more = t.classes.length - fcs.length;
    const fcPorts: Port[] = fcs.map((c) => ({ id: c.name, label: clip(c.name, 22), count: c.ok ?? c.count }));
    if (more) fcPorts.push({ id: "_more", label: `+ ${more} more classes`, count: t.classes.slice(fcs.length).reduce((s, c) => s + c.count, 0) });
    const pre = `L${t.code}`;
    const fcMap = add({ id: `${pre}fc`, kind: "xf", x: X.fc, y: laneY, w: 230, title: `AttributeValueMapper — ${t.code}`, icon: "map",
      inputs: [{ id: "i", label: "Input" }], outputs: [{ id: "o", label: "Output", count: t.features }],
      params: ["layer + geometry → target_fc", ...fcs.slice(0, 4).map((c) => `${clip(c.layers[0] || "", 13)} → ${clip(c.name, 14)}`),
        ...(t.classes.length > 4 ? [`… ${t.classes.length - 4} more`] : [])] });
    const fieldsMax = Math.max(0, ...t.classes.map((c) => c.fields));
    const attr = add({ id: `${pre}attr`, kind: "xf", x: X.attr, y: laneY, w: 230, title: "AttributeManager", icon: "gear",
      inputs: [{ id: "i", label: "Input" }], outputs: [{ id: "o", label: "Output", count: t.features }],
      params: [`template fields: up to ${fieldsMax} / class`, "labels & block attrs → fields", "required values · defaults",
        m.elevation ? "Z / elevation ← DEM" : "Z / elevation ← CAD"] });
    const dom = add({ id: `${pre}dom`, kind: "xf", x: X.dom, y: laneY, w: 210, title: "AttributeFilter — domains", icon: "funnel",
      inputs: [{ id: "i", label: "Input" }],
      outputs: [{ id: "p", label: "Valid", count: t.ok ?? t.features }, { id: "f", label: "Rejected", count: t.failed ?? "—", reject: true }],
      params: ["coded values vs template domains", "NOT NULL · length · type"] });
    const geot = add({ id: `${pre}geo`, kind: "xf", x: X.geot, y: laneY, w: 220, title: "Reprojector + GeometryValidator", icon: "globe",
      inputs: [{ id: "i", label: "Input" }], outputs: [{ id: "o", label: "Output", count: t.ok ?? t.features }],
      params: [`→ ${m.epsgOut ? "EPSG:" + m.epsgOut : "template CRS"}`, "repair · snap · close rings", "match template geometry"] });
    const fan = add({ id: `${pre}fan`, kind: "xf", x: X.fan, y: laneY, w: 200, title: "FeatureTypeFanout", icon: "fan",
      inputs: [{ id: "i", label: "Input" }], outputs: fcPorts, params: ["fanout by: target_fc"] });
    const wr = add({ id: `${pre}wr`, kind: "writer", x: X.wr, y: laneY, w: WFW * WCOLS + WGAP * (WCOLS - 1), title: `Esri File Geodatabase Writer — ${t.gdb}`, icon: "db",
      inputs: [], outputs: [{ id: "s", label: "Summary" }],
      params: [`Template: ${t.id} (V${t.version}) — copied, schema unchanged`,
        `${t.totalClasses} feature classes kept · ${t.classes.length} receive features · domains & subtypes kept`] });
    writers.push(wr);
    const wfts: string[] = [];
    let gy = laneY + height(wr) + 18;
    const shownFc = t.classes.slice(0, fcs.length);
    for (let i = 0; i < shownFc.length; i += WCOLS) {
      const row = shownFc.slice(i, i + WCOLS).map((c, j) => {
        const at = c.attrList || [];
        return add({ id: `${pre}fc_${c.name}`, kind: "wft", x: X.wr + j * (WFW + WGAP), y: gy, w: WFW, title: clip(c.name, 28), icon: "layer",
          geom: c.geometry, tag: `${GEO_LABEL[c.geometry]} · ${fmt(c.ok ?? c.count)}`,
          inputs: [{ id: "i", label: `Input ← ${c.layers.length} CAD layer${c.layers.length === 1 ? "" : "s"}` }], outputs: [], params: [],
          thumb: c.shape && c.shape.n ? c.shape : null, attrs: at.slice(0, 9), attrMore: Math.max(0, at.length - 9) });
      });
      row.forEach((n) => wfts.push(n.id));
      gy += Math.max(...row.map(height)) + 18;
    }
    if (more) {
      const n = add({ id: `${pre}fc__more`, kind: "wft", x: X.wr, y: gy, w: WFW, title: `+ ${more} more feature classes`, icon: "layer",
        inputs: [{ id: "i", label: "Input" }], outputs: [], params: [] });
      wfts.push(n.id); gy += height(n) + 18;
    }
    edges.push({ from: ["route", t.code], to: [fcMap.id, "i"], count: t.features });
    edges.push({ from: [fcMap.id, "o"], to: [attr.id, "i"], count: t.features });
    edges.push({ from: [attr.id, "o"], to: [dom.id, "i"], count: t.features });
    edges.push({ from: [dom.id, "p"], to: [geot.id, "i"], count: t.ok ?? t.features });
    edges.push({ from: [geot.id, "o"], to: [fan.id, "i"], count: t.ok ?? t.features });
    fcPorts.forEach((p) => edges.push({ from: [fan.id, p.id], to: [p.id === "_more" ? `${pre}fc__more` : `${pre}fc_${p.id}`, "i"], count: p.count }));
    marks.push({ title: `${t.gdb} — ${t.label}`, ids: [wr.id, ...wfts], tint: "#FFF3E2", pad: 14 });
    laneIds.push([fcMap.id, attr.id, dom.id, geot.id, fan.id, wr.id, ...wfts]);
    laneY = Math.max(laneY + Math.max(...[fcMap, attr, dom, geot, fan].map(height)), gy) + 70;
  });

  // rejected features + QC
  add({ id: "rejected", kind: "sink", x: X.geot, y: laneY + 10, w: 220, title: "Logger — rejected features", icon: "log",
    inputs: [{ id: "i", label: "Input" }], outputs: [],
    params: ["reason · CAD handle · layer", "→ report: Failed Features sheet"] });
  tpls.forEach((t) => edges.push({ from: [`L${t.code}dom`, "f"], to: ["rejected", "i"], count: t.failed ?? undefined, reject: true }));
  const midLanes = writers.length ? (writers[0].y + writers[writers.length - 1].y + height(writers[writers.length - 1])) / 2 : top;
  const qc = add({ id: "qc", kind: "qc", x: X.qc, y: Math.max(top + 60, midLanes - 90), w: 230, title: "Template Validator (DMT QC)", icon: "shield",
    inputs: tpls.map((t) => ({ id: t.code, label: t.gdb })),
    outputs: [{ id: "p", label: "Passed", count: m.qc ? (m.qc.passed ? "✓" : "—") : undefined },
              { id: "f", label: "Failed", count: m.qc ? m.qc.errors : undefined, reject: true }],
    params: ["schema = original template", "domains · subtypes · topology", "standard V7 file naming"] });
  add({ id: "report", kind: "sink", x: X.rep, y: qc.y + 10, w: 230, title: "Mapping & Validation Report", icon: "log",
    inputs: [{ id: "i", label: "Input" }], outputs: [],
    params: ["CAD layer → GDB → feature class", "geometry · counts · status", "XLSX + PDF"] });
  writers.forEach((w, i) => edges.push({ from: [w.id, "s"], to: ["qc", tpls[i].code] }));
  edges.push({ from: ["qc", "p"], to: ["report", "i"] });
  edges.push({ from: ["qc", "f"], to: ["report", "i"], reject: true });

  const all = laneIds.flat();
  marks.push({ title: "4 · Feature Class & Attribute Mapping", ids: all.filter((id) => /fc$|attr$/.test(id)), tint: "#EAF1F8" });
  marks.push({ title: "5 · Domain Validation & Geometry Transfer", ids: [...all.filter((id) => /dom$|geo$/.test(id)), "rejected"], tint: "#FBEFE6" });
  marks.unshift({ title: "6 · Dynamic Fanout → One Writer per Original Template (feature classes with geometry & attributes)", ids: all.filter((id) => /fan$|wr$|fc_/.test(id)), tint: "#FDF3E4", pad: 34 });
  marks.push({ title: "7 · Quality Control", ids: ["qc", "report"], tint: "#EFEAF9" });
  notes.push({ x: X.fan, y: laneY + 10, w: 560, text: [
    "Each writer starts from a copy of its original template geodatabase:",
    "every feature dataset, feature class, domain and subtype is preserved.",
    "Only the features routed to that template are written into it." ] });

  const bottom = Math.max(readerBottom, laneY + 140, aside.y + height(aside) + 80, ...notes.map((n) => n.y + 30 + n.text.length * 18));
  const wfOrder = (b: Bookmark) => (b.title.startsWith("6 ·") ? 0 : b.title.includes(".gdb —") ? 2 : 1);
  marks.sort((a, b) => wfOrder(a) - wfOrder(b));
  return { nodes, edges, marks, notes, width: X.rep + 230 + 90, height: bottom + 40 };
}

// ── drawing ─────────────────────────────────────────────────────────────
function drawNode(n: Node) {
  const p = PAL[n.kind];
  const h = height(n);
  const icon = n.icon === "geo" ? geomIcon(n.geom) : ICON[n.icon] || "";
  const gcol = GCOL[n.geom || "point"];
  const out: string[] = [];
  out.push(`<g class="node" data-id="${esc(n.id)}" style="cursor:move">`);
  out.push(`<rect x="${n.x + 2}" y="${n.y + 3}" width="${n.w}" height="${h}" rx="6" fill="#0F172A" opacity="0.07"/>`);
  out.push(`<rect x="${n.x}" y="${n.y}" width="${n.w}" height="${h}" rx="6" fill="${p.body}" stroke="${p.line}" stroke-width="1.2"/>`);
  out.push(`<path d="M${n.x} ${n.y + 6}a6 6 0 0 1 6-6h${n.w - 12}a6 6 0 0 1 6 6v${HEAD - 6}h-${n.w}z" fill="${p.head}"/>`);
  out.push(`<line x1="${n.x}" y1="${n.y + HEAD}" x2="${n.x + n.w}" y2="${n.y + HEAD}" stroke="${p.line}" stroke-width="1"/>`);
  if (n.icon === "layer" && n.geom) {
    // GIS layer icon: stacked sheets + the geometry glyph in the layer's colour
    out.push(`<g transform="translate(${n.x + 7} ${n.y + 5})"><path d="M9 1 1 5l8 4 8-4z" fill="#FFFFFF" stroke="${p.headText}" stroke-width="1.1"/>`
      + `<path d="m1 9 8 4 8-4" fill="none" stroke="${p.headText}" stroke-width="1.1"/><path d="m1 13 8 4 8-4" fill="none" stroke="${p.headText}" stroke-width="1.1"/>`
      + `<g transform="translate(5.2 2.2) scale(.48)" fill="none" stroke="${gcol.stroke}" stroke-width="2.6" stroke-linecap="round">${geomIcon(n.geom)}</g></g>`);
  } else {
    out.push(`<g transform="translate(${n.x + 8} ${n.y + 6})" fill="none" stroke="${p.headText}" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">${icon}</g>`);
  }
  out.push(`<text x="${n.x + 30}" y="${n.y + 18.5}" font-family="${FONT}" font-size="12.5" font-weight="600" fill="${p.headText}">${esc(n.title)}</text>`);
  if (n.tag) {
    const tw = n.tag.length * 6 + 12;
    out.push(`<rect x="${n.x + n.w - tw - 6}" y="${n.y + 6}" width="${tw}" height="16" rx="8" fill="#FFFFFF" fill-opacity=".8" stroke="${gcol.stroke}" stroke-opacity=".5"/>`
      + `<text x="${n.x + n.w - tw / 2 - 6}" y="${n.y + 17.5}" text-anchor="middle" font-family="${FONT}" font-size="10" font-weight="600" fill="${gcol.stroke}">${esc(n.tag)}</text>`);
  }
  n.inputs.forEach((pt, i) => {
    const y = portY(n, i);
    out.push(`<path d="M${n.x - 1} ${y - 5} l7 5 -7 5z" fill="#4B5563"/>`);
    out.push(`<text x="${n.x + 11}" y="${y + 4}" font-family="${FONT}" font-size="11" fill="#374151">${esc(pt.label)}</text>`);
  });
  n.outputs.forEach((pt, i) => {
    const y = portY(n, i);
    const c = pt.reject ? "#C2410C" : "#4B5563";
    out.push(`<path d="M${n.x + n.w - 7} ${y - 5} l7 5 -7 5z" fill="${c}"/>`);
    out.push(`<text x="${n.x + n.w - 11}" y="${y + 4}" text-anchor="end" font-family="${FONT}" font-size="11" fill="${pt.reject ? "#9A3412" : "#374151"}">${esc(pt.label)}</text>`);
  });
  let by = bodyTop(n) + 4;
  const th = thumbH(n);
  if (th && n.thumb) {
    const tx = n.x + 8, tw = n.w - 16;
    out.push(`<rect x="${tx}" y="${by}" width="${tw}" height="${th}" rx="3" fill="#E9EEF0" stroke="#C7D0D6"/>`);
    out.push(`<svg x="${tx}" y="${by}" width="${tw}" height="${th}" viewBox="0 0 1000 ${FRAME_H}" preserveAspectRatio="xMidYMid meet" overflow="hidden">`
      + `<use href="#wfctx"/>`
      + (n.thumb.a ? `<path d="${n.thumb.a}" fill="${gcol.fill}" stroke="${gcol.stroke}" stroke-width="1.3" vector-effect="non-scaling-stroke"/>` : "")
      + (n.thumb.l ? `<path d="${n.thumb.l}" fill="none" stroke="${gcol.stroke}" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>` : "")
      + (n.thumb.p ? `<path d="${n.thumb.p}" fill="none" stroke="${gcol.stroke}" stroke-width="4" stroke-linecap="round" vector-effect="non-scaling-stroke"/>` : "")
      + `</svg>`);
    out.push(`<text x="${tx + tw - 5}" y="${by + th - 5}" text-anchor="end" font-family="${FONT}" font-size="9.5" fill="#4B5563">${fmt(n.thumb.n)} ${n.thumb.n === 1 ? "geometry" : "geometries"}</text>`);
    by += th + 8;
  }
  if (n.attrs?.length) {
    out.push(`<text x="${n.x + 10}" y="${by + 9}" font-family="${FONT}" font-size="9.5" font-weight="700" letter-spacing=".06em" fill="#6B7280">ATTRIBUTES</text>`);
    by += 9;
    n.attrs.forEach((a, i) => {
      const yy = by + 12 + i * AROW;
      out.push(`<text x="${n.x + 10}" y="${yy}" font-family="${MONO}" font-size="10" fill="#1F2937">${esc(clip(a.name, 24))}</text>`);
      out.push(`<text x="${n.x + 10 + Math.min(24, a.name.length) * 6.05 + 6}" y="${yy}" font-family="${MONO}" font-size="9.5" fill="#9CA3AF">${esc(a.type)}</text>`);
      const right = a.src ? clip(a.src, 20) : a.required ? "required" : "";
      if (right) out.push(`<text x="${n.x + n.w - 10}" y="${yy}" text-anchor="end" font-family="${FONT}" font-size="9.5" fill="${a.src ? "#0E7490" : "#B45309"}">${esc(right)}</text>`);
    });
    if (n.attrMore) out.push(`<text x="${n.x + 10}" y="${by + 12 + n.attrs.length * AROW}" font-family="${FONT}" font-size="9.5" fill="#6B7280">+ ${n.attrMore} more attributes</text>`);
    by += (n.attrs.length + (n.attrMore ? 1 : 0)) * AROW + 4;
  }
  const py = by;
  if (n.params.length) out.push(`<line x1="${n.x + 8}" y1="${py - 3}" x2="${n.x + n.w - 8}" y2="${py - 3}" stroke="${p.line}" stroke-opacity="0.45" stroke-dasharray="2 3"/>`);
  const maxc = Math.floor((n.w - 18) / 6.05);
  n.params.forEach((t, i) => out.push(`<text x="${n.x + 10}" y="${py + 11 + i * PARAM}" font-family="${MONO}" font-size="10" fill="#4B5563">${esc(clip(t, maxc))}</text>`));
  out.push(`</g>`);
  return out.join("");
}

function drawEdge(e: Edge, byId: Map<string, Node>) {
  const a = byId.get(e.from[0]), b = byId.get(e.to[0]);
  if (!a || !b) return "";
  const ai = a.outputs.findIndex((p) => p.id === e.from[1]);
  const bi = b.inputs.findIndex((p) => p.id === e.to[1]);
  if (ai < 0 || bi < 0) return "";
  const x1 = a.x + a.w, y1 = portY(a, ai), x2 = b.x - 1, y2 = portY(b, bi);
  const dx = Math.max(40, (x2 - x1) * 0.5);
  const col = e.reject ? "#D9692E" : "#5B6B7F";
  const path = x2 > x1
    ? `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`
    : `M${x1} ${y1} C${x1 + 80} ${y1} ${x1 + 80} ${y1 + 60} ${(x1 + x2) / 2} ${(y1 + y2) / 2} S${x2 - 80} ${y2} ${x2} ${y2}`;
  let s = `<path d="${path}" fill="none" stroke="${col}" stroke-width="1.5"${e.reject ? ' stroke-dasharray="5 4"' : ""} opacity="0.9"/>`;
  if (e.count !== undefined && e.count !== "" && e.count !== 0) {
    const t = fmt(e.count);
    const w = String(t).length * 6.4 + 8;
    s += `<rect x="${x1 + 12}" y="${y1 - 15}" width="${w}" height="13" rx="3" fill="#FFFFFF" stroke="${col}" stroke-opacity="0.35"/>`;
    s += `<text x="${x1 + 16}" y="${y1 - 5}" font-family="${MONO}" font-size="10" fill="${e.reject ? "#9A3412" : "#1F2937"}">${esc(t)}</text>`;
  }
  return s;
}

export type NodePos = Record<string, { x: number; y: number }>;

export function renderWorkspace(m: WorkflowModel, opts: { title?: string; pos?: NodePos } = {}): { svg: string; width: number; height: number } {
  FRAME_H = m.frame?.h || 0;
  const L = layout(m);
  // positions moved by the user (edges, group frames and the canvas size follow)
  const pos = opts.pos || {};
  L.nodes.forEach((n) => { const q = pos[n.id]; if (q) { n.x = q.x; n.y = q.y; } });
  const byId = new Map(L.nodes.map((n) => [n.id, n]));
  const W = Math.max(L.width, ...L.nodes.map((n) => n.x + n.w + 120));
  const H = Math.max(L.height, ...L.nodes.map((n) => n.y + height(n) + 80));
  const parts: string[] = [];
  parts.push(`<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" font-family="${FONT}">`);
  const ctx = m.frame?.ctx;
  parts.push(`<defs>${ctx ? `<g id="wfctx" fill="none" stroke="#C3CCD3" vector-effect="non-scaling-stroke">`
    + (ctx.a ? `<path d="${ctx.a}" fill="#D7DEE3" stroke="#C3CCD3" stroke-width="1" vector-effect="non-scaling-stroke"/>` : "")
    + (ctx.l ? `<path d="${ctx.l}" stroke-width="1" vector-effect="non-scaling-stroke"/>` : "")
    + (ctx.p ? `<path d="${ctx.p}" stroke-width="2.5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>` : "") + `</g>` : ""}</defs>`);
  parts.push(`<defs><pattern id="wfgrid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" stroke="#E3E6EA" stroke-width="1"/></pattern></defs>`);
  parts.push(`<rect width="${W}" height="${H}" fill="#F7F8FA"/><rect width="${W}" height="${H}" fill="url(#wfgrid)"/>`);

  // title strip
  const writersN = m.templates.length;
  parts.push(`<text x="70" y="62" font-size="26" font-weight="700" fill="#1B1E23" font-family="${FONT}">${esc(opts.title || "Automated CAD → Multi-GDB Workspace")}</text>`);
  parts.push(`<text x="70" y="90" font-size="14" fill="#4B5563">${esc(m.file)} · ${fmt(m.layers.length)} CAD layers → ${writersN} geodatabase writer${writersN === 1 ? "" : "s"} · ${m.stage === "delivered" ? "delivered" : m.stage === "validated" ? "validated" : "mapped, not yet validated"}</text>`);
  // legend
  const lg: [string, string][] = [["Reader / feature type", PAL.ft.head], ["Transformer", PAL.xf.head], ["Writer", PAL.writer.head], ["Quality control", PAL.qc.head]];
  let lx = W - 640;
  lg.forEach(([t, c]) => { parts.push(`<rect x="${lx}" y="52" width="14" height="14" rx="3" fill="${c}" stroke="#9CA3AF" stroke-width="0.6"/><text x="${lx + 20}" y="63.5" font-size="12" fill="#374151">${t}</text>`); lx += t.length * 6.6 + 44; });
  parts.push(`<path d="M${W - 640} 84h30" stroke="#D9692E" stroke-width="1.5" stroke-dasharray="5 4"/><text x="${W - 602}" y="88" font-size="12" fill="#374151">rejected / set aside</text>`);
  parts.push(`<rect x="${W - 470}" y="78" width="40" height="13" rx="3" fill="#fff" stroke="#9CA3AF"/><text x="${W - 465}" y="88" font-size="10" font-family="${MONO}">1,315</text><text x="${W - 422}" y="88" font-size="12" fill="#374151">features on the connection</text>`);

  // bookmarks
  L.marks.forEach((b) => {
    const ns = b.ids.map((id) => byId.get(id)).filter(Boolean) as Node[];
    if (!ns.length) return;
    const pad = b.pad ?? 22;
    const x0 = Math.min(...ns.map((n) => n.x)) - pad, y0 = Math.min(...ns.map((n) => n.y)) - pad - 26;
    const x1 = Math.max(...ns.map((n) => n.x + n.w)) + pad, y1 = Math.max(...ns.map((n) => n.y + height(n))) + pad;
    parts.push(`<rect x="${x0}" y="${y0}" width="${x1 - x0}" height="${y1 - y0}" rx="8" fill="${b.tint}" fill-opacity="0.75" stroke="#B9C0CA" stroke-width="1"/>`);
    parts.push(`<path d="M${x0} ${y0 + 8}a8 8 0 0 1 8-8h${x1 - x0 - 16}a8 8 0 0 1 8 8v18h-${x1 - x0}z" fill="#FFFFFF" fill-opacity="0.65"/>`);
    parts.push(`<g transform="translate(${x0 + 10} ${y0 + 6})" fill="none" stroke="#4B5563" stroke-width="1.3"><path d="M3 1h8v12l-4-3-4 3z"/></g>`);
    parts.push(`<text x="${x0 + 28}" y="${y0 + 18}" font-size="12.5" font-weight="600" fill="#1F2937">${esc(b.title)}</text>`);
  });

  L.edges.forEach((e) => parts.push(drawEdge(e, byId)));
  L.nodes.forEach((n) => parts.push(drawNode(n)));

  L.notes.forEach((nt) => {
    const h = 22 + nt.text.length * 18;
    parts.push(`<rect x="${nt.x}" y="${nt.y}" width="${nt.w}" height="${h}" rx="4" fill="#FFF7D1" stroke="#E2C86B"/>`);
    parts.push(`<path d="M${nt.x + nt.w - 14} ${nt.y + h}l14 -14v14z" fill="#F0DC8F"/>`);
    nt.text.forEach((t, i) => parts.push(`<text x="${nt.x + 12}" y="${nt.y + 24 + i * 18}" font-size="12.5" fill="#5B4A10">${esc(t)}</text>`));
  });
  parts.push(`</svg>`);
  return { svg: parts.join("\n"), width: W, height: H };
}
