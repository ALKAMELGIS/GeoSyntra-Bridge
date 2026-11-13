import { useEffect, useMemo, useRef, useState } from "react";
import { Box, Button, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import AddRounded from "@mui/icons-material/AddRounded";
import RemoveRounded from "@mui/icons-material/RemoveRounded";
import FitScreenOutlined from "@mui/icons-material/FitScreenOutlined";
import DownloadOutlined from "@mui/icons-material/DownloadOutlined";
import RestartAltOutlined from "@mui/icons-material/RestartAltOutlined";
import ZoomInMapOutlined from "@mui/icons-material/ZoomInMapOutlined";
import type { WorkflowModel } from "./model";
import { renderWorkspace, type NodePos } from "./render";
import { C } from "../theme";

const key = (m: WorkflowModel) => `gsb_wf_pos:${m.file}:${m.layers.length}:${m.templates.length}`;
const loadPos = (k: string): NodePos => { try { return JSON.parse(localStorage.getItem(k) || "{}"); } catch { return {}; } };

/** Workspace canvas: drag the background to pan, drag a box to move it (the connections follow), wheel to zoom.
 *  The arrangement is kept for this drawing; "Reset layout" returns to the automatic one. */
export function WorkflowCanvas({ model }: { model: WorkflowModel }) {
  const storeKey = key(model);
  const [pos, setPos] = useState<NodePos>(() => loadPos(storeKey));
  useEffect(() => { setPos(loadPos(storeKey)); }, [storeKey]);
  const { svg, width, height: h } = useMemo(() => renderWorkspace(model, { pos }), [model, pos]);
  const box = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ k: 0.8, x: 0, y: 0 });
  const pan = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const move = useRef<{ id: string; sx: number; sy: number; nx: number; ny: number } | null>(null);
  const [moving, setMoving] = useState(false);
  const raf = useRef(0);

  // readable start: about 85% scale, the beginning of the flow at the top left (not a tiny fit-all)
  const start = () => {
    const el = box.current; if (!el) return;
    const k = Math.min(1, Math.max(0.7, el.clientHeight / h));
    setView({ k, x: 16, y: 8 });
  };
  const fit = () => {
    const el = box.current; if (!el) return;
    const k = Math.min(el.clientWidth / width, el.clientHeight / h) * 0.98;
    setView({ k, x: (el.clientWidth - width * k) / 2, y: 8 });
  };
  useEffect(start, [storeKey]);

  const zoomAt = (f: number, cx?: number, cy?: number) => setView((v) => {
    const el = box.current!;
    const px = cx ?? el.clientWidth / 2, py = cy ?? el.clientHeight / 2;
    const k = Math.min(3, Math.max(0.08, v.k * f));
    return { k, x: px - ((px - v.x) * k) / v.k, y: py - ((py - v.y) * k) / v.k };
  });
  useEffect(() => {
    const el = box.current; if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // current position of a node, read from the drawn svg (its first rect is the shadow at x+2, y+3)
  const nodeXY = (g: Element) => {
    const r = g.querySelectorAll("rect")[1] as SVGRectElement | undefined;
    return r ? { x: Number(r.getAttribute("x")), y: Number(r.getAttribute("y")) } : null;
  };
  const onDown = (e: React.MouseEvent) => {
    const g = (e.target as Element).closest("[data-id]");
    if (g) {
      const p0 = nodeXY(g);
      if (p0) { move.current = { id: g.getAttribute("data-id")!, sx: e.clientX, sy: e.clientY, nx: p0.x, ny: p0.y }; setMoving(true); return; }
    }
    pan.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
  };
  const onMove = (e: React.MouseEvent) => {
    const m = move.current;
    if (m) {
      const cx = e.clientX, cy = e.clientY;
      cancelAnimationFrame(raf.current);
      raf.current = requestAnimationFrame(() => {
        const snap = (v: number) => Math.round(v / 10) * 10;            // 10 px grid keeps the boxes aligned
        setPos((p) => ({ ...p, [m.id]: { x: snap(m.nx + (cx - m.sx) / view.k), y: snap(m.ny + (cy - m.sy) / view.k) } }));
      });
      return;
    }
    const d = pan.current;
    if (d) setView((v) => ({ ...v, x: d.vx + e.clientX - d.x, y: d.vy + e.clientY - d.y }));
  };
  const onUp = () => {
    if (move.current) { try { localStorage.setItem(storeKey, JSON.stringify(pos)); } catch { /* private mode */ } }
    move.current = null; pan.current = null; setMoving(false);
  };
  useEffect(() => { if (!moving) { try { localStorage.setItem(storeKey, JSON.stringify(pos)); } catch { /* ignore */ } } }, [moving]);
  const reset = () => { setPos({}); try { localStorage.removeItem(storeKey); } catch { /* ignore */ } setTimeout(start, 0); };

  const save = (blob: Blob, name: string) => {
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  const base = (model.file || "workspace").replace(/\.[^.]+$/, "") + "_workspace";
  const downloadSvg = () => save(new Blob([svg], { type: "image/svg+xml" }), `${base}.svg`);
  const downloadPng = () => {
    const img = new Image();
    img.onload = () => {
      const s = Math.min(2, 16000 / Math.max(width, h));
      const cv = document.createElement("canvas"); cv.width = Math.round(width * s); cv.height = Math.round(h * s);
      const ctx = cv.getContext("2d")!; ctx.scale(s, s); ctx.drawImage(img, 0, 0);
      cv.toBlob((b) => b && save(b, `${base}.png`), "image/png");
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  };
  const moved = Object.keys(pos).length;

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: { xs: 2, md: 3 }, minHeight: 48, bgcolor: "#FBFAF8", borderBottom: `1px solid ${C.line}`, flexWrap: "wrap" }}>
        <Typography sx={{ fontSize: 13.5, fontWeight: 500, minWidth: 0 }} noWrap>
          {model.layers.length} CAD layers → {model.templates.length} geodatabase writer{model.templates.length === 1 ? "" : "s"}
          {moved > 0 && <Box component="span" sx={{ ml: 1, color: C.muted, fontWeight: 400 }}>· {moved} box{moved > 1 ? "es" : ""} moved</Box>}
        </Typography>
        <Typography sx={{ flex: 1, fontSize: 12.5, color: C.muted, display: { xs: "none", lg: "block" }, pl: 2 }} noWrap>
          Drag a box to move it · drag the background to pan · scroll to zoom
        </Typography>
        <Tooltip title="Zoom out"><IconButton size="small" aria-label="Zoom out" onClick={() => zoomAt(1 / 1.25)}><RemoveRounded fontSize="small" /></IconButton></Tooltip>
        <Typography variant="caption" sx={{ width: 42, textAlign: "center" }}>{Math.round(view.k * 100)}%</Typography>
        <Tooltip title="Zoom in"><IconButton size="small" aria-label="Zoom in" onClick={() => zoomAt(1.25)}><AddRounded fontSize="small" /></IconButton></Tooltip>
        <Tooltip title="Readable size, from the start of the flow"><IconButton size="small" aria-label="Readable size" onClick={start}><ZoomInMapOutlined fontSize="small" /></IconButton></Tooltip>
        <Tooltip title="Whole workspace"><IconButton size="small" aria-label="Fit to window" onClick={fit}><FitScreenOutlined fontSize="small" /></IconButton></Tooltip>
        <Button size="small" startIcon={<RestartAltOutlined />} disabled={!moved} onClick={reset}>Reset layout</Button>
        <Button size="small" startIcon={<DownloadOutlined />} onClick={downloadPng}>PNG</Button>
        <Button size="small" startIcon={<DownloadOutlined />} onClick={downloadSvg}>SVG</Button>
      </Stack>
      <Box ref={box} onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}
        sx={{ position: "relative", height: "calc(100vh - 166px)", minHeight: 480, overflow: "hidden", bgcolor: C.bg,
              cursor: moving ? "move" : pan.current ? "grabbing" : "grab", userSelect: "none" }}>
        <Box sx={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0", transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, lineHeight: 0,
                   "& .node:hover > rect:nth-of-type(2)": { stroke: "#1F8FA5", strokeWidth: 2 } }}
          dangerouslySetInnerHTML={{ __html: svg }} />
      </Box>
    </Box>
  );
}
