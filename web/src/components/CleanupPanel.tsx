import { useEffect, useMemo, useState } from "react";
import {
  Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Stack, Tab, Tabs, TextField, Tooltip, Typography,
} from "@mui/material";
import CheckCircle from "@mui/icons-material/CheckCircle";
import { api } from "../api";
import type { JobState } from "../types";
import { C } from "../theme";
import GeoPreview, { type GeoShape } from "./GeoPreview";

interface Proposal {
  id: string; kind: string; label: string; action: string; layer: string; layers: string[]; detail: string;
  handles: string[]; keep: string | null; decision: string; to?: [number, number]; to_ll?: [number, number];
  geom: { h: string; k: string; c: number[][]; ll?: number[][]; role: "change" | "keep" }[];
}
interface Cleanup {
  settings: { tolerance: number; review_tolerance: number; gap_tolerance: number };
  auto: { action: string; count: number; layers: Record<string, number>; note?: string }[];
  reviewed: { action: string; count: number }[];
  summary: Record<string, { label: string; total: number; approved: number; rejected: number }>;
  proposals: Proposal[];
}
const ACTION: Record<string, string> = { remove: "Remove", snap: "Snap end", close: "Close as area", repair: "Repair", review: "Check only" };

/** Step 1 — automatic clean-up (already done, safe) and advanced clean-up (proposed, applied after review). */
export function CleanupPanel({ job, onApplied, inline }: { job: JobState; onApplied: (j: JobState) => void; inline?: boolean }) {
  const [cl, setCl] = useState<Cleanup | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => { api.cleanup(job.job_id).then(setCl).catch(() => setCl(null)); }, [job.job_id, job.status]);
  if (!cl) return <Typography variant="caption">Checking the drawing…</Typography>;
  const autoTotal = cl.auto.reduce((t, a) => t + a.count, 0);
  const pending = cl.proposals.filter((p) => p.decision === "pending").length;
  const applied = cl.reviewed.reduce((t, a) => t + a.count, 0);
  if (inline) {
    const dialog = open && <ReviewDialog job={job} cl={cl} onClose={() => setOpen(false)} onApplied={(j) => { setOpen(false); onApplied(j); }} />;
    return (
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip size="small" label={pending ? "review" : "clean"} sx={{ height: 22, fontWeight: 600, bgcolor: pending ? C.amberSoft : C.greenSoft, color: pending ? C.amber : C.green }} />
        <Typography variant="body2">
          {autoTotal ? `${autoTotal.toLocaleString()} fixed automatically` : "No duplicates or zero-length objects"}
          {pending ? <> · <b>{pending} to review</b></> : applied ? ` · ${applied} applied` : ""}
        </Typography>
        {cl.auto.length > 0 && (
          <Tooltip title={cl.auto.map((a) => `${a.action} · ${a.count}`).join("\n")}><Typography variant="caption" sx={{ cursor: "help", textDecoration: "underline dotted" }}>details</Typography></Tooltip>
        )}
        <Box sx={{ flex: 1 }} />
        {cl.proposals.length > 0 && <Button size="small" variant={pending ? "contained" : "text"} onClick={() => setOpen(true)}>Review</Button>}
        {dialog}
      </Stack>
    );
  }
  return (
    <Stack spacing={1.25}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip size="small" icon={<CheckCircle sx={{ fontSize: 16 }} />} label="Automatic" sx={{ bgcolor: C.greenSoft, color: C.green, fontWeight: 600, "& .MuiChip-icon": { color: C.green } }} />
        {autoTotal === 0 && <Typography variant="body2">No duplicates or zero-length objects found.</Typography>}
        {cl.auto.map((a) => (
          <Tooltip key={a.action} title={`${a.note || ""} — ${Object.entries(a.layers).map(([l, n]) => `${l} (${n})`).join(", ")}`}>
            <Chip size="small" variant="outlined" label={`${a.action.replace(/^Removed /, "").replace(/ \(OVERKILL\)| \(PEDIT > Join\)/, "")} · ${a.count}`} />
          </Tooltip>
        ))}
      </Stack>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip size="small" label="Advanced" sx={{ bgcolor: pending ? "#FEF3C7" : "#EEF0F3", color: pending ? "#92400E" : C.muted, fontWeight: 600 }} />
        <Typography variant="body2">
          {cl.proposals.length === 0 ? "Nothing that needs a decision."
            : <>{pending ? <b>{pending} change{pending === 1 ? "" : "s"} to review</b> : "All reviewed"}{applied ? ` · ${applied} applied` : ""}</>}
        </Typography>
        {cl.proposals.length > 0 && <Button size="small" variant={pending ? "contained" : "outlined"} onClick={() => setOpen(true)}>Review</Button>}
      </Stack>
      {open && <ReviewDialog job={job} cl={cl} onClose={() => setOpen(false)} onApplied={(j) => { setOpen(false); onApplied(j); }} />}
    </Stack>
  );
}

function ReviewDialog({ job, cl, onClose, onApplied }: { job: JobState; cl: Cleanup; onClose: () => void; onApplied: (j: JobState) => void }) {
  const kinds = Object.keys(cl.summary);
  const [tab, setTab] = useState(kinds[0]);
  const [dec, setDec] = useState<Record<string, string>>(() => Object.fromEntries(cl.proposals.map((p) => [p.id, p.decision])));
  const [sel, setSel] = useState<string | null>(null);
  const [tol, setTol] = useState({ review_tolerance: cl.settings.review_tolerance, gap_tolerance: cl.settings.gap_tolerance });
  const [busy, setBusy] = useState(false);
  const list = cl.proposals.filter((p) => p.kind === tab);
  const cur = cl.proposals.find((p) => p.id === sel) || list[0];
  const approvedN = Object.values(dec).filter((v) => v === "approved").length;
  const changed = cl.proposals.some((p) => dec[p.id] !== p.decision)
    || tol.review_tolerance !== cl.settings.review_tolerance || tol.gap_tolerance !== cl.settings.gap_tolerance;
  const setAll = (v: string) => setDec({ ...dec, ...Object.fromEntries(list.filter((p) => p.action !== "review").map((p) => [p.id, v])) });
  const apply = async () => {
    setBusy(true);
    try {
      const changedDec = Object.fromEntries(Object.entries(dec).filter(([k, v]) => cl.proposals.find((p) => p.id === k)?.decision !== v));
      onApplied(await api.setCleanup(job.job_id, { decisions: changedDec, settings: tol, apply: true }));
    } finally { setBusy(false); }
  };
  return (
    <Dialog open fullWidth maxWidth="lg" onClose={onClose}>
      <DialogTitle sx={{ pb: 0 }}>Advanced clean-up — review before applying</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ color: C.muted, mb: 1 }}>Nothing here changes the drawing until you approve it.</Typography>
        <Tabs value={tab} onChange={(_, v) => { setTab(v); setSel(null); }} variant="scrollable" sx={{ borderBottom: `1px solid ${C.line}` }}>
          {kinds.map((k) => <Tab key={k} value={k} label={`${cl.summary[k].label} (${cl.summary[k].total})`} sx={{ textTransform: "none" }} />)}
        </Tabs>
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1.1fr 1fr" }, gap: 2, mt: 1.5 }}>
          <Box>
            <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
              <Button size="small" onClick={() => setAll("approved")}>Approve all</Button>
              <Button size="small" onClick={() => setAll("rejected")}>Reject all</Button>
            </Stack>
            <Box sx={{ maxHeight: 420, overflow: "auto", border: `1px solid ${C.line}`, borderRadius: 1 }}>
              {list.map((p) => (
                <Stack key={p.id} direction="row" spacing={1} alignItems="center" onClick={() => setSel(p.id)}
                  sx={{ px: 1, py: 0.75, borderBottom: `1px solid ${C.line}`, cursor: "pointer", bgcolor: cur?.id === p.id ? C.cyanSoft : "transparent" }}>
                  <Checkbox size="small" disabled={p.action === "review"} checked={dec[p.id] === "approved"}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => setDec({ ...dec, [p.id]: e.target.checked ? "approved" : "rejected" })} />
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    <Typography sx={{ fontSize: 13, fontWeight: 600 }} noWrap>{ACTION[p.action]} · {p.layer}</Typography>
                    <Typography variant="caption" component="div" noWrap>{p.detail}</Typography>
                  </Box>
                  {dec[p.id] === "rejected" && <Chip size="small" label="kept as drawn" />}
                </Stack>
              ))}
            </Box>
          </Box>
          <Box>{cur && <Preview p={cur} />}</Box>
        </Box>
        <Stack direction="row" spacing={2} sx={{ mt: 2 }} alignItems="center" flexWrap="wrap" useFlexGap>
          <Typography variant="caption">Tolerances (m)</Typography>
          <TextField size="small" type="number" label="Near-duplicate" value={tol.review_tolerance} sx={{ width: 140 }}
            inputProps={{ step: 0.01, min: 0.001 }} onChange={(e) => setTol({ ...tol, review_tolerance: Number(e.target.value) })} />
          <TextField size="small" type="number" label="Open gap" value={tol.gap_tolerance} sx={{ width: 140 }}
            inputProps={{ step: 0.1, min: 0.01 }} onChange={(e) => setTol({ ...tol, gap_tolerance: Number(e.target.value) })} />
          <Typography variant="caption">Automatic clean-up uses {cl.settings.tolerance} m.</Typography>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Typography variant="body2" sx={{ flex: 1 }}>{approvedN} approved</Typography>
        <Button onClick={onClose}>Close</Button>
        <Button variant="contained" disabled={busy || !changed} onClick={apply}>Apply and re-read</Button>
      </DialogActions>
    </Dialog>
  );
}

/** The geometry involved: red = would change, blue = kept, dashed arrow = snap target. */
function Preview({ p }: { p: Proposal }) {
  const geo = p.geom.length > 0 && p.geom.every((g) => g.ll && g.ll.length);
  const [sat, setSat] = useState(true);
  const shapes = useMemo<GeoShape[]>(() => !geo ? [] : [
    ...[...p.geom].sort((a, b) => (a.role === "keep" ? 0 : 1) - (b.role === "keep" ? 0 : 1)).map((g): GeoShape => ({
      c: g.ll!, k: (g.k === "point" || g.ll!.length === 1 ? "point" : g.k === "polygon" ? "polygon" : "line"),
      color: g.role === "keep" ? "rgba(96,165,250,.9)" : "#EF4444", width: g.role === "keep" ? 6 : 2.5,
      fill: g.k === "polygon" ? (g.role === "keep" ? "rgba(96,165,250,.18)" : "rgba(239,68,68,.16)") : undefined,
      halo: g.role !== "keep" })),
    ...(p.to_ll && p.geom[0]?.ll ? [{ c: [p.geom[0].ll[0], p.to_ll], k: "line", color: "#F59E0B", width: 2, dash: [5, 4] } as GeoShape] : []),
  ], [p, geo]);
  const v = useMemo(() => {
    const pts = p.geom.flatMap((g) => g.c).concat(p.to ? [p.to] : []);
    if (!pts.length) return null;
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const span = Math.max(x1 - x0, y1 - y0, 0.2);
    const pad = span * 0.15, W = 420, H = 300;
    const s = Math.min(W / (x1 - x0 + 2 * pad || 1), H / (y1 - y0 + 2 * pad || 1));
    const ox = (W - (x1 - x0) * s) / 2, oy = (H - (y1 - y0) * s) / 2;
    const T = (q: number[]) => [ox + (q[0] - x0) * s, H - (oy + (q[1] - y0) * s)];
    return { T, W, H, span };
  }, [p]);
  if (!v) return null;
  const d = (c: number[][]) => c.map((q, i) => `${i ? "L" : "M"}${v.T(q).join(" ")}`).join("");
  return (
    <Box>
      {geo && (
        <Stack direction="row" spacing={0.5} sx={{ mb: 1 }}>
          {[["Satellite", true], ["Drawing only", false]].map(([l, on]) => (
            <Button key={String(l)} size="small" onClick={() => setSat(on as boolean)}
              sx={{ height: 28, px: 1.25, fontWeight: 500, color: sat === on ? C.ink : C.muted, bgcolor: sat === on ? "#E4E2DD" : "transparent" }}>{l as string}</Button>
          ))}
        </Stack>
      )}
      {geo && sat ? <GeoPreview shapes={shapes} height={300} fitKey={p.id} /> :
      <Box sx={{ border: `1px solid ${C.line}`, borderRadius: 1, bgcolor: "#FAFBFC" }}>
        <svg viewBox={`0 0 ${v.W} ${v.H}`} width="100%" role="img" aria-label="Geometry preview">
          {[...p.geom].sort((a, b) => (a.role === "keep" ? 0 : 1) - (b.role === "keep" ? 0 : 1)).map((g, i) => (
            g.k === "point" || g.c.length === 1
              ? <circle key={i} cx={v.T(g.c[0])[0]} cy={v.T(g.c[0])[1]} r={g.role === "keep" ? 7 : 5}
                  fill={g.role === "keep" ? "none" : "#DC2626"} stroke={g.role === "keep" ? "#2563EB" : "#DC2626"} strokeWidth={2} />
              : <path key={i} d={d(g.c) + (g.k === "polygon" ? "Z" : "")} fill={g.k === "polygon" ? (g.role === "keep" ? "#2563EB18" : "#DC262618") : "none"}
                  stroke={g.role === "keep" ? "#2563EB" : "#DC2626"} strokeWidth={g.role === "keep" ? 5 : 2} strokeOpacity={g.role === "keep" ? 0.35 : 1} />
          ))}
          {p.to && p.geom[0] && (() => {
            const c = p.geom[0].c; const end = p.handles && (p as any).end === -1 ? c[c.length - 1] : c[0];
            const [ax, ay] = v.T(end), [bx, by] = v.T(p.to!);
            return <line x1={ax} y1={ay} x2={bx} y2={by} stroke="#D97706" strokeWidth={2} strokeDasharray="4 3" />;
          })()}
        </svg>
      </Box>}
      {!geo && <Typography variant="caption" component="div" sx={{ mt: 0.5, color: C.muted }}>Set the drawing's coordinate system to see this on the satellite image</Typography>}
      <Stack direction="row" spacing={2} sx={{ mt: 0.75 }}>
        <Typography variant="caption"><Box component="span" sx={{ color: "#DC2626", fontWeight: 700 }}>■</Box> would change</Typography>
        {p.keep && <Typography variant="caption"><Box component="span" sx={{ color: "#2563EB", fontWeight: 700 }}>■</Box> kept</Typography>}
        {(() => {
          let span = v.span;
          if (geo) {   // the drawing may be in mm: measure the real size from lon/lat
            const ll = p.geom.flatMap((g) => g.ll!); const xs = ll.map((q) => q[0]), ys = ll.map((q) => q[1]);
            const k = 111320 * Math.cos((ys[0] * Math.PI) / 180);
            span = Math.max((Math.max(...xs) - Math.min(...xs)) * k, (Math.max(...ys) - Math.min(...ys)) * 110574);
          }
          return <Typography variant="caption">object ≈ {span < 1 ? `${Math.round(span * 1000)} mm` : `${span.toFixed(1)} m`} wide</Typography>;
        })()}
      </Stack>
      <Typography variant="body2" sx={{ mt: 1 }}><b>{p.label}</b> — {p.detail}</Typography>
      <Typography variant="caption" component="div">Layer{p.layers.length > 1 ? "s" : ""}: {p.layers.join(", ")}</Typography>
    </Box>
  );
}
