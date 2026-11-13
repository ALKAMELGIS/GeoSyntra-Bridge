import { useEffect, useRef } from "react";
import { Box } from "@mui/material";
import "ol/ol.css";
import OlMap from "ol/Map";
import View from "ol/View";
import VectorLayer from "ol/layer/Vector";
import VectorSource from "ol/source/Vector";
import Feature from "ol/Feature";
import { LineString, Point, Polygon } from "ol/geom";
import { fromLonLat } from "ol/proj";
import { buffer, createEmpty, extend, getHeight, getWidth } from "ol/extent";
import { defaults as defaultControls, ScaleLine } from "ol/control";
import { Circle as CircleStyle, Fill, Stroke, Style } from "ol/style";
import { basemapLayers } from "./MapView";
import { C } from "../theme";

/** A shape in lon/lat. */
export interface GeoShape {
  c: number[][]; k: "point" | "line" | "polygon";
  color: string; width?: number; fill?: string; dash?: number[]; halo?: boolean;
}

/**
 * A small satellite map with shapes on it — shows where drawing geometry really lies.
 * `minSpan` (m) keeps some surroundings in view around tiny geometry.
 */
export default function GeoPreview({ shapes, height = 300, minSpan = 60, fitKey, fit }: {
  shapes: GeoShape[]; height?: number | string; minSpan?: number; fitKey?: string; fit?: number[][];
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<OlMap | null>(null);
  const src = useRef(new VectorSource());

  useEffect(() => {
    if (!el.current) return;
    const m = new OlMap({
      target: el.current,
      layers: [...basemapLayers("imagery"), new VectorLayer({ source: src.current })],
      view: new View({ center: fromLonLat([54.37, 24.45]), zoom: 10, maxZoom: 21 }),
      controls: defaultControls({ rotate: false, attributionOptions: { collapsible: true } }).extend([new ScaleLine({ minWidth: 64 })]),
    });
    map.current = m;
    return () => { m.setTarget(undefined); map.current = null; };
  }, []);

  useEffect(() => {
    const s = src.current;
    s.clear();
    const ext = createEmpty();
    shapes.forEach((g) => {
      const pts = g.c.filter((p) => p && isFinite(p[0]) && isFinite(p[1])).map((p) => fromLonLat([p[0], p[1]]));
      if (!pts.length) return;
      const geom = g.k === "point" || pts.length === 1 ? new Point(pts[0])
        : g.k === "polygon" ? new Polygon([pts]) : new LineString(pts);
      const f = new Feature(geom);
      const w = g.width ?? 2;
      const st: Style[] = [];
      if (geom instanceof Point) {
        st.push(new Style({ image: new CircleStyle({ radius: 5 + w / 2, fill: g.fill ? new Fill({ color: g.fill }) : undefined,
          stroke: new Stroke({ color: g.color, width: w }) }) }));
      } else {
        if (g.halo !== false) st.push(new Style({ stroke: new Stroke({ color: "rgba(255,255,255,.85)", width: w + 3 }) }));
        st.push(new Style({ stroke: new Stroke({ color: g.color, width: w, lineDash: g.dash }),
          fill: g.fill ? new Fill({ color: g.fill }) : undefined }));
      }
      f.setStyle(st);
      s.addFeature(f);
      extend(ext, geom.getExtent());
    });
    if (fit && fit.length) {   // fit to a given area instead of every shape
      const fe = createEmpty();
      fit.forEach((q) => extend(fe, [...fromLonLat(q), ...fromLonLat(q)]));
      ext.splice(0, 4, ...fe);
    }
    const m = map.current;
    if (!m || !isFinite(ext[0])) return;
    const lat = Math.abs(shapes.find((g) => g.c.length)?.c[0]?.[1] || 0);
    const span = minSpan / Math.cos((lat * Math.PI) / 180);
    const grow = Math.max(0, (span - Math.max(getWidth(ext), getHeight(ext))) / 2);
    m.updateSize();
    m.getView().fit(buffer(ext, grow), { padding: [24, 24, 24, 24], maxZoom: 20, duration: 0 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey ?? JSON.stringify(shapes.map((g) => g.c.length + ":" + (g.c[0] || []).join(",")))]);

  return <Box ref={el} sx={{ height, width: "100%", borderRadius: 1.5, overflow: "hidden", bgcolor: C.ink,
    "& .ol-scale-line": { left: 8, bottom: 8 }, "& .ol-zoom": { top: 8, left: 8 } }} />;
}
