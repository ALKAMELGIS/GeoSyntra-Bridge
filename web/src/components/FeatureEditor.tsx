/**
 * Map-based feature assignment & attribute editor.
 *
 * Map geometry (preview of the converted features) → fid / OBJECTID → the row of the delivered geodatabase →
 * this editor → save (validated by the engine, written to that row by OBJECTID and read back on the server).
 * Only the selected feature is ever sent; colours update in place after each save (no reload).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, CircularProgress, Collapse, Divider, IconButton, InputAdornment, LinearProgress,
  Menu, MenuItem, Stack, TextField, ToggleButton, Tooltip, Typography,
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
import { Circle as CircleStyle, Fill, Stroke, Style } from "ol/style";
import type { FeatureLike } from "ol/Feature";
import { defaults as defaultControls, ScaleLine } from "ol/control";
import ChevronLeft from "@mui/icons-material/ChevronLeft";
import ChevronRight from "@mui/icons-material/ChevronRight";
import SaveOutlined from "@mui/icons-material/SaveOutlined";
import SkipNextOutlined from "@mui/icons-material/SkipNextOutlined";
import SearchOutlined from "@mui/icons-material/SearchOutlined";
import ZoomOutMapOutlined from "@mui/icons-material/ZoomOutMapOutlined";
import CenterFocusStrongOutlined from "@mui/icons-material/CenterFocusStrongOutlined";
import HighlightOutlined from "@mui/icons-material/HighlightOutlined";
import LayersOutlined from "@mui/icons-material/LayersOutlined";
import MapOutlined from "@mui/icons-material/MapOutlined";
import Visibility from "@mui/icons-material/Visibility";
import VisibilityOff from "@mui/icons-material/VisibilityOff";
import StorageOutlined from "@mui/icons-material/StorageOutlined";
import ExpandMore from "@mui/icons-material/ExpandMore";
import ExpandLess from "@mui/icons-material/ExpandLess";
import { api } from "../api";
import type { AssignDetail, AssignField, AssignOverview, AssignRow, AssignState, GeoJSON, JobState } from "../types";
import { BASEMAPS, basemapLayers } from "./MapView";
import { C } from "../theme";

// one colour system for map, chips and stats
const COL: Record<AssignState | "current", string> = {
  current: "#1E6FD9", completed: "#2E9E6B", incomplete: "#8A94A3", error: "#D93025", skipped: "#E8871E",
};
const STATES: AssignState[] = ["incomplete", "completed", "error", "skipped"];

const T = {
  en: {
    total: "Features", completed: "Completed", incomplete: "To fill", error: "Errors", skipped: "Skipped",
    of: "of", prev: "Previous", next: "Next", save: "Save & Next", skip: "Skip", goto: "Go to ID…",
    toFill: "To fill", all: "All attributes", autoTitle: "Automatic values — check",
    autoHint: "Filled automatically because the drawing has no value (N/A, 0, a default). Replace them when you know the real value.", readOnly: "Read only", fit: "Fit all features", zoom: "Zoom to feature",
    highlight: "Highlight current feature", layers: "Layers", basemap: "Basemap", gdbRow: "Row in", pending: "Saved — written to the geodatabase on Generate",
    savedTo: "Saved to", unsaved: "Unsaved changes", discard: "Discard", required: "Required", none: "No feature matches", pick: "Click a feature on the map",
    placeholder: "placeholder", allDone: "All features are complete", noGdb: "Not generated yet — saves are applied when the geodatabase is generated",
    search: "OBJECTID, feature # or CAD handle", dir: "ltr" as const, lang: "ع", allLayers: "All layers", left: "to fill",
  },
  ar: {
    total: "العناصر", completed: "مكتمل", incomplete: "غير مكتمل", error: "أخطاء", skipped: "تم تخطيه",
    of: "من", prev: "السابق", next: "التالي", save: "حفظ والتالي", skip: "تخطي", goto: "انتقال إلى…",
    toFill: "حقول مطلوبة", all: "جميع الحقول", autoTitle: "قيم تلقائية — راجعها",
    autoHint: "عُبّئت تلقائياً لأن الرسم لا يحتوي قيمة (N/A أو 0 أو قيمة افتراضية). استبدلها بالقيمة الحقيقية إن عرفتها.", readOnly: "للقراءة فقط", fit: "عرض جميع العناصر", zoom: "تقريب إلى العنصر",
    highlight: "إبراز العنصر الحالي", layers: "الطبقات", basemap: "الخريطة الأساسية", gdbRow: "السجل في", pending: "تم الحفظ — يُكتب في قاعدة البيانات عند التوليد",
    savedTo: "تم الحفظ في", unsaved: "تعديلات غير محفوظة", discard: "تجاهل", required: "مطلوب", none: "لا يوجد عنصر مطابق", pick: "اضغط على عنصر في الخريطة",
    placeholder: "قيمة مؤقتة", allDone: "تم إكمال جميع العناصر", noGdb: "لم يتم التوليد بعد — تُطبق التعديلات عند توليد قاعدة البيانات",
    search: "OBJECTID أو رقم العنصر أو CAD handle", dir: "rtl" as const, lang: "EN", allLayers: "كل الطبقات", left: "متبقٍ",
  },
};
type Lang = keyof typeof T;

const LOCAL = new Projection({ code: "LOCAL-CAD", units: "m", extent: [-1e9, -1e9, 1e9, 1e9] });
const PLACEHOLDER = new Set(["N/A", "1900-01-01"]);

function normDate(v: unknown) {
  const s = String(v ?? "");
  const m = s.match(/^(\d{4})[-/](\d{2})[-/](\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : s;
}
function asText(v: unknown, type: string) {
  if (v === null || v === undefined) return "";
  if (type === "DATE") return normDate(v);
  return String(v);
}

interface Props { job: JobState; preview: GeoJSON | null; onError: (e: unknown) => void; onSaved?: () => void; onLayerApplied?: () => void }

export function FeatureEditor({ job, preview, onError, onSaved, onLayerApplied }: Props) {
  const [lang, setLang] = useState<Lang>(() => { try { return (localStorage.getItem("gsb_editor_lang") as Lang) || "en"; } catch { return "en"; } });
  const t = T[lang];
  const [ov, setOv] = useState<AssignOverview | null>(null);
  const [rows, setRows] = useState<Record<number, AssignRow>>({});
  const [cur, setCur] = useState<number | null>(null);
  const [detail, setDetail] = useState<AssignDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [initial, setInitial] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ tone: "success" | "error" | "info"; text: string } | null>(null);
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const [show, setShow] = useState<Record<AssignState, boolean>>({ incomplete: true, completed: true, error: true, skipped: true });
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [focus, setFocus] = useState<string | null>(null);
  const [highlight, setHighlight] = useState(true);
  const [allOpen, setAllOpen] = useState(false);
  const [wholeLayer, setWholeLayer] = useState(false);
  const [layersAnchor, setLayersAnchor] = useState<HTMLElement | null>(null);
  const [baseAnchor, setBaseAnchor] = useState<HTMLElement | null>(null);
  const [basemap, setBasemap] = useState<string>(() => { try { return localStorage.getItem("gsb_basemap") || "satellite"; } catch { return "satellite"; } });
  const [search, setSearch] = useState("");
  const [pendingNav, setPendingNav] = useState<number | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number; fid: number } | null>(null);
  const savingRef = useRef(false);

  // ---- data
  const load = useCallback(() => {
    api.assign(job.job_id).then((o) => {
      setOv(o);
      const m: Record<number, AssignRow> = {};
      o.features.forEach((r) => { m[r.fid] = r; });
      setRows(m);
    }).catch(onError);
  }, [job.job_id]);
  useEffect(() => { load(); }, [load]);

  const order = useMemo(() => (ov ? ov.features.map((r) => r.fid) : []), [ov]);
  const visible = useCallback((r?: AssignRow) => !!r && show[r.state] && !hidden[r.target] && (!focus || r.target === focus), [show, hidden, focus]);
  const scope = useMemo(() => order.filter((f) => !focus || rows[f]?.target === focus), [order, rows, focus]);
  const counts = useMemo(() => {
    const c: Record<AssignState, number> = { completed: 0, incomplete: 0, error: 0, skipped: 0 };
    scope.forEach((f) => { if (rows[f]) c[rows[f].state]++; });
    return c;
  }, [rows, scope]);
  const total = scope.length;
  const pct = total ? Math.round((100 * counts.completed) / total) : 0;
  const idx = cur != null ? scope.indexOf(cur) : -1;
  const byClass = useMemo(() => {
    const m: Record<string, { total: number; done: number; open: number; err: number }> = {};
    Object.values(rows).forEach((r) => {
      const x = m[r.target] || (m[r.target] = { total: 0, done: 0, open: 0, err: 0 });
      x.total++; if (r.state === "completed") x.done++; if (r.state === "incomplete") x.open++; if (r.state === "error") x.err++;
    });
    return m;
  }, [rows]);

  // ---- map
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<OlMap | null>(null);
  const vec = useRef<VectorLayer<any> | null>(null);
  const tiles = useRef<TileLayer<XYZ>[]>([]);
  const st = useRef({ rows, cur, show, hidden, highlight, focus });
  st.current = { rows, cur, show, hidden, highlight, focus };
  const styles = useRef<Record<string, Style[]>>({});

  const styleOf = (key: string, color: string, width: number, halo: boolean, dim: boolean) => {
    const k = `${key}|${color}|${width}|${halo}|${dim}`;
    if (styles.current[k]) return styles.current[k];
    const c = dim ? color + "55" : color;
    const s: Style[] = [];
    if (halo) s.push(new Style({ stroke: new Stroke({ color: "#ffffff", width: width + 5 }),
      image: new CircleStyle({ radius: 11, fill: new Fill({ color: "#ffffff" }) }) }));
    s.push(new Style({
      stroke: new Stroke({ color: c, width }), fill: new Fill({ color: c + (dim ? "" : "33") }),
      image: new CircleStyle({ radius: halo ? 8 : 5, fill: new Fill({ color: c }), stroke: new Stroke({ color: "#fff", width: 1.5 }) }),
    }));
    styles.current[k] = s;
    return s;
  };

  useEffect(() => {
    if (!el.current) return;
    const v = new VectorLayer({
      source: new VectorSource(),
      updateWhileInteracting: false,
      style: (f: FeatureLike) => {
        const s = st.current;
        const r = s.rows[f.get("fid") as number];
        if (!r || !s.show[r.state] || s.hidden[r.target]) return undefined;
        const isCur = r.fid === s.cur;
        if (isCur) return styleOf("c", COL.current, 5, true, false);
        if (s.focus && r.target !== s.focus) return styleOf("o", "#9AA3AF", 1.4, false, true);   // other layers: context only
        return styleOf("n", COL[r.state], 2.4, false, s.highlight && s.cur != null);
      },
    });
    vec.current = v;
    const tl = basemapLayers();
    tiles.current = tl;
    const m = new OlMap({ target: el.current, layers: [...tl, v], view: new View({ center: [0, 0], zoom: 2 }),
      controls: defaultControls({ attribution: true, rotate: false }).extend([new ScaleLine()]) });
    m.on("singleclick", (e) => {
      const f = m.forEachFeatureAtPixel(e.pixel, (x) => x, { hitTolerance: 6, layerFilter: (l) => l === v });
      if (f) selectRef.current(f.get("fid") as number, false);
    });
    let raf = 0;
    m.on("pointermove", (e) => {
      if (e.dragging) return;
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const f = m.forEachFeatureAtPixel(e.pixel, (x) => x, { hitTolerance: 6, layerFilter: (l) => l === v });
        const fid = f ? (f.get("fid") as number) : null;
        m.getTargetElement().style.cursor = fid != null ? "pointer" : "";
        setHover(fid != null ? { x: e.pixel[0], y: e.pixel[1], fid } : null);
      });
    });
    map.current = m;
    return () => { cancelAnimationFrame(raf); m.setTarget(undefined); };
  }, []);

  // real geometry of the converted features (the rows the geodatabase holds), loaded once
  useEffect(() => {
    const m = map.current, v = vec.current;
    if (!m || !v || !preview) return;
    const src = v.getSource()!;
    src.clear();
    const data = { ...preview, features: preview.features.filter((f: any) => f.properties?.fid != null) };
    const geo = preview.crs_is_geographic;
    const proj = geo ? "EPSG:3857" : LOCAL;
    if (m.getView().getProjection().getCode() !== (geo ? "EPSG:3857" : LOCAL.getCode())) m.setView(new View({ projection: proj, center: [0, 0], zoom: 2 }));
    src.addFeatures(new GeoJSONFormat().readFeatures(data, { dataProjection: geo ? "EPSG:4326" : LOCAL, featureProjection: proj }));
    const ext = src.getExtent();
    if (ext && isFinite(ext[0])) m.getView().fit(ext, { padding: [40, 40, 40, 40], maxZoom: 20 });
  }, [preview]);
  useEffect(() => { tiles.current.forEach((x) => x.setVisible(!!preview?.crs_is_geographic && x.get("basemap") === basemap)); }, [basemap, preview]);
  useEffect(() => { vec.current?.changed(); }, [rows, cur, show, hidden, highlight, focus]);

  const featureOf = (fid: number) => vec.current?.getSource()?.getFeatures().find((x: any) => x.get("fid") === fid);
  const zoomTo = (fid: number | null, animate = true) => {
    if (fid == null) return;
    const g = featureOf(fid)?.getGeometry();
    if (g && map.current) map.current.getView().fit(g.getExtent(), { padding: [90, 90, 90, 90], maxZoom: 20.5, duration: animate ? 350 : 0 });
  };
  const fitAll = () => {
    const src = vec.current?.getSource();
    const ext = src?.getExtent();
    if (ext && isFinite(ext[0])) map.current?.getView().fit(ext, { padding: [40, 40, 40, 40], maxZoom: 20, duration: 300 });
  };
  // a layer: its features on the map, its first feature still to fill in the editor
  const focusLayer = (k: string) => {
    setFocus(k);
    setHidden((h) => ({ ...h, [k]: false }));
    const fs = (vec.current?.getSource()?.getFeatures() || []).filter((f: any) => rows[f.get("fid")]?.target === k);
    if (fs.length && map.current) {
      const ext = fs.reduce((a: number[], f: any) => { const e = f.getGeometry().getExtent(); return [Math.min(a[0], e[0]), Math.min(a[1], e[1]), Math.max(a[2], e[2]), Math.max(a[3], e[3])]; }, [Infinity, Infinity, -Infinity, -Infinity]);
      map.current.getView().fit(ext, { padding: [60, 60, 60, 60], maxZoom: 20, duration: 300 });
    }
    const first = order.find((f) => rows[f]?.target === k && (rows[f].state === "incomplete" || rows[f].state === "error"))
      ?? order.find((f) => rows[f]?.target === k);
    if (first != null && first !== cur) select(first, false);
  };

  // ---- selection (lazy: one feature's record at a time)
  const spec = (cur != null && ov && rows[cur]) ? ov.classes[rows[cur].target] : null;
  const dirty = useMemo(() => Object.keys(form).some((k) => (form[k] ?? "") !== (initial[k] ?? "")), [form, initial]);
  const reqId = useRef(0);

  const open = (fid: number, zoom = true) => {
    setCur(fid); setMsg(null); setFieldErr({}); setPendingNav(null);
    if (zoom) setTimeout(() => zoomTo(fid), 0);
    const my = ++reqId.current;
    setLoadingDetail(true);
    api.assignDetail(job.job_id, fid).then((d) => {
      if (my !== reqId.current) return;           // a newer selection won
      setDetail(d);
      const sp = ov?.classes[d.target];
      const src = d.gdb?.values || d.attributes;  // the geodatabase row once generated
      const f: Record<string, string> = {};
      (sp?.fields || []).forEach((x) => {
        const v = asText((src as Record<string, unknown>)[x.name], x.type);
        f[x.name] = d.placeholders.includes(x.name) && PLACEHOLDER.has(v) ? "" : v;
      });
      setForm(f); setInitial(f);
    }).catch((e) => { if (my === reqId.current) onError(e); })
      .finally(() => { if (my === reqId.current) setLoadingDetail(false); });
  };
  const select = (fid: number, zoom = true) => {
    if (fid === cur) { if (zoom) zoomTo(fid); return; }
    if (dirty && !savingRef.current) { setPendingNav(fid); return; }
    open(fid, zoom);
  };
  const selectRef = useRef(select);
  selectRef.current = select;

  // first feature still to fill, once everything is loaded
  useEffect(() => {
    if (cur == null && ov && preview && order.length) {
      const first = order.find((f) => rows[f]?.state === "incomplete" || rows[f]?.state === "error") ?? order[0];
      open(first, true);
    }
  }, [ov, preview]);

  const step = (dir: 1 | -1, onlyOpen = false, fromRows = rows) => {
    if (!order.length) return null;
    const start = idx < 0 ? -1 : idx;
    for (let i = 1; i <= order.length; i++) {
      const f = order[(start + dir * i + order.length * 2) % order.length];
      const r = fromRows[f];
      if (!visible(r)) continue;
      if (onlyOpen && !(r.state === "incomplete" || r.state === "error")) continue;
      return f;
    }
    return null;
  };

  // ---- save (one feature; the server validates, writes its row by OBJECTID and reads it back)
  const changed = () => {
    const out: Record<string, unknown> = {};
    (spec?.fields || []).forEach((x) => {
      if (x.read_only) return;
      if ((form[x.name] ?? "") !== (initial[x.name] ?? "")) out[x.name] = form[x.name] === "" ? null : form[x.name];
    });
    return out;
  };
  const save = async (status: "completed" | "skipped", advance: boolean) => {
    if (cur == null || !detail || savingRef.current) return;     // no double save
    if (status === "completed") {
      const miss = (rows[cur]?.missing || []).filter((n) => !(detail.placeholders.includes(n) && !form[n]) && !String(form[n] ?? "").trim());
      const ph = detail.placeholders.filter((n) => !form[n]);
      const realMiss = miss.filter((n) => !ph.includes(n));
      if (realMiss.length) {
        const e: Record<string, string> = {};
        realMiss.forEach((n) => { e[n] = t.required; });
        setFieldErr(e);
        setMsg({ tone: "error", text: `${t.required}: ${realMiss.map((n) => spec?.fields.find((x) => x.name === n)?.alias || n).join(", ")}` });
        return;
      }
    }
    savingRef.current = true; setSaving(true); setMsg(null); setFieldErr({});
    try {
      const ch = status === "skipped" ? {} : changed();
      const r = await api.assignSave(job.job_id, cur, ch, status);
      if (wholeLayer && Object.keys(ch).length && rows[cur]) {
        // the same values for every feature of this layer: a constant of the class, the engine fills them all
        const fields: Record<string, unknown> = {};
        Object.entries(ch).forEach(([k, v]) => { fields[k] = v == null ? { source: "none" } : { source: "constant", value: v }; });
        await api.patchMapping(job.job_id, { fields: { [rows[cur].target]: fields } });
        setMsg({ tone: "success", text: `${Object.keys(ch).length} value(s) applied to all ${byClass[rows[cur].target]?.total || ""} features of ${spec?.alias || rows[cur].target} — validating…` });
        setWholeLayer(false);
        onLayerApplied?.();
        return;
      }
      const nextRows = { ...rows, [cur]: { ...rows[cur], state: r.state, missing: r.missing, edited: true, objectid: r.objectid ?? rows[cur].objectid } };
      setRows(nextRows);                                          // colours change in place
      setInitial({ ...form });
      setMsg({ tone: "success", text: r.gdb ? `${t.savedTo} ${r.gdb.gdb} · OBJECTID ${r.gdb.objectid}` : t.pending });
      onSaved?.();
      if (advance) {
        const n = step(1, true, nextRows) ?? null;
        if (n != null && n !== cur) open(n, true);
        else setMsg({ tone: "success", text: t.allDone });
      }
    } catch (e: any) {
      // not saved: stay on this feature and say why
      const text = String(e?.message || e);
      setMsg({ tone: "error", text });
      const e2: Record<string, string> = {};
      (spec?.fields || []).forEach((x) => { if (text.includes(x.name) || text.includes(x.alias)) e2[x.name] = text; });
      setFieldErr(e2);
    } finally {
      savingRef.current = false; setSaving(false);
    }
  };

  // keyboard: Ctrl+Enter = Save & Next, Alt+←/→ = previous / next
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); save("completed", true); }
      if (e.altKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
        e.preventDefault();
        const n = step(e.key === "ArrowRight" ? 1 : -1);
        if (n != null) select(n);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  const goSearch = () => {
    const q = search.trim();
    if (!q || !ov) return;
    const n = Number(q.replace(/^#/, ""));
    const r = ov.features.find((x) => x.objectid === n) || ov.features.find((x) => x.fid === n)
      || ov.features.find((x) => x.handle?.toUpperCase() === q.toUpperCase() || x.handle?.toUpperCase().endsWith(":" + q.toUpperCase()));
    if (!r) { setMsg({ tone: "error", text: `${t.none}: ${q}` }); return; }
    if (!visible(r)) { setShow((s) => ({ ...s, [r.state]: true })); setHidden((h) => ({ ...h, [r.target]: false })); if (focus && focus !== r.target) setFocus(null); }
    select(r.fid, true);
  };

  const row = cur != null ? rows[cur] : null;
  const missingSet = new Set(row?.missing || []);
  const toFill = (spec?.fields || []).filter((x) => !x.read_only && (missingSet.has(x.name) || fieldErr[x.name]));
  const autoSet = new Set(detail?.placeholders || []);
  const autoList = (spec?.fields || []).filter((x) => !x.read_only && autoSet.has(x.name) && !toFill.includes(x));
  const others = (spec?.fields || []).filter((x) => !toFill.includes(x) && !autoList.includes(x));
  const classCounts = useMemo(() => {
    const m: Record<string, number> = {};
    Object.values(rows).forEach((r) => { m[r.target] = (m[r.target] || 0) + 1; });
    return m;
  }, [rows]);

  const setLangP = (l: Lang) => { setLang(l); try { localStorage.setItem("gsb_editor_lang", l); } catch { /* ignore */ } };
  const hoverRow = hover ? rows[hover.fid] : null;

  return (
    <Box dir={t.dir} sx={{ border: `1px solid ${C.line}`, borderRadius: 2, overflow: "hidden", bgcolor: "#fff" }}>
      {/* ---- stats + navigation */}
      <Stack direction="row" alignItems="center" sx={{ gap: 2, px: 2, py: 1.25, borderBottom: `1px solid ${C.line}`, flexWrap: "wrap", rowGap: 1 }}>
        <Stat label={t.total} value={total} color={C.ink} />
        {STATES.map((s) => (
          <Tooltip key={s} title={show[s] ? "Hide on map" : "Show on map"}>
            <Box onClick={() => setShow((x) => ({ ...x, [s]: !x[s] }))} sx={{ cursor: "pointer", opacity: show[s] ? 1 : 0.4 }}>
              <Stat label={t[s]} value={counts[s]} color={COL[s]} dot />
            </Box>
          </Tooltip>
        ))}
        <Box sx={{ flex: 1, minWidth: 160 }}>
          <Stack direction="row" justifyContent="space-between"><Typography sx={{ fontSize: 12, color: C.muted }}>{t.completed}</Typography>
            <Typography sx={{ fontSize: 12, fontWeight: 700 }}>{pct}%</Typography></Stack>
          <LinearProgress variant="determinate" value={pct} sx={{ height: 6, borderRadius: 3, bgcolor: "#EEF0F3", "& .MuiLinearProgress-bar": { bgcolor: COL.completed } }} />
        </Box>
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Button size="small" startIcon={lang === "ar" ? <ChevronRight /> : <ChevronLeft />} disabled={saving || !total}
            onClick={() => { const n = step(-1); if (n != null) select(n); }}>{t.prev}</Button>
          <Typography sx={{ fontSize: 13, fontWeight: 700, minWidth: 84, textAlign: "center" }}>{idx >= 0 ? idx + 1 : "–"} {t.of} {total}</Typography>
          <Button size="small" endIcon={lang === "ar" ? <ChevronLeft /> : <ChevronRight />} disabled={saving || !total}
            onClick={() => { const n = step(1); if (n != null) select(n); }}>{t.next}</Button>
        </Stack>
        <TextField size="small" value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t.goto}
          onKeyDown={(e) => { if (e.key === "Enter") goSearch(); }} sx={{ width: 150 }}
          InputProps={{ endAdornment: <InputAdornment position="end"><Tooltip title={t.search}><IconButton size="small" onClick={goSearch}><SearchOutlined fontSize="small" /></IconButton></Tooltip></InputAdornment> }} />
        <Tooltip title="العربية / English"><Button size="small" variant="outlined" onClick={() => setLangP(lang === "en" ? "ar" : "en")} sx={{ minWidth: 40 }}>{t.lang}</Button></Tooltip>
      </Stack>

      <Box sx={{ display: "flex", height: "calc(100vh - 330px)", minHeight: 560 }}>
        {/* ---- layers: where the work is */}
        <Box sx={{ width: 250, flexShrink: 0, borderInlineEnd: `1px solid ${C.line}`, overflow: "auto", py: 1 }}>
          <LayerRow label={t.allLayers} active={!focus} total={order.length} done={Object.values(byClass).reduce((a, x) => a + x.done, 0)}
            open={Object.values(byClass).reduce((a, x) => a + x.open, 0)} err={Object.values(byClass).reduce((a, x) => a + x.err, 0)} left={t.left}
            onClick={() => { setFocus(null); setTimeout(fitAll, 0); }} />
          <Divider sx={{ my: 0.5 }} />
          {ov && Object.entries(ov.classes).map(([k, c]) => byClass[k] && (
            <LayerRow key={k} label={c.alias} sub={c.dataset || undefined} geom={c.geometry} active={focus === k} hidden={!!hidden[k]}
              total={byClass[k].total} done={byClass[k].done} open={byClass[k].open} err={byClass[k].err} left={t.left}
              onToggle={() => setHidden((h) => ({ ...h, [k]: !h[k] }))}
              onClick={() => focusLayer(k)} />
          ))}
        </Box>
        {/* ---- map */}
        <Box sx={{ position: "relative", flex: 1, minWidth: 0 }}>
          <Box ref={el} sx={{ position: "absolute", inset: 0, bgcolor: "#F7F8FA" }} />
          {!preview && <Box sx={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}><CircularProgress size={28} /></Box>}
          <Stack spacing={0.5} sx={{ position: "absolute", top: 10, [lang === "ar" ? "left" : "right"]: 10 }}>
            <Tool title={t.fit} onClick={fitAll}><ZoomOutMapOutlined fontSize="small" /></Tool>
            <Tool title={t.zoom} onClick={() => zoomTo(cur)} disabled={cur == null}><CenterFocusStrongOutlined fontSize="small" /></Tool>
            <Tool title={t.highlight} onClick={() => setHighlight(!highlight)} selected={highlight}><HighlightOutlined fontSize="small" /></Tool>
            <Tool title={t.layers} onClick={(e) => setLayersAnchor(e.currentTarget)}><LayersOutlined fontSize="small" /></Tool>
            {preview?.crs_is_geographic && <Tool title={t.basemap} onClick={(e) => setBaseAnchor(e.currentTarget)}><MapOutlined fontSize="small" /></Tool>}
          </Stack>
          <Menu anchorEl={layersAnchor} open={!!layersAnchor} onClose={() => setLayersAnchor(null)}>
            {ov && Object.entries(ov.classes).map(([k, c]) => (
              <MenuItem key={k} dense onClick={() => setHidden((h) => ({ ...h, [k]: !h[k] }))}>
                {hidden[k] ? <VisibilityOff fontSize="small" sx={{ color: C.muted, mr: 1 }} /> : <Visibility fontSize="small" sx={{ color: C.blue, mr: 1 }} />}
                <Typography sx={{ fontSize: 13, flex: 1, mr: 2 }}>{c.alias}</Typography>
                <Typography sx={{ fontSize: 12, color: C.muted }}>{classCounts[k] || 0}</Typography>
              </MenuItem>))}
          </Menu>
          <Menu anchorEl={baseAnchor} open={!!baseAnchor} onClose={() => setBaseAnchor(null)}>
            {Object.entries(BASEMAPS).map(([k, b]) => (
              <MenuItem key={k} dense selected={k === basemap} onClick={() => { setBasemap(k); try { localStorage.setItem("gsb_basemap", k); } catch { /* ignore */ } setBaseAnchor(null); }}>{b.label}</MenuItem>))}
          </Menu>
          {/* legend */}
          <Stack direction="row" sx={{ gap: 1.25, position: "absolute", bottom: 30, [lang === "ar" ? "right" : "left"]: 10, bgcolor: "rgba(255,255,255,.92)", px: 1.25, py: 0.6, borderRadius: 1.5, border: `1px solid ${C.line}` }}>
            {(["current", ...STATES] as const).map((s) => (
              <Stack key={s} direction="row" alignItems="center" spacing={0.5}>
                <Box sx={{ width: 14, height: 4, borderRadius: 2, bgcolor: COL[s] }} />
                <Typography sx={{ fontSize: 11.5 }}>{s === "current" ? (lang === "ar" ? "الحالي" : "Current") : t[s]}</Typography>
              </Stack>))}
          </Stack>
          {hover && hoverRow && (
            <Box sx={{ position: "absolute", left: hover.x + 14, top: hover.y + 12, pointerEvents: "none", bgcolor: C.graphite, color: "#fff", px: 1, py: 0.5, borderRadius: 1, fontSize: 12, whiteSpace: "nowrap", zIndex: 3 }} dir="ltr">
              <b>{hoverRow.objectid != null ? `OBJECTID ${hoverRow.objectid}` : `#${hoverRow.fid}`}</b> · {ov?.classes[hoverRow.target]?.alias}
              <Box component="span" sx={{ ml: 0.75, color: hoverRow.fid === cur ? "#8EC1FF" : COL[hoverRow.state], fontWeight: 700 }}>● {T[lang][hoverRow.state]}</Box>
            </Box>
          )}
        </Box>

        {/* ---- attribute editor */}
        <Box sx={{ width: 420, flexShrink: 0, borderInlineStart: `1px solid ${C.line}`, display: "flex", flexDirection: "column", minHeight: 0 }}>
          {!row || !spec ? (
            <Box sx={{ p: 3, color: C.muted }}>{t.pick}</Box>
          ) : (<>
            <Box sx={{ px: 2, pt: 1.75, pb: 1.25, borderBottom: `1px solid ${C.line}` }}>
              <Stack direction="row" alignItems="center" spacing={1}>
                <Typography sx={{ fontWeight: 700, fontSize: 15.5, flex: 1, minWidth: 0 }} noWrap>{spec.alias}</Typography>
                <Chip size="small" label={T[lang][row.state]} sx={{ bgcolor: COL[row.state] + "1F", color: COL[row.state], fontWeight: 700 }} />
              </Stack>
              <Stack direction="row" sx={{ gap: 1.25, mt: 0.75, flexWrap: "wrap" }} dir="ltr">
                <Mono k="OBJECTID" v={row.objectid ?? "—"} />
                <Mono k="Feature" v={`#${row.fid}`} />
                <Mono k="CAD" v={`${row.layer} · ${row.handle}`} />
              </Stack>
              {detail?.gdb ? (
                <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 0.75, color: detail.gdb.error ? C.red : C.muted }}>
                  <StorageOutlined sx={{ fontSize: 15 }} />
                  <Typography sx={{ fontSize: 12 }} noWrap>{detail.gdb.error || `${t.gdbRow} ${detail.gdb.name}`}</Typography>
                </Stack>
              ) : ov && !ov.gdb.generated ? <Typography sx={{ fontSize: 12, color: C.muted, mt: 0.75 }}>{t.noGdb}</Typography> : null}
            </Box>
            {(loadingDetail || saving) && <LinearProgress sx={{ height: 2 }} />}
            <Box sx={{ flex: 1, overflow: "auto", px: 2, py: 1.5, opacity: loadingDetail ? 0.5 : 1 }}>
              {detail?.failure && <Alert severity="error" sx={{ mb: 1.5, py: 0 }}>{detail.failure}</Alert>}
              {detail && detail.issues.filter((i) => i.severity === "error").slice(0, 3).map((i) => (
                <Alert key={i.id} severity="error" sx={{ mb: 1, py: 0, fontSize: 12.5 }}>{i.message.split(": ").slice(1).join(": ") || i.message}</Alert>))}
              {toFill.length > 0 && (<>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: C.red, textTransform: "uppercase", letterSpacing: 0.4, mb: 1 }}>{t.toFill} · {toFill.length}</Typography>
                <Stack spacing={1.5} sx={{ mb: 2 }}>
                  {toFill.map((x) => <FieldInput key={x.name} f={x} value={form[x.name] ?? ""} initial={initial[x.name] ?? ""} err={fieldErr[x.name]}
                    ph={detail?.placeholders.includes(x.name) ? t.placeholder : undefined} req={t.required}
                    onChange={(v) => setForm((s) => ({ ...s, [x.name]: v }))} />)}
                </Stack>
              </>)}
              {autoList.length > 0 && (<>
                <Typography sx={{ fontSize: 12, fontWeight: 700, color: "#B45309", textTransform: "uppercase", letterSpacing: 0.4, mb: 0.5 }}>{t.autoTitle} · {autoList.length}</Typography>
                <Typography sx={{ fontSize: 12, color: C.muted, mb: 1 }}>{t.autoHint}</Typography>
                <Stack spacing={1.5} sx={{ mb: 2 }}>
                  {autoList.map((x) => <FieldInput key={x.name} f={x} value={form[x.name] ?? ""} initial={initial[x.name] ?? ""} err={fieldErr[x.name]}
                    ph={t.placeholder} req={t.required} onChange={(v) => setForm((s) => ({ ...s, [x.name]: v }))} />)}
                </Stack>
              </>)}
              <Button size="small" onClick={() => setAllOpen(!allOpen)} endIcon={allOpen ? <ExpandLess /> : <ExpandMore />} sx={{ px: 0, color: C.ink, fontWeight: 700 }}>
                {t.all} · {others.length}
              </Button>
              <Collapse in={allOpen || toFill.length === 0}>
                <Stack spacing={1.25} sx={{ mt: 1 }}>
                  {others.map((x) => <FieldInput key={x.name} f={x} value={form[x.name] ?? ""} initial={initial[x.name] ?? ""} err={fieldErr[x.name]} req={t.required}
                    ro={x.read_only ? t.readOnly : undefined} onChange={(v) => setForm((s) => ({ ...s, [x.name]: v }))} />)}
                </Stack>
              </Collapse>
            </Box>
            <Box sx={{ px: 2, py: 1.25, borderTop: `1px solid ${C.line}`, bgcolor: "#FAFBFC" }}>
              {pendingNav != null && (
                <Alert severity="warning" sx={{ mb: 1, py: 0 }} action={<>
                  <Button size="small" color="inherit" onClick={() => { const n = pendingNav; setPendingNav(null); setForm(initial); open(n); }}>{t.discard}</Button>
                </>}>{t.unsaved}</Alert>
              )}
              {msg && <Alert severity={msg.tone} sx={{ mb: 1, py: 0, fontSize: 12.5 }} onClose={() => setMsg(null)}>{msg.text}</Alert>}
              <Stack direction="row" sx={{ gap: 1 }}>
                <Button variant="outlined" startIcon={<SkipNextOutlined />} disabled={saving || loadingDetail}
                  onClick={() => save("skipped", true)} sx={{ color: COL.skipped, borderColor: COL.skipped + "88" }}>{t.skip}</Button>
                <Button fullWidth variant="contained" startIcon={saving ? <CircularProgress size={16} color="inherit" /> : <SaveOutlined />}
                  disabled={saving || loadingDetail} onClick={() => save("completed", true)}>{t.save}</Button>
              </Stack>
              <Stack direction="row" alignItems="center" sx={{ mt: 0.75, gap: 0.5 }}>
                <Checkbox size="small" checked={wholeLayer} onChange={(e) => setWholeLayer(e.target.checked)} sx={{ p: 0.25 }} />
                <Typography sx={{ fontSize: 12, flex: 1 }}>{lang === "ar" ? `طبّق القيم المعدّلة على كل عناصر الطبقة (${byClass[row.target]?.total || 0})` : `Apply the changed values to every feature of this layer (${byClass[row.target]?.total || 0})`}</Typography>
                <Typography sx={{ fontSize: 11, color: C.muted }} dir="ltr">Ctrl+Enter</Typography>
              </Stack>
            </Box>
          </>)}
        </Box>
      </Box>
    </Box>
  );
}

function LayerRow({ label, sub, geom, active, hidden, total, done, open, err, left, onClick, onToggle }: {
  label: string; sub?: string; geom?: string; active: boolean; hidden?: boolean; total: number; done: number; open: number; err: number;
  left: string; onClick: () => void; onToggle?: () => void;
}) {
  const pct = total ? Math.round((100 * done) / total) : 0;
  return (
    <Box onClick={onClick} sx={{ px: 1.5, py: 0.9, cursor: "pointer", bgcolor: active ? "#E8F0FC" : "transparent",
      borderInlineStart: `3px solid ${active ? COL.current : "transparent"}`, opacity: hidden ? 0.45 : 1, "&:hover": { bgcolor: active ? "#E8F0FC" : "#F5F6F8" } }}>
      <Stack direction="row" alignItems="center" sx={{ gap: 0.75 }}>
        {geom && <Box sx={{ width: 12, height: geom === "line" ? 3 : 12, borderRadius: geom === "point" ? "50%" : 0.5, flexShrink: 0,
          bgcolor: geom === "polygon" ? "#8A94A355" : "#8A94A3", border: geom === "polygon" ? "1.5px solid #8A94A3" : "none" }} />}
        <Typography sx={{ fontSize: 13, fontWeight: active ? 700 : 600, flex: 1, minWidth: 0 }} noWrap>{label}</Typography>
        {onToggle && (
          <IconButton size="small" onClick={(e) => { e.stopPropagation(); onToggle(); }} sx={{ p: 0.25 }}>
            {hidden ? <VisibilityOff sx={{ fontSize: 16 }} /> : <Visibility sx={{ fontSize: 16, color: C.muted }} />}
          </IconButton>
        )}
      </Stack>
      {sub && <Typography sx={{ fontSize: 11, color: C.muted }} noWrap>{sub}</Typography>}
      <LinearProgress variant="determinate" value={pct} sx={{ mt: 0.5, height: 4, borderRadius: 2, bgcolor: "#EEF0F3", "& .MuiLinearProgress-bar": { bgcolor: COL.completed } }} />
      <Stack direction="row" sx={{ gap: 1, mt: 0.4 }}>
        <Typography sx={{ fontSize: 11.5, color: C.muted }}>{done}/{total}</Typography>
        {open > 0 && <Typography sx={{ fontSize: 11.5, color: C.ink, fontWeight: 650 }}>{open} {left}</Typography>}
        {err > 0 && <Typography sx={{ fontSize: 11.5, color: COL.error, fontWeight: 650 }}>{err} ✕</Typography>}
      </Stack>
    </Box>
  );
}

/** readable label: the template alias when it is a real one, else the field name in words */
export function nice(f: AssignField) {
  if (f.alias && f.alias.toUpperCase() !== f.name.toUpperCase()) return f.alias;
  const known: Record<string, string> = { PROJECTID: "Project ID", ASSETCATEGORY: "Asset category", MAINASSETNAME: "Main asset name",
    SUBASSETNAME: "Sub asset name", MAINT_OWNERSHIP: "Maintenance ownership", UNITTYPE: "Unit type", PLANTTYPE: "Plant type",
    IRRIGATIONSYSTEMTYPE: "Irrigation system type", IRRIGATIONWATERSOURCE: "Irrigation water source", ZCOORD: "Elevation (Z)" };
  if (known[f.name.toUpperCase()]) return known[f.name.toUpperCase()];
  const t = f.name.toLowerCase().replace(/_/g, " ").replace(/\bsummr\b/, "summer").replace(/\baverae\b/, "average");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

function Stat({ label, value, color, dot }: { label: string; value: number; color: string; dot?: boolean }) {
  return (
    <Box>
      <Stack direction="row" alignItems="center" sx={{ gap: 0.6 }}>
        {dot && <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: color }} />}
        <Typography sx={{ fontSize: 12, color: C.muted }}>{label}</Typography>
      </Stack>
      <Typography sx={{ fontSize: 20, fontWeight: 750, lineHeight: 1.15, color }}>{value.toLocaleString()}</Typography>
    </Box>
  );
}

function Mono({ k, v }: { k: string; v: string | number }) {
  return <Typography sx={{ fontSize: 12, color: C.muted }}>{k} <Box component="span" sx={{ color: C.ink, fontWeight: 650, fontFamily: "ui-monospace, monospace" }}>{v}</Box></Typography>;
}

function Tool({ title, onClick, children, selected, disabled }: { title: string; onClick: (e: React.MouseEvent<HTMLElement>) => void; children: React.ReactNode; selected?: boolean; disabled?: boolean }) {
  return (
    <Tooltip title={title} placement="left">
      <span>
        <ToggleButton value="x" size="small" selected={!!selected} disabled={disabled} onClick={onClick}
          sx={{ bgcolor: "#fff", p: 0.6, boxShadow: "0 1px 3px rgba(0,0,0,.12)", "&:hover": { bgcolor: "#fff" }, "&.Mui-selected, &.Mui-selected:hover": { bgcolor: "#E8F0FC", color: COL.current } }}>
          {children}
        </ToggleButton>
      </span>
    </Tooltip>
  );
}

export function FieldInput({ f, value, initial, onChange, err, ph, ro, req }: {
  f: AssignField; value: string; initial: string; onChange: (v: string) => void; err?: string; ph?: string; ro?: string; req: string;
}) {
  const changed = value !== initial;
  const label = <>{nice(f)}{f.required && <Box component="span" sx={{ color: C.red }}> *</Box>}</>;
  const helper = err || ro || (nice(f) !== f.name ? f.name : undefined);
  const hint = ph ? `N/A — ${ph}` : undefined;
  const sx: Record<string, unknown> = changed ? { "& .MuiOutlinedInput-root": { bgcolor: "#EEF5FF" } } : {};
  if (f.domain && f.name.toUpperCase().includes("NAME")) {      // names: any text, the template list only suggests
    const opts = f.domain.map((o) => o[0]);
    return (
      <Autocomplete size="small" freeSolo options={opts} value={value} disabled={!!ro}
        onInputChange={(_, v) => onChange(v)} onChange={(_, v) => onChange((v as string) || "")}
        renderInput={(p) => <TextField {...p} InputLabelProps={{ ...p.InputLabelProps, shrink: true }} label={label} error={!!err} helperText={helper} sx={sx} />} />
    );
  }
  if (f.domain) {
    const opts = f.domain;
    const sel = opts.find((o) => o[0] === value) || null;
    return (
      <Autocomplete size="small" options={opts} value={sel} disabled={!!ro}
        getOptionLabel={(o) => (o[1] && o[1] !== o[0] ? `${o[0]} — ${o[1]}` : o[0])}
        isOptionEqualToValue={(a, b) => a[0] === b[0]}
        onChange={(_, o) => onChange(o ? o[0] : "")}
        renderInput={(p) => <TextField {...p} InputLabelProps={{ ...p.InputLabelProps, ...(hint ? { shrink: true } : {}) }} label={label} error={!!err} helperText={helper} sx={sx}
          placeholder={hint || (!value && f.required ? req : undefined)} />} />
    );
  }
  const type = f.type === "DATE" ? "date" : ["INTEGER", "SMALLINTEGER", "DOUBLE", "FLOAT"].includes(f.type) ? "number" : "text";
  return (
    <TextField size="small" fullWidth label={label} type={type} value={value} placeholder={hint} disabled={!!ro} error={!!err} helperText={helper} sx={sx}
      InputLabelProps={type === "date" || hint ? { shrink: true } : undefined}
      inputProps={{ maxLength: f.length || undefined, step: f.type === "DOUBLE" || f.type === "FLOAT" ? "any" : undefined, dir: "auto" }}
      onChange={(e) => onChange(e.target.value)} />
  );
}

export { Divider };
