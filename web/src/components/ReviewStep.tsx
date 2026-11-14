/**
 * Review — one map-centred screen for everything after the layers are connected:
 * attributes (Feature / Layer tabs), validation and topology findings (Issues tab), and geometry editing
 * (icon toolbar on the map). Geometry saves re-run the validation automatically.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Badge, ButtonBase, Box, Button, Checkbox, CircularProgress, Drawer, IconButton, LinearProgress, Menu, MenuItem, Stack, Tab, Tabs,
  Tooltip, Typography,
} from "@mui/material";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import VectorImageLayer from "ol/layer/VectorImage";
import { getLength, getArea } from "ol/sphere";
import { toLonLat, getPointResolution } from "ol/proj";
import LineString from "ol/geom/LineString";
import MultiLineString from "ol/geom/MultiLineString";
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
import { never, always } from "ol/events/condition";
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
import BuildOutlined from "@mui/icons-material/BuildOutlined";
import ExpandMoreRounded from "@mui/icons-material/ExpandMoreRounded";
import ChevronRightRounded from "@mui/icons-material/ChevronRightRounded";
import CallMergeOutlined from "@mui/icons-material/CallMergeOutlined";
import CallSplitOutlined from "@mui/icons-material/CallSplitOutlined";
import DrawOutlined from "@mui/icons-material/DrawOutlined";
import ContentCutOutlined from "@mui/icons-material/ContentCutOutlined";
import RedoOutlined from "@mui/icons-material/RedoOutlined";
import ClearAllOutlined from "@mui/icons-material/BackspaceOutlined";
import SwapHorizOutlined from "@mui/icons-material/SwapHorizOutlined";
import StraightenOutlined from "@mui/icons-material/StraightenOutlined";
import SquareFootOutlined from "@mui/icons-material/SquareFootOutlined";
import CenterFocusStrongOutlined from "@mui/icons-material/CenterFocusStrongOutlined";
import DeselectOutlined from "@mui/icons-material/DeselectOutlined";
import ArrowDropDownRounded from "@mui/icons-material/ArrowDropDownRounded";
import ZoomInOutlined from "@mui/icons-material/ZoomInOutlined";
import ZoomOutOutlined from "@mui/icons-material/ZoomOutOutlined";
import { api } from "../api";
import type { AutofixReport, AssignOverview, AssignRow, GeoJSON, JobState, Results, TopologyError } from "../types";
import { BASEMAPS, basemapLayers } from "./MapView";
import { nice } from "./FeatureEditor";
import { AttributesPane } from "./AttributesPane";
import { CadCompare } from "./CadCompare";
import { C } from "../theme";

type Tool = "select" | "box" | "modify" | "move" | "draw" | "split" | "distance" | "area";
type TabK = "attributes" | "issues";
const LOCAL = new Projection({ code: "LOCAL-CAD", units: "m", extent: [-1e9, -1e9, 1e9, 1e9] });
// layer colours: strong enough on imagery, calm on light gray; one hue per feature class
const PAL = ["#2FA3B5", "#E0952F", "#6F8FE0", "#8DBB55", "#D06DB0", "#E46A4C", "#46B88C", "#A98BE6", "#D8C24B", "#5FA8E8"];
const RED = "#E5484D", AMBER = "#E8A33D", SEL = "#00D8FF", PEND = "#FF7A1A";   // map-only: brighter than the UI tokens so they read on imagery
const SEL_UI = C.cyan;
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
  const msrc = useRef(new VectorSource());          // measurements
  const flayer = useRef<any>(null);
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
  const [hover, setHover] = useState<{ fid: number } | null>(null);
  const clickRef = useRef<((e: any) => void) | null>(null);
  const [pick, setPick] = useState<{ x: number; y: number; fids: number[] } | null>(null);   // overlapping features under one click
  const tipEl = useRef<HTMLDivElement>(null);
  const xyEl = useRef<HTMLSpanElement>(null);
  const scaleEl = useRef<HTMLSpanElement>(null);
  const geoRef = useRef(false);
  const writeXY = (c: number[]) => {
    if (!xyEl.current || !c) return;
    if (geoRef.current) { const [lo, la] = toLonLat(c); xyEl.current.textContent = `${la.toFixed(6)}° N  ${lo.toFixed(6)}° E`; }
    else xyEl.current.textContent = `X ${c[0].toFixed(2)}  Y ${c[1].toFixed(2)} m`;
  };
  const writeScale = () => {
    const v = map.current?.getView(); if (!v || !scaleEl.current) return;
    const r = v.getResolution() || 0, c = v.getCenter() || [0, 0];
    const mpp = geoRef.current ? getPointResolution(v.getProjection(), r, c, "m") : r;
    scaleEl.current.textContent = `1:${Math.round(mpp * 96 / 0.0254).toLocaleString()}`;
  };
  const [snapOpt, setSnapOpt] = useState({ vertex: true, edge: true, tol: 10 });
  const [snapMenu, setSnapMenu] = useState<HTMLElement | null>(null);
  const [measure, setMeasure] = useState<string | null>(null);
  const [rtab, setRtab] = useState<"map" | "edit">(() => { try { return localStorage.getItem("gsb_ribbon") === "map" ? "map" : "edit"; } catch { return "edit"; } });
  const setRtabP = (k: "map" | "edit") => { setRtab(k); try { localStorage.setItem("gsb_ribbon", k); } catch { /* ignore */ } };
  const zoomBy = (d: number) => { const v = map.current?.getView(); if (v) v.animate({ zoom: (v.getZoom() || 0) + d, duration: 200 }); };
  const redo = useRef<{ kind: "geom" | "add" | "delete"; fid: number; geom?: any; feature?: Feature }[][]>([]);
  const [redoN, setRedoN] = useState(0);
  const [baseMenu, setBaseMenu] = useState<HTMLElement | null>(null);
  const [basemap, setBasemap] = useState<string>(() => { try { return localStorage.getItem("gsb_basemap") || "satellite"; } catch { return "satellite"; } });
  const st = useRef({ rows, hidden, focus, curErr });
  st.current = { rows, hidden, focus, curErr };

  useEffect(() => {
    if (!el.current) return;
    const cache: Record<string, Style[]> = {};
    const ecache: Record<string, Style> = {};
    const fl = new VectorImageLayer({
      source: fsrc.current, imageRatio: 1.5,
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
        const ek = `${on}|${kindOf(f.getGeometry()?.getType())}`;
        if (ecache[ek]) return ecache[ek];
        return ecache[ek] = new Style({
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
    const ml = new VectorLayer({ source: msrc.current, style: [
      new Style({ stroke: new Stroke({ color: "rgba(0,0,0,.5)", width: 5 }) }),
      new Style({ stroke: new Stroke({ color: "#FFE14D", width: 2.5, lineDash: [10, 6] }), fill: new Fill({ color: "rgba(255,225,77,.18)" }) })] });
    const m = new OlMap({ target: el.current, layers: [...tl, fl, el_, pl, ml], view: new View({ center: [0, 0], zoom: 2 }),
      controls: defaultControls({ attribution: true, rotate: false, zoom: false }).extend([new ScaleLine()]) });
    const sel = new Select({
      layers: [fl], condition: never, hitTolerance: 6,   // clicks are handled below (overlaps, modifiers, issue markers)
      style: (f: FeatureLike) => [
        new Style({ stroke: new Stroke({ color: "rgba(0,0,0,.55)", width: 7 }), image: new CircleStyle({ radius: 10, fill: new Fill({ color: "rgba(0,0,0,.55)" }) }) }),
        new Style({ stroke: new Stroke({ color: SEL, width: 3 }), fill: new Fill({ color: SEL + "30" }),
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
    let lastFid: number | null = null;
    m.on("pointermove", (e) => {
      if (e.dragging) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        writeXY(e.coordinate);
        const f = m.forEachFeatureAtPixel(e.pixel, (x) => x, { hitTolerance: 6, layerFilter: (l) => l === fl });
        const fid = f ? (f.get("fid") as number) : null;
        if (tipEl.current) tipEl.current.style.transform = `translate(${e.pixel[0] + 14}px, ${e.pixel[1] - 34}px)`;
        if (fid !== lastFid) { lastFid = fid; setHover(fid != null ? { fid } : null); }
      });
    });
    m.getView().on("change:resolution", writeScale);
    m.on("moveend", writeScale);
    map.current = m;
    m.on("click", (e) => clickRef.current?.(e));
    return () => { cancelAnimationFrame(raf); m.setTarget(undefined); };
  }, []);

  const geo = !!preview?.crs_is_geographic;
  geoRef.current = geo;
  const proj = geo ? "EPSG:3857" : LOCAL;
  const loadFeatures = () => {
    const m = map.current;
    if (!m || !preview) return;
    if (m.getView().getProjection().getCode() !== (geo ? "EPSG:3857" : LOCAL.getCode())) m.setView(new View({ projection: proj, center: [0, 0], zoom: 2 }));
    const keep = ((select.current?.getFeatures().getArray() || []) as Feature[]).map((f) => f.get("fid") as number);
    fsrc.current.clear();
    select.current?.getFeatures().clear();
    const data = { ...preview, features: preview.features.filter((f: any) => f.properties?.fid != null && f.properties.status !== "skipped") };
    const feats = fmt.current.readFeatures(data, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj }) as Feature[];
    original.current = {};
    feats.forEach((f) => { f.setId(f.get("fid")); original.current[f.get("fid")] = JSON.stringify((f.getGeometry() as any)?.getCoordinates?.()); });
    fsrc.current.addFeatures(feats);
    undo.current = []; setUndoN(0); setPending({});
    const back = keep.map((id) => fsrc.current.getFeatureById(id)).filter(Boolean) as Feature[];
    if (back.length) select.current?.getFeatures().extend(back);
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
    sel.setActive(tool !== "box" && tool !== "draw" && tool !== "split" && tool !== "distance" && tool !== "area");
    const snapshot = (fs: Feature[]) => { undo.current.push(fs.map((f) => ({ fid: f.get("fid"), geom: f.getGeometry()!.clone() }))); setUndoN(undo.current.length); clearRedo(); };
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
      box.on("boxend", (e: any) => {
        const ev = e.mapBrowserEvent?.originalEvent as MouseEvent | undefined;
        const add = !!ev?.shiftKey, remove = !!(ev?.ctrlKey || ev?.metaKey);
        const fs = sel.getFeatures(); if (!add && !remove) fs.clear();
        const ext = box.getGeometry().getExtent();
        const have = new Set(fs.getArray());
        fsrc.current.forEachFeatureIntersectingExtent(ext, (f) => {
          if (st.current.hidden[String(f.get("target"))] || !f.getGeometry()?.intersectsExtent(ext)) return;   // the shape itself, not just its bounding box
          if (remove) { if (have.has(f)) fs.remove(f); } else if (!have.has(f)) fs.push(f);
        });
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
        undo.current.push([{ fid, feature: f, added: true }]); setUndoN(undo.current.length); clearRedo();
        setPending((p) => ({ ...p, [fid]: "add" }));
      });
      m.addInteraction(dr); inter.current.draw = dr;
    }
    if (tool === "split") {
      const dr = new Draw({ type: "LineString" });
      dr.on("drawend", (e) => { splitWith(e.feature as Feature); });
      m.addInteraction(dr); inter.current.draw = dr;
    }
    if (tool === "distance" || tool === "area") {
      const dr = new Draw({ source: msrc.current, type: tool === "area" ? "Polygon" : "LineString" });
      const fmtLen = (m: number) => m >= 1000 ? `${(m / 1000).toFixed(3)} km` : `${m.toFixed(2)} m`;
      const fmtArea = (a: number) => a >= 10000 ? `${(a / 10000).toFixed(3)} ha  ·  ${Math.round(a).toLocaleString()} m²` : `${a.toFixed(2)} m²`;
      const read = (g: any) => {
        if (tool === "area") { const a = geoRef.current ? getArea(g) : g.getArea(); const ring = new LineString(g.getLinearRing(0).getCoordinates());
          return `Area ${fmtArea(a)}  ·  perimeter ${fmtLen(geoRef.current ? getLength(ring) : ring.getLength())}`; }
        return `Distance ${fmtLen(geoRef.current ? getLength(g) : g.getLength())}`;
      };
      dr.on("drawstart", (e) => { msrc.current.clear(); e.feature.getGeometry()!.on("change", (ev: any) => setMeasure(read(ev.target))); });
      dr.on("drawend", (e) => setMeasure(read(e.feature.getGeometry())));
      m.addInteraction(dr); inter.current.draw = dr;
    } else { msrc.current.clear(); setMeasure(null); }
    if (snapOn && (tool === "modify" || tool === "move" || tool === "draw" || tool === "split" || tool === "distance" || tool === "area")) {
      const sn = new Snap({ source: fsrc.current, pixelTolerance: snapOpt.tol, vertex: snapOpt.vertex, edge: snapOpt.edge });
      m.addInteraction(sn); inter.current.snap = sn;
    }
    setPick(null);
  }, [tool, snapOn, drawCls, snapOpt]);

  const del = () => {
    const fs = (select.current?.getFeatures().getArray().slice() || []) as Feature[];
    if (!fs.length) return;
    undo.current.push(fs.map((f) => ({ fid: f.get("fid"), feature: f, deleted: true }))); setUndoN(undo.current.length); clearRedo();
    fs.forEach((f) => fsrc.current.removeFeature(f));
    select.current?.getFeatures().clear();
    setPending((p) => { const n = { ...p }; fs.forEach((f) => { const id = f.get("fid") as number; if (id < 0) delete n[id]; else n[id] = "delete"; }); return n; });
  };
  const clearRedo = () => { redo.current = []; setRedoN(0); };
  /** pending state of each fid, read from the map itself (present? moved? new?) */
  const recalc = (fids: number[]) => setPending((p) => {
    const n = { ...p };
    fids.forEach((fid) => {
      const f = fsrc.current.getFeatureById(fid) as Feature | null;
      if (fid < 0) { if (f) n[fid] = "add"; else delete n[fid]; return; }
      if (!f) { n[fid] = "delete"; return; }
      if (JSON.stringify((f.getGeometry() as any)?.getCoordinates?.()) === original.current[fid]) { delete n[fid]; f.unset("pending"); }
      else { n[fid] = "geometry"; f.set("pending", true); }
    });
    return n;
  });
  const doUndo = () => {
    const last = undo.current.pop(); setUndoN(undo.current.length);
    if (!last) return;
    const back: { kind: "geom" | "add" | "delete"; fid: number; geom?: any; feature?: Feature }[] = [];
    last.forEach((u) => {
      if (u.added && u.feature) { if (fsrc.current.hasFeature(u.feature)) fsrc.current.removeFeature(u.feature); back.push({ kind: "add", fid: u.fid, feature: u.feature }); return; }
      if (u.deleted && u.feature) { if (!fsrc.current.hasFeature(u.feature)) fsrc.current.addFeature(u.feature); back.push({ kind: "delete", fid: u.fid, feature: u.feature }); return; }
      const f = fsrc.current.getFeatureById(u.fid) as Feature | null;
      if (f && u.geom) { back.push({ kind: "geom", fid: u.fid, geom: f.getGeometry()!.clone() }); f.setGeometry(u.geom); }
    });
    redo.current.push(back); setRedoN(redo.current.length);
    recalc(last.map((u) => u.fid));
  };
  const doRedo = () => {
    const next = redo.current.pop(); setRedoN(redo.current.length);
    if (!next) return;
    const fwd: { fid: number; geom?: any; feature?: Feature; deleted?: boolean; added?: boolean }[] = [];
    next.forEach((r) => {
      if (r.kind === "add" && r.feature) { fsrc.current.addFeature(r.feature); fwd.push({ fid: r.fid, feature: r.feature, added: true }); return; }
      if (r.kind === "delete" && r.feature) { if (fsrc.current.hasFeature(r.feature)) fsrc.current.removeFeature(r.feature); fwd.push({ fid: r.fid, feature: r.feature, deleted: true }); return; }
      const f = fsrc.current.getFeatureById(r.fid) as Feature | null;
      if (f && r.geom) { fwd.push({ fid: r.fid, geom: f.getGeometry()!.clone() }); f.setGeometry(r.geom); }
    });
    undo.current.push(fwd); setUndoN(undo.current.length);
    recalc(next.map((r) => r.fid));
  };
  const discardAll = () => { while (undo.current.length) doUndo(); clearRedo(); select.current?.getFeatures().clear(); };
  /** reverse the digitising direction of the selected lines (flow direction for pipes) */
  const flipSel = () => {
    const fs = ((select.current?.getFeatures().getArray() || []) as Feature[]).filter((f) => kindOf(f.getGeometry()?.getType()) === "line");
    if (!fs.length) { onError("Select one or more lines to flip."); return; }
    undo.current.push(fs.map((f) => ({ fid: f.get("fid"), geom: f.getGeometry()!.clone() }))); setUndoN(undo.current.length); clearRedo();
    fs.forEach((f) => {
      const g = f.getGeometry() as any;
      if (g instanceof LineString) f.setGeometry(new LineString(g.getCoordinates().slice().reverse()));
      else if (g instanceof MultiLineString) f.setGeometry(new MultiLineString(g.getCoordinates().map((c: any[]) => c.slice().reverse()).reverse()));
    });
    recalc(fs.map((f) => f.get("fid") as number));
    onNotice(`Flipped · ${fs.length}`);
  };
  const zoomSel = () => zoomTo(((select.current?.getFeatures().getArray() || []) as Feature[]).map((f) => f.get("fid") as number));
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
      setPending({}); undo.current = []; setUndoN(0); clearRedo();
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
  /** Map click while selecting: Shift adds, Ctrl removes, an issue marker opens its issue, overlapping features offer a choice. */
  clickRef.current = (e: any) => {
    const m = map.current, sel = select.current;
    if (!m || !sel || !sel.getActive() || e.dragging) return;
    setPick(null);
    const ev = e.originalEvent as MouseEvent;
    const add = ev.shiftKey, remove = ev.ctrlKey || ev.metaKey;
    const hits: Feature[] = [];
    m.forEachFeatureAtPixel(e.pixel, (f) => { if (!hits.includes(f as Feature)) hits.push(f as Feature); }, { hitTolerance: 6, layerFilter: (l) => l === flayer.current });
    // an issue marker is drawn on top: clicking it opens the issue (and selects the features involved)
    if (!add && !remove) {
      const errHit = m.forEachFeatureAtPixel(e.pixel, (f) => f, { hitTolerance: 4, layerFilter: (l) => l === elayer.current }) as Feature | undefined;
      const te = errHit?.get("err") as TopologyError | undefined;
      const it = te && items.find((i) => i.err?.id === te.id);
      if (it) { goItem(it); return; }
    }
    if (!hits.length) { if (!add && !remove) sel.getFeatures().clear(); return; }
    // points and lines win over the area they sit on, then the smaller shape
    const rank = (f: Feature) => { const k = kindOf(f.getGeometry()?.getType()); const x = f.getGeometry()!.getExtent(); return (k === "point" ? 0 : k === "line" ? 1e12 : 2e12) + (x[2] - x[0]) * (x[3] - x[1]); };
    hits.sort((a, b) => rank(a) - rank(b));
    const fs = sel.getFeatures(), top = hits[0], on = fs.getArray().includes(top);
    if (remove) { if (on) fs.remove(top); return; }
    if (add) { if (on) fs.remove(top); else fs.push(top); return; }
    fs.clear(); fs.push(top);
    if (hits.length > 1) setPick({ x: e.pixel[0], y: e.pixel[1], fids: hits.slice(0, 12).map((f) => f.get("fid") as number) });
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
    try { setRunning(true); onJob(await api.autofixRun(job.job_id, codes)); onNotice("Fixing…"); }
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
      const k0 = e.key.toLowerCase();
      if (!typing && (e.ctrlKey || e.metaKey) && (k0 === "y" || (k0 === "z" && e.shiftKey))) { e.preventDefault(); doRedo(); }
      else if (!typing && (e.ctrlKey || e.metaKey) && k0 === "z") { e.preventDefault(); doUndo(); }
      else if (!typing && (e.ctrlKey || e.metaKey) && k0 === "s") { e.preventDefault(); saveGeometry(); }
      else if (!typing && e.key === "Delete") del();
      else if (!typing && !e.ctrlKey && !e.metaKey) {
        const k = e.key.toLowerCase();
        if (k === "v") setTool("select"); if (k === "b") setTool("box"); if (k === "e") setTool("modify"); if (k === "m") setTool("move");
        if (k === "d" && drawCls) setTool("draw");
        if (k === "escape" && tool === "select") { setPick(null); select.current?.getFeatures().clear(); }
        if (k === "escape") { if (inter.current.draw && (tool === "distance" || tool === "area" || tool === "draw" || tool === "split")) inter.current.draw.abortDrawing(); setTool("select"); }
        if (k === "f" && nSel) flipSel();
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
      {/* ================= map: edit ribbon · map · status bar */}
      <Box sx={{ display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0 }}>
        {/* ribbon: tabs, then the groups of the active tab */}
        <Box sx={{ flex: "none", borderBottom: `1px solid ${C.line}`, bgcolor: "#FAFBFC" }}>
          <Stack direction="row" role="tablist" aria-label="Map tools" sx={{ px: 1, gap: 0.5, borderBottom: `1px solid ${C.line}`, bgcolor: "#F7F8FA" }}>
            {(["map", "edit"] as const).map((k) => (
              <ButtonBase key={k} role="tab" aria-selected={rtab === k} onClick={() => setRtabP(k)}
                sx={{ px: 1.5, height: 30, fontSize: 13, fontWeight: rtab === k ? 600 : 400, color: rtab === k ? C.ink : C.muted,
                      boxShadow: rtab === k ? `inset 0 -2px 0 ${C.ink}` : "none", "&:hover": { color: C.ink } }}>
                {k === "map" ? "Map" : "Edit"}
                {k === "edit" && nPending > 0 && <Box component="span" sx={{ ml: 0.75, px: 0.6, borderRadius: 99, bgcolor: C.amberSoft, color: C.amber, fontSize: 11, fontWeight: 600 }}>{nPending}</Box>}
              </ButtonBase>
            ))}
          </Stack>
          <Stack direction="row" alignItems="stretch" sx={{ px: 1, py: 0.5, gap: 0.5, overflowX: "auto", scrollbarWidth: "thin" }}>
            {rtab === "map" ? (<>
              <Grp label="Navigate">
                <RB big t="Explore" k="V" tip="Pan, zoom and pick features · Shift+click adds · Ctrl+click removes" on={tool === "select"} onClick={() => setTool("select")}><NearMeOutlined /></RB>
                <Stack>
                  <RS t="Zoom in" onClick={() => zoomBy(1)}><ZoomInOutlined /></RS>
                  <RS t="Zoom out" onClick={() => zoomBy(-1)}><ZoomOutOutlined /></RS>
                </Stack>
                <Stack>
                  <RS t="Full extent" onClick={fitAll}><ZoomOutMapOutlined /></RS>
                  <RS t="Zoom to selection" disabled={!nSel} onClick={zoomSel}><CenterFocusStrongOutlined /></RS>
                </Stack>
              </Grp>
              <Grp label="Layer">
                <RB big t="Basemap" tip={geo ? "Change the basemap" : "The drawing has no real-world coordinates"} disabled={!geo}
                  onClick={(e) => setBaseMenu(e!.currentTarget as HTMLElement)}><MapOutlined /><ArrowDropDownRounded sx={{ mt: -1 }} /></RB>
              </Grp>
              <Grp label="Selection">
                <RB big t="Select by box" k="B" tip="Drag a box · Shift adds · Ctrl removes" on={tool === "box"} onClick={() => setTool("box")}><HighlightAltOutlined /></RB>
                <Stack>
                  <RS t="Clear" disabled={!nSel} onClick={() => select.current?.getFeatures().clear()}><DeselectOutlined /></RS>
                </Stack>
              </Grp>
              <Grp label="Inquiry" last>
                <RB big t="Distance" on={tool === "distance"} onClick={() => setTool(tool === "distance" ? "select" : "distance")}><StraightenOutlined /></RB>
                <RB big t="Area" on={tool === "area"} onClick={() => setTool(tool === "area" ? "select" : "area")}><SquareFootOutlined /></RB>
              </Grp>
            </>) : (<>
              <Grp label="Manage edits">
                <RB big t="Save" k="Ctrl+S" tip={nPending ? `Save ${nPending} edit${nPending > 1 ? "s" : ""} — checks run again` : "No geometry edits"} disabled={!nPending || lock} onClick={saveGeometry}>
                  <Badge badgeContent={nPending} color="warning" sx={{ "& .MuiBadge-badge": { fontSize: 10, height: 16, minWidth: 16 } }}>
                    {saving ? <CircularProgress size={20} /> : <SaveOutlined />}
                  </Badge>
                </RB>
                <Stack>
                  <RS t="Discard" tip="Throw away every unsaved geometry edit" disabled={!nPending || lock} onClick={discardAll}><ClearAllOutlined /></RS>
                  <Stack direction="row">
                    <RS t="Undo" k="Ctrl+Z" iconOnly disabled={!undoN || lock} onClick={doUndo}><UndoOutlined /></RS>
                    <RS t="Redo" k="Ctrl+Y" iconOnly disabled={!redoN || lock} onClick={doRedo}><RedoOutlined /></RS>
                  </Stack>
                </Stack>
              </Grp>
              <Grp label="Snapping">
                <RB big t={snapOn ? "Snapping" : "Snap off"} tip={`Snapping to ${[snapOpt.vertex && "vertices", snapOpt.edge && "edges"].filter(Boolean).join(" and ") || "nothing"} · ${snapOpt.tol} px`}
                  on={snapOn} accent onClick={() => setSnapOn(!snapOn)}><CenterFocusWeakOutlined /></RB>
                <Stack>
                  <RS t="Snap options" onClick={(e) => setSnapMenu(e!.currentTarget as HTMLElement)}><ArrowDropDownRounded /></RS>
                </Stack>
              </Grp>
              <Grp label="Selection">
                <RB big t="Select" k="V" tip="Click a feature · Shift+click adds · Ctrl+click removes · Esc clears" on={tool === "select"} onClick={() => setTool("select")}><NearMeOutlined /></RB>
                <Stack>
                  <RS t="Select by box" k="B" tip="Drag a box · Shift adds · Ctrl removes" on={tool === "box"} onClick={() => setTool("box")}><HighlightAltOutlined /></RS>
                  <RS t="Clear" disabled={!nSel} onClick={() => select.current?.getFeatures().clear()}><DeselectOutlined /></RS>
                </Stack>
              </Grp>
              <Grp label="Features">
                <RB big t="Create" k="D" tip={drawCls && tool === "draw" ? `Drawing ${ov?.classes[drawCls]?.alias} — click to place, double-click to finish` : "Draw a new feature in a layer"}
                  on={tool === "draw"} disabled={lock} onClick={(e) => setDrawMenu(e!.currentTarget as HTMLElement)}><DrawOutlined /></RB>
                <RB big t="Modify" k="E" tip="Edit vertices — drag a vertex · click an edge to add one · Alt+click to remove one" on={tool === "modify"} disabled={lock} onClick={() => setTool("modify")}><PolylineOutlined /></RB>
                <RB big t="Delete" k="Del" disabled={lock || !nSel} onClick={del}><DeleteOutline /></RB>
              </Grp>
              <Grp label="Tools" last>
                <Stack>
                  <RS t="Move" k="M" on={tool === "move"} disabled={lock} onClick={() => setTool("move")}><OpenWithOutlined /></RS>
                  <RS t="Split" tip={nSel === 1 ? "Draw a line across the selected feature" : "Select one line or polygon first"} on={tool === "split"} disabled={lock || nSel !== 1}
                    onClick={() => setTool(tool === "split" ? "select" : "split")}><ContentCutOutlined /></RS>
                </Stack>
                <Stack>
                  <RS t="Merge" tip={nSel >= 2 ? `Merge the ${nSel} selected features (one layer)` : "Select two or more features of one layer"} disabled={lock || nSel < 2} onClick={mergeSel}><CallMergeOutlined /></RS>
                  <RS t="Explode" tip="One feature per part of a multipart feature" disabled={lock || nSel !== 1} onClick={explodeSel}><CallSplitOutlined /></RS>
                </Stack>
                <Stack>
                  <RS t="Flip" k="F" tip="Reverse line direction (flow)" disabled={lock || !nSel} onClick={flipSel}><SwapHorizOutlined /></RS>
                </Stack>
              </Grp>
            </>)}
          </Stack>
        </Box>

      <Box sx={{ position: "relative", minWidth: 0, flex: 1, minHeight: 0 }}>
        <Box ref={el} sx={{ position: "absolute", inset: 0, bgcolor: "#EEF0F2", cursor: tool === "box" || tool === "distance" || tool === "area" ? "crosshair" : undefined,
                             "& .ol-attribution button": { width: 26, height: 26 } }} />
        {(!preview || busy) && <LinearProgress sx={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, zIndex: 3 }} />}
        {measure && (
          <Stack direction="row" alignItems="center" spacing={1} sx={{ position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)", zIndex: 3,
                     bgcolor: C.graphite, color: "#fff", borderRadius: 2, pl: 1.5, pr: 0.5, py: 0.5, fontSize: 13, fontVariantNumeric: "tabular-nums" }}>
            <span>{measure}</span>
            <IconButton size="small" aria-label="Clear measurement" onClick={() => { msrc.current.clear(); setMeasure(null); }} sx={{ color: "#fff" }}><CloseOutlined sx={{ fontSize: 16 }} /></IconButton>
          </Stack>
        )}

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

        <Menu anchorEl={drawMenu} open={!!drawMenu} onClose={() => setDrawMenu(null)} anchorOrigin={{ vertical: "bottom", horizontal: "left" }}>
          <MenuItem disabled dense sx={{ fontSize: 12, opacity: "1 !important", color: C.muted }}>New feature in…</MenuItem>
          {classes.map(([k, c]) => (
            <MenuItem key={k} dense selected={k === drawCls} onClick={() => { setDrawCls(k); setTool("draw"); setDrawMenu(null); }}>
              <Box sx={{ width: 10, height: 10, borderRadius: 0.75, bgcolor: colorOf(k), mr: 1.25 }} />{c.alias}
              <Typography component="span" sx={{ ml: 1.5, fontSize: 11, color: C.muted }}>{c.geometry}</Typography>
            </MenuItem>
          ))}
        </Menu>
        <Menu anchorEl={snapMenu} open={!!snapMenu} onClose={() => setSnapMenu(null)}>
          <MenuItem dense onClick={() => setSnapOpt((o) => ({ ...o, vertex: !o.vertex }))}><Checkbox size="small" checked={snapOpt.vertex} sx={{ p: 0, mr: 1 }} />Vertices and ends</MenuItem>
          <MenuItem dense onClick={() => setSnapOpt((o) => ({ ...o, edge: !o.edge }))}><Checkbox size="small" checked={snapOpt.edge} sx={{ p: 0, mr: 1 }} />Edges</MenuItem>
          <MenuItem disabled dense sx={{ fontSize: 12, opacity: "1 !important", color: C.muted, mt: 0.5 }}>Tolerance</MenuItem>
          {[5, 10, 15, 25].map((t) => <MenuItem key={t} dense selected={snapOpt.tol === t} onClick={() => setSnapOpt((o) => ({ ...o, tol: t }))}>{t} px</MenuItem>)}
        </Menu>
        <Menu anchorEl={baseMenu} open={!!baseMenu} onClose={() => setBaseMenu(null)}>
          {Object.entries(BASEMAPS).map(([k, b]) => <MenuItem key={k} dense selected={k === basemap}
            onClick={() => { setBasemap(k); try { localStorage.setItem("gsb_basemap", k); } catch { /* ignore */ } setBaseMenu(null); }}>{b.label}</MenuItem>)}
        </Menu>

        {pick && (
          <Box sx={{ position: "absolute", left: Math.min(pick.x + 12, (el.current?.clientWidth || 600) - 260), top: Math.min(pick.y + 12, (el.current?.clientHeight || 400) - 40 - pick.fids.length * 32),
                     width: 248, bgcolor: "#fff", borderRadius: 2, boxShadow: "0 8px 24px rgba(20,24,31,.2)", zIndex: 4, py: 0.5 }}>
            <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 0.5 }}>
              <Typography sx={{ fontSize: 12, color: C.muted, flex: 1 }}>{pick.fids.length} features here</Typography>
              <ButtonBase onClick={() => { setSelection(pick.fids); setPick(null); }} sx={{ fontSize: 12, color: C.cyan, px: 0.5 }}>Select all</ButtonBase>
            </Stack>
            {pick.fids.map((fid) => {
              const r = rows[fid]; const on = selFids.length === 1 && selFids[0] === fid;
              return (
                <ButtonBase key={fid} onClick={() => { setSelection([fid]); setPick(null); }}
                  onMouseEnter={() => setSelection([fid])}
                  sx={{ width: "100%", justifyContent: "flex-start", gap: 1, px: 1.5, height: 32, fontSize: 13, bgcolor: on ? "#EEF0F3" : undefined, "&:hover": { bgcolor: "#F5F6F8" } }}>
                  <Box sx={{ width: 8, height: 8, borderRadius: 0.5, flex: "none", bgcolor: colorOf(r?.target || "") }} />
                  <Box component="span" sx={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", textAlign: "left" }}>{ov?.classes[r?.target || ""]?.alias || r?.target}</Box>
                  <Box component="span" sx={{ color: C.muted, fontFamily: '"Geist Mono", monospace', fontSize: 11 }}>#{r?.objectid ?? fid}</Box>
                </ButtonBase>
              );
            })}
          </Box>
        )}
        <Box ref={tipEl} sx={{ position: "absolute", left: 0, top: 0, pointerEvents: "none", zIndex: 3, display: hover && hoverRow ? "block" : "none" }}>
          {hover && hoverRow && (
            <Box sx={{ bgcolor: C.graphite, color: "#fff", px: 1.1, py: 0.5, borderRadius: 1.5, fontSize: 12, fontWeight: 500, whiteSpace: "nowrap" }}>
              {ov?.classes[hoverRow.target]?.alias} #{hoverRow.objectid ?? hoverRow.fid}
              <Box component="span" sx={{ ml: 0.75, color: hoverRow.state === "completed" ? "#86EFAC" : hoverRow.state === "error" ? "#FCA5A5" : "#FCD34D" }}>
                · {hoverRow.state === "completed" ? "complete" : hoverRow.state === "error" ? "error" : `${hoverRow.missing.length} to fill`}
              </Box>
            </Box>
          )}
        </Box>
      </Box>

        {/* status bar */}
        <Stack direction="row" alignItems="center" spacing={2} sx={{ flex: "none", height: 28, px: 1.5, borderTop: `1px solid ${C.line}`, bgcolor: "#FAFBFC",
                   fontSize: 12, color: C.muted, fontFamily: '"Geist Mono", ui-monospace, monospace', whiteSpace: "nowrap", overflow: "hidden" }}>
          <span ref={scaleEl}>1:—</span>
          <span ref={xyEl} style={{ minWidth: 230 }}>—</span>
          <span>{geo ? "WGS 84 / Web Mercator" : "Drawing units (m)"}</span>
          <Box sx={{ flex: 1 }} />
          {nSel > 0 && <span style={{ color: SEL_UI }}>{nSel.toLocaleString()} selected</span>}
          {nPending > 0 && <span style={{ color: C.amber }}>{nPending} unsaved</span>}
          <span>Snap {snapOn ? "on" : "off"}</span>
        </Stack>
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
                <BuildOutlined sx={{ fontSize: 20, color: C.cyan }} />
                <Tooltip title="Fix → check → undo what got worse"><Typography sx={{ fontSize: 13.5, fontWeight: 700, flex: 1 }}>Fix all</Typography></Tooltip>
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

/** A ribbon group: buttons over a caption, the way the Edit tab groups its tools. */
function Grp({ label, children, last }: { label: string; children: React.ReactNode; last?: boolean }) {
  return (
    <Stack sx={{ flex: "none", px: 0.75, borderRight: last ? undefined : `1px solid ${C.line}` }}>
      <Stack direction="row" spacing={0.25} alignItems="center" sx={{ flex: 1 }}>{children}</Stack>
      <Typography sx={{ fontSize: 10.5, color: C.muted, textAlign: "center", lineHeight: 1.4 }}>{label}</Typography>
    </Stack>
  );
}
function Tip({ t, tip, k }: { t: string; tip?: string; k?: string }) {
  return <>{tip || t}{k ? <Box component="span" sx={{ ml: 1, px: 0.6, borderRadius: 0.75, bgcolor: "rgba(255,255,255,.16)", fontFamily: "monospace" }}>{k}</Box> : null}</>;
}
// state ladder: rest = muted glyph · hover = surface step · armed tool = soft accent · toggle on = accent glyph only · focus = 1px accent outline
const tone = (on?: boolean, accent?: boolean, disabled?: boolean) => ({
  color: disabled ? "#B3B9C3" : on ? C.cyan : "#3A414C",
  bgcolor: on && !accent ? C.cyanSoft : "transparent",
  transition: "background-color 120ms, color 120ms",
  "&:hover": { bgcolor: on && !accent ? "#D4EDF2" : "#E8EBEF", color: on ? C.cyan : C.ink },
  "&.Mui-focusVisible": { outline: `1px solid ${C.cyan}`, outlineOffset: -1 },
});
/** Large ribbon button: icon over label. */
function RB({ t, k, tip, on, onClick, disabled, children, accent }: { t: string; k?: string; tip?: string; on?: boolean; onClick: (e?: React.MouseEvent<HTMLElement>) => void; disabled?: boolean; children: React.ReactNode; accent?: boolean; big?: boolean }) {
  return (
    <Tooltip title={<Tip t={t} tip={tip} k={k} />}>
      <span style={{ display: "flex" }}>
        <ButtonBase onClick={(e) => onClick(e)} disabled={disabled} aria-label={tip ? `${t} — ${tip}` : t} aria-pressed={on}
          sx={{ flexDirection: "column", justifyContent: "center", gap: 0.4, minWidth: 56, height: 64, px: 0.75, borderRadius: 1.5, fontSize: 11.5, lineHeight: 1.1,
                ...tone(on, accent, disabled), "& svg": { fontSize: 26 } }}>
          <Box sx={{ display: "flex", flexDirection: "column", alignItems: "center" }}>{children}</Box><span>{t}</span>
        </ButtonBase>
      </span>
    </Tooltip>
  );
}
/** Small ribbon button: icon beside label, stacked two per column. */
function RS({ t, k, tip, on, onClick, disabled, children, iconOnly }: { t: string; k?: string; tip?: string; on?: boolean; onClick: (e?: React.MouseEvent<HTMLElement>) => void; disabled?: boolean; children: React.ReactNode; iconOnly?: boolean }) {
  return (
    <Tooltip title={<Tip t={t} tip={tip} k={k} />} placement="bottom-start">
      <span style={{ display: "flex" }}>
        <ButtonBase onClick={(e) => onClick(e)} disabled={disabled} aria-label={tip ? `${t} — ${tip}` : t} aria-pressed={on}
          sx={{ justifyContent: "flex-start", gap: 0.75, height: 30, px: 0.75, borderRadius: 1.25, fontSize: 12, whiteSpace: "nowrap",
                ...tone(on, false, disabled), "& svg": { fontSize: 18 } }}>
          {children}{!iconOnly && <span>{t}</span>}
        </ButtonBase>
      </span>
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
