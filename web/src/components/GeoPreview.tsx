import { useEffect, useRef } from "react";
import { Box } from "@mui/material";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import Feature from "ol/Feature";
import { LineString, Point, Polygon } from "ol/geom";
import { fromLonLat, toLonLat } from "ol/proj";
import { buffer, createEmpty, extend, getHeight, getWidth } from "ol/extent";
import { defaults as defaultControls, ScaleLine } from "ol/control";
import { Circle as CircleStyle, Fill, Stroke, Style, Text } from "ol/style";
import Projection from "ol/proj/Projection";
import { basemapLayers } from "./MapView";
import { C } from "../theme";

/** A shape in lon/lat. */
export interface GeoShape {
  c: number[][]; k: "point" | "line" | "polygon";
  color: string; width?: number; fill?: string; dash?: number[]; halo?: boolean; label?: string;
}

/**
 * A small satellite map with shapes on it — shows where drawing geometry really lies.
 * `minSpan` (m) keeps some surroundings in view around tiny geometry.
 */
export default function GeoPreview({ shapes, height = 300, minSpan = 60, fitKey, fit, onPick, local, picking, bg }: {
  shapes: GeoShape[]; height?: number | string; minSpan?: number; fitKey?: string; fit?: number[][];
  /** click on the map: lon/lat (or drawing x/y when `local`, snapped to the nearest vertex within 12 px) */
  onPick?: (c: number[]) => void;
  /** drawing coordinates, no basemap */
  local?: boolean;
  picking?: boolean;
  bg?: string;
}) {
  const P = (q: number[]) => local ? [q[0], q[1]] : fromLonLat([q[0], q[1]]);
  const pickRef = useRef(onPick); pickRef.current = onPick;
  const shapesRef = useRef(shapes); shapesRef.current = shapes;
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<OlMap | null>(null);
  const src = useRef(new VectorSource());

  useEffect(() => {
    if (!el.current) return;
    const proj = local ? new Projection({ code: "GSB_LOCAL", units: "m", extent: [-1e9, -1e9, 1e9, 1e9] }) : undefined;
    const m = new OlMap({
      target: el.current,
      layers: [...(local ? [] : basemapLayers("imagery")), new VectorLayer({ source: src.current })],
      view: local ? new View({ projection: proj, center: [0, 0], zoom: 2, maxZoom: 42 })
        : new View({ center: fromLonLat([54.37, 24.45]), zoom: 9, maxZoom: 21 }),
      controls: defaultControls({ rotate: false, attributionOptions: { collapsible: true } }).extend([new ScaleLine({ minWidth: 64 })]),
    });
    map.current = m;
    m.on("singleclick", (ev) => {
      const f = pickRef.current;
      if (!f) return;
      if (!local) { const [lon, lat] = toLonLat(ev.coordinate); f([lon, lat]); return; }
      // drawing: snap to the nearest vertex on screen
      let best: number[] | null = null, bd = 12;
      for (const g of shapesRef.current) for (const q of g.c) {
        const px = m.getPixelFromCoordinate([q[0], q[1]]);
        if (!px) continue;
        const d = Math.hypot(px[0] - ev.pixel[0], px[1] - ev.pixel[1]);
        if (d < bd) { bd = d; best = q; }
      }
      f(best ? [best[0], best[1]] : ev.coordinate);
    });
    return () => { m.setTarget(undefined); map.current = null; };
  }, []);

  const shapesKey = JSON.stringify(shapes.map((g) => `${g.k}${g.c.length}:${(g.c[0] || []).join(",")}:${g.color}:${g.label || ""}`));
  useEffect(() => {             // draw — on every change of the shapes
    const s = src.current;
    s.clear();
    shapes.forEach((g) => {
      const pts = g.c.filter((p) => p && isFinite(p[0]) && isFinite(p[1])).map(P);
      if (!pts.length) return;
      const geom = g.k === "point" || pts.length === 1 ? new Point(pts[0])
        : g.k === "polygon" ? new Polygon([pts]) : new LineString(pts);
      const f = new Feature(geom);
      const w = g.width ?? 2;
      const st: Style[] = [];
      if (geom instanceof Point) {
        st.push(new Style({ image: new CircleStyle({ radius: 5 + w / 2, fill: g.fill ? new Fill({ color: g.fill }) : undefined,
          stroke: new Stroke({ color: g.color, width: w }) }),
          text: g.label ? new Text({ text: g.label, offsetY: -16, font: "600 12px Geist, sans-serif", fill: new Fill({ color: "#fff" }),
            backgroundFill: new Fill({ color: g.color }), padding: [1, 5, 1, 5] }) : undefined }));
      } else {
        if (g.halo !== false) st.push(new Style({ stroke: new Stroke({ color: "rgba(255,255,255,.85)", width: w + 3 }) }));
        st.push(new Style({ stroke: new Stroke({ color: g.color, width: w, lineDash: g.dash }),
          fill: g.fill ? new Fill({ color: g.fill }) : undefined }));
      }
      f.setStyle(st);
      s.addFeature(f);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shapesKey]);

  useEffect(() => {             // fit — only when the fit key changes (adding a marker must not move the view)
    const ext = createEmpty();
    src.current.getFeatures().forEach((f) => { const g = f.getGeometry(); if (g) extend(ext, g.getExtent()); });
    if (fit && fit.length) {   // fit to a given area instead of every shape
      const fe = createEmpty();
      fit.forEach((q) => extend(fe, [...P(q), ...P(q)]));
      ext.splice(0, 4, ...fe);
    }
    const m = map.current;
    if (!m || !isFinite(ext[0])) return;
    const lat = Math.abs(shapes.find((g) => g.c.length)?.c[0]?.[1] || 0);
    const span = local ? 0 : minSpan / Math.cos((lat * Math.PI) / 180);
    const grow = Math.max(0, (span - Math.max(getWidth(ext), getHeight(ext))) / 2);
    m.updateSize();
    m.getView().fit(buffer(ext, grow), { padding: [24, 24, 24, 24], maxZoom: local ? 42 : 20, duration: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey ?? shapesKey]);

  return <Box ref={el} sx={{ height, width: "100%", borderRadius: 1.5, overflow: "hidden", bgcolor: bg || C.ink, border: bg ? `1px solid ${C.line}` : 0, cursor: picking ? "crosshair" : undefined,
    "& .ol-viewport": { cursor: picking ? "crosshair !important" : undefined },
    "& .ol-scale-line": { left: 8, bottom: 8 }, "& .ol-zoom": { top: 8, left: 8 } }} />;
}
