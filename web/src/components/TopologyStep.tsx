/**
 * Topology step — map, error inspector and geometry editing tools (select, box select, modify vertices, move,
 * delete, snapping, undo, save / discard, validate), in the spirit of a desktop GIS edit session.
 * Edits stay pending on the map until Save; Save sends them to the server, which checks them and the next
 * validation re-runs every topology rule on the edited geometry.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Divider, IconButton, Menu, MenuItem, Stack, Table, TableBody, TableCell,
  TableHead, TableRow, ToggleButton, Tooltip, Typography,
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
import DragBox from "ol/interaction/DragBox";
import { click, shiftKeyOnly, always } from "ol/events/condition";
import type { FeatureLike } from "ol/Feature";
import NearMeOutlined from "@mui/icons-material/NearMeOutlined";
import HighlightAltOutlined from "@mui/icons-material/HighlightAltOutlined";
import DeselectOutlined from "@mui/icons-material/DeselectOutlined";
import EditOutlined from "@mui/icons-material/EditOutlined";
import OpenWithOutlined from "@mui/icons-material/OpenWithOutlined";
import DeleteOutline from "@mui/icons-material/DeleteOutline";
import GridOnOutlined from "@mui/icons-material/GridOnOutlined";
import UndoOutlined from "@mui/icons-material/UndoOutlined";
import SaveOutlined from "@mui/icons-material/SaveOutlined";
import ClearOutlined from "@mui/icons-material/ClearOutlined";
import FactCheckOutlined from "@mui/icons-material/FactCheckOutlined";
import ZoomOutMapOutlined from "@mui/icons-material/ZoomOutMapOutlined";
import CenterFocusStrongOutlined from "@mui/icons-material/CenterFocusStrongOutlined";
import MoreVert from "@mui/icons-material/MoreVert";
import MapOutlined from "@mui/icons-material/MapOutlined";
import { api } from "../api";
import type { GeoJSON, JobState, Results, TopologyError } from "../types";
import { BASEMAPS, basemapLayers } from "./MapView";
import { C } from "../theme";

type Tool = "select" | "box" | "modify" | "move";
const LOCAL = new Projection({ code: "LOCAL-CAD", units: "m", extent: [-1e9, -1e9, 1e9, 1e9] });
const PAL = ["#2E5C9A", "#2E9E6B", "#8E44AD", "#D35400", "#16A085", "#7F8C8D", "#2C3E50", "#B7791F", "#1ABC9C", "#5D6D7E"];
const ERR = "#E53935", EXC = "#43A047", SEL = "#00B8D4";

function shapeKind(t?: string) { return !t ? "point" : t.includes("Polygon") ? "polygon" : t.includes("Line") ? "line" : "point"; }

interface Props { job: JobState; preview: GeoJSON | null; results: Results | null; busy: boolean; onValidate: () => void; onError: (m: string) => void; onNotice: (m: string) => void }

export function TopologyStep({ job, preview, results, busy, onValidate, onError, onNotice }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<OlMap | null>(null);
  const fsrc = useRef(new VectorSource());
  const esrc = useRef(new VectorSource());
  const flayer = useRef<VectorLayer<any> | null>(null);
  const elayer = useRef<VectorLayer<any> | null>(null);
  const tiles = useRef<TileLayer<XYZ>[]>([]);
  const select = useRef<Select | null>(null);
  const inter = useRef<{ modify?: Modify; translate?: Translate; box?: DragBox; snap?: Snap }>({});
  const fmt = useRef(new GeoJSONFormat());
  const geoRef = useRef(true);

  const [tool, setTool] = useState<Tool>("select");
  const [snapOn, setSnapOn] = useState(true);
  const [snapVertex, setSnapVertex] = useState(true);
  const [snapEdge, setSnapEdge] = useState(true);
  const [snapMenu, setSnapMenu] = useState<HTMLElement | null>(null);
  const [baseMenu, setBaseMenu] = useState<HTMLElement | null>(null);
  const [basemap, setBasemap] = useState<string>(() => { try { return localStorage.getItem("gsb_basemap") || "satellite"; } catch { return "satellite"; } });
  const [errs, setErrs] = useState<TopologyError[]>([]);
  const [loadingErrs, setLoadingErrs] = useState(false);
  const [filter, setFilter] = useState<"errors" | "exceptions" | "all">("errors");
  const [curErr, setCurErr] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [pending, setPending] = useState<Record<number, "geometry" | "delete">>({});
  const [undoN, setUndoN] = useState(0);
  const [saving, setSaving] = useState(false);
  const [hiddenCls, setHiddenCls] = useState<Record<string, boolean>>({});
  const [showErr, setShowErr] = useState({ point: true, line: true, polygon: true, exception: true });
  const [coord, setCoord] = useState<string>("");
  const [rowMenu, setRowMenu] = useState<{ el: HTMLElement; e: TopologyError } | null>(null);
  const undo = useRef<{ fid: number; geom?: any; feature?: Feature; deleted?: boolean }[][]>([]);
  const original = useRef<Record<number, string>>({});
  const st = useRef({ hiddenCls, showErr, curErr });
  st.current = { hiddenCls, showErr, curErr };

  const classes = useMemo(() => {
    const m: Record<string, number> = {};
    (preview?.features || []).forEach((f: any) => { if (f.properties?.fid != null) m[f.properties.target] = (m[f.properties.target] || 0) + 1; });
    return m;
  }, [preview]);
  const color = (cls: string) => PAL[Math.abs([...cls].reduce((h, c) => (h * 31 + c.charCodeAt(0)) | 0, 0)) % PAL.length];

  // ---- map
  useEffect(() => {
    if (!el.current) return;
    const cache: Record<string, Style> = {};
    const fl = new VectorLayer({
      source: fsrc.current,
      style: (f: FeatureLike) => {
        const cls = String(f.get("target"));
        if (st.current.hiddenCls[cls]) return undefined;
        const c = f.get("pending") ? "#F57C00" : color(cls);
        const k = c + shapeKind(f.getGeometry()?.getType());
        return cache[k] || (cache[k] = new Style({
          stroke: new Stroke({ color: c, width: 2 }), fill: new Fill({ color: c + "26" }),
          image: new CircleStyle({ radius: 4.5, fill: new Fill({ color: c }), stroke: new Stroke({ color: "#fff", width: 1.2 }) }),
        }));
      },
    });
    const el_ = new VectorLayer({
      source: esrc.current,
      style: (f: FeatureLike) => {
        const e = f.get("err") as TopologyError;
        const kind = shapeKind(f.getGeometry()?.getType());
        if (!st.current.showErr[kind as "point"] || (e.exception && !st.current.showErr.exception)) return undefined;
        const cur = st.current.curErr === e.id;
        const c = e.exception ? EXC : ERR;
        return new Style({
          stroke: new Stroke({ color: cur ? "#FFD600" : c, width: cur ? 5 : 3 }),
          fill: new Fill({ color: (cur ? "#FFD600" : c) + "55" }),
          image: kind === "point" ? new RegularShape({ points: 4, radius: cur ? 10 : 7, angle: Math.PI / 4,
            fill: new Fill({ color: cur ? "#FFD600" : c }), stroke: new Stroke({ color: "#fff", width: 1.5 }) }) : undefined,
        });
      },
    });
    flayer.current = fl; elayer.current = el_;
    const tl = basemapLayers();
    tiles.current = tl;
    const m = new OlMap({ target: el.current, layers: [...tl, fl, el_], view: new View({ center: [0, 0], zoom: 2 }),
      controls: defaultControls({ attribution: true, rotate: false }).extend([new ScaleLine()]) });
    const sel = new Select({
      layers: [fl], condition: click, toggleCondition: shiftKeyOnly, hitTolerance: 6,
      style: (f: FeatureLike) => new Style({
        stroke: new Stroke({ color: SEL, width: 4 }), fill: new Fill({ color: SEL + "33" }),
        image: new CircleStyle({ radius: 7, fill: new Fill({ color: SEL }), stroke: new Stroke({ color: "#fff", width: 2 }) }),
        geometry: f.getGeometry() as any,
      }),
    });
    sel.getFeatures().on(["add", "remove"], () => setSelected(sel.getFeatures().getArray().map((f) => f.get("fid") as number)));
    m.addInteraction(sel);
    select.current = sel;
    m.on("pointermove", (e) => {
      const c = e.coordinate;
      setCoord(geoRef.current ? (() => { const ll = (m.getView().getProjection().getCode() === "EPSG:3857") ? toLL(c) : c; return `${ll[0].toFixed(6)}, ${ll[1].toFixed(6)}`; })() : `${c[0].toFixed(3)}, ${c[1].toFixed(3)}`);
    });
    map.current = m;
    return () => m.setTarget(undefined);
  }, []);

  // features (real converted geometry)
  const loadFeatures = () => {
    const m = map.current;
    if (!m || !preview) return;
    const geo = !!preview.crs_is_geographic;
    geoRef.current = geo;
    const proj = geo ? "EPSG:3857" : LOCAL;
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
  const fitAll = () => {
    const ext = fsrc.current.getExtent();
    if (ext && isFinite(ext[0])) map.current?.getView().fit(ext, { padding: [30, 30, 30, 30], maxZoom: 20, duration: 250 });
  };
  useEffect(() => { loadFeatures(); setTimeout(fitAll, 0); }, [preview]);
  useEffect(() => { tiles.current.forEach((x) => x.setVisible(!!preview?.crs_is_geographic && x.get("basemap") === basemap)); }, [basemap, preview]);
  useEffect(() => { flayer.current?.changed(); elayer.current?.changed(); }, [hiddenCls, showErr, curErr]);

  // errors of the last validation
  const loadErrors = () => {
    setLoadingErrs(true);
    api.topology(job.job_id).then((r) => setErrs(r.errors)).catch((e) => onError(String(e.message || e))).finally(() => setLoadingErrs(false));
  };
  useEffect(() => { if (results) loadErrors(); }, [results?.generated_at, job.job_id]);
  useEffect(() => {
    esrc.current.clear();
    const geo = !!preview?.crs_is_geographic;
    const proj = geo ? "EPSG:3857" : LOCAL;
    errs.forEach((e) => {
      let g: any = null;
      if (e.shape) g = fmt.current.readGeometry(e.shape, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj });
      else if (e.lonlat) g = new Point(geo ? fromLonLat(e.lonlat) : e.lonlat);
      if (!g) return;
      const f = new Feature({ geometry: g, err: e });
      esrc.current.addFeature(f);
    });
  }, [errs, preview]);

  // ---- tools
  useEffect(() => {
    const m = map.current, sel = select.current;
    if (!m || !sel) return;
    const I = inter.current;
    [I.modify, I.translate, I.box, I.snap].forEach((x) => x && m.removeInteraction(x));
    inter.current = {};
    sel.setActive(tool === "select" || tool === "modify" || tool === "move");
    const snapshot = (feats: Feature[]) => {
      undo.current.push(feats.map((f) => ({ fid: f.get("fid"), geom: f.getGeometry()!.clone() })));
      setUndoN(undo.current.length);
    };
    const mark = (feats: Feature[]) => {
      setPending((p) => {
        const n = { ...p };
        feats.forEach((f) => {
          const fid = f.get("fid") as number;
          const same = JSON.stringify((f.getGeometry() as any)?.getCoordinates?.()) === original.current[fid];
          if (same) { delete n[fid]; f.unset("pending"); } else { n[fid] = "geometry"; f.set("pending", true); }
        });
        return n;
      });
    };
    if (tool === "modify") {
      const mod = new Modify({ features: sel.getFeatures(), pixelTolerance: 8 });
      let before: Feature[] = [];
      mod.on("modifystart", (e) => { before = e.features.getArray() as Feature[]; snapshot(before); });
      mod.on("modifyend", (e) => mark(e.features.getArray() as Feature[]));
      m.addInteraction(mod);
      inter.current.modify = mod;
    }
    if (tool === "move") {
      const tr = new Translate({ features: sel.getFeatures() });
      tr.on("translatestart", (e) => snapshot(e.features.getArray() as Feature[]));
      tr.on("translateend", (e) => mark(e.features.getArray() as Feature[]));
      m.addInteraction(tr);
      inter.current.translate = tr;
    }
    if (tool === "box") {
      const box = new DragBox({ condition: always });
      box.on("boxend", () => {
        const ext = box.getGeometry().getExtent();
        const fs = sel.getFeatures();
        fs.clear();
        fsrc.current.forEachFeatureIntersectingExtent(ext, (f) => { if (!st.current.hiddenCls[String(f.get("target"))]) fs.push(f); });
      });
      m.addInteraction(box);
      inter.current.box = box;
    }
    if (snapOn && (tool === "modify" || tool === "move")) {      // snapping last, so it sees the other interactions' pointer
      const sn = new Snap({ source: fsrc.current, vertex: snapVertex, edge: snapEdge, pixelTolerance: 10 });
      m.addInteraction(sn);
      inter.current.snap = sn;
    }
  }, [tool, snapOn, snapVertex, snapEdge]);

  const del = () => {
    const sel = select.current;
    if (!sel) return;
    const fs = sel.getFeatures().getArray().slice() as Feature[];
    if (!fs.length) return;
    undo.current.push(fs.map((f) => ({ fid: f.get("fid"), feature: f, deleted: true })));
    setUndoN(undo.current.length);
    fs.forEach((f) => fsrc.current.removeFeature(f));
    sel.getFeatures().clear();
    setPending((p) => { const n = { ...p }; fs.forEach((f) => { n[f.get("fid")] = "delete"; }); return n; });
  };
  const doUndo = () => {
    const last = undo.current.pop();
    setUndoN(undo.current.length);
    if (!last) return;
    setPending((p) => {
      const n = { ...p };
      last.forEach((u) => {
        if (u.deleted && u.feature) { fsrc.current.addFeature(u.feature); }
        else {
          const f = fsrc.current.getFeatureById(u.fid) as Feature | null;
          if (f && u.geom) f.setGeometry(u.geom);
        }
        const f = fsrc.current.getFeatureById(u.fid) as Feature | null;
        const same = f && JSON.stringify((f.getGeometry() as any)?.getCoordinates?.()) === original.current[u.fid];
        if (same) { delete n[u.fid]; f?.unset("pending"); } else { n[u.fid] = "geometry"; f?.set("pending", true); }
      });
      return n;
    });
  };
  const discard = () => { loadFeatures(); onNotice("Edits discarded."); };
  const save = async () => {
    const ids = Object.keys(pending).map(Number);
    if (!ids.length || saving) return;
    const geo = !!preview?.crs_is_geographic;
    const edits = ids.map((fid) => {
      if (pending[fid] === "delete") return { fid, delete: true as const };
      const f = fsrc.current.getFeatureById(fid) as Feature;
      return { fid, geometry: fmt.current.writeGeometryObject(f.getGeometry()!, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: geo ? "EPSG:3857" : LOCAL }) };
    });
    setSaving(true);
    try {
      const r = await api.saveGeometry(job.job_id, edits);
      onNotice(`Saved ${r.geometry} edit(s) and ${r.deleted} deletion(s) — validating the topology again…`);
      setPending({}); undo.current = []; setUndoN(0);
      onValidate();
    } catch (e: any) { onError(String(e.message || e)); }
    finally { setSaving(false); }
  };

  // ---- error inspector
  const shown = errs.filter((e) => filter === "all" || (filter === "errors" ? !e.exception : e.exception));
  const zoomErr = (e: TopologyError) => {
    setCurErr(e.id);
    const f = esrc.current.getFeatures().find((x) => (x.get("err") as TopologyError).id === e.id);
    const g = f?.getGeometry();
    if (g && map.current) map.current.getView().fit(g.getExtent(), { padding: [120, 120, 120, 120], maxZoom: 21, duration: 300 });
    const fs = select.current?.getFeatures();
    if (fs) {
      fs.clear();
      [e.fid, ...e.related].forEach((fid) => { const x = fid != null && fsrc.current.getFeatureById(fid); if (x) fs.push(x as Feature); });
    }
  };
  const toggleException = async (e: TopologyError) => {
    try {
      await api.topologyException(job.job_id, e.key, !e.exception);
      setErrs((xs) => xs.map((x) => (x.key === e.key ? { ...x, exception: !e.exception } : x)));
      onNotice(e.exception ? "Exception removed — validate to report it again." : "Marked as exception — validate to apply.");
    } catch (er: any) { onError(String(er.message || er)); }
  };
  const nErr = errs.filter((e) => !e.exception).length;
  const nExc = errs.length - nErr;
  const nPending = Object.keys(pending).length;
  const lock = saving || busy;

  return (
    <Box sx={{ border: `1px solid ${C.line}`, borderRadius: 2, overflow: "hidden", bgcolor: "#fff" }}>
      {/* ---- ribbon */}
      <Stack direction="row" sx={{ borderBottom: `1px solid ${C.line}`, bgcolor: "#FAFBFC", px: 1, py: 0.5, gap: 0.5, flexWrap: "wrap", alignItems: "stretch" }}>
        <Group label="Selection">
          <TB t="Select (click · Shift+click to add)" on={tool === "select"} onClick={() => setTool("select")} icon={<NearMeOutlined />} label="Select" />
          <TB t="Select by rectangle" on={tool === "box"} onClick={() => setTool("box")} icon={<HighlightAltOutlined />} label="Box" />
          <TB t="Clear selection" onClick={() => select.current?.getFeatures().clear()} icon={<DeselectOutlined />} label="Clear" />
        </Group>
        <Group label="Tools">
          <TB t="Modify vertices — drag a vertex, click an edge to add one, Alt+click a vertex to remove it" on={tool === "modify"} disabled={lock} onClick={() => setTool("modify")} icon={<EditOutlined />} label="Modify" />
          <TB t="Move the selected features" on={tool === "move"} disabled={lock} onClick={() => setTool("move")} icon={<OpenWithOutlined />} label="Move" />
          <TB t="Delete the selected features" disabled={lock || !selected.length} onClick={del} icon={<DeleteOutline />} label="Delete" />
        </Group>
        <Group label="Snapping">
          <TB t="Snapping on / off" on={snapOn} onClick={() => setSnapOn(!snapOn)} icon={<GridOnOutlined />} label="Snapping" />
          <Button size="small" onClick={(e) => setSnapMenu(e.currentTarget)} sx={{ minWidth: 0, px: 0.75, fontSize: 11, color: C.muted }}>
            {[snapVertex && "Vertex", snapEdge && "Edge"].filter(Boolean).join(" · ") || "none"}
          </Button>
        </Group>
        <Group label="Manage edits">
          <TB t="Undo the last edit" disabled={!undoN || lock} onClick={doUndo} icon={<UndoOutlined />} label="Undo" />
          <TB t="Save the pending edits" disabled={!nPending || lock} onClick={save} icon={saving ? <CircularProgress size={18} /> : <SaveOutlined />} label={`Save${nPending ? ` (${nPending})` : ""}`} accent={!!nPending} />
          <TB t="Discard the pending edits" disabled={!nPending || lock} onClick={discard} icon={<ClearOutlined />} label="Discard" />
        </Group>
        <Group label="Topology">
          <TB t="Validate — re-run every topology rule" disabled={lock || nPending > 0} onClick={onValidate} icon={busy ? <CircularProgress size={18} /> : <FactCheckOutlined />} label="Validate" />
        </Group>
        <Box sx={{ flex: 1 }} />
        <Stack justifyContent="center" sx={{ pr: 1 }}>
          {nPending > 0 && <Chip size="small" label={`${nPending} pending edit${nPending > 1 ? "s" : ""}`} sx={{ bgcolor: "#FFF3E0", color: "#E65100", fontWeight: 700 }} />}
        </Stack>
      </Stack>

      <Box sx={{ display: "flex", height: "calc(100vh - 470px)", minHeight: 420 }}>
        {/* ---- contents */}
        <Box sx={{ width: 230, flexShrink: 0, borderRight: `1px solid ${C.line}`, overflow: "auto", py: 1 }}>
          <Typography sx={{ px: 1.5, fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>Topology errors</Typography>
          {(["point", "line", "polygon"] as const).map((k) => (
            <Row key={k} checked={showErr[k]} onChange={() => setShowErr((s) => ({ ...s, [k]: !s[k] }))}
              swatch={<Box sx={{ width: 14, height: k === "line" ? 3 : 10, bgcolor: ERR, borderRadius: k === "point" ? "50%" : 0.5 }} />}
              label={`${k[0].toUpperCase() + k.slice(1)} errors`} n={errs.filter((e) => !e.exception && shapeKind(e.shape?.type) === k).length} />
          ))}
          <Row checked={showErr.exception} onChange={() => setShowErr((s) => ({ ...s, exception: !s.exception }))}
            swatch={<Box sx={{ width: 10, height: 10, bgcolor: EXC, borderRadius: 0.5 }} />} label="Exceptions" n={nExc} />
          <Divider sx={{ my: 1 }} />
          <Typography sx={{ px: 1.5, fontSize: 12, fontWeight: 700, color: C.muted, textTransform: "uppercase" }}>Feature classes</Typography>
          {Object.entries(classes).sort().map(([k, n]) => (
            <Row key={k} checked={!hiddenCls[k]} onChange={() => setHiddenCls((h) => ({ ...h, [k]: !h[k] }))}
              swatch={<Box sx={{ width: 10, height: 10, bgcolor: color(k), borderRadius: 0.5 }} />} label={k} n={n} />
          ))}
        </Box>
        {/* ---- map */}
        <Box sx={{ position: "relative", flex: 1, minWidth: 0 }}>
          <Box ref={el} sx={{ position: "absolute", inset: 0, bgcolor: "#F7F8FA", cursor: tool === "box" ? "crosshair" : undefined }} />
          <Stack spacing={0.5} sx={{ position: "absolute", top: 10, right: 10 }}>
            <MapBtn t="Fit all features" onClick={fitAll}><ZoomOutMapOutlined fontSize="small" /></MapBtn>
            <MapBtn t="Zoom to the selection" onClick={() => {
              const fs = select.current?.getFeatures().getArray() || [];
              if (!fs.length) return;
              const ext = fs.reduce((a: number[], f) => { const e = f.getGeometry()!.getExtent(); return [Math.min(a[0], e[0]), Math.min(a[1], e[1]), Math.max(a[2], e[2]), Math.max(a[3], e[3])]; }, [Infinity, Infinity, -Infinity, -Infinity]);
              map.current?.getView().fit(ext, { padding: [120, 120, 120, 120], maxZoom: 21, duration: 250 });
            }}><CenterFocusStrongOutlined fontSize="small" /></MapBtn>
            {preview?.crs_is_geographic && <MapBtn t="Basemap" onClick={(e) => setBaseMenu(e.currentTarget)}><MapOutlined fontSize="small" /></MapBtn>}
          </Stack>
          <Menu anchorEl={baseMenu} open={!!baseMenu} onClose={() => setBaseMenu(null)}>
            {Object.entries(BASEMAPS).map(([k, b]) => <MenuItem key={k} dense selected={k === basemap} onClick={() => { setBasemap(k); setBaseMenu(null); }}>{b.label}</MenuItem>)}
          </Menu>
          <Menu anchorEl={snapMenu} open={!!snapMenu} onClose={() => setSnapMenu(null)}>
            <MenuItem dense onClick={() => setSnapVertex(!snapVertex)}><Checkbox size="small" checked={snapVertex} sx={{ p: 0.25, mr: 1 }} />Vertex / end point</MenuItem>
            <MenuItem dense onClick={() => setSnapEdge(!snapEdge)}><Checkbox size="small" checked={snapEdge} sx={{ p: 0.25, mr: 1 }} />Edge</MenuItem>
          </Menu>
          {tool === "modify" && <Alert severity="info" icon={false} sx={{ position: "absolute", left: 10, top: 10, py: 0, px: 1.25, fontSize: 12.5 }}>
            Select a feature, then drag its vertices · click an edge to add a vertex · Alt+click to remove one</Alert>}
        </Box>
      </Box>

      {/* ---- error inspector */}
      <Box sx={{ borderTop: `1px solid ${C.line}` }}>
        <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 1.5, py: 0.75, bgcolor: "#FAFBFC", borderBottom: `1px solid ${C.line}` }}>
          <Typography sx={{ fontWeight: 700, fontSize: 13.5 }}>Error Inspector</Typography>
          {(["errors", "exceptions", "all"] as const).map((k) => (
            <Chip key={k} size="small" clickable onClick={() => setFilter(k)} label={k === "errors" ? `Errors ${nErr}` : k === "exceptions" ? `Exceptions ${nExc}` : `All ${errs.length}`}
              sx={{ bgcolor: filter === k ? C.ink : "#EEF0F3", color: filter === k ? "#fff" : C.ink, fontWeight: 650 }} />
          ))}
          {loadingErrs && <CircularProgress size={14} />}
          <Box sx={{ flex: 1 }} />
          <Typography sx={{ fontSize: 12, color: C.muted, fontFamily: "ui-monospace, monospace" }}>{coord}</Typography>
          <Typography sx={{ fontSize: 12, color: C.muted }}>{selected.length} selected</Typography>
        </Stack>
        <Box sx={{ maxHeight: 220, overflow: "auto" }}>
          {!shown.length ? (
            <Typography sx={{ p: 2, color: C.muted, fontSize: 13.5 }}>{filter === "errors" ? "No topology errors — the geometry follows every rule of the template." : "Nothing to show."}</Typography>
          ) : (
            <Table size="small" stickyHeader>
              <TableHead><TableRow>
                {["Shape", "Rule", "Feature 1", "Feature 2", "Detail", "Exception", ""].map((h) => <TableCell key={h} sx={{ fontWeight: 700, fontSize: 12, bgcolor: "#F4F5F7", py: 0.5 }}>{h}</TableCell>)}
              </TableRow></TableHead>
              <TableBody>
                {shown.map((e) => (
                  <TableRow key={e.id} hover selected={curErr === e.id} onClick={() => zoomErr(e)} sx={{ cursor: "pointer", "& td": { fontSize: 12.5, py: 0.4 } }}>
                    <TableCell><Box sx={{ width: 12, height: shapeKind(e.shape?.type) === "line" ? 3 : 12, bgcolor: e.exception ? EXC : ERR,
                      borderRadius: shapeKind(e.shape?.type) === "point" ? "50%" : 0.5 }} /></TableCell>
                    <TableCell>{e.rule}{e.standard && <Chip size="small" label="standard" sx={{ ml: 0.75, height: 18, fontSize: 10.5 }} />}</TableCell>
                    <TableCell>{e.class}{e.fid != null ? ` #${e.fid}` : ""}</TableCell>
                    <TableCell>{e.related.length ? `${e.related_class} #${e.related.join(", #")}` : "—"}</TableCell>
                    <TableCell sx={{ maxWidth: 380, color: C.muted }}>{e.message}</TableCell>
                    <TableCell><Checkbox size="small" checked={e.exception} onClick={(ev) => ev.stopPropagation()} onChange={() => toggleException(e)} sx={{ p: 0.25 }} /></TableCell>
                    <TableCell align="right"><IconButton size="small" onClick={(ev) => { ev.stopPropagation(); setRowMenu({ el: ev.currentTarget, e }); }}><MoreVert fontSize="small" /></IconButton></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Box>
        <Menu anchorEl={rowMenu?.el} open={!!rowMenu} onClose={() => setRowMenu(null)}>
          {rowMenu && [
            <MenuItem key="z" dense onClick={() => { zoomErr(rowMenu.e); setRowMenu(null); }}>Zoom to error and select its features</MenuItem>,
            <MenuItem key="m" dense onClick={() => { zoomErr(rowMenu.e); setTool("modify"); setRowMenu(null); }}>Fix: modify the geometry</MenuItem>,
            <MenuItem key="d" dense disabled={rowMenu.e.fid == null} onClick={() => {
              const x = rowMenu.e.fid != null && fsrc.current.getFeatureById(rowMenu.e.fid);
              if (x) { select.current?.getFeatures().clear(); select.current?.getFeatures().push(x as Feature); del(); }
              setRowMenu(null);
            }}>Fix: delete Feature 1</MenuItem>,
            <MenuItem key="e" dense onClick={() => { toggleException(rowMenu.e); setRowMenu(null); }}>{rowMenu.e.exception ? "Remove the exception" : "Mark as exception"}</MenuItem>,
          ]}
        </Menu>
      </Box>
    </Box>
  );
}

function toLL(c: number[]) {
  const R = 6378137;
  return [(c[0] / R) * 180 / Math.PI, (2 * Math.atan(Math.exp(c[1] / R)) - Math.PI / 2) * 180 / Math.PI];
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Stack sx={{ px: 0.75, borderRight: `1px solid ${C.line}` }}>
      <Stack direction="row" spacing={0.25} alignItems="center" sx={{ flex: 1 }}>{children}</Stack>
      <Typography sx={{ fontSize: 10.5, color: C.muted, textAlign: "center", mt: 0.25 }}>{label}</Typography>
    </Stack>
  );
}

function TB({ t, on, onClick, icon, label, disabled, accent }: { t: string; on?: boolean; onClick: () => void; icon: React.ReactNode; label: string; disabled?: boolean; accent?: boolean }) {
  return (
    <Tooltip title={t}>
      <span>
        <Button size="small" disabled={disabled} onClick={onClick}
          sx={{ flexDirection: "column", minWidth: 54, px: 0.75, py: 0.4, gap: 0.1, fontSize: 11, fontWeight: 600, textTransform: "none",
                color: accent ? "#E65100" : C.ink, bgcolor: on ? "#E1F5FE" : "transparent", border: on ? `1px solid ${SEL}` : "1px solid transparent",
                "& svg": { fontSize: 20 } }}>
          {icon}{label}
        </Button>
      </span>
    </Tooltip>
  );
}

function Row({ checked, onChange, swatch, label, n }: { checked: boolean; onChange: () => void; swatch: React.ReactNode; label: string; n: number }) {
  return (
    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ px: 1, py: 0.1 }}>
      <Checkbox size="small" checked={checked} onChange={onChange} sx={{ p: 0.4 }} />
      {swatch}
      <Typography sx={{ fontSize: 12.5, flex: 1, minWidth: 0 }} noWrap>{label}</Typography>
      <Typography sx={{ fontSize: 11.5, color: C.muted }}>{n}</Typography>
    </Stack>
  );
}

function MapBtn({ t, onClick, children }: { t: string; onClick: (e: React.MouseEvent<HTMLElement>) => void; children: React.ReactNode }) {
  return (
    <Tooltip title={t} placement="left">
      <ToggleButton value="x" size="small" onClick={onClick} sx={{ bgcolor: "#fff", p: 0.6, boxShadow: "0 1px 3px rgba(0,0,0,.12)", "&:hover": { bgcolor: "#fff" } }}>{children}</ToggleButton>
    </Tooltip>
  );
}
