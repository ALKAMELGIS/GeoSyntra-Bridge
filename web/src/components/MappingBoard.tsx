import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Autocomplete, Box, Button, Chip, Stack, TextField, Tooltip, Typography } from "@mui/material";
import LinkOutlined from "@mui/icons-material/LinkOutlined";
import FolderOutlined from "@mui/icons-material/FolderOutlined";
import StorageOutlined from "@mui/icons-material/StorageOutlined";
import CloseRounded from "@mui/icons-material/CloseRounded";
import { api } from "../api";
import type { Discovery, JobState, LayerMap, Mapping, SchemaDetail, SchemaLayer } from "../types";
import { C } from "../theme";
import { tr } from "../i18n";

/**
 * Connection board: CAD layers on the left, the original geodatabases on the right
 * (geodatabase → feature dataset → feature class). A wire is a mapping; click a layer, then a feature class to
 * connect, click a wire's × to set the layer aside.
 */
interface Props {
  job: JobState; schema: SchemaDetail; discovery: Discovery; mapping: Mapping;
  onPatch: (patch: unknown) => void; onMapping: (m: Mapping) => void; onError: (msg: string) => void;
}

const LABEL: Record<string, string> = { IR: "Irrigation", HL: "Hard Landscaping", RD: "Roads", SL: "Soft Landscaping",
  SW: "Storm Water", STL: "Street Lighting", ITC: "ITC" };
const GEO: Record<string, string> = { point: "point", line: "line", polygon: "area" };
const gdbOf = (l: SchemaLayer, fallback: string) => {
  const m = /ADM_([A-Z]+)/i.exec(l.template || "") || /^adm_([a-z]+)/i.exec(fallback);
  const code = m ? m[1].toUpperCase() : "GDB";
  return { code, name: `ADM_${code}.gdb`, label: LABEL[code] || code };
};
const review = (m?: LayerMap) => !!m && m.role !== "ignore" && !!m.target && ["SUGGESTED", "MANUAL"].includes(m.status);
const ASIDE: Record<string, string> = { basemap: "base map", sheet: "sheet", annotation: "annotation" };

export function MappingBoard({ job, schema, discovery, mapping, onPatch, onMapping, onError }: Props) {
  // several layers can be picked at once (Shift / Ctrl+click) and connected or set aside together
  const [sels, setSels] = useState<string[]>([]);
  const sel = sels[0] ?? null;
  const setSel = (k: string | null) => setSels(k ? [k] : []);
  const [q, setQ] = useState("");
  const [hoverWire, setHoverWire] = useState<string | null>(null);
  const [showAside, setShowAside] = useState(false);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const ports = useRef<Record<string, HTMLElement | null>>({});
  const [geo, setGeo] = useState<{ w: number; h: number; pts: Record<string, { x: number; y: number }> }>({ w: 0, h: 0, pts: {} });

  const fcByName = useMemo(() => new Map(schema.layers.map((l) => [l.name, l])), [schema]);
  const groups = discovery.groups;
  const rows = useMemo(() => groups.map((g) => ({ g, m: mapping.layers[g.key] }))
    .filter(({ m }) => showAside || (m && m.role !== "ignore"))
    .filter(({ g, m }) => !q || `${g.layer} ${g.style || ""} ${m?.target || ""}`.toLowerCase().includes(q.toLowerCase())), [groups, mapping, showAside, q]);
  const connected = groups.filter((g) => { const m = mapping.layers[g.key]; return m && m.role !== "ignore" && m.target; });
  const toReview = groups.filter((g) => review(mapping.layers[g.key])).length;
  const aside = groups.filter((g) => mapping.layers[g.key]?.role === "ignore").length;
  const pending = groups.filter((g) => { const m = mapping.layers[g.key]; return m && m.role !== "ignore" && !m.target; }).length;
  const suggestible = groups.filter((g) => { const m = mapping.layers[g.key]; return m && m.role !== "ignore" && (review(m) || (!m.target && m.alternatives?.length)); }).length;

  // right side: feature classes that are connected, plus the selected layer's suggestions
  const selMap = sel ? mapping.layers[sel] : undefined;
  const suggested = new Set((selMap?.alternatives || []).slice(0, 3).map((a) => a.target).filter((t) => t !== selMap?.target));
  const tree = useMemo(() => {
    const want = new Map<string, number>();
    connected.forEach((g) => { const t = mapping.layers[g.key].target!; want.set(t, (want.get(t) || 0) + g.count); });
    suggested.forEach((t) => want.has(t) || want.set(t, 0));
    const out = new Map<string, { code: string; name: string; label: string; total: number; ds: Map<string, SchemaLayer[]> }>();
    want.forEach((_, t) => {
      const l = fcByName.get(t); if (!l) return;
      const g = gdbOf(l, schema.schema_id);
      const e = out.get(g.name) || { ...g, total: 0, ds: new Map() };
      e.total += want.get(t) || 0;
      const ds = l.feature_dataset || "(root)";
      e.ds.set(ds, [...(e.ds.get(ds) || []), l]);
      out.set(g.name, e);
    });
    return Array.from(out.values()).sort((a, b) => b.total - a.total);
  }, [connected.map((g) => g.key + mapping.layers[g.key].target).join(), [...suggested].join(), fcByName]);
  // left column follows the right column's order (geodatabase → dataset → feature class): wires run parallel
  const fcOrder = new Map<string, number>();
  tree.forEach((gdb) => gdb.ds.forEach((fcs) => fcs.forEach((l) => fcOrder.set(l.name, fcOrder.size))));
  const sortedRows = [...rows].sort((a, b) => {
    const oa = a.m && a.m.role !== "ignore" && a.m.target ? fcOrder.get(a.m.target) ?? 900 : 999;
    const ob = b.m && b.m.role !== "ignore" && b.m.target ? fcOrder.get(b.m.target) ?? 900 : 999;
    return oa - ob || a.g.layer.localeCompare(b.g.layer);
  });
  const allFc = useMemo(() => schema.layers.map((l) => ({ l, g: gdbOf(l, schema.schema_id) }))
    .sort((a, b) => a.g.name.localeCompare(b.g.name) || a.l.name.localeCompare(b.l.name)), [schema]);

  const measure = useCallback(() => {
    const el = box.current; if (!el) return;
    const r = el.getBoundingClientRect();
    const pts: Record<string, { x: number; y: number }> = {};
    Object.entries(ports.current).forEach(([k, p]) => {
      if (!p) return; const b = p.getBoundingClientRect();
      pts[k] = { x: Math.round(b.left + b.width / 2 - r.left), y: Math.round(b.top + b.height / 2 - r.top) };
    });
    const next = { w: Math.round(r.width), h: Math.round(r.height), pts };
    setGeo((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
  }, []);
  useLayoutEffect(measure);
  useEffect(() => {
    const ro = new ResizeObserver(measure); if (box.current) ro.observe(box.current);
    return () => ro.disconnect();
  }, [measure]);

  const connect = (_key: string, target: string) => {
    onPatch({ layers: Object.fromEntries(sels.map((k) => [k, { target, role: "feature" }])) }); setSels([]);
  };
  const setAside = (key: string) => onPatch({ layers: { [key]: { role: "ignore" } } });
  const asideSel = () => { onPatch({ layers: Object.fromEntries(sels.map((k) => [k, { role: "ignore" }])) }); setSels([]); };
  /** click a layer: plain = only this one · Shift = range from the last one · Ctrl/⌘ = add or remove */
  const lastClick = useRef<string | null>(null);
  const pickLayer = (key: string, e: React.MouseEvent | React.KeyboardEvent) => {
    const order = sortedRows.filter((r) => r.m && r.m.role !== "ignore").map((r) => r.g.key);
    if (e.shiftKey && lastClick.current && order.includes(lastClick.current)) {
      const a = order.indexOf(lastClick.current), b = order.indexOf(key);
      const range = order.slice(Math.min(a, b), Math.max(a, b) + 1);
      setSels((cur) => [...cur.filter((k) => !range.includes(k)), ...range]);
    } else if (e.ctrlKey || e.metaKey) setSels((cur) => cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]);
    else setSels((cur) => cur.length === 1 && cur[0] === key ? [] : [key]);
    lastClick.current = key;
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") setSels([]); };
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, []);
  const include = (key: string) => { onPatch({ layers: { [key]: { role: "feature" } } }); setSel(key); };
  const autoMap = async () => {
    setBusy(true);
    try { onMapping(await api.acceptSuggestions(job.job_id, 0.45)); } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };

  const wires = connected.filter((g) => rows.some((r) => r.g.key === g.key)).map((g) => {
    const m = mapping.layers[g.key];
    const a = geo.pts[`L:${g.key}`], b = geo.pts[`R:${m.target}`];
    if (!a || !b) return null;
    const dx = Math.max(60, (b.x - a.x) * 0.5);
    return { key: g.key, d: `M${a.x} ${a.y} C${a.x + dx} ${a.y} ${b.x - dx} ${b.y} ${b.x} ${b.y}`,
      mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, warn: review(m), active: sels.includes(g.key) || hoverWire === g.key };
  }).filter(Boolean) as { key: string; d: string; mid: { x: number; y: number }; warn: boolean; active: boolean }[];

  return (
    <Box sx={{ minHeight: "calc(100vh - 118px)", display: "flex", flexDirection: "column" }}>
      {/* header */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: { xs: 2, md: 3 }, minHeight: 64, bgcolor: "#FAFBFC", borderBottom: `1px solid ${C.line}` }}>
        <Box sx={{ width: 36, height: 36, borderRadius: 1.5, bgcolor: C.bg, display: "grid", placeItems: "center", fontSize: 10.5, fontWeight: 500, color: C.muted, fontFamily: "ui-monospace, Menlo, monospace" }}>
          {(job.file.format || "dwg").toUpperCase()}</Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography sx={{ fontWeight: 500, fontSize: 15 }} noWrap>{job.file.name}</Typography>
          <Typography variant="caption">{tr("{a} layers · {b} objects", { a: groups.length, b: discovery.feature_entity_count })}</Typography>
        </Box>
        <Stack direction="row" spacing={2.5} sx={{ display: { xs: "none", md: "flex" }, pr: 1 }}>
          {(["point", "line", "polygon"] as const).map((k) => {
            const all = groups.filter((g) => g.geometry === k);
            const n = all.reduce((t, g) => t + g.count, 0);
            const conn = all.filter((g) => { const m = mapping.layers[g.key]; return m && m.role !== "ignore" && m.target; }).reduce((t, g) => t + g.count, 0);
            return (
              <Tooltip key={k} title={n ? `${conn.toLocaleString()} of ${n.toLocaleString()} ${GEO[k]} objects connected` : `The drawing has no ${GEO[k]} objects on asset layers`}>
                <Stack direction="row" spacing={0.75} alignItems="center" sx={{ fontSize: 13, color: C.muted, whiteSpace: "nowrap" }}>
                  <Box component="span" sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: !n ? "#C5CBD3" : conn === n ? C.green : C.amber }} />
                  <span>{tr(GEO[k][0].toUpperCase() + GEO[k].slice(1) + "s")}</span>
                  <Box component="span" sx={{ color: C.ink, fontVariantNumeric: "tabular-nums" }}>{conn.toLocaleString()} / {n.toLocaleString()}</Box>
                </Stack>
              </Tooltip>
            );
          })}
        </Stack>
        <Button size="small" onClick={() => setShowAside(!showAside)} sx={{ color: C.muted }}>
          {showAside ? tr("Hide set-aside") : tr("Show set-aside")} ({aside})</Button>
        <Button variant="outlined" startIcon={<LinkOutlined />} disabled={busy || !suggestible} onClick={autoMap}
          sx={{ height: 36 }}>{tr("Apply matches")} {suggestible || ""}</Button>
      </Stack>

      {/* board */}
      <Box ref={box} dir="ltr" sx={{ position: "relative", display: "grid", gridTemplateColumns: "minmax(260px, 1fr) minmax(90px, 200px) minmax(280px, 1.1fr)",
                          px: { xs: 2, md: 3 }, py: 3, gap: 0, flex: 1 }} onClick={(e) => { if (e.target === e.currentTarget) setSel(null); }}>
        <svg width={geo.w} height={geo.h} style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "visible" }}>
          {wires.map((w) => (
            <g key={w.key} style={{ pointerEvents: "stroke", cursor: "pointer" }}
               onMouseEnter={() => setHoverWire(w.key)} onMouseLeave={() => setHoverWire(null)} onClick={() => setSel(w.key)}>
              <path d={w.d} fill="none" stroke="transparent" strokeWidth={14} />
              <path d={w.d} fill="none" stroke={w.warn ? "#D97706" : "#1B1E23"} strokeWidth={w.active ? 2.6 : 1.6}
                    strokeDasharray={w.warn ? "6 5" : undefined} opacity={sel && !w.active ? 0.25 : 1} />
            </g>
          ))}
        </svg>
        {wires.filter((w) => w.active).map((w) => (
          <Tooltip key={`x${w.key}`} title="Disconnect (set aside)">
            <Box component="button" aria-label="Disconnect" onClick={() => { setAside(w.key); setSel(null); }}
              onMouseEnter={() => setHoverWire(w.key)}
              sx={{ position: "absolute", left: w.mid.x - 11, top: w.mid.y - 11, width: 22, height: 22, borderRadius: 99, zIndex: 2,
                    border: `1px solid ${C.line}`, bgcolor: "#fff", display: "grid", placeItems: "center", cursor: "pointer", p: 0 }}>
              <CloseRounded sx={{ fontSize: 14 }} /></Box>
          </Tooltip>
        ))}

        {/* left: CAD layers */}
        <Box>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5, minHeight: 32 }}>
            <Typography sx={{ fontSize: 12.5, color: C.muted, flex: 1 }}>{tr("In the drawing")}</Typography>
            <TextField size="small" placeholder={tr("Filter layers")} value={q} onChange={(e) => setQ(e.target.value)} inputProps={{ "aria-label": tr("Filter layers") }}
              sx={{ width: 200, "& input": { py: 0.6, fontSize: 13 } }} />
          </Stack>
          {sels.length > 0 && (
            <Stack direction="row" alignItems="center" spacing={1} sx={{ position: "sticky", top: 8, zIndex: 4, mb: 1, px: 1.5, py: 0.75, borderRadius: 1.5, bgcolor: C.ink, color: "#fff" }}>
              <Typography sx={{ fontSize: 13, flex: 1 }}>{sels.length} selected — click a feature class to connect {sels.length > 1 ? "them" : "it"}</Typography>
              <Button size="small" onClick={asideSel} sx={{ color: "#fff", minWidth: 0 }}>{tr("Set aside")}</Button>
              <Button size="small" onClick={() => setSels([])} sx={{ color: "#C5CBD3", minWidth: 0 }}>{tr("Clear")}</Button>
            </Stack>
          )}
          {!sortedRows.length && <Typography variant="body2" sx={{ color: C.muted, py: 2 }}>No layer matches “{q}”</Typography>}
          <Stack spacing={1}>
            {sortedRows.map(({ g, m }) => {
              const off = !m || m.role === "ignore";
              const on = sels.includes(g.key);
              const blocks = g.blocks?.slice(0, 2).map((b) => b.name).join(" · ");
              return (
                <Box key={g.key} role="button" tabIndex={0} aria-pressed={on}
                  onClick={(e) => (off ? include(g.key) : pickLayer(g.key, e))}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); off ? include(g.key) : pickLayer(g.key, e); } }}
                  sx={{ position: "relative", display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 1, pr: 3.5, borderRadius: 1.5, cursor: "pointer",
                        bgcolor: on ? "#F2FAFB" : "#fff", opacity: off ? 0.5 : 1, userSelect: "none", "&:focus-visible": { outline: `2px solid ${C.cyan}`, outlineOffset: 2 },
                        border: `1px solid ${on ? C.cyan : off ? C.line : "#8A9099"}`, boxShadow: on ? `0 0 0 3px ${C.cyanSoft}` : "none" }}>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography sx={{ fontWeight: 600, fontSize: 13.5 }} noWrap>{g.layer}{g.style ? <Box component="span" sx={{ color: C.muted, fontWeight: 400 }}> · {g.style}</Box> : null}</Typography>
                    <Typography noWrap sx={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: C.muted }}>
                      {off ? `set aside${m?.layer_role && ASIDE[m.layer_role] ? " · " + ASIDE[m.layer_role] : ""} — click to include`
                        : `${blocks || Object.keys(g.entity_types).slice(0, 2).join(" · ")} · ${g.count.toLocaleString()}`}</Typography>
                  </Box>
                  <Typography sx={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, color: C.muted }}>{GEO[g.geometry]}</Typography>
                  <Box ref={(el: HTMLElement | null) => { ports.current[`L:${g.key}`] = el; }}
                    sx={{ position: "absolute", right: -7, top: "50%", mt: "-7px", width: 14, height: 14, borderRadius: 99, bgcolor: "#fff",
                          border: `2px solid ${m?.target && !off ? "#1B1E23" : "#C3C8CF"}`,
                          "&::after": m?.target && !off ? { content: '""', position: "absolute", inset: 2, borderRadius: 99, bgcolor: "#1B1E23" } : {} }} />
                </Box>
              );
            })}
          </Stack>
        </Box>
        <Box />

        {/* right: geodatabase → dataset → feature class */}
        <Box>
          <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
            <Typography sx={{ fontSize: 12.5, color: C.muted, flex: 1 }}>{tr("In the geodatabases")}</Typography>
            {sel && (
              <Autocomplete size="small" sx={{ width: 260 }} options={allFc} groupBy={(o) => `${o.g.name} — ${o.g.label}`}
                getOptionLabel={(o) => `${o.l.name} (${GEO[o.l.geometry_type]})`}
                onChange={(_, v) => v && connect(sel, v.l.name)}
                renderInput={(p) => <TextField {...p} placeholder={tr("Connect to any feature class…")} />} />
            )}
          </Stack>
          {!tree.length && <Typography variant="body2" sx={{ color: C.muted, py: 4 }}>{tr("Select a layer on the left to connect it.")}</Typography>}
          <Stack spacing={1.5}>
            {tree.map((gdb) => (
              <Box key={gdb.name} sx={{ border: `1px solid ${C.line}`, borderRadius: 2, p: 1.25, bgcolor: "#FBFBFC" }}>
                <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
                  <StorageOutlined sx={{ fontSize: 18, color: "#B4651B" }} />
                  <Typography sx={{ fontWeight: 700, fontSize: 13.5 }}>{gdb.name}</Typography>
                  <Typography variant="caption">{gdb.label}</Typography>
                </Stack>
                {Array.from(gdb.ds.entries()).map(([ds, fcs]) => (
                  <Box key={ds} sx={{ ml: 1, pl: 1.25, borderLeft: `1px dashed ${C.line}` }}>
                    <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mb: 0.75 }}>
                      <FolderOutlined sx={{ fontSize: 16, color: C.muted }} />
                      <Typography sx={{ fontSize: 12, fontWeight: 600, color: C.muted }}>{ds}</Typography>
                    </Stack>
                    <Stack spacing={0.75} sx={{ mb: 0.5 }}>
                      {fcs.map((l) => {
                        const feeders = connected.filter((g) => mapping.layers[g.key].target === l.name);
                        const sug = suggested.has(l.name);
                        const warn = feeders.some((g) => review(mapping.layers[g.key]));
                        return (
                          <Box key={l.name} role="button" tabIndex={0}
                            onClick={() => (sel ? connect(sel, l.name) : setSels(feeders.map((g) => g.key)))}
                            onKeyDown={(e) => { if (e.key === "Enter") (sel ? connect(sel, l.name) : setSels(feeders.map((g) => g.key))); }}
                            sx={{ position: "relative", display: "flex", alignItems: "center", gap: 1, px: 1.5, py: 0.85, pl: 2.5, borderRadius: 1.5,
                                  bgcolor: "#fff", cursor: "pointer", "&:focus-visible": { outline: `2px solid ${C.cyan}`, outlineOffset: 2 },
                                  border: sug ? `1px dashed ${C.cyan}` : warn ? "1px dashed #D97706" : "1px solid #8A9099",
                                  "&:hover": sel ? { boxShadow: `0 0 0 3px ${C.cyanSoft}` } : {} }}>
                            <Box ref={(el: HTMLElement | null) => { ports.current[`R:${l.name}`] = el; }}
                              sx={{ position: "absolute", left: -7, top: "50%", mt: "-7px", width: 14, height: 14, borderRadius: 99, bgcolor: "#fff",
                                    border: `2px solid ${feeders.length ? (warn ? "#D97706" : "#1B1E23") : sug ? C.cyan : "#C3C8CF"}`,
                                    "&::after": feeders.length ? { content: '""', position: "absolute", inset: 2, borderRadius: 99, bgcolor: warn ? "#D97706" : "#1B1E23" } : {} }} />
                            <Box sx={{ flex: 1, minWidth: 0 }}>
                              <Typography sx={{ fontWeight: 600, fontSize: 13.5 }} noWrap>{l.name}</Typography>
                              <Typography noWrap sx={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, color: C.muted }}>
                                {sug ? "suggested — click to connect" : feeders.map((g) => g.layer).join(" · ")}</Typography>
                            </Box>
                            <Typography sx={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 10.5, color: C.muted }}>{GEO[l.geometry_type]}</Typography>
                          </Box>
                        );
                      })}
                    </Stack>
                  </Box>
                ))}
              </Box>
            ))}
          </Stack>
        </Box>
      </Box>

      {/* footer */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: { xs: 2, md: 3 }, minHeight: 48, bgcolor: "#FAFBFC", borderTop: `1px solid ${C.line}`,
                                                                  flexWrap: "wrap", position: "sticky", bottom: 0, zIndex: 3 }}>
        <Typography variant="body2"><b>{connected.length} / {groups.length - aside}</b> {tr("connected")}</Typography>
        {toReview > 0 && <Chip size="small" label={`${toReview} to review`} sx={{ bgcolor: "#FEF3C7", color: "#92400E" }} />}
        {pending > 0 && <Chip size="small" label={`${pending} not connected`} sx={{ bgcolor: C.redSoft, color: C.red }} />}
        <Typography variant="caption" sx={{ flex: 1 }}>{tr("{a} set aside · {b} geodatabases", { a: aside, b: tree.length })}</Typography>
        <Typography variant="caption" sx={{ color: C.muted }}>{tr("Click a layer, then a feature class · Shift or Ctrl+click picks several · click a feature class to pick its layers · Esc clears")}</Typography>
      </Stack>
    </Box>
  );
}
