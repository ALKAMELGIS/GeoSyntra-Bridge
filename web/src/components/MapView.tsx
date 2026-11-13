import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Box, Menu, MenuItem, Stack, ToggleButton, Tooltip, Typography } from "@mui/material";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import TileLayer from "ol/layer/Tile";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import XYZ from "ol/source/XYZ";
import GeoJSONFormat from "ol/format/GeoJSON";
import Projection from "ol/proj/Projection";
import { fromLonLat } from "ol/proj";
import { Circle as CircleStyle, Fill, Stroke, Style, Text } from "ol/style";
import type { FeatureLike } from "ol/Feature";
import { defaults as defaultControls, ScaleLine } from "ol/control";
import MapOutlined from "@mui/icons-material/MapOutlined";
import type { GeoJSON } from "../types";
import { C } from "../theme";

// Esri basemaps (ArcGIS Online tile services, attribution required)
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
type BaseLayer = { url: string; attributions: string; maxZoom: number; minZoom?: number };
// World Imagery has no tiles at the highest zooms in many places (it then draws "Map data not yet available").
// The imagery is stacked: a base that always exists, then sharper levels asked with blankTile=false, so a
// missing tile is simply absent and the level below shows through.
const IMG = `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`;
const IMG_ATTR = "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community";
const IMAGERY: BaseLayer[] = [
  { url: IMG, maxZoom: 15, attributions: IMG_ATTR },
  { url: `${IMG}?blankTile=false`, maxZoom: 17, minZoom: 14.5, attributions: IMG_ATTR },
  { url: `${IMG}?blankTile=false`, maxZoom: 19, minZoom: 16.5, attributions: IMG_ATTR },
];
export const BASEMAPS: Record<string, { label: string; layers: BaseLayer[] }> = {
  satellite: { label: "Satellite (Esri World Imagery)", layers: [...IMAGERY,
    { url: `${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, maxZoom: 19, attributions: "Labels © Esri" }] },
  imagery: { label: "Satellite without labels", layers: IMAGERY },
  gray: { label: "Light gray (Esri)", layers: [
    { url: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`, maxZoom: 16, attributions: "© Esri, HERE, Garmin, OpenStreetMap contributors" }] },
  none: { label: "No basemap", layers: [] },
};

/** Tile layers of every basemap (hidden; show the chosen key with `layer.get("basemap") === key`). */
export function basemapLayers(only?: string): TileLayer<XYZ>[] {
  return Object.entries(BASEMAPS).filter(([k]) => !only || k === only).flatMap(([key, bm]) => bm.layers.map((l) => {
    const t = new TileLayer({ source: new XYZ({ url: l.url, attributions: l.attributions, maxZoom: l.maxZoom, crossOrigin: "anonymous" }),
      visible: !!only, minZoom: l.minZoom });
    t.set("basemap", key);
    return t;
  }));
}
const savedBase = () => { try { return localStorage.getItem("gsb_basemap") || "satellite"; } catch { return "satellite"; } };

export interface MapHandle {
  zoomTo: (lonlat: number[] | null | undefined, fid?: number | null) => void;
}

interface Props {
  data: GeoJSON | null;
  mode: "cad" | "result" | "elevation";
  highlightLayer?: string | null;
  selectedFid?: number | null;
  onSelect?: (props: Record<string, unknown>) => void;
  height?: number | string;
}

const LOCAL = new Projection({ code: "LOCAL-CAD", units: "m", extent: [-1e9, -1e9, 1e9, 1e9] });
const PALETTE = ["#2E5C9A", "#2E9E6B", "#8E44AD", "#D35400", "#16A085", "#C0392B", "#7F8C8D", "#2C3E50", "#B7791F", "#1ABC9C"];

function hash(s: string) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

// elevation ramp (low → high): blue, teal, green, yellow, orange, brown
const RAMP = ["#2C7BB6", "#00A6CA", "#00CCBC", "#90EB9D", "#F9D057", "#F29E2E", "#A6611A"];
const NO_Z = "#1F2937";
export function zColor(z: number | null | undefined, lo: number, hi: number) {
  if (z == null || !isFinite(z)) return NO_Z;
  const t = hi > lo ? Math.min(1, Math.max(0, (z - lo) / (hi - lo))) : 0.5;
  return RAMP[Math.min(RAMP.length - 1, Math.floor(t * RAMP.length))];
}

function styleFor(color: string, width = 2, emphasize = false, kind?: string) {
  const stroke = new Stroke({ color, width: emphasize ? width + 2 : width });
  return new Style({
    stroke,
    fill: new Fill({ color: kind === "polygon" || !kind ? color + "22" : "transparent" }),
    image: new CircleStyle({ radius: emphasize ? 7 : 4.5, fill: new Fill({ color }), stroke: new Stroke({ color: "#fff", width: 1.5 }) }),
  });
}

export const MapView = forwardRef<MapHandle, Props>(function MapView(
  { data, mode, highlightLayer, selectedFid, onSelect, height = "100%" }, ref) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<OlMap | null>(null);
  const vec = useRef<VectorLayer<any> | null>(null);
  const base = useRef<TileLayer<XYZ>[]>([]);
  const [basemap, setBasemapState] = useState<string>(savedBase());
  const [menu, setMenu] = useState<HTMLElement | null>(null);
  const setBasemap = (k: string) => { setBasemapState(k); try { localStorage.setItem("gsb_basemap", k); } catch { /* private mode */ } };
  const zr = (() => {
    const zs = (data?.features || []).map((f: any) => f.properties?.z_mean).filter((z: any) => typeof z === "number") as number[];
    return zs.length ? [Math.min(...zs), Math.max(...zs)] : [0, 1];
  })();
  const state = useRef({ highlightLayer, selectedFid, mode, zr });
  state.current = { highlightLayer, selectedFid, mode, zr };

  useEffect(() => {
    if (!el.current) return;
    const v = new VectorLayer({
      source: new VectorSource(),
      style: (f: FeatureLike, resolution: number) => {
        const p = f.getProperties();
        const st = state.current;
        if (st.mode === "cad") {
          if (p.kind === "text") {
            if (resolution > 0.6) return undefined;
            return new Style({ text: new Text({ text: String(p.text || "").slice(0, 40), font: "10px Inter, sans-serif", fill: new Fill({ color: "#6B7280" }) }) });
          }
          const hi = st.highlightLayer && p.layer === st.highlightLayer;
          const col = hi ? "#E67E22" : st.highlightLayer ? "#A9B4C4" : PALETTE[hash(String(p.layer)) % PALETTE.length];
          return styleFor(col, hi ? 3 : 1.6, !!hi, p.kind);
        }
        if (st.mode === "elevation") {
          if (p.dem_footprint) return new Style({ stroke: new Stroke({ color: "#0F2341", width: 1.2, lineDash: [6, 4] }), fill: new Fill({ color: "#0F234108" }) });
          const sel = st.selectedFid != null && p.fid === st.selectedFid;
          return styleFor(sel ? "#E67E22" : zColor(p.z_mean as number, st.zr[0], st.zr[1]), 2.4, sel, "point");
        }
        if (p.status === "label") return styleFor(C.amber, 1.5, false, "point");
        const sel = st.selectedFid != null && p.fid === st.selectedFid;
        const col = p.status === "failed" ? C.red : p.status === "skipped" ? "#95A1B3" :
          p.severity === "error" ? C.red : p.severity === "warning" ? C.amber : C.green;
        return styleFor(sel ? "#E67E22" : col, 2.2, sel, String(p.status === "failed" ? "point" : ""));
      },
    });
    vec.current = v;
    const tiles = basemapLayers();
    base.current = tiles;
    const m = new OlMap({
      target: el.current, layers: [...tiles, v],
      view: new View({ center: [0, 0], zoom: 2 }),
      controls: defaultControls({ attribution: true, rotate: false }).extend([new ScaleLine()]),
    });
    m.on("singleclick", (e) => {
      const f = m.forEachFeatureAtPixel(e.pixel, (x) => x, { hitTolerance: 5 });
      if (f && onSelectRef.current) onSelectRef.current(f.getProperties());
    });
    map.current = m;
    return () => m.setTarget(undefined);
  }, []);

  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const m = map.current, v = vec.current;
    if (!m || !v) return;
    const src = v.getSource()!;
    src.clear();
    if (!data || !data.features.length) return;
    const geographic = data.crs_is_geographic;
    const proj = geographic ? "EPSG:3857" : LOCAL;
    if (m.getView().getProjection().getCode() !== (geographic ? "EPSG:3857" : LOCAL.getCode())) {
      m.setView(new View({ projection: proj, center: [0, 0], zoom: 2 }));
    }
    base.current.forEach((t) => t.setVisible(geographic && t.get("basemap") === basemap));
    const feats = new GeoJSONFormat().readFeatures(data, {
      dataProjection: geographic ? "EPSG:4326" : LOCAL, featureProjection: proj,
    });
    src.addFeatures(feats);
    const ext = src.getExtent();
    if (ext && isFinite(ext[0])) m.getView().fit(ext, { padding: [30, 30, 30, 30], maxZoom: 20, duration: 0 });
  }, [data]);

  useEffect(() => { vec.current?.changed(); }, [highlightLayer, selectedFid, mode]);
  useEffect(() => { base.current.forEach((t) => t.setVisible(!!data?.crs_is_geographic && t.get("basemap") === basemap)); }, [basemap, data]);

  useImperativeHandle(ref, () => ({
    zoomTo: (lonlat, fid) => {
      const m = map.current, v = vec.current;
      if (!m || !v) return;
      if (fid != null) {
        const f = v.getSource()!.getFeatures().find((x: any) => x.get("fid") === fid);
        const g = f?.getGeometry();
        if (g) {
          m.getView().fit(g.getExtent(), { padding: [80, 80, 80, 80], maxZoom: 21, duration: 350 });
          return;
        }
      }
      if (lonlat) {
        const c = data?.crs_is_geographic ? fromLonLat(lonlat) : lonlat;
        m.getView().animate({ center: c, zoom: Math.max(m.getView().getZoom() || 18, 19), duration: 350 });
      }
    },
  }), [data]);

  return (
    <Box sx={{ position: "relative", height, minHeight: 260, border: `1px solid ${C.line}`, borderRadius: 1.5, overflow: "hidden", bgcolor: "#FBFCFD" }}>
      <Box ref={el} sx={{ position: "absolute", inset: 0 }} />
      <Stack direction="row" spacing={0.5} sx={{ position: "absolute", top: 8, right: 8 }}>
        {data?.crs_is_geographic && (
          <Tooltip title={`Basemap: ${BASEMAPS[basemap]?.label || ""}`}>
            <ToggleButton value="b" size="small" selected={basemap !== "none"} onClick={(e) => setMenu(e.currentTarget)}
              sx={{ bgcolor: "#fff", p: 0.5, "&.Mui-selected, &.Mui-selected:hover": { bgcolor: "#fff", color: C.navy }, "&:hover": { bgcolor: "#fff" } }}>
              <MapOutlined sx={{ fontSize: 18 }} />
            </ToggleButton>
          </Tooltip>
        )}
        <Menu anchorEl={menu} open={!!menu} onClose={() => setMenu(null)}>
          {Object.entries(BASEMAPS).map(([k, b]) => (
            <MenuItem key={k} dense selected={k === basemap} onClick={() => { setBasemap(k); setMenu(null); }}>{b.label}</MenuItem>))}
        </Menu>
      </Stack>
      {!data?.features.length && (
        <Typography variant="caption" sx={{ position: "absolute", top: "50%", width: "100%", textAlign: "center" }}>
          No geometry to display yet
        </Typography>
      )}
      {data && !data.crs_is_geographic && data.features.length > 0 && (
        <Typography variant="caption" sx={{ position: "absolute", left: 8, top: 62, bgcolor: "#ffffffcc", px: 0.75, borderRadius: 1 }}>
          Drawing coordinates (no source CRS — basemap off)
        </Typography>
      )}
      {mode === "elevation" && (
        <Stack direction="row" spacing={0.75} alignItems="center" sx={{ position: "absolute", left: 44, top: 8, bgcolor: "#ffffffe6", px: 1, py: 0.5, borderRadius: 1, border: `1px solid ${C.line}` }}>
          <Typography variant="caption">{state.current.zr[0].toFixed(2)} m</Typography>
          <Box sx={{ width: 110, height: 9, borderRadius: 1, background: `linear-gradient(90deg, ${RAMP.join(",")})` }} />
          <Typography variant="caption">{state.current.zr[1].toFixed(2)} m</Typography>
          <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: NO_Z, ml: 1 }} />
          <Typography variant="caption">no elevation</Typography>
        </Stack>
      )}
      {mode === "result" && (
        <Stack direction="row" spacing={1.5} sx={{ position: "absolute", left: 44, top: 8, bgcolor: "#ffffffe6", px: 1, py: 0.5, borderRadius: 1, border: `1px solid ${C.line}` }}>
          {[["OK", C.green], ["Warning", C.amber], ["Error / failed", C.red], ["Skipped", "#95A1B3"]].map(([l, c]) => (
            <Stack key={l} direction="row" alignItems="center" spacing={0.5}>
              <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: c }} />
              <Typography variant="caption">{l}</Typography>
            </Stack>
          ))}
        </Stack>
      )}
    </Box>
  );
});
