import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, ButtonBase, Checkbox, Chip, FormControlLabel, LinearProgress, MenuItem, Paper, Stack, Tab, Table, TableBody,
  TableCell, TableHead, TableRow, Tabs, TextField, Typography,
} from "@mui/material";
import FileDownloadOutlined from "@mui/icons-material/FileDownloadOutlined";
import UploadFileOutlined from "@mui/icons-material/UploadFileOutlined";
import CheckRounded from "@mui/icons-material/CheckRounded";
import { api } from "../api";
import type { AuthUser, Capabilities, SchemaSummary, TemplateUpdate, TemplateVersion } from "../types";
import { C } from "../theme";
import { AppHeader } from "../components/AppHeader";
import { KIND, SEV, SummaryChips } from "../components/TemplateUpdates";
import { tr } from "../i18n";

interface Props { me: AuthUser | null; mode: string; go: (r: string) => void; onLogout: () => void; params: URLSearchParams }
type Hist = { current: { version: string; template: string; applied_at?: string; applied_by?: string } | null; updates: TemplateUpdate[]; versions: TemplateVersion[] };

const MONO = '"Geist Mono", ui-monospace, monospace';
const codeOf = (s: SchemaSummary) => (/ADM_([A-Z]+)/i.exec(s.source_template || "")?.[1] || /adm_([a-z]+)/i.exec(s.schema_id)?.[1] || "").toUpperCase();
const when = (iso?: string) => (iso ? iso.slice(0, 16).replace("T", " ") : "—");

/**
 * ADM template geodatabases: what each delivery is built from. The authority publishes new releases; an administrator
 * uploads the .gdb as a .zip, the system reads and compares it, and nothing changes until the release is applied.
 * Every replaced release is archived and can be downloaded or restored.
 */
export function Templates({ me, mode, go, onLogout, params }: Props) {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [list, setList] = useState<SchemaSummary[] | null>(null);
  const [sid, setSid] = useState<string | null>(params.get("schema"));
  const [hist, setHist] = useState<Hist | null>(null);
  const [rev, setRev] = useState<TemplateUpdate | null>(null);   // the release under review
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const admin = !!(caps?.admin ?? (me && (me.role === "owner" || me.role === "admin")));

  const loadList = () => api.schemas().then((s) => {
    const t = s.filter((x) => x.schema_id !== "adm_all");
    setList(t); if (!sid && t.length) setSid(t[0].schema_id);
  }).catch((e) => setErr(e.message));
  useEffect(() => { api.capabilities().then(setCaps).catch(() => undefined); loadList(); }, []);
  const loadHist = async (id: string) => {
    try {
      const h = await api.templateUpdates(id); setHist(h);
      const p = h.updates.find((u) => u.status === "pending");
      setRev(p ? await api.templateUpdate(id, p.update_id) : null);
    } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { if (sid) { setHist(null); setRev(null); setOk(null); loadHist(sid); window.history.replaceState(null, "", `#/templates?schema=${sid}`); } }, [sid]);
  const cur = list?.find((s) => s.schema_id === sid) || null;

  const run = async (label: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(label); setErr(null); setOk(null);
    try { await fn(); if (done) setOk(done); } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };
  const upload = (f: File) => sid && run("Reading and comparing the release…", async () => {
    if (!/\.zip$/i.test(f.name)) throw new Error(tr("Upload the template geodatabase as a .zip of the .gdb folder"));
    const u = await api.stageTemplate(sid, f);
    await loadHist(sid); setRev(await api.templateUpdate(sid, u.update_id));
  });

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppHeader me={me} mode={mode} go={go} onLogout={onLogout} active="templates" />
      <Box sx={{ maxWidth: 1520, mx: "auto", px: { xs: 2, md: 4 }, py: 3, display: "flex", flexDirection: "column", gap: 2.5 }}>
        <Box>
          <Typography component="h1" sx={{ fontSize: 22, fontWeight: 500 }}>{tr("ADM templates")}</Typography>
          <Typography sx={{ fontSize: 13.5, color: C.muted, mt: 0.5, maxWidth: 760 }}>
            {tr("The geodatabases every delivery is built from. When the authority publishes a new release, upload it here: it is checked and compared first, and nothing changes until you apply it.")}
          </Typography>
        </Box>
        {err && <Alert severity="error" onClose={() => setErr(null)}>{err}</Alert>}
        {ok && <Alert severity="success" onClose={() => setOk(null)}>{ok}</Alert>}

        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 2.5, alignItems: "flex-start" }}>
          {/* ---- the templates */}
          <Paper sx={{ flex: "1 1 280px", maxWidth: 340, borderRadius: 3, overflow: "hidden" }}>
            {!list && <LinearProgress />}
            {list?.map((s) => {
              const on = s.schema_id === sid;
              return (
                <ButtonBase key={s.schema_id} onClick={() => setSid(s.schema_id)} aria-pressed={on}
                  sx={{ width: "100%", justifyContent: "flex-start", textAlign: "start", gap: 1.5, px: 2, py: 1.5, borderBottom: `1px solid #EEF0F3`,
                        bgcolor: on ? "#EEF0F3" : "transparent", boxShadow: on ? `inset 3px 0 0 ${C.ink}` : "none", "&:hover": { bgcolor: on ? "#EEF0F3" : "#F5F6F8" } }}>
                  <Box sx={{ width: 40, height: 40, flex: "none", borderRadius: 2, bgcolor: "#fff", border: `1px solid ${C.line}`, display: "grid", placeItems: "center",
                             fontFamily: MONO, fontSize: 12, fontWeight: 500 }}>{codeOf(s)}</Box>
                  <Box sx={{ minWidth: 0, flex: 1 }}>
                    <Typography sx={{ fontSize: 14, fontWeight: 500 }} noWrap>{s.display_name.split(" — ")[0]}</Typography>
                    <Typography sx={{ fontSize: 12, color: C.muted, fontFamily: MONO }} noWrap>v{s.version.split(" ")[0]} · {s.feature_class_count} {tr("classes")}</Typography>
                  </Box>
                </ButtonBase>
              );
            })}
          </Paper>

          {/* ---- the selected template */}
          {cur && (
            <Stack spacing={2.5} sx={{ flex: "999 1 640px", minWidth: 0 }}>
              <Paper sx={{ p: 2.5, borderRadius: 3 }}>
                <Stack direction="row" alignItems="flex-start" flexWrap="wrap" useFlexGap gap={2}>
                  <Box sx={{ flex: 1, minWidth: 240 }}>
                    <Typography sx={{ fontSize: 12, color: C.muted }}>{tr("In use")}</Typography>
                    <Typography sx={{ fontSize: 18, fontWeight: 500 }}>{cur.display_name.split(" — ")[0]}</Typography>
                    <Typography sx={{ fontSize: 12.5, color: C.muted, fontFamily: MONO, mt: 0.25 }}>{(cur.source_template || "—").replace(/(\.gdb)?$/i, ".gdb")}</Typography>
                  </Box>
                  <Button variant="outlined" startIcon={<FileDownloadOutlined />} href={api.templateZipUrl(cur.schema_id)} disabled={!cur.source_template}>{tr("Download .zip")}</Button>
                  {admin && <Button variant="contained" startIcon={<UploadFileOutlined />} disabled={!!busy || !!rev} onClick={() => fileRef.current?.click()}>{tr("Upload new release")}</Button>}
                  <input ref={fileRef} type="file" accept=".zip" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
                </Stack>
                <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0,1fr))", md: "repeat(5, minmax(0,1fr))" }, gap: 2, mt: 2.5, pt: 2, borderTop: `1px solid ${C.line}` }}>
                  <Fig k={tr("Version")} v={cur.version} />
                  <Fig k={tr("Feature classes")} v={cur.feature_class_count} />
                  <Fig k={tr("Domains")} v={cur.domain_count} />
                  <Fig k={tr("Coordinate system")} v={cur.spatial_reference.epsg ? `EPSG:${cur.spatial_reference.epsg}` : "—"} />
                  <Fig k={tr("Applied")} v={hist?.current?.applied_at ? `${when(hist.current.applied_at)}` : tr("as shipped")} sub={hist?.current?.applied_by} />
                </Box>
                {admin && !rev && (
                  <Box onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) upload(f); }}
                    sx={{ mt: 2, p: 2, borderRadius: 2, border: `1px dashed ${C.line}`, color: C.muted, fontSize: 13, textAlign: "center" }}>
                    {tr("Drop the new release here — the .gdb folder zipped, as published by the authority")}
                  </Box>
                )}
                {busy && <Box sx={{ mt: 2 }}><LinearProgress /><Typography sx={{ fontSize: 12.5, color: C.muted, mt: 0.75 }}>{tr(busy)}</Typography></Box>}
              </Paper>

              {rev && rev.diff && rev.impact && cur && (
                <Review rev={rev} admin={admin} busy={!!busy}
                  onApply={(renames) => run("Applying the release…", async () => {
                    await api.applyTemplate(cur.schema_id, rev.update_id, renames);
                    setRev(null); await loadHist(cur.schema_id); await loadList();
                  }, tr("{v} is now in use. The previous release is archived below.", { v: rev.new_version }))}
                  onReject={() => run("Rejecting…", async () => { await api.rejectTemplate(cur.schema_id, rev.update_id); setRev(null); await loadHist(cur.schema_id); })} />
              )}

              {/* ---- history */}
              <Paper sx={{ borderRadius: 3, overflow: "hidden" }}>
                <Typography sx={{ px: 2.5, pt: 2, pb: 1, fontSize: 15, fontWeight: 500 }}>{tr("Release history")}</Typography>
                <Box sx={{ overflowX: "auto" }}>
                  <Table size="small" sx={{ minWidth: 720, "& td, & th": { borderColor: "#EEF0F3" } }}>
                    <TableHead><TableRow sx={{ "& th": { fontWeight: 500, fontSize: 12, color: C.muted } }}>
                      <TableCell sx={{ pl: 2.5 }}>{tr("Uploaded")}</TableCell><TableCell>{tr("File")}</TableCell><TableCell>{tr("Version")}</TableCell>
                      <TableCell>{tr("Changes")}</TableCell><TableCell>{tr("Status")}</TableCell><TableCell />
                    </TableRow></TableHead>
                    <TableBody>
                      {hist && hist.updates.length === 0 && <TableRow><TableCell colSpan={6} sx={{ pl: 2.5, color: C.muted }}>{tr("No release uploaded yet")}</TableCell></TableRow>}
                      {hist?.updates.map((u) => (
                        <TableRow key={u.update_id} hover>
                          <TableCell sx={{ pl: 2.5, whiteSpace: "nowrap", fontSize: 12.5 }}>{when(u.uploaded_at)}<Box component="span" sx={{ color: C.muted }}> · {u.uploaded_by}</Box></TableCell>
                          <TableCell sx={{ fontFamily: MONO, fontSize: 12 }}>{u.file}</TableCell>
                          <TableCell sx={{ fontFamily: MONO, fontSize: 12, whiteSpace: "nowrap" }}>{u.current_version} → {u.new_version}</TableCell>
                          <TableCell>{u.summary.identical ? <Typography sx={{ fontSize: 12, color: C.muted }}>{tr("identical")}</Typography> : <SummaryChips s={u.summary} />}</TableCell>
                          <TableCell><StatusDot s={u.status} /></TableCell>
                          <TableCell align="right" sx={{ whiteSpace: "nowrap", pr: 2 }}>
                            {u.status !== "pending" && <Button size="small" onClick={async () => setRev(await api.templateUpdate(cur.schema_id, u.update_id))}>{tr("Report")}</Button>}
                            {admin && <Button size="small" href={api.uploadedZipUrl(cur.schema_id, u.update_id)} sx={{ minWidth: 0 }} aria-label={tr("Download .zip")}><FileDownloadOutlined fontSize="small" /></Button>}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              </Paper>

              <Paper sx={{ borderRadius: 3, overflow: "hidden" }}>
                <Typography sx={{ px: 2.5, pt: 2, pb: 0.5, fontSize: 15, fontWeight: 500 }}>{tr("Archived releases")}</Typography>
                <Typography sx={{ px: 2.5, pb: 1, fontSize: 12.5, color: C.muted }}>{tr("Every release that was replaced. Restoring one archives the release in use first.")}</Typography>
                <Box sx={{ overflowX: "auto" }}>
                  <Table size="small" sx={{ minWidth: 640, "& td, & th": { borderColor: "#EEF0F3" } }}>
                    <TableHead><TableRow sx={{ "& th": { fontWeight: 500, fontSize: 12, color: C.muted } }}>
                      <TableCell sx={{ pl: 2.5 }}>{tr("Version")}</TableCell><TableCell>{tr("Template")}</TableCell><TableCell align="right">{tr("Classes")}</TableCell>
                      <TableCell>{tr("Archived")}</TableCell><TableCell />
                    </TableRow></TableHead>
                    <TableBody>
                      {hist && hist.versions.length === 0 && <TableRow><TableCell colSpan={5} sx={{ pl: 2.5, color: C.muted }}>{tr("Nothing archived yet")}</TableCell></TableRow>}
                      {hist?.versions.map((v) => (
                        <TableRow key={v.folder} hover>
                          <TableCell sx={{ pl: 2.5, fontFamily: MONO, fontSize: 12 }}>{v.version}</TableCell>
                          <TableCell sx={{ fontFamily: MONO, fontSize: 12 }}>{v.template}</TableCell>
                          <TableCell align="right" sx={{ fontFamily: MONO, fontSize: 12 }}>{v.layers}</TableCell>
                          <TableCell sx={{ fontSize: 12.5 }}>{when(v.archived_at)}<Box component="span" sx={{ color: C.muted }}> · {v.reason}</Box></TableCell>
                          <TableCell align="right" sx={{ whiteSpace: "nowrap", pr: 2 }}>
                            {admin && <Button size="small" href={api.archivedZipUrl(cur.schema_id, v.folder)} startIcon={<FileDownloadOutlined fontSize="small" />}>{tr(".zip")}</Button>}
                            {admin && <Button size="small" disabled={!!busy || !!rev} onClick={() => {
                              if (!window.confirm(tr("Restore {v}? The release in use is archived first.", { v: v.version }))) return;
                              run("Restoring…", async () => { await api.restoreTemplate(cur.schema_id, v.folder); await loadHist(cur.schema_id); await loadList(); }, tr("{v} restored.", { v: v.version }));
                            }}>{tr("Restore")}</Button>}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Box>
              </Paper>
            </Stack>
          )}
        </Box>
      </Box>
    </Box>
  );
}

function Fig({ k, v, sub }: { k: string; v: string | number; sub?: string }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography sx={{ fontSize: 12, color: C.muted }}>{k}</Typography>
      <Typography sx={{ fontSize: 15, fontWeight: 500, fontVariantNumeric: "tabular-nums" }} noWrap>{v}</Typography>
      {sub && <Typography sx={{ fontSize: 12, color: C.muted }} noWrap>{sub}</Typography>}
    </Box>
  );
}

function StatusDot({ s }: { s: string }) {
  const c = s === "applied" ? C.green : s === "pending" ? C.amber : C.muted;
  return <Stack direction="row" spacing={0.75} alignItems="center" sx={{ color: c, fontSize: 12.5 }}><Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: c }} /><span>{tr(s === "applied" ? "Applied" : s === "pending" ? "Waiting for review" : "Rejected")}</span></Stack>;
}

/** The uploaded release: checked, compared with the release in use, with what it affects. Apply or reject. */
function Review({ rev, admin, busy, onApply, onReject }: { rev: TemplateUpdate; admin: boolean; busy: boolean; onApply: (renames: unknown) => void; onReject: () => void }) {
  const [tab, setTab] = useState(0);
  const [kind, setKind] = useState("all");
  const [ren, setRen] = useState<Record<string, boolean>>(() => {
    const r: Record<string, boolean> = {};
    rev.diff?.renames.classes.forEach((x) => (r[`c:${x.old}`] = true));
    rev.diff?.renames.fields.forEach((x) => (r[`f:${x.layer}.${x.old}`] = true));
    return r;
  });
  const d = rev.diff!, im = rev.impact!;
  const changes = useMemo(() => d.changes.filter((c) => kind === "all" || c.kind === kind || c.severity === kind), [d, kind]);
  const pending = rev.status === "pending";
  const apply = () => {
    const classes: Record<string, string> = {}, fields: Record<string, string> = {};
    d.renames.classes.forEach((x) => { if (ren[`c:${x.old}`]) classes[x.old] = x.new; });
    d.renames.fields.forEach((x) => { if (ren[`f:${x.layer}.${x.old}`]) fields[`${x.layer}.${x.old}`] = x.new; });
    onApply({ classes, fields });
  };
  const steps = [tr("Uploaded"), tr("Read and checked"), tr("Compared"), pending ? tr("Your decision") : tr(rev.status === "applied" ? "Applied" : "Rejected")];
  return (
    <Paper sx={{ borderRadius: 3, overflow: "hidden", border: pending ? `1px solid ${C.ink}` : undefined }}>
      <Box sx={{ p: 2.5, borderBottom: `1px solid ${C.line}` }}>
        <Stack direction="row" alignItems="center" flexWrap="wrap" useFlexGap gap={2}>
          <Box sx={{ flex: 1, minWidth: 260 }}>
            <Typography sx={{ fontSize: 12, color: C.muted }}>{pending ? tr("New release — waiting for your decision") : tr("Release report")}</Typography>
            <Typography sx={{ fontSize: 16, fontWeight: 500 }}>{rev.template} <Box component="span" sx={{ fontFamily: MONO, fontSize: 13, color: C.muted }}>{rev.current_version} → {rev.new_version}</Box></Typography>
          </Box>
          {pending && admin && <>
            <Button sx={{ color: C.muted }} disabled={busy} onClick={onReject}>{d.identical ? tr("Discard") : tr("Reject")}</Button>
            <Button variant="contained" disabled={busy || d.identical} onClick={apply}>{tr("Apply — make {v} current", { v: rev.new_version })}</Button>
          </>}
        </Stack>
        <Stack direction="row" spacing={2.5} sx={{ mt: 2 }} flexWrap="wrap" useFlexGap>
          {steps.map((s, i) => (
            <Stack key={s} direction="row" spacing={0.75} alignItems="center" sx={{ fontSize: 12.5, color: i < 3 || !pending ? C.ink : C.muted }}>
              {i < 3 || !pending ? <CheckRounded sx={{ fontSize: 16, color: C.green }} /> : <Box sx={{ width: 14, height: 14, borderRadius: "50%", border: `1.5px solid ${C.line}` }} />}
              <span>{s}</span>
            </Stack>
          ))}
        </Stack>
      </Box>
      <Box sx={{ p: 2.5 }}>
        <Stack spacing={1.5}>
          <SummaryChips s={rev.summary} />
          {d.identical && <Alert severity="info">{tr("Identical to the release in use — nothing to apply.")}</Alert>}
          {d.breaking > 0 && <Alert severity="warning">{tr("{n} breaking changes: removed classes, fields or coded values, or a changed geometry or type. Layers connected to them must be connected again; drawings checked on the previous release are checked again before generating.", { n: d.breaking })}</Alert>}
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 40, "& .MuiTab-root": { minHeight: 40, textTransform: "none" } }}>
            <Tab label={`${tr("Changes")} (${d.changes.length})`} />
            <Tab label={`${tr("Possible renames")} (${d.renames.classes.length + d.renames.fields.length})`} />
            <Tab label={`${tr("Impact")} (${im.profiles.length + im.learned_rules.length + im.jobs.length})`} />
          </Tabs>
          {tab === 0 && (<>
            <TextField select size="small" sx={{ width: 260 }} label={tr("Show")} value={kind} onChange={(e) => setKind(e.target.value)}>
              <MenuItem value="all" dense>{tr("All changes")}</MenuItem>
              <MenuItem value="breaking" dense>{tr("Breaking only")}</MenuItem>
              <MenuItem value="warning" dense>{tr("To check")}</MenuItem>
              {Object.entries(d.counts).map(([k, n]) => <MenuItem key={k} value={k} dense>{KIND[k] || k} ({n})</MenuItem>)}
            </TextField>
            <Box sx={{ maxHeight: 420, overflow: "auto", border: `1px solid ${C.line}`, borderRadius: 2 }}>
              <Table size="small" stickyHeader>
                <TableHead><TableRow><TableCell /><TableCell>{tr("Change")}</TableCell><TableCell>{tr("Object")}</TableCell><TableCell>{tr("Detail")}</TableCell></TableRow></TableHead>
                <TableBody>
                  {changes.length === 0 && <TableRow><TableCell colSpan={4} sx={{ color: C.muted }}>{tr("No changes")}</TableCell></TableRow>}
                  {changes.slice(0, 1500).map((c, i) => (
                    <TableRow key={i}>
                      <TableCell><Chip size="small" label={SEV[c.severity].label} sx={{ bgcolor: SEV[c.severity].bg, color: SEV[c.severity].fg, height: 18, fontSize: 10 }} /></TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap" }}>{KIND[c.kind] || c.kind}</TableCell>
                      <TableCell sx={{ fontFamily: MONO, fontSize: 12 }}>{c.object}</TableCell>
                      <TableCell sx={{ fontSize: 12 }}>{c.detail}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Box>
          </>)}
          {tab === 1 && (
            <Box>
              <Typography sx={{ fontSize: 12.5, color: C.muted, mb: 1 }}>{tr("A class or field that disappeared while a similar one appeared. Ticked renames are carried into saved mapping profiles and learned layer rules when the release is applied; both are backed up first.")}</Typography>
              {d.renames.classes.length + d.renames.fields.length === 0 && <Typography sx={{ fontSize: 13, color: C.muted }}>{tr("None detected")}</Typography>}
              {d.renames.classes.map((x) => <FormControlLabel key={x.old} sx={{ display: "flex" }} control={<Checkbox size="small" disabled={!pending} checked={!!ren[`c:${x.old}`]} onChange={(e) => setRen({ ...ren, [`c:${x.old}`]: e.target.checked })} />}
                label={<span><b>{x.old}</b> → <b>{x.new}</b> <Box component="span" sx={{ color: C.muted, fontSize: 12 }}>({Math.round(x.shared_fields * 100)}%)</Box></span>} />)}
              {d.renames.fields.map((x) => <FormControlLabel key={`${x.layer}.${x.old}`} sx={{ display: "flex" }} control={<Checkbox size="small" disabled={!pending} checked={!!ren[`f:${x.layer}.${x.old}`]} onChange={(e) => setRen({ ...ren, [`f:${x.layer}.${x.old}`]: e.target.checked })} />}
                label={<span>{x.layer}: <b>{x.old}</b> → <b>{x.new}</b></span>} />)}
            </Box>
          )}
          {tab === 2 && (
            <Stack spacing={1}>
              <Typography sx={{ fontSize: 13.5 }}><b style={{ fontWeight: 500 }}>{tr("Saved mapping profiles")}</b> · {im.profiles.length || tr("none affected")}</Typography>
              {im.profiles.map((p) => <Typography key={p.profile} sx={{ fontSize: 12.5, color: C.muted }}>{p.profile}: {[...p.classes, ...p.fields].join(", ")}</Typography>)}
              <Typography sx={{ fontSize: 13.5 }}><b style={{ fontWeight: 500 }}>{tr("Learned layer rules")}</b> · {im.learned_rules.length || tr("none affected")}</Typography>
              <Typography sx={{ fontSize: 13.5 }}><b style={{ fontWeight: 500 }}>{tr("Open conversions")}</b> · {im.jobs.length || tr("none affected")}</Typography>
              {im.jobs.slice(0, 20).map((j) => <Typography key={j.job_id} sx={{ fontSize: 12.5, color: C.muted }}>{j.file} · {j.status}</Typography>)}
            </Stack>
          )}
        </Stack>
      </Box>
    </Paper>
  );
}
