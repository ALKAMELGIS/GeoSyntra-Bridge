import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel,
  LinearProgress, MenuItem, Stack, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, TextField, Typography,
} from "@mui/material";
import UpdateOutlined from "@mui/icons-material/UpdateOutlined";
import { api } from "../api";
import type { Capabilities, SchemaDetail, TemplateUpdate, TemplateVersion } from "../types";
import { C } from "../theme";

const SEV: Record<string, { bg: string; fg: string; label: string }> = {
  breaking: { bg: C.redSoft, fg: C.red, label: "breaking" },
  warning: { bg: C.amberSoft, fg: C.amber, label: "check" },
  info: { bg: C.blueSoft, fg: C.blue, label: "info" },
};
const KIND: Record<string, string> = {
  class_added: "Class added", class_removed: "Class removed", geometry_changed: "Geometry changed", z_changed: "Z changed",
  dataset_changed: "Dataset changed", field_added: "Field added", field_removed: "Field removed",
  field_type_changed: "Field type", field_length_changed: "Text length", field_nullable_changed: "Nullability",
  field_domain_changed: "Field domain", field_default_changed: "Default value", field_alias_changed: "Alias",
  domain_added: "Domain added", domain_removed: "Domain removed", values_added: "Values added",
  values_removed: "Values removed", values_relabelled: "Values relabelled", range_changed: "Range changed",
  topology_added: "Topology rule added", topology_removed: "Topology rule removed", crs_changed: "Coordinate system",
  label_rule_dropped: "Label rule dropped",
};

interface Props { schema: SchemaDetail; caps: Capabilities | null; onUpdated: () => void; onError: (m: string) => void }

/** The municipality publishes a new template: upload it, review what changed and what it affects, then apply. */
export function TemplateUpdates({ schema, caps, onUpdated, onError }: Props) {
  const [open, setOpen] = useState(false);
  const [list, setList] = useState<{ current: any; updates: TemplateUpdate[]; versions: TemplateVersion[] } | null>(null);
  const [sel, setSel] = useState<TemplateUpdate | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState(0);
  const [kind, setKind] = useState("all");
  const [renames, setRenames] = useState<Record<string, boolean>>({});
  const fileRef = useRef<HTMLInputElement>(null);
  const admin = caps?.admin ?? true;

  const load = () => api.templateUpdates(schema.schema_id).then(setList).catch((e) => onError(e.message));
  useEffect(() => { if (open) load(); }, [open, schema.schema_id]);
  const pending = list?.updates.find((u) => u.status === "pending");

  const show = async (u: TemplateUpdate) => {
    const full = await api.templateUpdate(schema.schema_id, u.update_id);
    setSel(full); setTab(0); setKind("all");
    const r: Record<string, boolean> = {};
    full.diff?.renames.classes.forEach((x) => (r[`c:${x.old}`] = true));
    full.diff?.renames.fields.forEach((x) => (r[`f:${x.layer}.${x.old}`] = true));
    setRenames(r);
  };

  const upload = async (f: File) => {
    setBusy(true);
    try { const u = await api.stageTemplate(schema.schema_id, f); await load(); await show(u); }
    catch (e: any) { onError(e.message); }
    finally { setBusy(false); }
  };

  const apply = async () => {
    if (!sel) return;
    const cls: Record<string, string> = {}; const flds: Record<string, string> = {};
    sel.diff?.renames.classes.forEach((x) => { if (renames[`c:${x.old}`]) cls[x.old] = x.new; });
    sel.diff?.renames.fields.forEach((x) => { if (renames[`f:${x.layer}.${x.old}`]) flds[`${x.layer}.${x.old}`] = x.new; });
    setBusy(true);
    try { await api.applyTemplate(schema.schema_id, sel.update_id, { classes: cls, fields: flds }); setSel(null); await load(); onUpdated(); }
    catch (e: any) { onError(e.message); }
    finally { setBusy(false); }
  };

  const changes = useMemo(() => (sel?.diff?.changes || []).filter((c) => kind === "all" || c.kind === kind || c.severity === kind), [sel, kind]);

  return (
    <>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 1 }}>
        <Typography variant="caption" color="text.secondary" sx={{ flex: 1, fontFamily: "monospace" }}>
          {schema.source_template || "no template"} · v{schema.version}
        </Typography>
        <Button size="small" startIcon={<UpdateOutlined />} onClick={() => setOpen(true)}>Template versions</Button>
      </Stack>
      <Dialog open={open} onClose={() => { setOpen(false); setSel(null); }} maxWidth="lg" fullWidth>
        <DialogTitle>Template updates — {schema.display_name}</DialogTitle>
        <DialogContent dividers>
          {busy && <LinearProgress sx={{ mb: 1 }} />}
          {!sel && list && (
            <Stack spacing={2}>
              <Box>
                <Typography variant="body2"><b>Current:</b> {list.current?.template} · version {list.current?.version}
                  {list.current?.applied_at ? ` · applied ${list.current.applied_at.slice(0, 16).replace("T", " ")} by ${list.current.applied_by}` : ""}</Typography>
                <Typography variant="caption" color="text.secondary" component="div">
                  Upload the new template geodatabase published by the municipality (the .gdb folder zipped). You get a change report and
                  the impact on saved profiles, learned rules and open jobs; nothing changes until it is applied, and every replaced
                  version is archived and can be restored.
                </Typography>
                <input ref={fileRef} type="file" accept=".zip" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload(f); }} />
                <Button variant="contained" size="small" sx={{ mt: 1 }} disabled={!admin || busy || !!pending} onClick={() => fileRef.current?.click()}>
                  Upload new template (.gdb.zip)
                </Button>
                {!admin && <Typography variant="caption" sx={{ ml: 1, color: C.amber }}>administrators only</Typography>}
                {pending && <Typography variant="caption" sx={{ ml: 1, color: C.amber }}>an update is waiting for a decision</Typography>}
              </Box>
              <Box>
                <Typography variant="subtitle2">Uploaded templates</Typography>
                <Table size="small">
                  <TableHead><TableRow><TableCell>Template</TableCell><TableCell>Version</TableCell><TableCell>Changes</TableCell>
                    <TableCell>Status</TableCell><TableCell>Uploaded</TableCell><TableCell /></TableRow></TableHead>
                  <TableBody>
                    {list.updates.length === 0 && <TableRow><TableCell colSpan={6} sx={{ color: C.muted }}>none yet</TableCell></TableRow>}
                    {list.updates.map((u) => (
                      <TableRow key={u.update_id} hover>
                        <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{u.template}</TableCell>
                        <TableCell>{u.current_version} → {u.new_version}</TableCell>
                        <TableCell>{u.summary.identical ? "identical" : <SummaryChips s={u.summary} />}</TableCell>
                        <TableCell><Chip size="small" label={u.status} color={u.status === "applied" ? "success" : u.status === "pending" ? "warning" : "default"} /></TableCell>
                        <TableCell sx={{ fontSize: 12 }}>{u.uploaded_at.slice(0, 16).replace("T", " ")} · {u.uploaded_by}</TableCell>
                        <TableCell><Button size="small" onClick={() => show(u)}>Report</Button></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
              <Box>
                <Typography variant="subtitle2">Archived versions</Typography>
                <Table size="small">
                  <TableHead><TableRow><TableCell>Version</TableCell><TableCell>Template</TableCell><TableCell>Classes</TableCell>
                    <TableCell>Archived</TableCell><TableCell /></TableRow></TableHead>
                  <TableBody>
                    {list.versions.length === 0 && <TableRow><TableCell colSpan={5} sx={{ color: C.muted }}>none</TableCell></TableRow>}
                    {list.versions.map((v) => (
                      <TableRow key={v.folder}>
                        <TableCell>{v.version}</TableCell>
                        <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{v.template}</TableCell>
                        <TableCell>{v.layers}</TableCell>
                        <TableCell sx={{ fontSize: 12 }}>{v.archived_at.slice(0, 16).replace("T", " ")} · {v.reason}</TableCell>
                        <TableCell><Button size="small" disabled={!admin || busy} onClick={async () => {
                          setBusy(true);
                          try { await api.restoreTemplate(schema.schema_id, v.folder); await load(); onUpdated(); }
                          catch (e: any) { onError(e.message); } finally { setBusy(false); }
                        }}>Restore</Button></TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Box>
            </Stack>
          )}
          {sel && sel.diff && sel.impact && (
            <Stack spacing={1.5}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                <Typography variant="body2"><b>{sel.template}</b> · version {sel.current_version} → <b>{sel.new_version}</b></Typography>
                <SummaryChips s={sel.summary} />
              </Stack>
              {sel.summary.identical && <Alert severity="info" sx={{ py: 0 }}>Identical to the current template — nothing to apply.</Alert>}
              {sel.diff.breaking > 0 && (
                <Alert severity="warning" sx={{ py: 0 }}>
                  {sel.diff.breaking} breaking change(s): removed classes / fields / coded values, or a changed geometry or type.
                  CAD layers mapped to them must be re-mapped; jobs validated on the previous version are validated again before generating.
                </Alert>)}
              <Tabs value={tab} onChange={(_, v) => setTab(v)}>
                <Tab label={`Changes (${sel.diff.changes.length})`} />
                <Tab label={`Possible renames (${sel.diff.renames.classes.length + sel.diff.renames.fields.length})`} />
                <Tab label={`Impact (${sel.impact.profiles.length + sel.impact.learned_rules.length + sel.impact.jobs.length})`} />
              </Tabs>
              {tab === 0 && (
                <>
                  <TextField select size="small" sx={{ width: 260 }} label="Show" value={kind} onChange={(e) => setKind(e.target.value)}>
                    <MenuItem value="all" dense>All changes</MenuItem>
                    <MenuItem value="breaking" dense>Breaking only</MenuItem>
                    <MenuItem value="warning" dense>To check</MenuItem>
                    <Divider />
                    {Object.entries(sel.diff.counts).map(([k, n]) => <MenuItem key={k} value={k} dense>{KIND[k] || k} ({n})</MenuItem>)}
                  </TextField>
                  <Box sx={{ maxHeight: 420, overflow: "auto", border: `1px solid ${C.line}`, borderRadius: 1 }}>
                    <Table size="small" stickyHeader>
                      <TableHead><TableRow><TableCell /><TableCell>Change</TableCell><TableCell>Object</TableCell><TableCell>Detail</TableCell></TableRow></TableHead>
                      <TableBody>
                        {changes.slice(0, 1500).map((c, i) => (
                          <TableRow key={i}>
                            <TableCell><Chip size="small" label={SEV[c.severity].label} sx={{ bgcolor: SEV[c.severity].bg, color: SEV[c.severity].fg, height: 18, fontSize: 10 }} /></TableCell>
                            <TableCell sx={{ whiteSpace: "nowrap" }}>{KIND[c.kind] || c.kind}</TableCell>
                            <TableCell sx={{ fontFamily: "monospace", fontSize: 12 }}>{c.object}</TableCell>
                            <TableCell sx={{ fontSize: 12 }}>{c.detail}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </Box>
                </>
              )}
              {tab === 1 && (
                <Box>
                  <Typography variant="caption" color="text.secondary" component="div" sx={{ mb: 1 }}>
                    A class or field that disappeared while a similar one appeared. Ticked renames are carried into the saved mapping
                    profiles and learned CAD rules when the update is applied (both are backed up first).
                  </Typography>
                  {sel.diff.renames.classes.length + sel.diff.renames.fields.length === 0 && <Typography variant="body2" color="text.secondary">none detected</Typography>}
                  {sel.diff.renames.classes.map((x) => (
                    <FormControlLabel key={x.old} control={<Checkbox size="small" checked={!!renames[`c:${x.old}`]} disabled={sel.status !== "pending"}
                      onChange={(e) => setRenames({ ...renames, [`c:${x.old}`]: e.target.checked })} />}
                      label={<span>class <b>{x.old}</b> → <b>{x.new}</b> <Typography component="span" variant="caption" color="text.secondary">({Math.round(x.shared_fields * 100)}% fields shared)</Typography></span>}
                      sx={{ display: "flex" }} />
                  ))}
                  {sel.diff.renames.fields.map((x) => (
                    <FormControlLabel key={`${x.layer}.${x.old}`} control={<Checkbox size="small" checked={!!renames[`f:${x.layer}.${x.old}`]} disabled={sel.status !== "pending"}
                      onChange={(e) => setRenames({ ...renames, [`f:${x.layer}.${x.old}`]: e.target.checked })} />}
                      label={<span>{x.layer}: field <b>{x.old}</b> → <b>{x.new}</b></span>} sx={{ display: "flex" }} />
                  ))}
                </Box>
              )}
              {tab === 2 && (
                <Stack spacing={1}>
                  <Typography variant="body2"><b>Saved mapping profiles</b> using removed classes / fields: {sel.impact.profiles.length || "none"}</Typography>
                  {sel.impact.profiles.map((p) => <Typography key={p.profile} variant="caption" component="div">• {p.profile}: {[...p.classes, ...p.fields].join(", ")}</Typography>)}
                  <Typography variant="body2"><b>Learned CAD rules</b> pointing at removed classes: {sel.impact.learned_rules.length || "none"}
                    {sel.impact.learned_rules.length > 0 && ` (${[...new Set(sel.impact.learned_rules.map((r) => r.target))].join(", ")})`}</Typography>
                  <Typography variant="body2"><b>Open jobs</b> on this schema: {sel.impact.jobs.length || "none"} — they are validated again on the new version before generating.</Typography>
                  {sel.impact.jobs.slice(0, 20).map((j) => <Typography key={j.job_id} variant="caption" component="div">• {j.file} ({j.status})</Typography>)}
                </Stack>
              )}
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          {sel ? (
            <>
              <Button onClick={() => setSel(null)}>Back</Button>
              {sel.status === "pending" && admin && (
                <>
                  <Button color="inherit" disabled={busy} onClick={async () => { await api.rejectTemplate(schema.schema_id, sel.update_id); setSel(null); load(); }}>Reject</Button>
                  <Button variant="contained" disabled={busy || sel.summary.identical} onClick={apply}>Apply — make {sel.new_version} current</Button>
                </>
              )}
            </>
          ) : <Button onClick={() => setOpen(false)}>Close</Button>}
        </DialogActions>
      </Dialog>
    </>
  );
}

function SummaryChips({ s }: { s: Record<string, any> }) {
  const items: [string, number, string][] = [
    ["classes +", s.classes_added, C.green], ["classes −", s.classes_removed, C.red], ["fields +", s.fields_added, C.green],
    ["fields −", s.fields_removed, C.red], ["fields changed", s.fields_changed, C.amber], ["values +", s.domain_values_added, C.green],
    ["values −", s.domain_values_removed, C.red], ["topology ±", s.topology_changes, C.blue],
  ];
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {items.filter(([, n]) => n).map(([l, n, c]) => <Chip key={l} size="small" label={`${l}${n}`} sx={{ height: 20, fontSize: 11, color: c, borderColor: c }} variant="outlined" />)}
    </Stack>
  );
}
