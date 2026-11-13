import { useEffect, useMemo, useRef, useState } from "react";
import { Autocomplete, Box, Button, IconButton, Stack, TextField, Tooltip, Typography } from "@mui/material";
import DeleteOutline from "@mui/icons-material/DeleteOutline";
import { api } from "../api";
import type { CrsInfo, CrsSuggestion, GeorefFit, GeorefPoint, JobState, Mapping, SchemaDetail, TransformOption } from "../types";
import { C } from "../theme";
import GeoPreview, { type GeoShape } from "./GeoPreview";

/*
 * Coordinate system of the drawing, in two parts:
 *   Define     — what system the drawing coordinates are in: detected, searched, read from a .prj / WKT, or
 *                georeferenced from control points (coordinate labels in the drawing, or points typed in)
 *   Transform  — how they reach the template's system: the datum transformation, and a point check
 * The drawing is shown on the satellite image throughout, so every choice can be judged where it lands.
 */

const SCALE_WORD: Record<string, string> = { "0.001": "millimetres", "0.01": "centimetres", "0.1": "decimetres", "0.3048": "feet", "0.3048006096012192": "US survey feet", "1": "metres" };
const fmtLL = (c?: number[]) => c ? `${Math.abs(c[1]).toFixed(5)}°${c[1] >= 0 ? "N" : "S"} ${Math.abs(c[0]).toFixed(5)}°${c[0] >= 0 ? "E" : "W"}` : "";
const fmtXY = (c?: number[] | null) => c ? `${c[0].toLocaleString(undefined, { maximumFractionDigits: 3 })}  ${c[1].toLocaleString(undefined, { maximumFractionDigits: 3 })}` : "—";
const CONF: Record<string, [string, string]> = {
  high: ["#16794C", "Read from the drawing"], medium: ["#16794C", "Detected automatically"],
  low: ["#9A5B0F", "Best guess — confirm on the satellite image"], none: ["#C0392B", "Define it below — Detected, Search, .prj or Control points"],
};

interface Props {
  job: JobState; mapping: Mapping; schema: SchemaDetail | null;
  onPatch: (p: unknown) => Promise<void>;
}

type Mode = "detected" | "search" | "prj" | "points";

function Seg<T extends string>({ value, options, onChange }: { value: T; options: [T, string][]; onChange: (v: T) => void }) {
  return (
    <Box role="tablist" sx={{ display: "inline-flex", p: "3px", borderRadius: 2, bgcolor: "#ECEAE6", gap: "2px", flexWrap: "wrap" }}>
      {options.map(([k, l]) => (
        <Box key={k} role="tab" aria-selected={value === k} tabIndex={0} onClick={() => onChange(k)}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onChange(k)}
          sx={{ px: 1.5, height: 30, display: "flex", alignItems: "center", borderRadius: 1.5, cursor: "pointer", fontSize: 13, whiteSpace: "nowrap",
            fontWeight: value === k ? 500 : 400, color: value === k ? C.ink : C.muted, bgcolor: value === k ? "#FBFAF8" : "transparent",
            boxShadow: value === k ? "0 1px 2px rgba(22,23,26,.08)" : "none", transition: "background-color .15s, color .15s",
            "&:focus-visible": { outline: `2px solid ${C.ink}`, outlineOffset: 1 } }}>{l}</Box>
      ))}
    </Box>
  );
}

const Label = ({ children }: { children: React.ReactNode }) =>
  <Typography sx={{ fontSize: 12.5, color: C.muted, mb: 0.75 }}>{children}</Typography>;

function CrsSearch({ onPick, placeholder }: { onPick: (c: CrsInfo) => void; placeholder?: string }) {
  const [opts, setOpts] = useState<CrsInfo[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => { const t = setTimeout(() => { api.crs(q).then(setOpts).catch(() => undefined); }, 250); return () => clearTimeout(t); }, [q]);
  return (
    <Autocomplete size="small" options={opts} filterOptions={(x) => x} value={null} blurOnSelect
      getOptionLabel={(o) => `EPSG:${o.epsg} — ${o.name}`} isOptionEqualToValue={(a, b) => a.epsg === b.epsg}
      onInputChange={(_, v, r) => r === "input" && setQ(v.replace(/^EPSG:/i, ""))}
      onChange={(_, v) => v && onPick(v)}
      renderInput={(params) => <TextField {...params} placeholder={placeholder || "EPSG code or name — e.g. 32640, Dubai, Nahrwan"} />} />
  );
}

export function CoordinatePanel({ job, mapping: m, schema, onPatch }: Props) {
  const tgt = schema?.spatial_reference.epsg || null;
  const [part, setPart] = useState<"define" | "transform">("define");
  const [mode, setMode] = useState<Mode>(m.georef ? "points" : "detected");
  const [sug, setSug] = useState<CrsSuggestion>(m.crs_suggestion);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "warn" | "err"; text: string } | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const [lines, setLines] = useState<GeoShape[]>([]);
  const [srcInfo, setSrcInfo] = useState<CrsInfo | null>(null);

  const detect = async (apply: boolean) => {
    setBusy("detect");
    try {
      const r = await api.crsDetect(job.job_id);
      setSug(r);
      if (apply && r.suggested) await onPatch({ source_crs: r.suggested, unit_scale: r.suggested_scale || 1 });
    } catch { /* keep the stored suggestion */ } finally { setBusy(""); }
  };
  useEffect(() => { if (m.crs_suggestion?.v !== 2) detect(!m.source_crs); /* eslint-disable-next-line */ }, [job.job_id]);
  useEffect(() => {
    if (!m.source_crs) { setSrcInfo(null); return; }
    api.crs(String(m.source_crs)).then((r) => setSrcInfo(r.find((c) => c.epsg === m.source_crs) || null)).catch(() => undefined);
  }, [m.source_crs]);

  const cands = sug.candidates.filter((c) => c.center);
  const shown = mode === "detected" ? (cands.find((c) => c.epsg === hover) || null) : null;
  // map: a hovered candidate previews that system; otherwise the job as it is set (with georeferencing)
  const viewEpsg = shown?.epsg ?? m.source_crs;
  const preview = shown ? { epsg: shown.epsg, scale: shown.scale || 1 } : null;
  useEffect(() => {
    if (!viewEpsg) { setLines([]); return; }
    let live = true;
    const call = preview ? api.cadPreview(job.job_id, { ...preview, limit: 3000 }) : api.cadPreview(job.job_id);
    call.then((g) => {
      if (!live || !g.crs_is_geographic) return;
      const feats = preview ? g.features : g.features.filter((_: any, i: number) => i % Math.max(1, Math.floor(g.features.length / 3000)) === 0);
      setLines(feats.flatMap((f: any): GeoShape[] => {
        const t = f.geometry?.type, c = f.geometry?.coordinates;
        if (t === "LineString") return [{ c, k: "line", color: "#FDE047", width: 1.25, halo: false }];
        if (t === "Polygon") return [{ c: c[0], k: "line", color: "#FDE047", width: 1.25, halo: false }];
        return [];
      }));
    }).catch(() => live && setLines([]));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, viewEpsg, preview?.scale, m.unit_scale, JSON.stringify(m.georef || null), m.transform_op]);
  const fitArea = useMemo(() => {
    if (shown?.footprint) return shown.footprint;
    const pts = lines.flatMap((g) => g.c);
    if (pts.length < 2) return undefined;
    const xs = pts.map((q) => q[0]).sort((a, b) => a - b), ys = pts.map((q) => q[1]).sort((a, b) => a - b);
    const k = pts.length < 200 ? 0 : Math.floor(pts.length * 0.02), n = pts.length - 1 - k;
    return [[xs[k], ys[k]], [xs[n], ys[n]]];
  }, [shown, lines]);
  const near = (g: GeoShape) => {
    if (!fitArea) return true;
    const xs = fitArea.map((q) => q[0]), ys = fitArea.map((q) => q[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const mx = (x1 - x0) * 2 + 0.002, my = (y1 - y0) * 2 + 0.002;
    return g.c.every((q) => q[0] > x0 - mx && q[0] < x1 + mx && q[1] > y0 - my && q[1] < y1 + my);
  };
  const shapes: GeoShape[] = [
    ...(shown?.footprint ? [{ c: [...shown.footprint, shown.footprint[0]], k: "polygon", color: "#F97316", width: 2, dash: [6, 4] } as GeoShape] : []),
    ...lines.filter(near),
  ];

  const inList = cands.some((c) => c.epsg === m.source_crs);
  const [dot, label] = !m.source_crs ? CONF.none : m.georef ? ["#16794C", "Georeferenced from control points"]
    : inList ? (CONF[sug.confidence] || CONF.low) : ["#9A5B0F", "Defined by hand — check it on the satellite image"];
  const name = m.source_crs ? `EPSG:${m.source_crs}${srcInfo ? ` — ${srcInfo.name}` : ""}` : "Not defined";
  const say = (tone: "ok" | "warn" | "err", text: string) => setMsg({ tone, text });

  return (
    <Box>
      {/* status line: what the drawing is in, and where it goes */}
      <Stack direction="row" spacing={1} alignItems="center" sx={{ minHeight: 32 }} flexWrap="wrap" useFlexGap>
        <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: dot }} />
        <Typography sx={{ fontSize: 13.5, fontWeight: 500 }}>{name}</Typography>
        <Typography sx={{ fontSize: 12.5, color: C.muted }}>{label}</Typography>
        {tgt && <Typography sx={{ fontSize: 12.5, color: C.muted }}>→ delivered in EPSG:{tgt}</Typography>}
        <Box sx={{ flex: 1 }} />
        <Seg value={part} onChange={(v) => { setPart(v); setMsg(null); }} options={[["define", "Define projection"], ["transform", "Transform"]]} />
      </Stack>

      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(400px, 520px) 1fr" }, gap: 3, mt: 2 }}>
        <Box sx={{ minWidth: 0 }}>
          {part === "define" ? (
            <>
              <Seg value={mode} onChange={(v) => { setMode(v); setMsg(null); }}
                options={[["detected", "Detected"], ["search", "Search"], ["prj", ".prj / WKT"], ["points", "Control points"]]} />
              <Box sx={{ mt: 2 }}>
                {mode === "detected" && <Detected cands={cands} m={m} sug={sug} busy={busy === "detect"} onDetect={() => detect(true)}
                  onHover={setHover} onPick={(c) => onPatch({ source_crs: c.epsg, unit_scale: c.scale || 1 })} />}
                {mode === "search" && <SearchDefine m={m} onPatch={onPatch} />}
                {mode === "prj" && <PrjDefine onPatch={onPatch} say={say} />}
                {mode === "points" && <ControlPoints job={job} m={m} onPatch={onPatch} say={say} sug={sug} />}
              </Box>
            </>
          ) : (
            <TransformPart job={job} m={m} tgt={tgt} onPatch={onPatch} />
          )}
          {msg && <Typography sx={{ mt: 1.5, fontSize: 12.5, color: msg.tone === "ok" ? C.green : msg.tone === "warn" ? C.amber : C.red }}>{msg.text}</Typography>}
        </Box>

        <Box sx={{ minWidth: 0 }}>
          {viewEpsg ? <GeoPreview shapes={shapes} height={440} minSpan={120} fit={fitArea} fitKey={`${viewEpsg}:${preview?.scale}:${lines.length}:${JSON.stringify(m.georef?.offset || null)}`} />
            : <Box sx={{ height: 440, borderRadius: 1.5, bgcolor: "#F4F3F0", display: "grid", placeItems: "center", color: C.muted, fontSize: 13, px: 4, textAlign: "center" }}>
                The drawing appears on the satellite image once its projection is defined</Box>}
          {viewEpsg && <Stack direction="row" spacing={2} sx={{ mt: 0.75, fontSize: 12.5, color: C.muted }}>
            <Stack direction="row" spacing={0.75} alignItems="center"><Box sx={{ width: 14, height: 2, bgcolor: "#CA8A04" }} /><span>Drawing</span></Stack>
            {shown && <Stack direction="row" spacing={0.75} alignItems="center"><Box sx={{ width: 14, borderTop: "2px dashed #F97316" }} /><span>Extent · {shown.size_m! >= 1000 ? `${(shown.size_m! / 1000).toFixed(1)} km` : `${Math.round(shown.size_m!)} m`}</span></Stack>}
            {shown && shown.epsg !== m.source_crs && <span>Previewing EPSG:{shown.epsg}</span>}
          </Stack>}
        </Box>
      </Box>
    </Box>
  );
}

/* ---------------- Define: detected candidates */
function Detected({ cands, m, sug, busy, onDetect, onHover, onPick }: {
  cands: CrsSuggestion["candidates"]; m: Mapping; sug: CrsSuggestion; busy: boolean;
  onDetect: () => void; onHover: (e: number | null) => void; onPick: (c: { epsg: number; scale?: number }) => void;
}) {
  return (
    <Box onMouseLeave={() => onHover(null)}>
      {sug.reasons.length > 0 && <Typography sx={{ fontSize: 12.5, color: C.muted, mb: 1.5 }}>{sug.reasons.join(" · ")}</Typography>}
      {cands.length === 0 && <Typography sx={{ fontSize: 13, color: C.ink, mb: 1 }}>
        No system places these coordinates on the map — the drawing uses a local grid. Use Control points to tie it to real coordinates, or
        Search / .prj if you know its system.</Typography>}
      {cands.map((c, i) => {
        const on = !m.georef && c.epsg === m.source_crs && Math.abs((c.scale || 1) - m.unit_scale) < 1e-9;
        return (
          <Box key={c.epsg} role="radio" aria-checked={on} tabIndex={0} onMouseEnter={() => onHover(c.epsg)} onFocus={() => onHover(c.epsg)}
            onClick={() => onPick(c)} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPick(c)}
            sx={{ display: "grid", gridTemplateColumns: "18px 1fr", gap: 1.25, px: 1.5, py: 1.25, mb: 0.5, borderRadius: 1.5, cursor: "pointer",
              border: `1px solid ${on ? C.ink : "transparent"}`, bgcolor: on ? "#FBFAF8" : "transparent", transition: "background-color .15s",
              "&:hover": { bgcolor: on ? "#FBFAF8" : "#F2F1EE" }, "&:focus-visible": { outline: `2px solid ${C.ink}`, outlineOffset: 2 } }}>
            <Box sx={{ mt: "3px", width: 14, height: 14, borderRadius: "50%", border: `1.5px solid ${on ? C.ink : "#A3A19B"}`, display: "grid", placeItems: "center" }}>
              {on && <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: C.ink }} />}
            </Box>
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" spacing={1} alignItems="baseline">
                <Typography sx={{ fontSize: 13.5, fontWeight: 500 }} noWrap>{c.name}</Typography>
                {i === 0 && <Typography sx={{ fontSize: 12, color: C.green, flexShrink: 0 }}>Best match</Typography>}
              </Stack>
              <Typography sx={{ fontSize: 12.5, color: C.muted, fontVariantNumeric: "tabular-nums" }}>
                EPSG:{c.epsg} · {fmtLL(c.center)}{c.region ? ` · ${c.region}` : ""}</Typography>
              {(c.scale || 1) !== 1 && <Typography sx={{ fontSize: 12.5, color: C.muted }}>Coordinates in {SCALE_WORD[String(c.scale)] || "drawing units"} · ×{c.scale}</Typography>}
            </Box>
          </Box>
        );
      })}
      <Button size="small" disabled={busy} onClick={onDetect} sx={{ mt: 1, height: 32 }}>{busy ? "Detecting…" : "Detect again"}</Button>
    </Box>
  );
}

/* ---------------- Define: search the EPSG register */
function SearchDefine({ m, onPatch }: { m: Mapping; onPatch: (p: unknown) => Promise<void> }) {
  return (
    <Stack spacing={2}>
      <Box>
        <Label>The drawing's coordinate system</Label>
        <CrsSearch onPick={(c) => onPatch({ source_crs: c.epsg })} />
      </Box>
      <Stack direction="row" spacing={1.5} alignItems="flex-end">
        <Box>
          <Label>Drawing units to metres</Label>
          <TextField size="small" type="number" value={m.georef ? m.georef.scale : m.unit_scale} disabled={!!m.georef} sx={{ width: 140 }}
            inputProps={{ step: "any", min: 0 }} onChange={(e) => Number(e.target.value) > 0 && onPatch({ unit_scale: Number(e.target.value) })} />
        </Box>
        <Stack direction="row" spacing={0.5} sx={{ pb: 0.25 }}>
          {[["m", 1], ["mm", 0.001], ["cm", 0.01], ["ft", 0.3048]].map(([l, v]) => (
            <Button key={l as string} size="small" disabled={!!m.georef} onClick={() => onPatch({ unit_scale: v })}
              sx={{ minWidth: 40, height: 32, color: m.unit_scale === v ? C.ink : C.muted, bgcolor: m.unit_scale === v ? "#E4E2DD" : "transparent" }}>{l as string}</Button>
          ))}
        </Stack>
      </Stack>
      <Typography sx={{ fontSize: 12.5, color: C.muted }}>Drawing says: {m.drawing_units}</Typography>
      {m.source_crs && <Button size="small" sx={{ alignSelf: "start", color: C.muted }} onClick={() => onPatch({ source_crs: null, georef: null })}>
        Clear — coordinates are written as they are</Button>}
    </Stack>
  );
}

/* ---------------- Define: a .prj file or a WKT / PROJ definition */
function PrjDefine({ onPatch, say }: { onPatch: (p: unknown) => Promise<void>; say: (t: "ok" | "warn" | "err", s: string) => void }) {
  const [text, setText] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const apply = async (t: string) => {
    try {
      const c = await api.crsParse(t);
      await onPatch({ source_crs: c.epsg });
      say("ok", `Defined as EPSG:${c.epsg} — ${c.name}`);
    } catch (e: any) { say("err", e?.message || "Could not read this definition"); }
  };
  return (
    <Stack spacing={1.5}>
      <Label>Load the .prj that came with the drawing, or paste a WKT / PROJ definition</Label>
      <input ref={file} type="file" accept=".prj,.txt,.wkt" hidden onChange={async (e) => {
        const f = e.target.files?.[0]; if (!f) return; const t = await f.text(); setText(t); apply(t); e.target.value = "";
      }} />
      <Button variant="outlined" sx={{ alignSelf: "start", height: 36 }} onClick={() => file.current?.click()}>Choose .prj file</Button>
      <TextField multiline minRows={5} maxRows={10} size="small" value={text} onChange={(e) => setText(e.target.value)}
        placeholder={'PROJCS["WGS_1984_UTM_Zone_40N", …]  or  +proj=utm +zone=40 +datum=WGS84'}
        InputProps={{ sx: { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 } }} />
      <Button variant="contained" disabled={!text.trim()} onClick={() => apply(text)} sx={{ alignSelf: "start", height: 36 }}>Define projection</Button>
    </Stack>
  );
}

/* ---------------- Define: georeference from control points */
function ControlPoints({ job, m, onPatch, say, sug }: {
  job: JobState; m: Mapping; onPatch: (p: unknown) => Promise<void>; say: (t: "ok" | "warn" | "err", s: string) => void; sug: CrsSuggestion;
}) {
  const [pts, setPts] = useState<GeorefPoint[]>(m.georef?.points || []);
  const [fit, setFit] = useState<GeorefFit | null>(m.georef || null);
  const [crs, setCrs] = useState<CrsInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const realEpsg = crs?.epsg || m.source_crs || sug.suggested || null;
  useEffect(() => {
    if (pts.length < 1 || pts.some((p) => [p.x, p.y, p.E, p.N].some((v) => !isFinite(v)))) { setFit(null); return; }
    const t = setTimeout(() => api.georefFit(pts, pts.length === 1 ? m.unit_scale : undefined).then(setFit).catch(() => setFit(null)), 300);
    return () => clearTimeout(t);
  }, [JSON.stringify(pts)]);
  const fromLabels = async () => {
    setBusy(true);
    try {
      const r = await api.georefLabels(job.job_id);
      if (!r.points.length) { say("warn", r.message); return; }
      setPts(r.points);
      const best = r.crs?.candidates?.[0];
      if (best && !m.source_crs) setCrs(best);
      say(r.already_real ? "ok" : "ok", `${r.found} coordinate labels found — ${r.message}`);
    } catch (e: any) { say("err", e?.message || "Could not read the labels"); } finally { setBusy(false); }
  };
  const set = (i: number, k: keyof GeorefPoint, v: string) => setPts(pts.map((p, j) => j === i ? { ...p, [k]: v === "" ? NaN : Number(v) } : p));
  const apply = async () => {
    if (!fit || !realEpsg) return;
    const identity = Math.abs(fit.rotation) < 1e-6 && Math.abs(fit.offset[0]) < 0.05 && Math.abs(fit.offset[1]) < 0.05;
    await onPatch(identity ? { source_crs: realEpsg, unit_scale: fit.scale, georef: null }
      : { source_crs: realEpsg, georef: { ...fit, points: pts } });
    say("ok", identity ? `The drawing is already in real coordinates — defined as EPSG:${realEpsg}` : `Georeferenced and defined as EPSG:${realEpsg}`);
  };
  const cell = { size: "small" as const, type: "number", inputProps: { step: "any", style: { fontVariantNumeric: "tabular-nums", fontSize: 12.5, padding: "6px 8px" } } };
  return (
    <Stack spacing={2}>
      <Typography sx={{ fontSize: 13, color: C.ink }}>
        Tie drawing points to their real coordinates. Two points or more fix shift, rotation and scale; one point fixes the shift.</Typography>
      <Button variant="outlined" disabled={busy} onClick={fromLabels} sx={{ alignSelf: "start", height: 36 }}>
        {busy ? "Reading labels…" : "Use coordinate labels in the drawing"}</Button>

      <Box>
        <Box sx={{ display: "grid", gridTemplateColumns: "0.8fr 0.8fr 1.25fr 1.25fr 52px 28px", gap: 0.75, alignItems: "center", fontSize: 12, color: C.muted, mb: 0.5 }}>
          <span>Drawing X</span><span>Drawing Y</span><span>Real E</span><span>Real N</span><span style={{ textAlign: "right" }}>Error</span><span />
        </Box>
        {pts.map((p, i) => (
          <Tooltip key={i} title={p.text || ""} placement="left" disableHoverListener={!p.text}>
            <Box sx={{ display: "grid", gridTemplateColumns: "0.8fr 0.8fr 1.25fr 1.25fr 52px 28px", gap: 0.75, alignItems: "center", mb: 0.75 }}>
              {(["x", "y", "E", "N"] as const).map((k) => <TextField key={k} {...cell} value={isFinite(p[k]) ? p[k] : ""} onChange={(e) => set(i, k, e.target.value)} />)}
              <Typography sx={{ fontSize: 12, textAlign: "right", fontVariantNumeric: "tabular-nums",
                color: fit?.residuals?.[i] == null ? C.muted : fit.residuals[i] > 0.5 ? C.red : fit.residuals[i] > 0.05 ? C.amber : C.green }}>
                {fit?.residuals?.[i] != null ? `${fit.residuals[i] < 1 ? (fit.residuals[i] * 1000).toFixed(0) + " mm" : fit.residuals[i].toFixed(1) + " m"}` : ""}</Typography>
              <IconButton size="small" aria-label="Remove point" onClick={() => setPts(pts.filter((_, j) => j !== i))} sx={{ color: C.muted }}><DeleteOutline sx={{ fontSize: 17 }} /></IconButton>
            </Box>
          </Tooltip>
        ))}
        <Button size="small" onClick={() => setPts([...pts, { x: NaN, y: NaN, E: NaN, N: NaN }])} sx={{ height: 30, color: C.muted }}>Add point</Button>
      </Box>

      {fit && (
        <Box sx={{ display: "grid", gridTemplateColumns: "repeat(4, auto)", justifyContent: "start", columnGap: 3, rowGap: 0.25, fontSize: 12.5 }}>
          {[["Scale", fit.scale.toFixed(6)], ["Rotation", `${fit.rotation.toFixed(4)}°`], ["Shift", fmtXY(fit.offset)], ["RMS", fit.rms == null ? "—" : `${fit.rms < 1 ? (fit.rms * 1000).toFixed(0) + " mm" : fit.rms.toFixed(2) + " m"}`]].map(([k, v]) => (
            <Box key={k}><Box sx={{ color: C.muted }}>{k}</Box><Box sx={{ fontVariantNumeric: "tabular-nums" }}>{v}</Box></Box>
          ))}
        </Box>
      )}

      <Box>
        <Label>Real coordinates are in</Label>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography sx={{ fontSize: 13.5, fontWeight: 500, minWidth: 0 }} noWrap>{crs ? `EPSG:${crs.epsg} — ${crs.name}` : realEpsg ? `EPSG:${realEpsg}` : "Choose the system"}</Typography>
        </Stack>
        <Box sx={{ mt: 1 }}><CrsSearch onPick={setCrs} placeholder="Change — EPSG code or name" /></Box>
      </Box>
      <Stack direction="row" spacing={1}>
        <Button variant="contained" disabled={!fit || !realEpsg} onClick={apply} sx={{ height: 36 }}>Apply georeferencing</Button>
        {m.georef && <Button onClick={() => { onPatch({ georef: null, unit_scale: 1 }); setPts([]); }} sx={{ height: 36, color: C.muted }}>Remove</Button>}
      </Stack>
    </Stack>
  );
}

/* ---------------- Transform: source -> template system, datum transformation, point check */
function TransformPart({ job, m, tgt, onPatch }: { job: JobState; m: Mapping; tgt: number | null; onPatch: (p: unknown) => Promise<void> }) {
  const [ops, setOps] = useState<TransformOption[] | null>(null);
  const [x, setX] = useState(""); const [y, setY] = useState("");
  const [chk, setChk] = useState<Awaited<ReturnType<typeof api.crsCheck>> | null>(null);
  useEffect(() => {
    setOps(null);
    if (m.source_crs && tgt && m.source_crs !== tgt) api.crsTransforms(m.source_crs, tgt).then(setOps).catch(() => setOps([]));
  }, [m.source_crs, tgt]);
  if (!m.source_crs) return <Typography sx={{ fontSize: 13 }}>Define the drawing's projection first — then its coordinates are transformed into the template's system.</Typography>;
  const same = m.source_crs === tgt;
  const chosen = m.transform_op || ops?.find((o) => o.best)?.id || null;
  return (
    <Stack spacing={2.5}>
      <Box sx={{ display: "grid", gridTemplateColumns: "1fr 24px 1fr", alignItems: "center", gap: 1 }}>
        <Box><Label>From the drawing</Label><Typography sx={{ fontSize: 13.5, fontWeight: 500 }}>EPSG:{m.source_crs}</Typography></Box>
        <Box sx={{ color: C.muted, textAlign: "center", pt: 2.5 }}>→</Box>
        <Box><Label>To the template (kept as published)</Label><Typography sx={{ fontSize: 13.5, fontWeight: 500 }}>EPSG:{tgt ?? "—"}</Typography></Box>
      </Box>
      {same ? <Typography sx={{ fontSize: 13, color: C.muted }}>Same system — coordinates are written without a transformation.</Typography> : (
        <Box>
          <Label>Datum transformation</Label>
          {!ops && <Typography sx={{ fontSize: 13, color: C.muted }}>Loading…</Typography>}
          {ops && ops.length === 0 && <Typography sx={{ fontSize: 13, color: C.muted }}>A direct conversion — no datum change is needed.</Typography>}
          {ops?.map((o, i) => {
            const on = (chosen && o.id === chosen) || (!chosen && i === 0);
            return (
              <Box key={i} role="radio" aria-checked={!!on} aria-disabled={!o.available} tabIndex={o.available ? 0 : -1}
                onClick={() => o.available && onPatch({ transform_op: o.best ? null : o.id })}
                sx={{ display: "grid", gridTemplateColumns: "18px 1fr auto", gap: 1.25, px: 1.5, py: 1, mb: 0.5, borderRadius: 1.5,
                  cursor: o.available ? "pointer" : "default", opacity: o.available ? 1 : 0.55,
                  border: `1px solid ${on ? C.ink : "transparent"}`, "&:hover": { bgcolor: o.available && !on ? "#F2F1EE" : undefined } }}>
                <Box sx={{ mt: "3px", width: 14, height: 14, borderRadius: "50%", border: `1.5px solid ${on ? C.ink : "#A3A19B"}`, display: "grid", placeItems: "center" }}>
                  {on && <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: C.ink }} />}
                </Box>
                <Typography sx={{ fontSize: 13, minWidth: 0 }}>{o.name}{o.best && <Box component="span" sx={{ color: C.green, ml: 1, fontSize: 12 }}>Recommended</Box>}
                  {!o.available && <Box component="span" sx={{ color: C.muted, ml: 1, fontSize: 12 }}>needs a grid file</Box>}</Typography>
                <Typography sx={{ fontSize: 12.5, color: C.muted, fontVariantNumeric: "tabular-nums" }}>{o.accuracy != null ? `± ${o.accuracy} m` : "accuracy unknown"}</Typography>
              </Box>
            );
          })}
        </Box>
      )}
      <Box>
        <Label>Check a point — drawing X / Y</Label>
        <Stack direction="row" spacing={1}>
          <TextField size="small" type="number" placeholder="X" value={x} onChange={(e) => setX(e.target.value)} sx={{ flex: 1 }} />
          <TextField size="small" type="number" placeholder="Y" value={y} onChange={(e) => setY(e.target.value)} sx={{ flex: 1 }} />
          <Button variant="outlined" disabled={x === "" || y === ""} onClick={() => api.crsCheck(job.job_id, Number(x), Number(y)).then(setChk).catch(() => setChk(null))} sx={{ height: 40 }}>Check</Button>
        </Stack>
        {chk && (
          <Box sx={{ mt: 1.5, display: "grid", gridTemplateColumns: "auto 1fr", columnGap: 2, rowGap: 0.5, fontSize: 12.5, fontVariantNumeric: "tabular-nums" }}>
            <Box sx={{ color: C.muted }}>EPSG:{chk.source_epsg}</Box><Box>{fmtXY(chk.source)}</Box>
            {chk.target && <><Box sx={{ color: C.muted }}>EPSG:{chk.target_epsg}</Box><Box>{fmtXY(chk.target)}</Box></>}
            {chk.lonlat && <><Box sx={{ color: C.muted }}>Lat / lon</Box><Box>{fmtLL(chk.lonlat)}</Box></>}
          </Box>
        )}
      </Box>
    </Stack>
  );
}
