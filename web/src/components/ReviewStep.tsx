/**
 * Review — one map-centred screen for everything after the layers are connected:
 * attributes (Feature / Layer tabs), validation and topology findings (Issues tab), and geometry editing
 * (icon toolbar on the map). Geometry saves re-run the validation automatically.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Badge, Box, Button, CircularProgress, Drawer, IconButton, LinearProgress, Menu, MenuItem, Stack, Tab, Tabs,
  Tooltip, Typography,
} from "@mui/material";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import XYZ from "ol/source/XYZ";
import GeoJSONFormat from "ol/format/GeoJSON";
import Projection from "ol/proj/Projection";
import Feature from "ol/Feature";
import Point from "ol/geom/Point";
import { fromLonLat } from "ol/proj";
import { Circle as CircleStyle, Fill, RegularShape, Stroke, Style } from "ol/style";
import { defaults as defaultControls, ScaleLine } from "ol/control";
import Select from "ol/interaction/Select";
import Modify from "ol/interaction/Modify";
import Translate from "ol/interaction/Translate";
import Snap from "ol/interaction/Snap";
import Draw from "ol/interaction/Draw";
import DragBox from "ol/interaction/DragBox";
import { click, shiftKeyOnly, always } from "ol/events/condition";
import type { FeatureLike } from "ol/Feature";
import NearMeOutlined from "@mui/icons-material/NearMeOutlined";
import HighlightAltOutlined from "@mui/icons-material/HighlightAltOutlined";
import PolylineOutlined from "@mui/icons-material/PolylineOutlined";
import OpenWithOutlined from "@mui/icons-material/OpenWithOutlined";
import DeleteOutline from "@mui/icons-material/DeleteOutline";
import CenterFocusWeakOutlined from "@mui/icons-material/CenterFocusWeakOutlined";
import UndoOutlined from "@mui/icons-material/UndoOutlined";
import SaveOutlined from "@mui/icons-material/SaveOutlined";
import ZoomOutMapOutlined from "@mui/icons-material/ZoomOutMapOutlined";
import MapOutlined from "@mui/icons-material/MapOutlined";
import Visibility from "@mui/icons-material/Visibility";
import VisibilityOff from "@mui/icons-material/VisibilityOff";
import CheckRounded from "@mui/icons-material/CheckRounded";
import DoNotDisturbOnOutlined from "@mui/icons-material/DoNotDisturbOnOutlined";
import TableRowsOutlined from "@mui/icons-material/TableRowsOutlined";
import CloseOutlined from "@mui/icons-material/CloseOutlined";
import AutoFixHighOutlined from "@mui/icons-material/AutoFixHighOutlined";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import CallMergeOutlined from "@mui/icons-material/CallMergeOutlined";
import CallSplitOutlined from "@mui/icons-material/CallSplitOutlined";
import DrawOutlined from "@mui/icons-material/DrawOutlined";
import ContentCutOutlined from "@mui/icons-material/ContentCutOutlined";
import { api } from "../api";
import type { AutofixReport, AssignOverview, AssignRow, GeoJSON, JobState, Results, TopologyError } from "../types";
import { BASEMAPS, basemapLayers } from "./MapView";
import { nice } from "./FeatureEditor";
import { AttributesPane } from "./AttributesPane";
import { CadCompare } from "./CadCompare";
import { C } from "../theme";

type Tool = "select" | "box" | "modify" | "move" | "draw" | "split";
type TabK = "attributes" | "issues";
const LOCAL = new Projection({ code: "LOCAL-CAD", units: "m", extent: [-1e9, -1e9, 1e9, 1e9] });
const PAL = ["#7C3AED", "#0D9488", "#B45309", "#2563EB", "#DB2777", "#4B5563", "#059669", "#9333EA", "#0891B2", "#65A30D"];
const RED = "#DC2626", AMBER = "#D97706", SEL = "#1F8FA5", PEND = "#EA580C";
const kindOf = (t?: string) => (!t ? "point" : t.includes("Polygon") ? "polygon" : t.includes("Line") ? "line" : "point");
const colorOf = (cls: string) => PAL[Math.abs([...cls].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 0)) % PAL.length];

interface Props {
  job: JobState; preview: GeoJSON | null; results: Results | null; busy: boolean;
  onValidate: () => void; onError: (m: string) => void; onNotice: (m: string) => void;
  onPatch: (p: unknown) => Promise<void>;
  onJob: (j: JobState) => void;
  openDetails: (what: "attributes" | "issues") => void;
}

const ELEV = /ZCOORD|GROUND_LEVEL|COVER_LEVEL|INVERT_LEVEL|TOP_LEVEL|BOTTOM_LEVEL|BASE_LEVEL|AVG_GLEVEL|ELEVATION/i;

export function ReviewStep({ job, preview, results, busy, onValidate, onError, onNotice, onPatch, onJob, openDetails }: Props) {
  // ---------------------------------------------------------------- data
  const [ov, setOv] = useState<AssignOverview | null>(null);
  const [rows, setRows] = useState<Record<number, AssignRow>>({});
  const [errs, setErrs] = useState<TopologyError[]>([]);
  const load = useCallback(() => {
    api.assign(job.job_id).then((o) => {
      setOv(o);
      const m: Record<number, AssignRow> = {};
      o.features.forEach((r) => { m[r.fid] = r; });
      setRows(m);
    }).catch((e) => onError(String(e.message || e)));
    api.topology(job.job_id).then((r) => setErrs(r.errors)).catch(() => setErrs([]));
  }, [job.job_id]);
  useEffect(() => { if (results) load(); }, [results?.generated_at, job.job_id]);

  const ovRef = useRef<AssignOverview | null>(null);
  ovRef.current = ov;
  const [focus, setFocus] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [tab, setTab] = useState<TabK>("attributes");

  const byClass = useMemo(() => {
    const m: Record<string, { total: number; open: number }> = {};
    Object.values(rows).forEach((r) => {
      const x = m[r.target] || (m[r.target] = { total: 0, open: 0 });
      x.total++; if (r.state === "incomplete" || r.state === "error") x.open++;
    });
    errs.forEach((e) => { if (!e.exception && e.class && m[e.class]) m[e.class].open++; });
    return m;
  }, [rows, errs]);
  const openErrs = errs.filter((e) => !e.exception);
  const failure = useMemo(() => {
    const m: Record<number, string> = {};
    (results?.features || []).forEach((f) => { if (f.failure) m[f.fid] = f.failure; });
    return m;
  }, [results]);
  const noLevel = useMemo(() => new Set(Object.entries(failure).filter(([, t]) => ELEV.test(t)).map(([k]) => Number(k))), [failure]);

  // ---------------------------------------------------------------- map
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<OlMap | null>(null);
  const fsrc = useRef(new VectorSource());
  const psrc = useRef(new VectorSource());          // the shape a fix would give (preview)
  const esrc = useRef(new VectorSource());
  const flayer = useRef<VectorLayer<any> | null>(null);
  const elayer = useRef<VectorLayer<any> | null>(null);
  const tiles = useRef<TileLayer<XYZ>[]>([]);
  const select = useRef<Select | null>(null);
  const inter = useRef<{ modify?: Modify; translate?: Translate; box?: DragBox; snap?: Snap; draw?: Draw }>({});
  const fmt = useRef(new GeoJSONFormat());
  const original = useRef<Record<number, string>>({});
  const [tool, setTool] = useState<Tool>("select");
  const [snapOn, setSnapOn] = useState(true);
  const [pending, setPending] = useState<Record<number, "geometry" | "delete" | "add">>({});
  const undo = useRef<{ fid: number; geom?: any; feature?: Feature; deleted?: boolean; added?: boolean }[][]>([]);
  const [drawCls, setDrawCls] = useState<string | null>(null);
  const [drawMenu, setDrawMenu] = useState<HTMLElement | null>(null);
  const tempId = useRef(-1);
  const [undoN, setUndoN] = useState(0);
  const [saving, setSaving] = useState(false);
  const [nSel, setNSel] = useState(0);
  const [curErr, setCurErr] = useState<number | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number; fid: number } | null>(null);
  const [baseMenu, setBaseMenu] = useState<HTMLElement | null>(null);
  const [basemap, setBasemap] = useState<string>(() => { try { return localStorage.getItem("gsb_basemap") || "satellite"; } catch { return "satellite"; } });
  const st = useRef({ rows, hidden, focus, curErr });
  st.current = { rows, hidden, focus, curErr };

  useEffect(() => {
    if (!el.current) return;
    const cache: Record<string, Style[]> = {};
    const fl = new VectorLayer({
      source: fsrc.current,
      style: (f: FeatureLike) => {
        const s = st.current;
        const cls = String(f.get("target"));
        if (s.hidden[cls]) return undefined;
        const r = s.rows[f.get("fid") as number];
        const dim = !!s.focus && s.focus !== cls;
        const c = f.get("pending") ? PEND : dim ? "#9CA3AF" : colorOf(cls);
        const ring = dim ? "" : r?.state === "error" ? RED : r?.state === "incomplete" ? AMBER : "";
        const k = `${c}|${ring}|${kindOf(f.getGeometry()?.getType())}|${dim}`;
        if (!cache[k]) {
          const out: Style[] = [];
          if (ring) out.push(new Style({ stroke: new Stroke({ color: ring + "AA", width: 7 }),
            image: new CircleStyle({ radius: 9, fill: new Fill({ color: ring + "55" }) }) }));
          out.push(new Style({
            stroke: new Stroke({ color: c, width: dim ? 1.2 : 2 }), fill: new Fill({ color: c + (dim ? "14" : "2E") }),
            image: new CircleStyle({ radius: dim ? 3.5 : 5, fill: new Fill({ color: c }), stroke: new Stroke({ color: "#fff", width: 1.2 }) }),
          }));
          cache[k] = out;
        }
        return cache[k];
      },
    });
    const el_ = new VectorLayer({
      source: esrc.current,
      style: (f: FeatureLike) => {
        const e = f.get("err") as TopologyError;
        if (e.exception) return undefined;
        const on = st.current.curErr === e.id;
        const c = on ? "#FACC15" : RED;
        return new Style({
          stroke: new Stroke({ color: c, width: on ? 5 : 3 }), fill: new Fill({ color: c + "44" }),
          image: kindOf(f.getGeometry()?.getType()) === "point" ? new RegularShape({ points: 4, radius: on ? 10 : 7, angle: Math.PI / 4,
            fill: new Fill({ color: c }), stroke: new Stroke({ color: "#fff", width: 1.5 }) }) : undefined,
        });
      },
    });
    flayer.current = fl; elayer.current = el_;
    const tl = basemapLayers();
    tiles.current = tl;
    const pl = new VectorLayer({ source: psrc.current, style: [
      new Style({ stroke: new Stroke({ color: "#fff", width: 6 }) }),
      new Style({ stroke: new Stroke({ color: "#16A34A", width: 3, lineDash: [8, 6] }), fill: new Fill({ color: "#16A34A22" }),
        image: new CircleStyle({ radius: 8, stroke: new Stroke({ color: "#16A34A", width: 3 }), fill: new Fill({ color: "#fff" }) }) })] });
    const m = new OlMap({ target: el.current, layers: [...tl, fl, el_, pl], view: new View({ center: [0, 0], zoom: 2 }),
      controls: defaultControls({ attribution: true, rotate: false, zoom: false }).extend([new ScaleLine()]) });
    const sel = new Select({
      layers: [fl], condition: click, toggleCondition: shiftKeyOnly, hitTolerance: 6,
      style: (f: FeatureLike) => [
        new Style({ stroke: new Stroke({ color: "#fff", width: 8 }), image: new CircleStyle({ radius: 11, fill: new Fill({ color: "#fff" }) }) }),
        new Style({ stroke: new Stroke({ color: SEL, width: 4 }), fill: new Fill({ color: SEL + "33" }),
          image: new CircleStyle({ radius: 7, fill: new Fill({ color: SEL }), stroke: new Stroke({ color: "#fff", width: 2 }) }), geometry: f.getGeometry() as any }),
      ],
    });
    sel.getFeatures().on(["add", "remove"], () => {
      const a = sel.getFeatures().getArray();
      setNSel(a.length);
      cancelAnimationFrame(selRaf.current);
      selRaf.current = requestAnimationFrame(() => setSelFids(sel.getFeatures().getArray().map((f) => f.get("fid") as number).filter((x) => x >= 0)));
    });
    m.addInteraction(sel);
    select.current = sel;
    let raf = 0;
    m.on("pointermove", (e) => {
      if (e.dragging) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const f = m.forEachFeatureAtPixel(e.pixel, (x) => x, { hitTolerance: 6, layerFilter: (l) => l === fl });
        const fid = f ? (f.get("fid") as number) : null;
        setHover(fid != null ? { x: e.pixel[0], y: e.pixel[1], fid } : null);
      });
    });
    map.current = m;
    return () => { cancelAnimationFrame(raf); m.setTarget(undefined); };
  }, []);

  const geo = !!preview?.crs_is_geographic;
  const proj = geo ? "EPSG:3857" : LOCAL;
  const loadFeatures = () => {
    const m = map.current;
    if (!m || !preview) return;
    if (m.getView().getProjection().getCode() !== (geo ? "EPSG:3857" : LOCAL.getCode())) m.setView(new View({ projection: proj, center: [0, 0], zoom: 2 }));
    fsrc.current.clear();
    select.current?.getFeatures().clear();
    const data = { ...preview, features: preview.features.filter((f: any) => f.properties?.fid != null && f.properties.status !== "skipped") };
    const feats = fmt.current.readFeatures(data, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj }) as Feature[];
    original.current = {};
    feats.forEach((f) => { f.setId(f.get("fid")); original.current[f.get("fid")] = JSON.stringify((f.getGeometry() as any)?.getCoordinates?.()); });
    fsrc.current.addFeatures(feats);
    undo.current = []; setUndoN(0); setPending({});
  };
  const fitExt = (ext: number[], pad = 60) => { if (ext && isFinite(ext[0])) map.current?.getView().fit(ext, { padding: [pad, pad, pad, pad], maxZoom: 20.5, duration: 300 }); };
  const fitAll = () => fitExt(fsrc.current.getExtent(), 50);
  useEffect(() => { loadFeatures(); setTimeout(fitAll, 0); }, [preview]);
  useEffect(() => { tiles.current.forEach((x) => x.setVisible(geo && x.get("basemap") === basemap)); }, [basemap, preview]);
  useEffect(() => { flayer.current?.changed(); elayer.current?.changed(); }, [rows, hidden, focus, curErr]);
  useEffect(() => {
    esrc.current.clear();
    errs.forEach((e) => {
      let g: any = null;
      if (e.shape) g = fmt.current.readGeometry(e.shape, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj });
      else if (e.lonlat) g = new Point(geo ? fromLonLat(e.lonlat) : e.lonlat);
      if (g) esrc.current.addFeature(new Feature({ geometry: g, err: e }));
    });
  }, [errs, preview]);

  // ---------------------------------------------------------------- editing tools
  useEffect(() => {
    const m = map.current, sel = select.current;
    if (!m || !sel) return;
    const I = inter.current;
    [I.modify, I.translate, I.box, I.snap, I.draw].forEach((x) => x && m.removeInteraction(x));
    inter.current = {};
    sel.setActive(tool !== "box" && tool !== "draw" && tool !== "split");
    const snapshot = (fs: Feature[]) => { undo.current.push(fs.map((f) => ({ fid: f.get("fid"), geom: f.getGeometry()!.clone() }))); setUndoN(undo.current.length); };
    const mark = (fs: Feature[]) => setPending((p) => {
      const n = { ...p };
      fs.forEach((f) => {
        const fid = f.get("fid") as number;
        if (fid < 0) { n[fid] = "add"; return; }
        if (JSON.stringify((f.getGeometry() as any)?.getCoordinates?.()) === original.current[fid]) { delete n[fid]; f.unset("pending"); }
        else { n[fid] = "geometry"; f.set("pending", true); }
      });
      return n;
    });
    if (tool === "modify") {
      const mod = new Modify({ features: sel.getFeatures(), pixelTolerance: 8 });
      mod.on("modifystart", (e) => snapshot(e.features.getArray() as Feature[]));
      mod.on("modifyend", (e) => mark(e.features.getArray() as Feature[]));
      m.addInteraction(mod); inter.current.modify = mod;
    }
    if (tool === "move") {
      const tr = new Translate({ features: sel.getFeatures() });
      tr.on("translatestart", (e) => snapshot(e.features.getArray() as Feature[]));
      tr.on("translateend", (e) => mark(e.features.getArray() as Feature[]));
      m.addInteraction(tr); inter.current.translate = tr;
    }
    if (tool === "box") {
      const box = new DragBox({ condition: always });
      box.on("boxend", () => {
        const fs = sel.getFeatures(); fs.clear();
        fsrc.current.forEachFeatureIntersectingExtent(box.getGeometry().getExtent(), (f) => { if (!st.current.hidden[String(f.get("target"))]) fs.push(f); });
      });
      m.addInteraction(box); inter.current.box = box;
    }
    if (tool === "draw" && drawCls) {
      const k = (ovRef.current?.classes[drawCls]?.geometry || "point").toLowerCase();
      const dr = new Draw({ source: fsrc.current, type: k === "polygon" ? "Polygon" : k === "line" ? "LineString" : "Point" });
      dr.on("drawend", (e) => {
        const f = e.feature as Feature;
        const fid = tempId.current--;
        f.setId(fid); f.set("fid", fid); f.set("target", drawCls); f.set("pending", true);
        undo.current.push([{ fid, feature: f, added: true }]); setUndoN(undo.current.length);
        setPending((p) => ({ ...p, [fid]: "add" }));
      });
      m.addInteraction(dr); inter.current.draw = dr;
    }
    if (tool === "split") {
      const dr = new Draw({ type: "LineString" });
      dr.on("drawend", (e) => { splitWith(e.feature as Feature); });
      m.addInteraction(dr); inter.current.draw = dr;
    }
    if (snapOn && (tool === "modify" || tool === "move" || tool === "draw" || tool === "split")) {
      const sn = new Snap({ source: fsrc.current, pixelTolerance: 10 });
      m.addInteraction(sn); inter.current.snap = sn;
    }
  }, [tool, snapOn, drawCls]);

  const del = () => {
    const fs = (select.current?.getFeatures().getArray().slice() || []) as Feature[];
    if (!fs.length) return;
    undo.current.push(fs.map((f) => ({ fid: f.get("fid"), feature: f, deleted: true }))); setUndoN(undo.current.length);
    fs.forEach((f) => fsrc.current.removeFeature(f));
    select.current?.getFeatures().clear();
    setPending((p) => { const n = { ...p }; fs.forEach((f) => { const id = f.get("fid") as number; if (id < 0) delete n[id]; else n[id] = "delete"; }); return n; });
  };
  const doUndo = () => {
    const last = undo.current.pop(); setUndoN(undo.current.length);
    if (!last) return;
    setPending((p) => {
      const n = { ...p };
      last.forEach((u) => {
        if (u.added && u.feature) { if (fsrc.current.hasFeature(u.feature)) fsrc.current.removeFeature(u.feature); delete n[u.fid]; return; }
        if (u.fid < 0 && u.deleted && u.feature) { fsrc.current.addFeature(u.feature); n[u.fid] = "add"; return; }
        if (u.fid < 0 && u.feature == null) { n[u.fid] = "add"; const f = fsrc.current.getFeatureById(u.fid) as Feature | null; if (f && u.geom) f.setGeometry(u.geom); return; }
        if (u.deleted && u.feature) fsrc.current.addFeature(u.feature);
        else { const f = fsrc.current.getFeatureById(u.fid) as Feature | null; if (f && u.geom) f.setGeometry(u.geom); }
        const f = fsrc.current.getFeatureById(u.fid) as Feature | null;
        if (f && JSON.stringify((f.getGeometry() as any)?.getCoordinates?.()) === original.current[u.fid]) { delete n[u.fid]; f.unset("pending"); }
        else { n[u.fid] = "geometry"; f?.set("pending", true); }
      });
      return n;
    });
  };
  const nPending = Object.keys(pending).length;
  const saveGeometry = async () => {
    const ids = Object.keys(pending).map(Number);
    if (!ids.length || saving) return;
    const edits = ids.map((fid) => {
      if (pending[fid] === "delete") return { fid, delete: true as const };
      const f = fsrc.current.getFeatureById(fid) as Feature;
      const geometry = fmt.current.writeGeometryObject(f.getGeometry()!, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj });
      if (pending[fid] === "add") return { add: String(f.get("target")), geometry };
      return { fid, geometry };
    });
    setSaving(true);
    try {
      await api.saveGeometry(job.job_id, edits);
      onNotice("Saved");
      setPending({}); undo.current = []; setUndoN(0);
      onValidate();
    } catch (e: any) { onError(String(e.message || e)); }
    finally { setSaving(false); }
  };

  const splitWith = async (cut: Feature) => {
    const target = select.current?.getFeatures().getArray()[0] as Feature | undefined;
    const fid = target?.get("fid") as number | undefined;
    if (fid == null || fid < 0) { onError("Select the line or polygon to split first, then draw the cut line across it."); return; }
    if (Object.keys(pendingRef.current).length) { onError("Save the geometry edits first, then split."); return; }
    setSaving(true);
    try {
      const cutter = fmt.current.writeGeometryObject(cut.getGeometry()!, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj });
      const r = await api.splitFeature(job.job_id, fid, cutter);
      onNotice(`Split · ${r.parts}`);
      setTool("select");
      onValidate();
    } catch (e: any) { onError(String(e.message || e)); } finally { setSaving(false); }
  };
  const geomOp = async (fn: () => Promise<unknown>, msg: string) => {
    if (Object.keys(pendingRef.current).length) { onError("Save the geometry edits first."); return; }
    setSaving(true);
    try { await fn(); onNotice(msg); onValidate(); } catch (e: any) { onError(String(e.message || e)); } finally { setSaving(false); }
  };
  const mergeSel = () => {
    const fids = (select.current?.getFeatures().getArray() || []).map((f) => f.get("fid") as number).filter((x) => x >= 0);
    geomOp(() => api.mergeFeatures(job.job_id, fids), `Merged · ${fids.length}`);
  };
  const explodeSel = () => {
    const fid = select.current?.getFeatures().getArray()[0]?.get("fid") as number | undefined;
    if (fid == null || fid < 0) return;
    geomOp(() => api.explodeFeature(job.job_id, fid), "Exploded");
  };
  const pendingRef = useRef(pending);
  pendingRef.current = pending;

  // ---------------------------------------------------------------- selection (drives the Attributes pane)
  const [selFids, setSelFids] = useState<number[]>([]);
  const selRaf = useRef(0);
  const cur = selFids.length === 1 ? selFids[0] : null;
  const [cadFid, setCadFid] = useState<number | null>(null);
  useEffect(() => { if (selFids.length !== 1) setCadFid(selFids.length ? cadFid : null); else setCadFid(selFids[0]); }, [selFids]);
  const [cadOpen, setCadOpen] = useState<boolean>(() => { try { return localStorage.getItem("gsb_cad_open") !== "0"; } catch { return true; } });
  const setCadP = (v: boolean) => { setCadOpen(v); try { localStorage.setItem("gsb_cad_open", v ? "1" : "0"); } catch { /* ignore */ } };
  const keepTab = useRef(false);            // a selection made from the Issues list stays on Issues
  useEffect(() => { if (keepTab.current) keepTab.current = false; else if (selFids.length) setTab("attributes"); }, [selFids]);
  const zoomTo = (fids: number[]) => {
    const ext = fids.reduce((a: number[], fid) => {
      const g = fsrc.current.getFeatureById(fid)?.getGeometry(); if (!g) return a;
      const e = g.getExtent(); return [Math.min(a[0], e[0]), Math.min(a[1], e[1]), Math.max(a[2], e[2]), Math.max(a[3], e[3])];
    }, [Infinity, Infinity, -Infinity, -Infinity]);
    fitExt(ext, 120);
  };
  const setSelection = (fids: number[]) => {
    const fs = select.current?.getFeatures(); if (!fs) return;
    fs.clear();
    const add = fids.map((f) => fsrc.current.getFeatureById(f)).filter(Boolean) as Feature[];
    fs.extend(add);
  };
  const openFeature = (fid: number, zoom = false) => { setTab("attributes"); setSelection([fid]); if (zoom) zoomTo([fid]); };
  const coordsOf = (fid: number): number[][][] | null => {
    const g = fsrc.current.getFeatureById(fid)?.getGeometry(); if (!g) return null;
    const o = fmt.current.writeGeometryObject(g, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj }) as any;
    const t = o.type as string, c = o.coordinates;
    return t === "Point" ? [[c]] : t === "MultiPoint" || t === "LineString" ? [c] : t === "Polygon" || t === "MultiLineString" ? c : t === "MultiPolygon" ? c.flat() : null;
  };

    // the work list: geometry/topology findings first, then features with errors, then features to fill
  type Item = { k: string; tone: string; title: string; sub: string; cls: string | null; fid: number | null; err?: TopologyError };
  const items: Item[] = useMemo(() => {
    const out: Item[] = [];
    openErrs.forEach((e) => out.push({ k: `t${e.id}`, tone: RED, title: e.rule.replace(/^[A-Z_]+:\s*/, ""), cls: e.class,
      sub: `${(e.class && ov?.classes[e.class]?.alias) || e.class || ""}${e.fid != null ? ` #${e.fid}` : ""}${e.related.length ? ` · ${(e.related_class && ov?.classes[e.related_class]?.alias) || e.related_class}` : ""}`, fid: e.fid, err: e }));
    const name = (r: AssignRow) => ov?.classes[r.target]?.alias || r.target;
    Object.values(rows).filter((r) => r.state === "error" && !noLevel.has(r.fid)).forEach((r) => out.push({ k: `e${r.fid}`, tone: RED,
      title: (failure[r.fid] || "Value not accepted").replace(/^NOT NULL field\(s\) without value:\s*/i, "Empty: ").slice(0, 80),
      sub: `${name(r)} #${r.objectid ?? r.fid}`, cls: r.target, fid: r.fid }));
    Object.values(rows).filter((r) => r.state === "incomplete").forEach((r) => out.push({ k: `i${r.fid}`, tone: AMBER,
      title: r.missing.length === 1 ? `${fieldLabel(r.target, r.missing[0])} empty` : `${r.missing.length} fields empty`,
      sub: `${name(r)} #${r.objectid ?? r.fid}`, cls: r.target, fid: r.fid }));
    return out;
  }, [errs, rows, ov, failure, noLevel]);
  function fieldLabel(cls: string, f: string) { const x = ov?.classes[cls]?.fields.find((y) => y.name === f); return x ? nice(x) : f; }
  const shown = items.filter((i) => !focus || i.cls === focus);
  const firstTab = useRef(true);             // first view: the work list, not an empty attributes pane
  useEffect(() => { if (firstTab.current && items.length && !selFids.length) { firstTab.current = false; setTab("issues"); } }, [items.length]);
  const nRed = items.filter((i) => i.tone === RED).length, nAmber = items.length - nRed;
  const nTotal = Object.keys(rows).length;
  const nClean = Math.max(0, nTotal - new Set(items.map((i) => i.fid).filter((f) => f != null)).size);
  const workFids = shown.filter((i) => i.fid != null && !i.err).map((i) => i.fid!) ;

  const goItem = (it: Item) => {
    if (it.err) {
      setCurErr(it.err.id);
      const f = esrc.current.getFeatures().find((x) => (x.get("err") as TopologyError).id === it.err!.id);
      const g = f?.getGeometry(); if (g) fitExt(g.getExtent(), 140);
      const fs = select.current?.getFeatures();
      keepTab.current = true;
      if (fs) { fs.clear(); [it.err.fid, ...it.err.related].forEach((fid) => { const x = fid != null && fsrc.current.getFeatureById(fid); if (x) fs.push(x as Feature); }); }
      setTool("modify");
      setTab("issues");
    } else if (it.fid != null) openFeature(it.fid, true);
  };
  const [fixing, setFixing] = useState<string | null>(null);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const [groupLimit, setGroupLimit] = useState<Record<string, number>>({});
  type Group = { id: string; title: string; tone: string; code: string | null; items: Item[]; fixable: number };
  const groups: Group[] = useMemo(() => {
    const m = new Map<string, Group>();
    shown.forEach((it) => {
      const id = it.err ? `t:${it.err.code}:${it.title}` : it.tone === RED ? "a:error" : "a:fill";
      if (!m.has(id)) m.set(id, { id, title: it.err ? it.title : it.tone === RED ? "Values not accepted" : "Fields to fill", tone: it.tone,
                                  code: it.err ? it.err.code : null, items: [], fixable: 0 });
      const g = m.get(id)!; g.items.push(it); if (it.err?.action && !it.err.tried) g.fixable++;
    });
    return [...m.values()].sort((a, b) => b.fixable - a.fixable || b.items.length - a.items.length);
  }, [shown]);
  const nFixable = groups.reduce((a, g) => a + g.fixable, 0);
  const curE = curErr != null ? errs.find((e) => e.id === curErr) || null : null;
  const [report, setReport] = useState<AutofixReport | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => { api.autofixReport(job.job_id).then(setReport).catch(() => setReport(null)); if (!busy) setRunning(false); }, [results?.generated_at, job.job_id]);
  const runAutofix = async (codes?: string[]) => {
    try { setRunning(true); onJob(await api.autofixRun(job.job_id, codes)); onNotice("Auto-Fix…"); }
    catch (e: any) { setRunning(false); onError(String(e.message || e)); }
  };
  const undoAutofix = async () => {
    try { onJob(await api.autofixUndo(job.job_id)); onNotice("Undone"); } catch (e: any) { onError(String(e.message || e)); }
  };
  const [prev, setPrev] = useState<{ key: string; note: string } | null>(null);
  const clearPreview = () => { psrc.current.clear(); setPrev(null); };
  useEffect(clearPreview, [curErr, errs]);
  const showPreview = async (e: TopologyError) => {
    try {
      const r = await api.autofixPreview(job.job_id, e.key);
      psrc.current.clear();
      if (!r.action) { onError(r.reason || "No automatic correction"); return; }
      if (r.geometry) {
        const g = fmt.current.readGeometry(r.geometry, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj });
        psrc.current.addFeature(new Feature({ geometry: g }));
        fitExt(g.getExtent(), 160);
      }
      setPrev({ key: e.key, note: r.delete ? "the duplicate is removed" : r.note || "" });
    } catch (er: any) { onError(String(er.message || er)); }
  };
  const autofix = async (keys: string[], bulk: boolean) => {
    if (!keys.length || fixing) return;
    setFixing(bulk ? "*" : keys[0]);
    try {
      const r = await api.topologyAutofix(job.job_id, keys, bulk);
      if (r.fixed.length) {
        onNotice(`Fixed ${r.fixed.length}`);
        onValidate();
      } else onError(r.skipped[0]?.reason || "Nothing could be corrected automatically");
    } catch (e: any) { onError(String(e.message || e)); } finally { setFixing(null); }
  };
  const toggleException = async (e: TopologyError) => {
    try {
      await api.topologyException(job.job_id, e.key, true);
      setErrs((xs) => xs.map((x) => (x.key === e.key ? { ...x, exception: true } : x)));
      onNotice("Kept as is (exception).");
    } catch (er: any) { onError(String(er.message || er)); }
  };

  const nextOpen = (fromRows = rows, cur: number | null = null) => {
    const order = workFids.length ? workFids : Object.values(fromRows).filter((r) => r.state !== "completed" && (!focus || r.target === focus)).map((r) => r.fid);
    const i = cur != null ? order.indexOf(cur) : -1;
    const isOpen = (f: number) => f !== cur && !!fromRows[f] && (fromRows[f].state === "incomplete" || fromRows[f].state === "error");
    for (let k = 1; k <= order.length; k++) { const f = order[(i + k + order.length) % order.length]; if (isOpen(f)) return f; }
    return null;
  };
    // keyboard: Ctrl+Enter save & next · Ctrl+Z undo · Delete
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const typing = /INPUT|TEXTAREA/.test((e.target as HTMLElement)?.tagName || "");
      if (!typing && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); doUndo(); }
      else if (!typing && e.key === "Delete") del();
      else if (!typing && !e.ctrlKey && !e.metaKey) {
        const k = e.key.toLowerCase();
        if (k === "v") setTool("select"); if (k === "b") setTool("box"); if (k === "e") setTool("modify"); if (k === "m") setTool("move");
        if (k === "d" && drawCls) setTool("draw");
        if (k === "escape") setTool("select");
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  // ---------------------------------------------------------------- render
  const lock = saving || busy;
  const hoverRow = hover ? rows[hover.fid] : null;
  const classes = ov ? Object.entries(ov.classes).filter(([k]) => byClass[k]) : [];

  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "minmax(0,1fr) 360px" }, height: "calc(100vh - 150px)", minHeight: 560,
               border: `1px solid ${C.line}`, borderRadius: 2, overflow: "hidden", bgcolor: "#fff" }}>
      {/* ================= map */}
      <Box sx={{ position: "relative", minWidth: 0 }}>
        <Box ref={el} sx={{ position: "absolute", inset: 0, bgcolor: "#F4F5F7", cursor: tool === "box" ? "crosshair" : undefined,
                             "& .ol-attribution button": { width: 26, height: 26 } }} />
        {(!preview || busy) && <LinearProgress sx={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, zIndex: 3 }} />}

        {/* toolbar */}
        <Stack direction="row" sx={{ position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)", bgcolor: "#fff", borderRadius: 2.5, p: 0.5, gap: 0.25,
                                      boxShadow: "0 4px 18px rgba(16,24,40,.14)", zIndex: 2 }}>
          <TI t="Select  (V)" on={tool === "select"} onClick={() => setTool("select")}><NearMeOutlined /></TI>
          <TI t="Select by box  (B)" on={tool === "box"} onClick={() => setTool("box")}><HighlightAltOutlined /></TI>
          <Sep />
          <TI t="Edit vertices  (E) — drag a vertex · click an edge to add one · Alt+click to remove" on={tool === "modify"} disabled={lock} onClick={() => setTool("modify")}><PolylineOutlined /></TI>
          <TI t="Move  (M)" on={tool === "move"} disabled={lock} onClick={() => setTool("move")}><OpenWithOutlined /></TI>
          <TI t={drawCls && tool === "draw" ? `Drawing ${ov?.classes[drawCls]?.alias} — click to place, double-click to finish` : "Draw a new feature  (D)"} on={tool === "draw"} disabled={lock}
            onClick={(e) => setDrawMenu(e!.currentTarget as HTMLElement)}><DrawOutlined /></TI>
          <TI t={nSel === 1 ? "Split — draw a line across the selected feature" : "Split — select one line or polygon first"} on={tool === "split"} disabled={lock || nSel !== 1}
            onClick={() => setTool(tool === "split" ? "select" : "split")}><ContentCutOutlined /></TI>
          <TI t={nSel >= 2 ? `Merge the ${nSel} selected features (one layer)` : "Merge — select two or more features of one layer"} disabled={lock || nSel < 2} onClick={mergeSel}><CallMergeOutlined /></TI>
          <TI t="Explode — one feature per part of a multipart feature" disabled={lock || nSel !== 1} onClick={explodeSel}><CallSplitOutlined /></TI>
          <TI t="Delete  (Del)" disabled={lock || !nSel} onClick={del}><DeleteOutline /></TI>
          <Sep />
          <TI t={snapOn ? "Snapping on" : "Snapping off"} on={snapOn} accent onClick={() => setSnapOn(!snapOn)}><CenterFocusWeakOutlined /></TI>
          <Sep />
          <TI t="Undo  (Ctrl+Z)" disabled={!undoN || lock} onClick={doUndo}><UndoOutlined /></TI>
          <TI t={nPending ? `Save ${nPending} edit${nPending > 1 ? "s" : ""} — checks run again` : "No geometry edits"} disabled={!nPending || lock} onClick={saveGeometry}>
            <Badge badgeContent={nPending} color="warning" sx={{ "& .MuiBadge-badge": { fontSize: 10, height: 16, minWidth: 16 } }}>
              {saving ? <CircularProgress size={18} /> : <SaveOutlined />}
            </Badge>
          </TI>
        </Stack>

        {/* layers */}
        <Box sx={{ position: "absolute", top: 12, left: 12, width: 210, maxHeight: "calc(100% - 24px)", overflow: "auto", bgcolor: "#fff", borderRadius: 2.5,
                   boxShadow: "0 4px 18px rgba(16,24,40,.12)", p: 0.75, zIndex: 2 }}>
          <LayerItem label="All layers" active={!focus} n={items.length + noLevel.size} onClick={() => { setFocus(null); fitAll(); }} />
          {classes.map(([k, c]) => (
            <LayerItem key={k} label={c.alias} color={colorOf(k)} active={focus === k} hidden={!!hidden[k]} n={byClass[k].open}
              onEye={() => setHidden((h) => ({ ...h, [k]: !h[k] }))}
              onClick={() => {
                setFocus(focus === k ? null : k); setHidden((h) => ({ ...h, [k]: false }));
                const ext = fsrc.current.getFeatures().filter((f) => f.get("target") === k).reduce((a: number[], f) => {
                  const e = f.getGeometry()!.getExtent(); return [Math.min(a[0], e[0]), Math.min(a[1], e[1]), Math.max(a[2], e[2]), Math.max(a[3], e[3])]; }, [Infinity, Infinity, -Infinity, -Infinity]);
                if (focus !== k) fitExt(ext);
              }} />
          ))}
        </Box>

        {/* view */}
        <Stack spacing={0.75} sx={{ position: "absolute", right: 12, bottom: 28, zIndex: 2 }}>
          <MB t="Zoom in" onClick={() => { const v = map.current!.getView(); v.animate({ zoom: (v.getZoom() || 0) + 1, duration: 200 }); }}>+</MB>
          <MB t="Zoom out" onClick={() => { const v = map.current!.getView(); v.animate({ zoom: (v.getZoom() || 0) - 1, duration: 200 }); }}>−</MB>
          <MB t="Whole drawing" onClick={fitAll}><ZoomOutMapOutlined sx={{ fontSize: 18 }} /></MB>
          {geo && <MB t="Basemap" onClick={(e) => setBaseMenu(e.currentTarget)}><MapOutlined sx={{ fontSize: 18 }} /></MB>}
        </Stack>
        <Menu anchorEl={drawMenu} open={!!drawMenu} onClose={() => setDrawMenu(null)} anchorOrigin={{ vertical: "bottom", horizontal: "left" }}>
          <MenuItem disabled dense sx={{ fontSize: 12, opacity: "1 !important", color: C.muted }}>New feature in…</MenuItem>
          {classes.map(([k, c]) => (
            <MenuItem key={k} dense selected={k === drawCls} onClick={() => { setDrawCls(k); setTool("draw"); setDrawMenu(null); }}>
              <Box sx={{ width: 10, height: 10, borderRadius: 0.75, bgcolor: colorOf(k), mr: 1.25 }} />{c.alias}
              <Typography component="span" sx={{ ml: 1.5, fontSize: 11, color: C.muted }}>{c.geometry}</Typography>
            </MenuItem>
          ))}
        </Menu>
        <Menu anchorEl={baseMenu} open={!!baseMenu} onClose={() => setBaseMenu(null)}>
          {Object.entries(BASEMAPS).map(([k, b]) => <MenuItem key={k} dense selected={k === basemap}
            onClick={() => { setBasemap(k); try { localStorage.setItem("gsb_basemap", k); } catch { /* ignore */ } setBaseMenu(null); }}>{b.label}</MenuItem>)}
        </Menu>

        {hover && hoverRow && (
          <Box sx={{ position: "absolute", left: hover.x + 14, top: hover.y - 34, pointerEvents: "none", bgcolor: C.graphite, color: "#fff", px: 1.1, py: 0.5,
                     borderRadius: 1.5, fontSize: 12, fontWeight: 600, whiteSpace: "nowrap", zIndex: 3 }}>
            {ov?.classes[hoverRow.target]?.alias} #{hoverRow.objectid ?? hoverRow.fid}
            <Box component="span" sx={{ ml: 0.75, color: hoverRow.state === "completed" ? "#86EFAC" : hoverRow.state === "error" ? "#FCA5A5" : "#FCD34D" }}>
              · {hoverRow.state === "completed" ? "complete" : hoverRow.state === "error" ? "error" : `${hoverRow.missing.length} to fill`}
            </Box>
          </Box>
        )}
      </Box>

      {/* ================= side panel */}
      <Box sx={{ borderLeft: `1px solid ${C.line}`, display: "flex", flexDirection: "column", minHeight: 0 }}>
        {/* ---- the feature as it is in the drawing: always on top, its own scroll */}
        {cadFid != null && (
          <Box sx={{ flex: "0 1 auto", maxHeight: cadOpen ? "38%" : undefined, display: "flex", flexDirection: "column", borderBottom: `2px solid ${C.line}`, bgcolor: "#fff" }}>
            <Stack direction="row" alignItems="center" spacing={1} onClick={() => setCadP(!cadOpen)}
              sx={{ px: 1.5, py: 0.9, cursor: "pointer", flex: "none", "&:hover": { bgcolor: "#F2F4F7" } }}>
              {cadOpen ? <ExpandMoreRounded sx={{ fontSize: 18 }} /> : <ChevronRightRounded sx={{ fontSize: 18 }} />}
              <Typography sx={{ fontSize: 12.5, fontWeight: 700 }}>CAD</Typography>
              <Typography sx={{ fontSize: 12, color: C.muted, flex: 1, minWidth: 0 }} noWrap>{rows[cadFid]?.layer} · {rows[cadFid]?.handle}</Typography>
            </Stack>
            {cadOpen && <Box sx={{ flex: 1, minHeight: 0, overflowY: "scroll", overflowX: "hidden", bgcolor: "#fff", "&::-webkit-scrollbar": { width: 8 }, "&::-webkit-scrollbar-thumb": { bgcolor: "#C4C9D0", borderRadius: 8, border: "2px solid #fff" }, "&::-webkit-scrollbar-track": { bgcolor: "#F2F4F7" }, scrollbarWidth: "thin", scrollbarColor: "#C4C9D0 #F2F4F7" }}>
              <CadCompare jobId={job.job_id} fid={cadFid} ov={ov} embedded /></Box>}
          </Box>
        )}
        <Stack direction="row" alignItems="center" sx={{ borderBottom: `1px solid ${C.line}`, pr: 0.5 }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="fullWidth" sx={{ flex: 1, minHeight: 44, "& .MuiTab-root": { minHeight: 44, textTransform: "none", fontWeight: 650, fontSize: 13.5 } }}>
            <Tab value="attributes" label={<Stack direction="row" spacing={0.75} alignItems="center"><span>Attributes</span>
              {selFids.length > 0 && <Box component="span" sx={{ px: 0.75, borderRadius: 99, bgcolor: "#E6F1FB", color: "#1E4E8C", fontSize: 11, fontWeight: 700 }}>{selFids.length.toLocaleString()}</Box>}</Stack>} />
            <Tab value="issues" label={<Stack direction="row" spacing={0.75} alignItems="center"><span>Issues</span>
              {items.length > 0 && <Box component="span" sx={{ px: 0.75, borderRadius: 99, bgcolor: openErrs.length ? "#FEE2E2" : "#FEF3C7", color: openErrs.length ? RED : "#92400E", fontSize: 11, fontWeight: 700 }}>{items.length}</Box>}</Stack>} />
          </Tabs>
        </Stack>

        {/* ---- attributes */}
        {tab === "attributes" && (
          <AttributesPane jobId={job.job_id} ov={ov} rows={rows} selection={selFids} color={colorOf} busy={busy} focus={focus}
            setSelection={setSelection} zoomTo={zoomTo} coordsOf={coordsOf} geographic={geo}
            onRows={(u) => setRows((r) => { const n = { ...r }; u.forEach((x) => { if (n[x.fid]) n[x.fid] = { ...n[x.fid], state: x.state, missing: x.missing, edited: true }; }); return n; })}
            onNext={(from) => { const n = nextOpen(rows, from); if (n != null) openFeature(n, true); else onNotice("All complete"); }}
            onError={onError} onNotice={onNotice} onActive={setCadFid}
            banner={(fid) => fid != null && noLevel.has(fid) ? <LevelFix n={noLevel.size} job={job} onPatch={onPatch} onValidate={onValidate} onOther={() => openDetails("attributes")} /> : null} />
        )}

                {/* ---- issues */}
        {tab === "issues" && (
          <Box sx={{ flex: 1, overflow: "auto" }}>
            {focus && <Stack direction="row" alignItems="center" sx={{ px: 2, py: 0.75, bgcolor: "#F7F8FA", borderBottom: `1px solid ${C.line}` }}>
              <Typography sx={{ fontSize: 12, flex: 1 }}>Only <b>{ov?.classes[focus]?.alias}</b></Typography>
              <IconButton size="small" onClick={() => setFocus(null)}><CloseOutlined sx={{ fontSize: 16 }} /></IconButton></Stack>}

            {/* where the check stands, in three numbers */}
            <Box sx={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", borderBottom: `1px solid ${C.line}`, textAlign: "center" }}>
              {([["Errors", nRed, nRed ? RED : C.muted], ["To fill", nAmber, nAmber ? AMBER : C.muted], ["Clean", nClean, C.green]] as [string, number, string][]).map(([k, v, c], i) => (
                <Box key={k} sx={{ py: 1.25, borderLeft: i ? `1px solid ${C.line}` : undefined }}>
                  <Typography sx={{ fontSize: 20, fontWeight: 500, lineHeight: 1.2, color: c, fontVariantNumeric: "tabular-nums" }}>{v.toLocaleString()}</Typography>
                  <Typography sx={{ fontSize: 12, color: C.muted }}>{k}</Typography>
                </Box>
              ))}
            </Box>

            {/* Auto-Fix: one button, with what the last run did */}
            <Box sx={{ m: 1.5, p: 1.5, borderRadius: 2, border: `1px solid ${C.line}`, bgcolor: "#FAFBFC" }}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <AutoFixHighOutlined sx={{ fontSize: 20, color: C.cyan }} />
                <Tooltip title="Fix → check → undo what got worse"><Typography sx={{ fontSize: 13.5, fontWeight: 700, flex: 1 }}>Auto-Fix</Typography></Tooltip>
                <Button size="small" variant="contained" disabled={busy || !nFixable} onClick={() => runAutofix()}
                  startIcon={busy && running ? <CircularProgress size={14} color="inherit" /> : undefined}>{nFixable ? `Fix ${nFixable}` : "None"}</Button>
              </Stack>
              {report && report.at && (
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1.25, pt: 1, borderTop: `1px dashed ${C.line}` }}>
                  <Typography sx={{ fontSize: 12, flex: 1 }}>
                    <b style={{ color: C.green }}>✓ {report.fixed_n}</b>
                    {report.reverted_n ? <> · <b style={{ color: AMBER }}>↺ {report.reverted_n}</b></> : null}
                    {" "}· {report.before} → <b>{report.after}</b>
                  </Typography>
                  {report.undo && <Button size="small" disabled={busy} onClick={undoAutofix} sx={{ color: C.muted }}>Undo</Button>}
                </Stack>
              )}
            </Box>

            {/* the guide for the finding in hand */}
            {curE && (
              <Box sx={{ mx: 1.5, mb: 1.5, p: 1.5, borderRadius: 2, bgcolor: "#FFFBEB", border: "1px solid #FDE68A" }}>
                <Stack direction="row" alignItems="center" spacing={1}>
                  <Typography sx={{ fontSize: 13.5, fontWeight: 700, flex: 1 }}>{curE.rule.replace(/^[A-Z0-9_]+:\s*/, "")}</Typography>
                  <IconButton size="small" onClick={() => { setCurErr(null); clearPreview(); }}><CloseOutlined sx={{ fontSize: 16 }} /></IconButton>
                </Stack>
                <Typography sx={{ fontSize: 12.5, mt: 0.25, color: "#7C5E10" }}>{curE.guide?.why} → {curE.guide?.how}</Typography>
                {curE.tried && <Typography sx={{ fontSize: 11.5, mt: 0.25, color: AMBER }}>↺ {curE.tried.replace(/^reverted: /, "")}</Typography>}
                {prev?.note && <Typography sx={{ fontSize: 11.5, mt: 0.25, color: C.green }}>{prev.note}</Typography>}
                <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }} flexWrap="wrap" useFlexGap>
                  {curE.action && (prev?.key === curE.key
                    ? <Button size="small" variant="contained" disabled={!!fixing || busy} onClick={() => autofix([curE.key], false)}>✓ {curE.action}</Button>
                    : <Button size="small" variant="contained" onClick={() => showPreview(curE)}>{curE.action}?</Button>)}
                  <Button size="small" variant="outlined" startIcon={<PolylineOutlined />} onClick={() => setTool("modify")}>Edit</Button>
                  <Button size="small" color="inherit" onClick={() => toggleException(curE)}>Keep</Button>
                </Stack>
              </Box>
            )}

            {noLevel.size > 0 && <LevelFix n={noLevel.size} job={job} onPatch={onPatch} onValidate={onValidate} onOther={() => openDetails("attributes")} />}
            {!shown.length && !noLevel.size && <Empty icon={<CheckRounded />} text="No issues" tone={C.green} />}

            {/* findings grouped by rule */}
            {groups.map((g) => {
              const open = openGroups[g.id] ?? groups.length <= 3;
              return (
                <Box key={g.id}>
                  <Stack direction="row" alignItems="center" spacing={1} onClick={() => setOpenGroups((s) => ({ ...s, [g.id]: !open }))}
                    sx={{ px: 1.5, py: 0.9, bgcolor: "#F7F8FA", borderTop: `1px solid ${C.line}`, borderBottom: `1px solid ${C.line}`, cursor: "pointer" }}>
                    {open ? <ExpandMoreRounded sx={{ fontSize: 18 }} /> : <ChevronRightRounded sx={{ fontSize: 18 }} />}
                    <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: g.tone }} />
                    <Typography sx={{ fontSize: 13, fontWeight: 700, flex: 1, minWidth: 0 }} noWrap>{g.title}</Typography>
                    <Typography sx={{ fontSize: 12, color: C.muted }}>{g.items.length}</Typography>
                    {g.code && g.fixable > 0 && (
                      <Button size="small" sx={{ minWidth: 0, px: 1, py: 0, minHeight: 28 }} disabled={busy}
                        onClick={(e) => { e.stopPropagation(); runAutofix([g.code!]); }}>Fix {g.fixable}</Button>
                    )}
                  </Stack>
                  {open && g.items.slice(0, groupLimit[g.id] || 60).map((it) => (
                    <Stack key={it.k} direction="row" alignItems="center" spacing={1.25} onClick={() => goItem(it)}
                      sx={{ pl: 4.5, pr: 1.5, py: 0.75, borderBottom: `1px solid #F1F2F4`, cursor: "pointer",
                            bgcolor: (it.err && curErr === it.err.id) || (!it.err && it.fid === cur) ? "#F0F9FB" : undefined, "&:hover": { bgcolor: "#F7F8FA" } }}>
                      <Box sx={{ flex: 1, minWidth: 0 }}>
                        <Typography sx={{ fontSize: 12.5, fontWeight: 600 }} noWrap>{it.err ? it.sub : it.title}</Typography>
                        <Typography sx={{ fontSize: 11, color: it.err?.tried ? AMBER : C.muted }} noWrap>
                          {it.err ? (it.err.tried ? "↺ reverted" : it.err.related_class ? (ov?.classes[it.err.related_class]?.alias || it.err.related_class) : "") : it.sub}
                        </Typography>
                      </Box>
                      {it.err ? (<>
                        {it.err.action && (
                          <Button size="small" variant="outlined" disabled={!!fixing || busy} sx={{ minWidth: 0, px: 1, py: 0, minHeight: 28, whiteSpace: "nowrap" }}
                            onClick={(e) => { e.stopPropagation(); autofix([it.err!.key], false); }}>
                            {fixing === it.err.key ? <CircularProgress size={14} /> : it.err.action}
                          </Button>
                        )}
                        <Tooltip title="Keep as is (exception)"><IconButton size="small" onClick={(e) => { e.stopPropagation(); toggleException(it.err!); }}><DoNotDisturbOnOutlined sx={{ fontSize: 16 }} /></IconButton></Tooltip>
                      </>) : <Button size="small" variant="outlined" sx={{ minWidth: 0, px: 1, py: 0, minHeight: 28 }} onClick={(e) => { e.stopPropagation(); goItem(it); }}>Fill</Button>}
                    </Stack>
                  ))}
                  {open && g.items.length > (groupLimit[g.id] || 60) && (
                    <Button size="small" sx={{ ml: 4, my: 0.5 }} onClick={() => setGroupLimit((s) => ({ ...s, [g.id]: (s[g.id] || 60) + 200 }))}>
                      Show more ({(g.items.length - (groupLimit[g.id] || 60)).toLocaleString()})</Button>
                  )}
                </Box>
              );
            })}
            <Box sx={{ p: 1.5 }}>
              <Button size="small" startIcon={<TableRowsOutlined />} onClick={() => openDetails("issues")} sx={{ color: C.muted }}>All checks as a table</Button>
            </Box>
          </Box>
        )}

      </Box>
    </Box>
  );
}

/** a drawer with the full tables (attribute sources, every check) — for the details, out of the way */
export function DetailsDrawer({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <Drawer anchor="right" open={open} onClose={onClose} PaperProps={{ sx: { width: { xs: "100%", md: "min(1100px, 90vw)" }, bgcolor: "#F2F3F5" } }}>
      <Stack direction="row" alignItems="center" sx={{ px: 2.5, py: 1.5, bgcolor: "#fff", borderBottom: `1px solid ${C.line}`, position: "sticky", top: 0, zIndex: 3 }}>
        <Typography sx={{ fontWeight: 700, fontSize: 16, flex: 1 }}>{title}</Typography>
        <IconButton onClick={onClose}><CloseOutlined /></IconButton>
      </Stack>
      <Box sx={{ p: 2.5 }}>{children}</Box>
    </Drawer>
  );
}

function TI({ t, on, onClick, disabled, children, accent }: { t: string; on?: boolean; onClick: (e?: React.MouseEvent<HTMLElement>) => void; disabled?: boolean; children: React.ReactNode; accent?: boolean }) {
  return (
    <Tooltip title={t}>
      <span>
        <IconButton onClick={(e) => onClick(e)} disabled={disabled} aria-label={t}
          sx={{ width: 38, height: 38, borderRadius: 1.75, color: on ? (accent ? SEL : "#fff") : C.ink, bgcolor: on ? (accent ? "#E3F3F6" : C.ink) : "transparent",
                "&:hover": { bgcolor: on ? (accent ? "#D4EDF2" : C.ink) : "#F1F2F4" }, "& svg": { fontSize: 20 } }}>
          {children}
        </IconButton>
      </span>
    </Tooltip>
  );
}
const Sep = () => <Box sx={{ width: "1px", bgcolor: C.line, my: 0.75, mx: 0.25 }} />;

function MB({ t, onClick, children }: { t: string; onClick: (e: React.MouseEvent<HTMLElement>) => void; children: React.ReactNode }) {
  return (
    <Tooltip title={t} placement="left">
      <IconButton onClick={onClick} aria-label={t} sx={{ width: 36, height: 36, borderRadius: 1.75, bgcolor: "#fff", color: C.ink, fontSize: 18, fontWeight: 500,
        boxShadow: "0 2px 8px rgba(16,24,40,.14)", "&:hover": { bgcolor: "#fff" } }}>{children}</IconButton>
    </Tooltip>
  );
}

function LayerItem({ label, color, active, hidden, n, onClick, onEye }: { label: string; color?: string; active: boolean; hidden?: boolean; n: number; onClick: () => void; onEye?: () => void }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1} onClick={onClick}
      sx={{ px: 1, py: 0.6, borderRadius: 1.5, cursor: "pointer", bgcolor: active ? "#F0F1F3" : undefined, opacity: hidden ? 0.45 : 1,
            "&:hover": { bgcolor: "#F4F5F7" }, "&:hover .eye": { opacity: 1 } }}>
      {color ? <Box sx={{ width: 10, height: 10, borderRadius: 0.75, bgcolor: color, flex: "none" }} /> : null}
      <Typography sx={{ fontSize: 13, fontWeight: active ? 700 : 500, flex: 1, minWidth: 0 }} noWrap>{label}</Typography>
      {onEye && (
        <IconButton className="eye" size="small" onClick={(e) => { e.stopPropagation(); onEye(); }} sx={{ p: 0.25, opacity: hidden ? 1 : 0 }}>
          {hidden ? <VisibilityOff sx={{ fontSize: 15 }} /> : <Visibility sx={{ fontSize: 15, color: C.muted }} />}
        </IconButton>
      )}
      {n > 0 ? <Box sx={{ px: 0.75, minWidth: 20, textAlign: "center", borderRadius: 99, bgcolor: "#FEF3C7", color: "#92400E", fontSize: 11, fontWeight: 700 }}>{n}</Box>
        : <CheckRounded sx={{ fontSize: 15, color: C.green }} />}
    </Stack>
  );
}



function Empty({ icon, text, action, tone }: { icon: React.ReactNode; text: string; action?: React.ReactNode; tone?: string }) {
  return (
    <Stack alignItems="center" justifyContent="center" spacing={1} sx={{ flex: 1, p: 4, color: tone || C.muted, "& > svg": { fontSize: 30 } }}>
      {icon}<Typography sx={{ fontSize: 13.5 }}>{text}</Typography>{action}
    </Stack>
  );
}

/** features that cannot be written without a level: one card, three answers */
function LevelFix({ n, job, onPatch, onValidate, onOther }: { n: number; job: JobState; onPatch: (p: unknown) => Promise<void>; onValidate: () => void; onOther: () => void }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  return (
    <Box sx={{ m: 1.5, p: 1.5, borderRadius: 2, bgcolor: "#FEF2F2", border: "1px solid #FECACA" }}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: RED }} />
        <Typography sx={{ fontSize: 13, fontWeight: 700, flex: 1 }}>{n.toLocaleString()} without elevation</Typography>
      </Stack>
      <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }} flexWrap="wrap" useFlexGap>
        <Button size="small" variant="contained" disabled={busy} onClick={async () => {
          setBusy(true); setMsg("");
          try { await api.prepareDem(job.job_id, "copernicus_glo30"); onValidate(); }
          catch (e: any) { setMsg(`${e.message || "DEM not available"} — needs internet`); } finally { setBusy(false); }
        }}>{busy ? "Fetching…" : "Free DEM"}</Button>
        <Button size="small" variant="outlined" onClick={onOther}>My DEM</Button>
        <Tooltip title="Write now with the no-data level -9999 (reported in the QC report)">
          <Button size="small" color="inherit" disabled={busy} onClick={async () => { await onPatch({ elevation: { missing_policy: "nodata_value", nodata_value: -9999 } }); onValidate(); }}>-9999</Button>
        </Tooltip>
      </Stack>
      {msg && <Typography sx={{ fontSize: 11.5, color: RED, mt: 0.75 }}>{msg}</Typography>}
    </Box>
  );
}
