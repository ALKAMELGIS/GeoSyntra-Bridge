import { useEffect, useState } from "react";
import { Box, Button, Chip, MenuItem, Paper, Stack, TextField, Tooltip, Typography } from "@mui/material";
import { api } from "../api";
import type { JobState } from "../types";
import { C } from "../theme";

interface Missing { field: string; alias?: string | null; type: string; classes: string[]; features: number; domain?: string | null;
  choices: Record<string, string> | null; description: string; elevation: boolean; remembered?: string | null }
interface Unmapped { field: string; value: string; domain: string; classes: string[]; features: number;
  candidates: { code: string; label: string }[]; choices: Record<string, string>; mapped_to?: string | null; suggested?: string | null }

/** What the drawing does not say and the templates require — asked once per field, for every class that has it. */
export function RequiredValues({ job, onPatch, onValidate, onGoElevation, embedded, wrap, namesOnly }: {
  job: JobState; onPatch: (p: unknown) => Promise<void>; onValidate: () => void; onGoElevation?: () => void;
  embedded?: boolean; wrap?: (content: React.ReactNode) => React.ReactNode; namesOnly?: boolean }) {
  const [rv, setRv] = useState<{ missing: Missing[]; unmapped_values: Unmapped[]; validated: boolean } | null>(null);
  const [ans, setAns] = useState<Record<string, string>>({});
  const [maps, setMaps] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [dem, setDem] = useState("");
  useEffect(() => {
    if (!["validated", "completed", "ready"].includes(job.status)) return;
    (api as any).requiredValues(job.job_id).then((r: any) => {
      setRv(r);
      const m: Record<string, string> = {};
      r.unmapped_values.forEach((u: Unmapped) => { const k = `${u.field}|${u.value}`; if (u.mapped_to) m[k] = u.mapped_to; else if (u.suggested) m[k] = u.suggested; else if (u.candidates.length === 1) m[k] = u.candidates[0].code; });
      setMaps(m);
      const a: Record<string, string> = {};
      r.missing.forEach((x: Missing) => { if (x.remembered != null && !x.elevation) a[x.field] = String(x.remembered); });
      setAns(a);
    }).catch(() => setRv(null));
  }, [job.job_id, job.status]);
  if (!rv || !rv.validated) return null;
  const typed = namesOnly ? [] : rv.missing.filter((m) => !m.elevation);
  const levels = namesOnly ? [] : rv.missing.filter((m) => m.elevation);
  if (!typed.length && !levels.length && !rv.unmapped_values.length) return null;
  const save = async () => {
    setBusy(true);
    try {
      const fields: Record<string, any> = {};
      Object.entries(ans).forEach(([f, v]) => { if (v !== "") fields[f] = { source: "constant", value: v }; });
      const vm: Record<string, Record<string, string>> = {};
      Object.entries(maps).forEach(([k, code]) => { const [f, ...raw] = k.split("|"); if (code) (vm[f] = vm[f] || {})[raw.join("|")] = code; });
      await onPatch({ ...(Object.keys(fields).length ? { fields: { "*": fields } } : {}), ...(Object.keys(vm).length ? { value_maps: vm } : {}) });
      onValidate();
    } finally { setBusy(false); }
  };
  const ready = typed.every((m) => (ans[m.field] ?? "") !== "");
  const body = (
    <>
      <Stack spacing={1.25}>
        {typed.map((m) => (
          <Box key={m.field} sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "280px 1fr 300px" }, gap: 1.5, alignItems: "center" }}>
            <Tooltip title={m.description || ""} placement="top-start">
              <Box>
                <Typography sx={{ fontWeight: 650 }}>{m.alias || m.field}</Typography>
                <Typography variant="caption" component="div">{m.features.toLocaleString()} features</Typography>
              </Box>
            </Tooltip>
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap alignItems="center">
              {m.classes.slice(0, 4).map((c) => <Chip key={c} size="small" label={c} />)}
              {m.classes.length > 4 && <Chip size="small" label={`+${m.classes.length - 4}`} />}
              {m.remembered != null && <Chip size="small" label="remembered" sx={{ bgcolor: C.cyanSoft, color: C.cyan }}
                onDelete={() => { api.forget(m.field).catch(() => undefined);
                  setRv({ ...rv, missing: rv.missing.map((x) => (x.field === m.field ? { ...x, remembered: null } : x)) }); setAns({ ...ans, [m.field]: "" }); }} />}
            </Stack>
            {m.choices ? (
              <TextField select size="small" label="Value for all" value={ans[m.field] ?? ""} onChange={(e) => setAns({ ...ans, [m.field]: e.target.value })}>
                {Object.entries(m.choices).map(([k, l]) => <MenuItem key={k} value={k} dense>{k === l ? k : `${l} (${k})`}</MenuItem>)}
              </TextField>
            ) : (
              <TextField size="small" label="Value for all" value={ans[m.field] ?? ""} type={m.type === "DATE" ? "date" : ["DOUBLE", "INTEGER", "SMALLINTEGER"].includes(m.type) ? "number" : "text"}
                InputLabelProps={m.type === "DATE" ? { shrink: true } : undefined} onChange={(e) => setAns({ ...ans, [m.field]: e.target.value })} />
            )}
          </Box>
        ))}
        {rv.unmapped_values.length > 0 && (
          <Box sx={{ pt: 1 }}>
            <Typography sx={{ fontWeight: 650, mb: 0.75 }}>Drawing names → template values</Typography>
            {rv.unmapped_values.map((u) => {
              const k = `${u.field}|${u.value}`;
              return (
                <Box key={k} sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "280px 1fr 300px" }, gap: 1.5, alignItems: "center", py: 0.5 }}>
                  <Box><Typography sx={{ fontFamily: "monospace", fontSize: 13 }}>“{u.value}”</Typography>
                    <Typography variant="caption">{u.field} · {u.features} features</Typography></Box>
                  <Stack direction="row" spacing={0.5} alignItems="center">
                    {u.classes.slice(0, 3).map((c) => <Chip key={c} size="small" label={c} />)}
                    {u.suggested && maps[k] === u.suggested && <Chip size="small" label="best match" sx={{ bgcolor: C.cyanSoft, color: C.cyan }} />}
                  </Stack>
                  <TextField select size="small" label="Means" value={maps[k] ?? ""} onChange={(e) => setMaps({ ...maps, [k]: e.target.value })}>
                    {u.candidates.map((c) => <MenuItem key={`c-${c.code}`} value={c.code} dense><b>{c.label}</b></MenuItem>)}
                    {Object.entries(u.choices).filter(([c]) => !u.candidates.some((x) => x.code === c)).map(([c, l]) => <MenuItem key={c} value={c} dense>{l}</MenuItem>)}
                  </TextField>
                </Box>
              );
            })}
          </Box>
        )}
        {levels.length > 0 && (() => {
          const total = levels.reduce((t, l) => t + l.features, 0);
          const names = levels.map((l) => l.field);
          return (
            <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap", p: 1.5, borderRadius: 2, border: `1px solid ${C.line}`, bgcolor: "#fff" }}>
              <Box sx={{ flex: "1 1 280px", minWidth: 0 }}>
                <Typography sx={{ fontSize: 14, fontWeight: 600 }}>{total.toLocaleString()} features have no level</Typography>
                <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap" sx={{ mt: 0.75 }}>
                  {names.slice(0, 4).map((n) => <Chip key={n} size="small" label={n} sx={{ height: 22, fontSize: 11, fontFamily: "monospace", bgcolor: "#F3F4F6" }} />)}
                  {names.length > 4 && <Tooltip title={names.slice(4).join(", ")}><Chip size="small" label={`+${names.length - 4} more`} sx={{ height: 22, fontSize: 11 }} /></Tooltip>}
                </Stack>
                {dem && dem !== "busy" ? <Typography sx={{ color: C.red, fontSize: 12.5, mt: 0.75 }}>{dem}</Typography> : null}
              </Box>
              <Stack direction="row" spacing={1} sx={{ flex: "none", "& .MuiButton-root": { whiteSpace: "nowrap", height: 36 } }}>
                <Button size="small" variant="contained" disabled={dem === "busy"} onClick={async () => {
                  setDem("busy");
                  try { await api.prepareDem(job.job_id, "copernicus_glo30"); setDem(""); onValidate(); }
                  catch (e: any) { setDem(`${e.message || "DEM not available"} — the free DEM needs internet. Upload a DEM or continue without levels.`); }
                }}>{dem === "busy" ? "Fetching DEM…" : "Free DEM · 30 m"}</Button>
                {onGoElevation && <Button size="small" variant="outlined" onClick={onGoElevation}>Upload DEM</Button>}
                <Tooltip title="Writes the features now with the no-data level -9999 (listed in the QC report) — replace it later with a DEM or surveyed levels">
                  <Button size="small" color="inherit" disabled={dem === "busy"} onClick={async () => {
                    await onPatch({ elevation: { missing_policy: "nodata_value", nodata_value: -9999 } });
                    onValidate();
                  }}>Continue with −9999</Button>
                </Tooltip>
              </Stack>
            </Box>
          );
        })()}
      </Stack>
      {(typed.length > 0 || rv.unmapped_values.length > 0) && <Stack direction="row" spacing={1} sx={{ mt: 1.75 }} alignItems="center">
        <Button variant="contained" disabled={busy || (!ready && !Object.keys(maps).length)} onClick={save}>Apply and validate</Button>
        <Typography variant="caption">Answers are remembered for your next drawings.</Typography>
      </Stack>}
    </>
  );
  if (wrap) return <>{wrap(body)}</>;
  if (embedded) return body;
  return (
    <Paper sx={{ p: 2.25, mb: 2, borderColor: C.amber, borderWidth: 1.5 }}>
      <Typography variant="h5" sx={{ mb: 1.5 }}>{namesOnly ? "Drawing names that are not in the template's lists" : "Values the drawing does not contain"}</Typography>
      {body}
    </Paper>
  );
}

export function DeliveryTable({ jobId, status }: { jobId: string; status: string }) {
  const [rows, setRows] = useState<any[] | null>(null);
  useEffect(() => { (api as any).delivery(jobId).then(setRows).catch(() => setRows(null)); }, [jobId, status]);
  if (!rows || !rows.length) return null;
  const tone = (s: string) => (s.startsWith("Delivered") || s.startsWith("Ready") ? C.green : s.startsWith("Not converted") ? C.muted : C.amber);
  return (
    <Paper sx={{ p: 2, mt: 2, overflowX: "auto" }}>
      <Typography variant="h5" sx={{ mb: 1 }}>Mapping and delivery</Typography>
      <Box component="table" sx={{ width: "100%", borderCollapse: "collapse", fontSize: 13, "& td, & th": { borderBottom: `1px solid ${C.line}`, py: 0.75, px: 1, textAlign: "left", verticalAlign: "top" },
        "& th": { color: C.muted, fontWeight: 600, fontSize: 12, bgcolor: "#F7F8FA" } }}>
        <thead><tr><th>Source CAD layer</th><th>Geometry</th><th>Target geodatabase</th><th>Feature class</th><th style={{ textAlign: "right" }}>Written</th><th>Status</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td><b>{r.layer}</b>{r.style ? <Typography variant="caption" component="div">{r.style}</Typography> : null}</td>
              <td>{r.cad_geometry}{r.fc_geometry && r.fc_geometry !== r.cad_geometry ? ` → ${r.fc_geometry}` : ""}</td>
              <td style={{ fontFamily: "monospace", fontSize: 12 }}>{r.geodatabase}</td>
              <td>{r.feature_class}</td>
              <td style={{ textAlign: "right" }}>{r.written || ""}</td>
              <td style={{ color: tone(r.status), fontWeight: 600 }}>{r.status}</td>
            </tr>
          ))}
        </tbody>
      </Box>
    </Paper>
  );
}
