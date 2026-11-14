import { useEffect, useMemo, useState } from "react";
import {
  Box, Button, ButtonBase, Checkbox, Chip, Dialog, Stack, Tab, Tabs, TextField, Tooltip, Typography,
} from "@mui/material";
import CheckCircle from "@mui/icons-material/CheckCircle";
import { api } from "../api";
import type { JobState } from "../types";
import { C } from "../theme";
import { tr } from "../i18n";
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
  const [showTol, setShowTol] = useState(false);
  const [busy, setBusy] = useState(false);
  const list = cl.proposals.filter((p) => p.kind === tab);
  const idx = Math.max(0, list.findIndex((p) => p.id === sel));
  const cur = list[idx];
  const approvedN = Object.values(dec).filter((v) => v === "approved").length;
  const doneN = list.filter((p) => dec[p.id] !== "pending").length;
  const changed = cl.proposals.some((p) => dec[p.id] !== p.decision)
    || tol.review_tolerance !== cl.settings.review_tolerance || tol.gap_tolerance !== cl.settings.gap_tolerance;
  const setAll = (v: string) => setDec({ ...dec, ...Object.fromEntries(list.filter((p) => p.action !== "review").map((p) => [p.id, v])) });
  const go = (i: number) => { const p = list[Math.max(0, Math.min(list.length - 1, i))]; if (p) { setSel(p.id); document.getElementById(`cl-${p.id}`)?.scrollIntoView({ block: "nearest" }); } };
  const decide = (v: "approved" | "rejected") => { if (!cur || cur.action === "review") return; setDec((d) => ({ ...d, [cur.id]: v })); go(idx + 1); };
  const apply = async () => {
    setBusy(true);
    try {
      const changedDec = Object.fromEntries(Object.entries(dec).filter(([k, v]) => cl.proposals.find((p) => p.id === k)?.decision !== v));
      onApplied(await api.setCleanup(job.job_id, { decisions: changedDec, settings: tol, apply: true }));
    } finally { setBusy(false); }
  };
  // keyboard: ↑ ↓ move · A approve · K keep as drawn
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT" && (e.target as HTMLInputElement).type !== "checkbox") return;
    if (e.key === "ArrowDown") { e.preventDefault(); go(idx + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); go(idx - 1); }
    else if (e.key.toLowerCase() === "a") decide("approved");
    else if (e.key.toLowerCase() === "k") decide("rejected");
  };
  return (
    <Dialog open fullWidth maxWidth="xl" onClose={onClose} onKeyDown={onKey}
      PaperProps={{ sx: { height: "min(88vh, 900px)", display: "flex", flexDirection: "column", borderRadius: 3 } }}>
      {/* header */}
      <Stack direction="row" alignItems="flex-end" spacing={3} sx={{ px: 3, pt: 2.5, borderBottom: `1px solid ${C.line}`, flex: "none" }}>
        <Box sx={{ pb: 1.5 }}>
          <Typography sx={{ fontSize: 18, fontWeight: 500 }}>{tr("Advanced clean-up")}</Typography>
          <Typography sx={{ fontSize: 13, color: C.muted }}>{tr("Nothing changes in the drawing until you apply")}</Typography>
        </Box>
        <Tabs value={tab} onChange={(_, v) => { setTab(v); setSel(null); }} variant="scrollable" sx={{ minHeight: 40, "& .MuiTab-root": { minHeight: 40, textTransform: "none", fontSize: 13.5 } }}>
          {kinds.map((k) => <Tab key={k} value={k} label={<span>{cl.summary[k].label} <Box component="span" sx={{ color: C.muted, fontVariantNumeric: "tabular-nums" }}>{cl.summary[k].total.toLocaleString()}</Box></span>} />)}
        </Tabs>
      </Stack>

      {/* body: list | map */}
      <Box sx={{ flex: 1, minHeight: 0, display: "grid", gridTemplateColumns: { xs: "1fr", md: "minmax(320px, 380px) minmax(0, 1fr)" } }}>
        <Box sx={{ display: "flex", flexDirection: "column", minHeight: 0, borderRight: `1px solid ${C.line}` }}>
          <Stack direction="row" alignItems="center" spacing={0.5} sx={{ px: 1.5, py: 1, borderBottom: `1px solid ${C.line}`, flex: "none" }}>
            <Typography sx={{ fontSize: 12, color: C.muted, flex: 1, fontVariantNumeric: "tabular-nums" }}>{doneN.toLocaleString()} of {list.length.toLocaleString()} decided</Typography>
            <Button size="small" onClick={() => setAll("approved")}>{tr("Approve all")}</Button>
            <Button size="small" sx={{ color: C.muted }} onClick={() => setAll("rejected")}>{tr("Keep all")}</Button>
          </Stack>
          <Box sx={{ flex: 1, overflow: "auto" }}>
            {list.map((p, i) => {
              const on = cur?.id === p.id, d = dec[p.id];
              return (
                <Stack key={p.id} id={`cl-${p.id}`} direction="row" spacing={1} alignItems="center" onClick={() => setSel(p.id)}
                  sx={{ pl: 0.5, pr: 1.5, height: 52, borderBottom: `1px solid #EEF0F3`, cursor: "pointer",
                        bgcolor: on ? "#EEF0F3" : "transparent", boxShadow: on ? `inset 3px 0 0 ${C.ink}` : "none", "&:hover": { bgcolor: on ? "#EEF0F3" : "#F5F6F8" } }}>
                  <Checkbox size="small" disabled={p.action === "review"} checked={d === "approved"} inputProps={{ "aria-label": `Approve ${ACTION[p.action]} on ${p.layer}` }}
                    onClick={(e) => e.stopPropagation()} onChange={(e) => setDec({ ...dec, [p.id]: e.target.checked ? "approved" : "rejected" })} />
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    <Typography sx={{ fontSize: 13, fontWeight: 500 }} noWrap>{ACTION[p.action]} · {p.layer}</Typography>
                    <Typography sx={{ fontSize: 12, color: C.muted }} noWrap>{p.detail}</Typography>
                  </Box>
                  <Typography sx={{ fontSize: 11, color: d === "approved" ? C.green : d === "rejected" ? C.muted : "#A0A7B3", fontFamily: '"Geist Mono", monospace', flex: "none" }}>
                    {d === "approved" ? "approve" : d === "rejected" ? "keep" : `#${i + 1}`}
                  </Typography>
                </Stack>
              );
            })}
          </Box>
          <Box sx={{ borderTop: `1px solid ${C.line}`, flex: "none" }}>
            <ButtonBase onClick={() => setShowTol(!showTol)} sx={{ width: "100%", justifyContent: "space-between", px: 1.5, height: 36, fontSize: 12, color: C.muted }}>
              <span>Tolerances · near-duplicate {tol.review_tolerance} m · open gap {tol.gap_tolerance} m</span><span>{showTol ? "Hide" : "Change"}</span>
            </ButtonBase>
            {showTol && (
              <Stack direction="row" spacing={1} sx={{ px: 1.5, pb: 1.5 }}>
                <TextField size="small" type="number" label="Near-duplicate (m)" value={tol.review_tolerance}
                  inputProps={{ step: 0.01, min: 0.001 }} onChange={(e) => setTol({ ...tol, review_tolerance: Number(e.target.value) })} />
                <TextField size="small" type="number" label="Open gap (m)" value={tol.gap_tolerance}
                  inputProps={{ step: 0.1, min: 0.01 }} onChange={(e) => setTol({ ...tol, gap_tolerance: Number(e.target.value) })} />
              </Stack>
            )}
          </Box>
        </Box>

        {/* the map is the decision surface */}
        <Box sx={{ position: "relative", minHeight: 360, bgcolor: C.ink }}>
          {cur ? <Preview p={cur} /> : <Typography sx={{ color: "#A0A7B3", p: 3 }}>Nothing in this list</Typography>}
          {cur && (
            <Box sx={{ position: "absolute", left: 16, right: 16, bottom: 16, bgcolor: "#fff", borderRadius: 2.5, boxShadow: "0 8px 28px rgba(20,24,31,.22)",
                       p: 2, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 2 }}>
              <Box sx={{ flex: "1 1 280px", minWidth: 0 }}>
                <Typography sx={{ fontSize: 12, color: C.muted, fontVariantNumeric: "tabular-nums" }}>{(idx + 1).toLocaleString()} of {list.length.toLocaleString()} · {cur.layers.join(", ")}</Typography>
                <Typography sx={{ fontSize: 15, fontWeight: 500 }}>{ACTION[cur.action]} · {cur.layer}</Typography>
                <Typography sx={{ fontSize: 13, color: C.muted }}>{cur.detail}</Typography>
              </Box>
              <Stack direction="row" spacing={1} alignItems="center">
                <Button size="small" sx={{ color: C.muted, minWidth: 0 }} aria-label="Previous" onClick={() => go(idx - 1)} disabled={idx === 0}>↑</Button>
                <Button size="small" sx={{ color: C.muted, minWidth: 0 }} aria-label="Next" onClick={() => go(idx + 1)} disabled={idx >= list.length - 1}>↓</Button>
                {cur.action !== "review" ? (<>
                  <Button variant="outlined" onClick={() => decide("rejected")} sx={{ height: 40 }}>{tr("Keep as drawn")} <Kbd>K</Kbd></Button>
                  <Button variant="contained" onClick={() => decide("approved")} sx={{ height: 40 }}>{tr(ACTION[cur.action])} <Kbd dark>A</Kbd></Button>
                </>) : <Typography sx={{ fontSize: 13, color: C.muted }}>Check only — nothing to apply</Typography>}
              </Stack>
            </Box>
          )}
        </Box>
      </Box>

      {/* footer */}
      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ px: 3, py: 1.5, borderTop: `1px solid ${C.line}`, flex: "none" }}>
        <Typography sx={{ fontSize: 13, flex: 1 }}><b style={{ fontWeight: 500 }}>{approvedN.toLocaleString()}</b> <span style={{ color: C.muted }}>approved · automatic clean-up uses {cl.settings.tolerance} m</span></Typography>
        <Button onClick={onClose} sx={{ color: C.muted }}>{tr("Close")}</Button>
        <Button variant="contained" disabled={busy || !changed} onClick={apply}>{tr("Apply and re-read")}</Button>
      </Stack>
    </Dialog>
  );
}

function Kbd({ children, dark }: { children: React.ReactNode; dark?: boolean }) {
  return <Box component="kbd" sx={{ ml: 1, px: 0.6, fontSize: 11, fontFamily: '"Geist Mono", monospace', borderRadius: 0.75,
    border: `1px solid ${dark ? "rgba(255,255,255,.3)" : C.line}`, color: dark ? "rgba(255,255,255,.85)" : C.muted }}>{children}</Box>;
}

/** The geometry involved, filling the right side: red = would change, blue = kept, dashed = snap target. */
function Preview({ p }: { p: Proposal }) {
  const geo = p.geom.length > 0 && p.geom.every((g) => g.ll && g.ll.length);
  const [sat, setSat] = useState(true);
  const shapes = useMemo<GeoShape[]>(() => !geo ? [] : [
    ...[...p.geom].sort((a, b) => (a.role === "keep" ? 0 : 1) - (b.role === "keep" ? 0 : 1)).map((g): GeoShape => ({
      c: g.ll!, k: (g.k === "point" || g.ll!.length === 1 ? "point" : g.k === "polygon" ? "polygon" : "line"),
      color: g.role === "keep" ? "rgba(96,165,250,.9)" : "#EF4444", width: g.role === "keep" ? 7 : 3,
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
    const pad = span * 0.2, W = 800, H = 520;
    const s = Math.min(W / (x1 - x0 + 2 * pad || 1), H / (y1 - y0 + 2 * pad || 1));
    const ox = (W - (x1 - x0) * s) / 2, oy = (H - (y1 - y0) * s) / 2;
    const T = (q: number[]) => [ox + (q[0] - x0) * s, H - (oy + (q[1] - y0) * s)];
    return { T, W, H, span };
  }, [p]);
  if (!v) return null;
  const d = (c: number[][]) => c.map((q, i) => `${i ? "L" : "M"}${v.T(q).join(" ")}`).join("");
  let span = v.span;
  if (geo) {   // the drawing may be in mm: measure the real size from lon/lat
    const ll = p.geom.flatMap((g) => g.ll!); const xs = ll.map((q) => q[0]), ys = ll.map((q) => q[1]);
    const k = 111320 * Math.cos((ys[0] * Math.PI) / 180);
    span = Math.max((Math.max(...xs) - Math.min(...xs)) * k, (Math.max(...ys) - Math.min(...ys)) * 110574);
  }
  const showSat = geo && sat;
  return (
    <Box sx={{ position: "absolute", inset: 0 }}>
      {showSat ? <GeoPreview shapes={shapes} height="100%" fitKey={p.id} /> : (
        <Box sx={{ position: "absolute", inset: 0, bgcolor: "#F5F6F8", backgroundImage: "linear-gradient(#ECEAE5 1px, transparent 1px), linear-gradient(90deg, #ECEAE5 1px, transparent 1px)", backgroundSize: "32px 32px" }}>
          <svg viewBox={`0 0 ${v.W} ${v.H}`} width="100%" height="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Geometry preview">
            {[...p.geom].sort((a, b) => (a.role === "keep" ? 0 : 1) - (b.role === "keep" ? 0 : 1)).map((g, i) => (
              g.k === "point" || g.c.length === 1
                ? <circle key={i} cx={v.T(g.c[0])[0]} cy={v.T(g.c[0])[1]} r={g.role === "keep" ? 9 : 6}
                    fill={g.role === "keep" ? "none" : "#DC2626"} stroke={g.role === "keep" ? "#2563EB" : "#DC2626"} strokeWidth={2} />
                : <path key={i} d={d(g.c) + (g.k === "polygon" ? "Z" : "")} fill={g.k === "polygon" ? (g.role === "keep" ? "#2563EB18" : "#DC262618") : "none"}
                    stroke={g.role === "keep" ? "#2563EB" : "#DC2626"} strokeWidth={g.role === "keep" ? 7 : 2.5} strokeOpacity={g.role === "keep" ? 0.35 : 1} />
            ))}
            {p.to && p.geom[0] && (() => {
              const c = p.geom[0].c; const end = p.handles && (p as any).end === -1 ? c[c.length - 1] : c[0];
              const [ax, ay] = v.T(end), [bx, by] = v.T(p.to!);
              return <line x1={ax} y1={ay} x2={bx} y2={by} stroke="#D97706" strokeWidth={2} strokeDasharray="5 4" />;
            })()}
          </svg>
        </Box>
      )}
      {/* view switch + legend, on the map */}
      <Stack direction="row" spacing={1} alignItems="center" sx={{ position: "absolute", top: 16, left: 16, zIndex: 2 }}>
        {geo && (
          <Stack direction="row" sx={{ bgcolor: "#fff", borderRadius: 2, p: 0.5, boxShadow: "0 2px 10px rgba(20,24,31,.18)" }}>
            {[["Satellite", true], ["Drawing", false]].map(([l, on]) => (
              <ButtonBase key={String(l)} onClick={() => setSat(on as boolean)} aria-pressed={sat === on}
                sx={{ height: 28, px: 1.5, borderRadius: 1.5, fontSize: 12.5, color: sat === on ? "#fff" : C.ink, bgcolor: sat === on ? C.ink : "transparent" }}>{l as string}</ButtonBase>
            ))}
          </Stack>
        )}
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ bgcolor: "rgba(255,255,255,.94)", borderRadius: 2, px: 1.5, height: 36, fontSize: 12, boxShadow: "0 2px 10px rgba(20,24,31,.18)" }}>
          <Stack direction="row" spacing={0.75} alignItems="center"><Box sx={{ width: 14, height: 3, bgcolor: "#DC2626", borderRadius: 1 }} /><span>{tr("would change")}</span></Stack>
          {p.keep && <Stack direction="row" spacing={0.75} alignItems="center"><Box sx={{ width: 14, height: 5, bgcolor: "#60A5FA", borderRadius: 1 }} /><span>{tr("kept")}</span></Stack>}
          <Box component="span" sx={{ color: C.muted, fontFamily: '"Geist Mono", monospace' }}>≈ {span < 1 ? `${Math.round(span * 1000)} mm` : `${span.toFixed(1)} m`}</Box>
        </Stack>
      </Stack>
      {!geo && <Typography sx={{ position: "absolute", top: 60, left: 16, fontSize: 12, color: C.muted }}>Set the drawing's coordinate system to see this on the satellite image</Typography>}
    </Box>
  );
}
