import { useEffect, useMemo, useState } from "react";
import { Box, Button, Checkbox, Chip, Container, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, ListItemIcon, Menu, MenuItem, Paper, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from "@mui/material";
import MoreHorizRounded from "@mui/icons-material/MoreHorizRounded";
import DeleteOutlineRounded from "@mui/icons-material/DeleteOutlineRounded";
import OpenInNewRounded from "@mui/icons-material/EastRounded";
import AddRounded from "@mui/icons-material/AddRounded";
import InsertDriveFileOutlined from "@mui/icons-material/InsertDriveFileOutlined";
import { api } from "../api";
import type { AuthUser, JobState } from "../types";
import { C } from "../theme";
import { AppHeader } from "../components/AppHeader";
import { LogoMark } from "../components/Logo";

interface Props { me: AuthUser | null; mode: string; go: (r: string) => void; onLogout: () => void }

const STATUS: Record<string, { label: string; fg: string; bg: string }> = {
  completed: { label: "Delivered", fg: C.green, bg: C.greenSoft },
  validated: { label: "Validated", fg: C.cyan, bg: C.cyanSoft },
  ready: { label: "Mapping", fg: C.ink, bg: "#ECEEF1" },
  running: { label: "Working", fg: C.cyan, bg: C.cyanSoft },
  queued: { label: "Queued", fg: C.muted, bg: "#ECEEF1" },
  failed: { label: "Needs attention", fg: C.red, bg: C.redSoft },
};

const ago = (iso?: string) => {
  if (!iso) return "";
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  if (m < 60 * 24) return `${Math.round(m / 60)} h ago`;
  return new Date(iso).toLocaleDateString();
};

/** Home of the signed-in area: start a conversion, or pick up one in progress. */
export function Workspace({ me, mode, go, onLogout }: Props) {
  const [jobs, setJobs] = useState<Partial<JobState>[] | null>(null);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState<{ el: HTMLElement; id: string } | null>(null);
  const [confirm, setConfirm] = useState<string[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const nameOf = (id: string) => (jobs || []).find((j) => j.job_id === id)?.file?.name || "drawing";
  const selected = Object.keys(sel).filter((k) => sel[k]);
  const doDelete = async () => {
    if (!confirm) return;
    setDeleting(true);
    const done: string[] = [];
    for (const id of confirm) { try { await api.deleteJob(id); done.push(id); } catch { /* left in the list */ } }
    setJobs((js) => (js || []).filter((j) => !done.includes(j.job_id || "")));
    setSel((x) => { const n = { ...x }; done.forEach((id) => delete n[id]); return n; });
    setDeleting(false); setConfirm(null);
  };
  useEffect(() => { api.jobs().then(setJobs).catch(() => setJobs([])); }, []);
  const list = useMemo(() => (jobs || []).filter((j) => !q || `${j.file?.name} ${j.schema_name}`.toLowerCase().includes(q.toLowerCase())), [jobs, q]);
  const delivered = (jobs || []).filter((j) => j.status === "completed").length;
  const open = (jobs || []).filter((j) => j.status !== "completed").length;
  const first = (me?.name || "").split(" ")[0];

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppHeader me={me} mode={mode} go={go} onLogout={onLogout} active="app" />
      <Box sx={{ bgcolor: "#fff", borderBottom: `1px solid ${C.line}` }}>
        <Container maxWidth="lg" sx={{ py: { xs: 3, md: 4.5 }, display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: 3 }}>
          <Box sx={{ flex: 1, minWidth: 280 }}>
            <Typography variant="h4">{first ? `Welcome back, ${first}` : "Conversions"}</Typography>
            <Typography sx={{ color: C.muted, mt: 1, fontSize: 15, maxWidth: 560 }}>
              Upload an as-built drawing, check how its layers map to the ADM template, and download the submission package.
            </Typography>
          </Box>
          <Stack direction="row" spacing={4} sx={{ mr: 2 }}>
            {mode === "accounts" && me?.credits != null && (me.role === "owner" || me.role === "admin"
              ? <Figure label="owner account" value="Unlimited" onClick={() => go("/account")} />
              : <Figure label="credits" value={me.credits} onClick={() => go("/account")} />)}
            <Figure label="delivered" value={delivered} />
            <Figure label="in progress" value={open} />
          </Stack>
          <Button variant="contained" size="large" startIcon={<AddRounded />} onClick={() => go("/app/new")}>New conversion</Button>
        </Container>
      </Box>

      <Container maxWidth="lg" sx={{ py: 3 }}>
        {jobs && jobs.length === 0 ? (
          <Paper sx={{ p: { xs: 3, md: 6 }, textAlign: "center" }}>
            <Box sx={{ color: C.ink, display: "flex", justifyContent: "center", mb: 2 }}><LogoMark size={64} hangers /></Box>
            <Typography variant="h5">Your first conversion takes a few minutes</Typography>
            <Typography sx={{ color: C.muted, mt: 1, mb: 3, maxWidth: 520, mx: "auto" }}>
              Choose the asset type, drop the DWG or DXF exactly as the draughtsman delivered it, and follow the steps to a verified geodatabase.
            </Typography>
            <Button variant="contained" size="large" startIcon={<AddRounded />} onClick={() => go("/app/new")}>Start a conversion</Button>
          </Paper>
        ) : (
          <Paper sx={{ overflow: "hidden" }}>
            <Stack direction="row" alignItems="center" sx={{ px: 2.5, py: 1.75, borderBottom: `1px solid ${C.line}` }}>
              <Typography variant="h5" sx={{ flex: 1 }}>Recent conversions</Typography>
              {selected.length > 0 && <Stack direction="row" spacing={1} alignItems="center" sx={{ mr: 2 }}>
                <Typography sx={{ fontSize: 13, color: C.muted }}>{selected.length} selected</Typography>
                <Button size="small" onClick={() => setSel({})}>Clear</Button>
                <Button size="small" color="error" variant="outlined" startIcon={<DeleteOutlineRounded />} onClick={() => setConfirm(selected)}>Delete</Button>
              </Stack>}
              <TextField placeholder="Find a drawing" value={q} onChange={(e) => setQ(e.target.value)} sx={{ width: 240 }} />
            </Stack>
            <Table>
              <TableHead><TableRow>
                <TableCell padding="checkbox" sx={{ pl: 1 }}><Checkbox size="small" inputProps={{ "aria-label": "Select all drawings" }}
                  checked={list.length > 0 && list.every((j) => sel[j.job_id || ""])}
                  indeterminate={selected.length > 0 && !list.every((j) => sel[j.job_id || ""])}
                  onChange={(e) => { const n = { ...sel }; list.forEach((j) => { n[j.job_id || ""] = e.target.checked; }); setSel(n); }} /></TableCell>
                <TableCell sx={{ pl: 0.5 }}>Drawing</TableCell><TableCell>Ready</TableCell><TableCell>Status</TableCell>
                <TableCell>Last change</TableCell><TableCell />
              </TableRow></TableHead>
              <TableBody>
                {list.map((j) => {
                  const s = STATUS[j.status || ""] || STATUS.ready;
                  return (
                    <TableRow key={j.job_id} hover sx={{ cursor: "pointer", "& td": { py: 1.4 } }} onClick={() => { window.location.hash = `job=${j.job_id}`; }}>
                      <TableCell padding="checkbox" sx={{ pl: 1 }} onClick={(e) => e.stopPropagation()}>
                        <Checkbox size="small" checked={!!sel[j.job_id || ""]} inputProps={{ "aria-label": `Select ${j.file?.name}` }}
                          onChange={(e) => setSel({ ...sel, [j.job_id || ""]: e.target.checked })} />
                      </TableCell>
                      <TableCell sx={{ pl: 0.5 }}>
                        <Stack direction="row" spacing={1.25} alignItems="center">
                          <InsertDriveFileOutlined sx={{ color: C.muted, fontSize: 20 }} />
                          <Box sx={{ minWidth: 0 }}>
                            <Typography sx={{ fontWeight: 600, fontSize: 13.5 }} noWrap>{j.file?.name}{j.extra_files?.length ? <Box component="span" sx={{ color: C.muted, fontWeight: 400 }}> + {j.extra_files.length} sheet{j.extra_files.length === 1 ? "" : "s"}</Box> : null}</Typography>
                            <Typography variant="caption" noWrap component="div" sx={{ maxWidth: 420 }}>{j.schema_name}</Typography>
                          </Box>
                        </Stack>
                      </TableCell>
                      <TableCell sx={{ minWidth: 170 }}><Readiness msg={j.message || ""} status={j.status || ""} /></TableCell>
                      <TableCell><Chip size="small" label={s.label} sx={{ color: s.fg, bgcolor: s.bg }} /></TableCell>
                      <TableCell sx={{ color: C.muted, whiteSpace: "nowrap" }}>{ago(j.updated_at || j.created_at)}</TableCell>
                      <TableCell align="right" sx={{ whiteSpace: "nowrap" }}>
                        <Button size="small">{j.status === "completed" ? "Open" : "Continue"}</Button>
                        <IconButton aria-label={`Actions for ${j.file?.name}`} sx={{ ml: 0.5 }}
                          onClick={(e) => { e.stopPropagation(); setMenu({ el: e.currentTarget, id: j.job_id || "" }); }}><MoreHorizRounded /></IconButton>
                      </TableCell>
                    </TableRow>
                  );
                })}
                {jobs === null && <TableRow><TableCell colSpan={6} sx={{ pl: 2.5, color: C.muted }}>Loading…</TableCell></TableRow>}
                {jobs && jobs.length > 0 && !list.length && <TableRow><TableCell colSpan={6} sx={{ pl: 2.5, color: C.muted }}>No drawing matches “{q}”.</TableCell></TableRow>}
              </TableBody>
            </Table>
            <Menu anchorEl={menu?.el} open={!!menu} onClose={() => setMenu(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}>
              <MenuItem onClick={() => { const id = menu?.id; setMenu(null); window.location.hash = `job=${id}`; }}>
                <ListItemIcon><OpenInNewRounded fontSize="small" /></ListItemIcon>Open</MenuItem>
              <MenuItem onClick={() => { const id = menu?.id || ""; setMenu(null); setConfirm([id]); }} sx={{ color: C.red }}>
                <ListItemIcon sx={{ color: C.red }}><DeleteOutlineRounded fontSize="small" /></ListItemIcon>Delete</MenuItem>
            </Menu>
            <Dialog open={!!confirm} onClose={() => !deleting && setConfirm(null)} maxWidth="xs" fullWidth>
              <DialogTitle sx={{ fontSize: 18 }}>{confirm && confirm.length === 1 ? `Delete ${nameOf(confirm[0])}` : `Delete ${confirm?.length} drawings`}</DialogTitle>
              <DialogContent><Typography sx={{ color: C.muted, fontSize: 14 }}>The drawing, its review edits and its packages are removed for good. This cannot be undone.</Typography></DialogContent>
              <DialogActions sx={{ px: 3, pb: 2.5 }}>
                <Button onClick={() => setConfirm(null)} disabled={deleting}>Keep</Button>
                <Button variant="contained" color="error" onClick={doDelete} disabled={deleting}>{deleting ? "Deleting…" : "Delete"}</Button>
              </DialogActions>
            </Dialog>
            <Typography variant="caption" component="div" sx={{ px: 2.5, py: 1.25, borderTop: `1px solid ${C.line}` }}>
              Drawings and outputs are kept for 72 hours, then deleted — download your packages before then.
            </Typography>
          </Paper>
        )}
      </Container>
    </Box>
  );
}

function Readiness({ msg, status }: { msg: string; status: string }) {
  const m = /(\d[\d,]*) features ready, (\d[\d,]*) failed/.exec(msg) || /(\d[\d,]*) features, verification/.exec(msg);
  if (!m) return <Typography variant="caption" sx={{ color: C.muted }}>{status === "completed" ? "delivered" : "—"}</Typography>;
  const ok = Number(m[1].replace(/,/g, "")), bad = Number((m[2] || "0").replace(/,/g, "")), tot = ok + bad || 1;
  return (
    <Box>
      <Typography variant="caption" sx={{ fontWeight: 600, color: C.ink }}>{ok.toLocaleString()} / {(ok + bad).toLocaleString()}</Typography>
      <Box sx={{ mt: 0.4, height: 5, borderRadius: 3, bgcolor: "#ECEEF1", overflow: "hidden", width: 140 }}>
        <Box sx={{ width: `${(ok / tot) * 100}%`, height: 5, bgcolor: bad ? C.amber : C.green }} />
      </Box>
    </Box>
  );
}

function Figure({ label, value, onClick }: { label: string; value: number | string; onClick?: () => void }) {
  return (
    <Box onClick={onClick} sx={{ cursor: onClick ? "pointer" : "default", textAlign: "right" }}>
      <Typography sx={{ fontSize: 28, fontWeight: 600, lineHeight: 1, color: C.ink }}>{value}</Typography>
      <Typography variant="caption">{label}</Typography>
    </Box>
  );
}
