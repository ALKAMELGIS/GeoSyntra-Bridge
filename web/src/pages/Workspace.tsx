import { useEffect, useMemo, useState } from "react";
import { Alert, Box, Button, ButtonBase, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, InputBase, ListItemIcon, Menu, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, Typography } from "@mui/material";
import MoreHorizRounded from "@mui/icons-material/MoreHorizRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import OpenInNewRounded from "@mui/icons-material/EastRounded";
import AddRounded from "@mui/icons-material/AddRounded";
import SearchRounded from "@mui/icons-material/SearchRounded";
import { api } from "../api";
import type { AuthUser, JobState } from "../types";
import { C } from "../theme";
import { AppHeader } from "../components/AppHeader";
import { LogoMark } from "../components/Logo";
import { tr } from "../i18n";

interface Props { me: AuthUser | null; mode: string; go: (r: string) => void; onLogout: () => void }
type Job = Partial<JobState>;
type Filter = "all" | "open" | "attention" | "done";

const STEPS = ["Upload", "Layers", "Review", "Deliver"];
const MONO = '"Geist Mono", ui-monospace, monospace';

/** Where a job stands: index of the current step (4 = delivered). */
const stepOf = (j: Job) => {
  if (j.status === "completed") return 4;
  if (j.status === "validated") return 3;
  if (j.status === "ready") return 1;
  if (j.status === "failed") return j.stage === "generate" ? 3 : j.stage === "validate" ? 2 : j.stage === "discovery" ? 0 : 1;
  return j.stage === "discovery" || !j.stage ? 0 : 1;
};
const counts = (msg = "") => {
  const m = /(\d[\d,]*) features ready, (\d[\d,]*) failed/.exec(msg) || /(\d[\d,]*) features, verification/.exec(msg);
  if (!m) return null;
  return { ok: Number(m[1].replace(/,/g, "")), bad: Number((m[2] || "0").replace(/,/g, "")) };
};
const needsYou = (j: Job) => j.status === "failed" || (j.status === "validated" && (counts(j.message)?.bad || 0) > 0);
const STATUS = (j: Job) => {
  if (j.status === "completed") return { label: tr("Delivered"), c: C.green };
  if (j.status === "failed") return { label: tr("Needs attention"), c: C.red };
  if (needsYou(j)) return { label: tr("{n} to fix", { n: counts(j.message)?.bad || 0 }), c: C.amber };
  if (j.status === "validated") return { label: tr("Ready to deliver"), c: C.cyan };
  if (j.status === "queued" || j.status === "running") return { label: tr("Working"), c: C.cyan };
  return { label: tr("Mapping layers"), c: C.muted };
};
const ext = (name = "") => (name.split(".").pop() || "").toUpperCase().slice(0, 4);
const ago = (iso?: string) => {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return tr("just now");
  if (m < 60) return tr("{n} min ago", { n: m });
  if (m < 60 * 24) return tr("{n} h ago", { n: Math.round(m / 60) });
  if (m < 60 * 48) return tr("Yesterday");
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
};
const sameMonth = (iso: string | undefined, d: Date) => !!iso && new Date(iso).getMonth() === d.getMonth() && new Date(iso).getFullYear() === d.getFullYear();

/** Home of the signed-in area: what was delivered, what waits on you, and every drawing in progress. */
export function Workspace({ me, mode, go, onLogout }: Props) {
  // the last list shows at once while the fresh one loads
  const [jobs, setJobs] = useState<Job[] | null>(() => { try { return JSON.parse(localStorage.getItem("gsb_jobs_cache") || "null"); } catch { return null; } });
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<{ el: HTMLElement; id: string } | null>(null);
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const nameOf = (id: string) => (jobs || []).find((j) => j.job_id === id)?.file?.name || "drawing";
  const selected = Object.keys(sel).filter((k) => sel[k]);
  const open = (id?: string) => {
    const j = (jobs || []).find((x) => x.job_id === id);
    try { if (j) sessionStorage.setItem("gsb_open_job", JSON.stringify(j)); } catch { /* ignore */ }
    window.location.hash = `job=${id}`;
  };
  const doDelete = async () => {
    if (!confirm) return;
    setDeleting(true);
    const done: string[] = [];
    for (const id of confirm) { try { await api.deleteJob(id); done.push(id); } catch { /* left in the list */ } }
    setJobs((js) => (js || []).filter((j) => !done.includes(j.job_id || "")));
    setSel((x) => { const n = { ...x }; done.forEach((id) => delete n[id]); return n; });
    setDeleting(false); setConfirm(null);
  };
  useEffect(() => {
    let t = 0;
    const load = () => api.jobs().then((js) => {
      setJobs(js); try { localStorage.setItem("gsb_jobs_cache", JSON.stringify(js.slice(0, 30))); } catch { /* ignore */ }
      if (js.some((j) => j.status === "queued" || j.status === "running")) t = window.setTimeout(load, 4000);   // live while something works
    }).catch(() => setJobs((x) => x || []));
    load();
    return () => clearTimeout(t);
  }, []);
  const [openErr, setOpenErr] = useState<string | null>(() => { try { const m = sessionStorage.getItem("gsb_open_error"); sessionStorage.removeItem("gsb_open_error"); return m; } catch { return null; } });

  const all = jobs || [];
  const groups: Record<Filter, Job[]> = useMemo(() => ({
    all,
    open: all.filter((j) => j.status !== "completed" && !needsYou(j)),
    attention: all.filter(needsYou),
    done: all.filter((j) => j.status === "completed"),
  }), [jobs]);
  const list = useMemo(() => groups[filter].filter((j) => !q || `${j.file?.name} ${j.schema_name}`.toLowerCase().includes(q.toLowerCase())), [groups, filter, q]);

  const now = new Date(), prev = new Date(now.getFullYear(), now.getMonth() - 1, 15);
  const dThis = groups.done.filter((j) => sameMonth(j.updated_at, now)).length;
  const dPrev = groups.done.filter((j) => sameMonth(j.updated_at, prev)).length;
  const feats = groups.done.reduce((s, j) => s + (counts(j.message)?.ok || 0), 0);
  const staff = me && (me.role === "owner" || me.role === "admin");
  const month = now.toLocaleDateString(undefined, { month: "long", year: "numeric" });

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppHeader me={me} mode={mode} go={go} onLogout={onLogout} active="app" />
      <Box sx={{ maxWidth: 1520, mx: "auto", px: { xs: 2, md: 4 }, py: 3, display: "flex", flexDirection: "column", gap: 3 }}>

        {openErr && <Alert severity="error" onClose={() => setOpenErr(null)}>{openErr}</Alert>}
        {/* title, search, primary action */}
        <Stack direction="row" alignItems="center" flexWrap="wrap" useFlexGap gap={1.5}>
          <Typography component="h1" sx={{ fontSize: 22, fontWeight: 500, letterSpacing: "-0.01em" }}>{tr("Conversions")}</Typography>
          <Typography sx={{ fontSize: 13, color: C.muted }}>{month}</Typography>
          <Box sx={{ flex: 1 }} />
          <Stack direction="row" alignItems="center" spacing={1} sx={{ height: 36, px: 1.25, width: 280, maxWidth: "100%", border: `1px solid ${C.line}`, borderRadius: 2, bgcolor: "#fff", color: C.muted }}>
            <SearchRounded sx={{ fontSize: 18 }} />
            <InputBase value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("Search drawings")} inputProps={{ "aria-label": tr("Search drawings") }} sx={{ flex: 1, fontSize: 13 }} />
          </Stack>
          <Button variant="contained" startIcon={<AddRounded />} onClick={() => go("/app/new")} sx={{ height: 36, borderRadius: 2, fontWeight: 500, fontSize: 14 }}>{tr("New conversion")}</Button>
        </Stack>

        {/* figures */}
        {!!jobs?.length && <Box sx={{ display: "grid", gridTemplateColumns: { xs: "repeat(2, minmax(0,1fr))", md: "repeat(4, minmax(0,1fr))" }, gap: 1.5 }}>
          <Stat label={tr("Delivered this month")} value={dThis}
            note={dPrev || dThis ? tr("{n} vs last month", { n: `${dThis - dPrev >= 0 ? "+" : ""}${dThis - dPrev}` }) : tr("No deliveries yet")} good={dThis >= dPrev && dThis > 0} />
          <Stat label={tr("Waiting on you")} value={groups.attention.length} note={groups.attention.length ? tr("Open the list on the right") : tr("Nothing to fix")} warn={groups.attention.length > 0} />
          <Stat label={tr("Features delivered")} value={feats.toLocaleString()} note={groups.done.length === 1 ? tr("in 1 geodatabase") : tr("in {n} geodatabases", { n: groups.done.length })} />
          {mode === "accounts" && me?.credits != null
            ? <Stat label={tr("Credits")} value={staff ? tr("Unlimited") : me.credits.toLocaleString()} note={tr("Top up or change plan")} onClick={() => go("/account")} />
            : <Stat label={tr("In progress")} value={groups.open.length} note={tr("Mapping or working")} />}
        </Box>}

        {jobs && jobs.length === 0 ? (
          <Paper sx={{ p: { xs: 3, md: 6 }, textAlign: "center", borderRadius: 3 }}>
            <Box sx={{ color: C.ink, display: "flex", justifyContent: "center", mb: 2 }}><LogoMark size={64} hangers /></Box>
            <Typography variant="h5">{tr("Your first conversion takes a few minutes")}</Typography>
            <Typography sx={{ color: C.muted, mt: 1, mb: 3, maxWidth: 480, mx: "auto" }}>{tr("Drop a DWG or DXF, confirm the layers, fix what the check flags, download the geodatabase.")}</Typography>
            <Button variant="contained" size="large" startIcon={<AddRounded />} onClick={() => go("/app/new")}>{tr("Start a conversion")}</Button>
          </Paper>
        ) : (
          <Box sx={{ display: "flex", flexWrap: "wrap", gap: 3, alignItems: "flex-start" }}>
            {/* table */}
            <Paper sx={{ flex: "999 1 640px", minWidth: 0, overflow: "hidden", borderRadius: 3 }}>
              <Stack direction="row" alignItems="center" flexWrap="wrap" useFlexGap gap={1} sx={{ px: 2, py: 1.5, borderBottom: `1px solid ${C.line}` }}>
                {([["all", tr("All")], ["attention", tr("Needs you")], ["open", tr("In progress")], ["done", tr("Delivered")]] as [Filter, string][]).map(([k, label]) => (
                  <ButtonBase key={k} onClick={() => setFilter(k)} aria-pressed={filter === k}
                    sx={{ height: 30, px: 1.5, gap: 0.75, borderRadius: 2, fontSize: 13, border: `1px solid ${filter === k ? C.ink : C.line}`,
                          bgcolor: filter === k ? C.ink : "#fff", color: filter === k ? "#F5F6F8" : C.ink }}>
                    {label}<Box component="span" sx={{ fontFamily: MONO, fontSize: 12, opacity: 0.7 }}>{groups[k].length}</Box>
                  </ButtonBase>
                ))}
                <Box sx={{ flex: 1 }} />
                {selected.length > 0
                  ? <Stack direction="row" spacing={1} alignItems="center">
                      <Typography sx={{ fontSize: 13, color: C.muted }}>{selected.length} {tr("selected")}</Typography>
                      <Button size="small" onClick={() => setSel({})}>{tr("Clear")}</Button>
                      <Button size="small" color="error" variant="outlined" startIcon={<DeleteOutlineRounded />} onClick={() => setConfirm(selected)}>{tr("Delete")}</Button>
                    </Stack>
                  : <Typography sx={{ fontSize: 12, color: C.muted }}>{tr("Files are deleted after 72 hours")}</Typography>}
              </Stack>
              <Box sx={{ overflowX: "auto" }}>
                <Table sx={{ minWidth: 760, "& td, & th": { borderColor: "#EDEFF2" } }}>
                  <TableHead><TableRow sx={{ "& th": { bgcolor: "#fff", fontWeight: 500, fontSize: 12 } }}>
                    <TableCell padding="checkbox" sx={{ pl: 1 }}><Checkbox size="small" inputProps={{ "aria-label": "Select all drawings" }}
                      checked={list.length > 0 && list.every((j) => sel[j.job_id || ""])}
                      indeterminate={selected.length > 0 && !list.every((j) => sel[j.job_id || ""])}
                      onChange={(e) => { const n = { ...sel }; list.forEach((j) => { n[j.job_id || ""] = e.target.checked; }); setSel(n); }} /></TableCell>
                    <TableCell sx={{ pl: 0.5 }}>{tr("Drawing")}</TableCell><TableCell>{tr("Progress")}</TableCell><TableCell>{tr("Status")}</TableCell>
                    <TableCell align="right">{tr("Features")}</TableCell><TableCell align="right">{tr("Updated")}</TableCell><TableCell />
                  </TableRow></TableHead>
                  <TableBody>
                    {list.map((j) => {
                      const s = STATUS(j), at = stepOf(j), n = counts(j.message);
                      return (
                        <TableRow key={j.job_id} hover sx={{ cursor: "pointer", "& td": { py: 1.4 } }} onClick={() => open(j.job_id)}>
                          <TableCell padding="checkbox" sx={{ pl: 1 }} onClick={(e) => e.stopPropagation()}>
                            <Checkbox size="small" checked={!!sel[j.job_id || ""]} inputProps={{ "aria-label": `Select ${j.file?.name}` }}
                              onChange={(e) => setSel({ ...sel, [j.job_id || ""]: e.target.checked })} />
                          </TableCell>
                          <TableCell sx={{ pl: 0.5 }}>
                            <Stack direction="row" spacing={1.25} alignItems="center">
                              <Box sx={{ width: 32, height: 32, flex: "none", borderRadius: 2, bgcolor: "#EEF0F3", display: "grid", placeItems: "center", fontFamily: MONO, fontSize: 10, color: C.muted }}>{ext(j.file?.name)}</Box>
                              <Box sx={{ minWidth: 0 }}>
                                <Typography sx={{ fontWeight: 500, fontSize: 13.5 }} noWrap>{j.file?.name}{j.extra_files?.length ? <Box component="span" sx={{ color: C.muted, fontWeight: 400 }}> + {j.extra_files.length} sheet{j.extra_files.length === 1 ? "" : "s"}</Box> : null}</Typography>
                                <Typography variant="caption" noWrap component="div" sx={{ maxWidth: 380 }}>{(j.schema_name || "").split(" — ")[0]}</Typography>
                              </Box>
                            </Stack>
                          </TableCell>
                          <TableCell>
                            <Stack direction="row" spacing={1} alignItems="center">
                              <Stack direction="row" spacing={0.4}>
                                {STEPS.map((t, i) => <Box key={t} title={t} sx={{ width: 18, height: 4, borderRadius: 2, bgcolor: i < at ? C.ink : i === at ? C.cyan : C.line }} />)}
                              </Stack>
                              <Typography sx={{ fontSize: 12, color: C.muted }}>{at >= 4 ? tr("Delivered") : tr(STEPS[at] || "")}</Typography>
                            </Stack>
                          </TableCell>
                          <TableCell>
                            <Stack direction="row" spacing={0.75} alignItems="center" sx={{ color: s.c, fontSize: 12.5 }}>
                              <Box sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: s.c }} /><span>{s.label}</span>
                            </Stack>
                          </TableCell>
                          <TableCell align="right" sx={{ fontFamily: MONO, fontSize: 12 }}>{n ? (n.ok + n.bad).toLocaleString() : "—"}</TableCell>
                          <TableCell align="right" sx={{ color: C.muted, fontSize: 12, whiteSpace: "nowrap" }}>{ago(j.updated_at || j.created_at)}</TableCell>
                          <TableCell align="right" sx={{ whiteSpace: "nowrap", pr: 1 }}>
                            <IconButton aria-label={`Actions for ${j.file?.name}`}
                              onClick={(e) => { e.stopPropagation(); setMenu({ el: e.currentTarget, id: j.job_id || "" }); }}><MoreHorizRounded /></IconButton>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                    {jobs === null && [0, 1, 2].map((i) => (
                      <TableRow key={i}><TableCell colSpan={7} sx={{ py: 2.25 }}>
                        <Box sx={{ height: 12, width: `${60 - i * 12}%`, borderRadius: 1, bgcolor: "#EEF0F3" }} />
                      </TableCell></TableRow>
                    ))}
                    {jobs && jobs.length > 0 && !list.length && <TableRow><TableCell colSpan={7} sx={{ pl: 2.5, color: C.muted }}>{q ? tr("No drawing matches “{q}”", { q }) : tr("Nothing here")}</TableCell></TableRow>}
                  </TableBody>
                </Table>
              </Box>
            </Paper>

            {/* waiting on you */}
            <Paper sx={{ flex: "1 1 280px", borderRadius: 3, p: 2.5, display: "flex", flexDirection: "column", gap: 1.5 }}>
              <Typography component="h2" sx={{ fontSize: 14, fontWeight: 500 }}>{tr("Needs you")}</Typography>
              {groups.attention.length === 0 && <Typography sx={{ fontSize: 13, color: C.muted }}>{tr("Nothing waiting. Every open drawing is moving.")}</Typography>}
              {groups.attention.slice(0, 6).map((j) => {
                const n = counts(j.message);
                return (
                  <ButtonBase key={j.job_id} onClick={() => open(j.job_id)} sx={{ justifyContent: "flex-start", textAlign: "left", gap: 1.25, alignItems: "flex-start", borderRadius: 2, p: 0.75, mx: -0.75, "&:hover": { bgcolor: "#F5F6F8" } }}>
                    <Box sx={{ width: 6, height: 6, mt: 0.9, flex: "none", borderRadius: "50%", bgcolor: j.status === "failed" ? C.red : C.amber }} />
                    <Box sx={{ minWidth: 0 }}>
                      <Typography sx={{ fontSize: 13.5, fontWeight: 500 }} noWrap>{j.file?.name}</Typography>
                      <Typography sx={{ fontSize: 12, color: C.muted }}>
                        {j.status === "failed" ? (j.error?.message || j.message || tr("Stopped — open to see why")).slice(0, 80) : tr("{n} features fail the check", { n: n?.bad || 0 })}
                      </Typography>
                    </Box>
                  </ButtonBase>
                );
              })}
            </Paper>
          </Box>
        )}

        <Menu anchorEl={menu?.el} open={!!menu} onClose={() => setMenu(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
          <MenuItem onClick={() => { const id = menu?.id; setMenu(null); open(id); }}>
            <ListItemIcon><OpenInNewRounded fontSize="small" /></ListItemIcon>{tr("Open")}</MenuItem>
          <MenuItem onClick={() => { const id = menu?.id || ""; setMenu(null); setConfirm([id]); }} sx={{ color: C.red }}>
            <ListItemIcon sx={{ color: C.red }}><DeleteOutlineRounded fontSize="small" /></ListItemIcon>{tr("Delete")}</MenuItem>
        </Menu>
        <Dialog open={!!confirm} onClose={() => !deleting && setConfirm(null)} maxWidth="xs" fullWidth>
          <DialogTitle sx={{ fontSize: 18 }}>{confirm && confirm.length === 1 ? tr("Delete {name}", { name: nameOf(confirm[0]) }) : tr("Delete {n} drawings", { n: confirm?.length || 0 })}</DialogTitle>
          <DialogContent><Typography sx={{ color: C.muted, fontSize: 14 }}>{tr("The drawing, its review edits and its packages are removed for good. This cannot be undone.")}</Typography></DialogContent>
          <DialogActions sx={{ px: 3, pb: 2.5 }}>
            <Button onClick={() => setConfirm(null)} disabled={deleting}>{tr("Keep")}</Button>
            <Button variant="contained" color="error" onClick={doDelete} disabled={deleting}>{deleting ? tr("Deleting…") : tr("Delete")}</Button>
          </DialogActions>
        </Dialog>
      </Box>
    </Box>
  );
}

function Stat({ label, value, note, good, warn, onClick }: { label: string; value: number | string; note: string; good?: boolean; warn?: boolean; onClick?: () => void }) {
  return (
    <Paper component={onClick ? ButtonBase : "div"} onClick={onClick}
      sx={{ px: 2.5, py: 2, borderRadius: 3, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 0.5, textAlign: "left", "&:hover": onClick ? { borderColor: C.ink } : undefined }}>
      <Typography sx={{ fontSize: 13, color: C.muted }}>{label}</Typography>
      <Typography sx={{ fontSize: 28, fontWeight: 500, lineHeight: 1.15, fontVariantNumeric: "tabular-nums", color: warn ? C.amber : C.ink }}>{value}</Typography>
      <Typography sx={{ fontSize: 12, color: good ? C.green : C.muted }}>{note}</Typography>
    </Paper>
  );
}
