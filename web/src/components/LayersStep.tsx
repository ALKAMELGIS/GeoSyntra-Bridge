import { useEffect, useMemo, useState } from "react";
import {
  Alert, Autocomplete, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel,
  Grid, IconButton, MenuItem, Paper, Select, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField,
  ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from "@mui/material";
import InfoOutlined from "@mui/icons-material/InfoOutlined";
import DoneAllOutlined from "@mui/icons-material/DoneAllOutlined";
import BookmarkAddOutlined from "@mui/icons-material/BookmarkAddOutlined";
import FolderOpenOutlined from "@mui/icons-material/FolderOpenOutlined";
import BlockOutlined from "@mui/icons-material/BlockOutlined";
import CleaningServicesOutlined from "@mui/icons-material/CleaningServicesOutlined";
import TravelExploreOutlined from "@mui/icons-material/TravelExploreOutlined";
import { api } from "../api";
import type { Discovery, GeoJSON, JobState, LayerMap, LayerRole, Mapping, SchemaDetail } from "../types";
import { C } from "../theme";
import { MapView } from "./MapView";
import { GeomIcon, MapStatus, SectionTitle } from "./ui";

interface Props {
  job: JobState; schema: SchemaDetail; discovery: Discovery; mapping: Mapping;
  onPatch: (p: unknown) => Promise<void>; onMapping: (m: Mapping) => void; onError: (m: string) => void;
  onNotice: (m: string) => void;
}

const ROLE: Record<LayerRole, { label: string; bg: string; fg: string }> = {
  asset: { label: "Asset", bg: C.greenSoft, fg: C.green },
  annotation: { label: "Annotation", bg: C.blueSoft, fg: C.blue },
  basemap: { label: "Base map", bg: "#EEF0F3", fg: C.muted },
  sheet: { label: "Sheet", bg: "#EEF0F3", fg: C.muted },
};

const compatible = (g: string, circles: boolean, t: string) =>
  g === t || (t === "point" && g === "polygon" && circles) || (t === "line" && g === "polygon");

export function LayersStep({ job, schema, discovery, mapping, onPatch, onMapping, onError, onNotice }: Props) {
  const [filter, setFilter] = useState<"all" | "review" | "unmapped" | "excluded">("all");
  const [refBusy, setRefBusy] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [cad, setCad] = useState<GeoJSON | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [loadOpen, setLoadOpen] = useState(false);

  useEffect(() => { api.cadPreview(job.job_id).then(setCad).catch(() => setCad(null)); }, [job.job_id, mapping.source_crs, mapping.unit_scale]);

  const groups = discovery.groups;
  const rows = useMemo(() => groups.filter((g) => {
    const m = mapping.layers[g.key];
    if (!m) return false;
    if (filter === "review") return m.role !== "ignore" && ["SUGGESTED", "MANUAL"].includes(m.status);
    if (filter === "unmapped") return m.role !== "ignore" && !m.target;
    if (filter === "excluded") return m.status === "EXCLUDED";
    return m.status !== "EXCLUDED";
  }), [groups, mapping, filter]);

  const counts = useMemo(() => {
    const v = Object.values(mapping.layers);
    return {
      review: v.filter((m) => m.role !== "ignore" && ["SUGGESTED", "MANUAL"].includes(m.status)).length,
      unmapped: v.filter((m) => m.role !== "ignore" && !m.target).length,
      excluded: v.filter((m) => m.status === "EXCLUDED").length,
    };
  }, [mapping]);

  const setRow = (key: string, upd: Partial<LayerMap>) => onPatch({ layers: { [key]: upd } });
  const acceptAll = async () => {
    try { onMapping(await api.acceptSuggestions(job.job_id)); onNotice("All suggestions accepted (targets and proposed exclusions)."); }
    catch (e: any) { onError(e.message); }
  };
  const keys = mapping.keys || [];

  const textLayers = discovery.layers.filter((l) => l.text_count > 0);
  const labelSet = mapping.label_layers;

  return (
    <Grid container spacing={2}>
      <Grid item xs={12} lg={7.5}>
        <Paper sx={{ p: 2 }}>
          <SectionTitle right={
            <Stack direction="row" spacing={1}>
              <Button variant="contained" color="secondary" startIcon={<DoneAllOutlined />} onClick={acceptAll}
                disabled={!Object.values(mapping.layers).some((m) => m.role !== "ignore" && !["USER", "PROFILE"].includes(m.status) && (m.target || m.suggested_role))}>
                Accept all suggestions
              </Button>
              <Button startIcon={<FolderOpenOutlined />} onClick={() => setLoadOpen(true)}>Load profile</Button>
              <Button startIcon={<BookmarkAddOutlined />} onClick={() => setSaveOpen(true)}>Save profile</Button>
            </Stack>}>
            CAD layer mapping
          </SectionTitle>
          <ToggleButtonGroup size="small" exclusive value={filter} onChange={(_, v) => v && setFilter(v)} sx={{ mb: 1 }}>
            <ToggleButton value="all">Asset groups ({groups.length - counts.excluded})</ToggleButton>
            <ToggleButton value="review">Needs review ({counts.review})</ToggleButton>
            <ToggleButton value="unmapped">Unmapped ({counts.unmapped})</ToggleButton>
            <ToggleButton value="excluded">Base map / sheet excluded ({counts.excluded})</ToggleButton>
          </ToggleButtonGroup>
          <Box sx={{ maxHeight: "calc(100vh - 300px)", minHeight: 300, overflow: "auto", border: `1px solid ${C.line}`, borderRadius: 1 }}>
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>CAD layer</TableCell>
                  <TableCell align="right">Count</TableCell>
                  <TableCell sx={{ minWidth: 230 }}>Target feature class</TableCell>
                  <TableCell>Subtype</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell padding="none" />
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((g) => {
                  const m = mapping.layers[g.key];
                  const ignored = m.role === "ignore";
                  const opts = schema.layers.filter((l) => compatible(g.geometry, g.has_circles, l.geometry_type));
                  const tl = schema.layers.find((l) => l.name === m.target);
                  const lay = discovery.layers.find((l) => l.name === g.layer);
                  return (
                    <TableRow key={g.key} hover selected={hover === g.layer} onMouseEnter={() => setHover(g.layer)}
                      sx={{ opacity: ignored ? 0.55 : 1 }}>
                      <TableCell>
                        <Stack direction="row" spacing={0.75} alignItems="center">
                          <GeomIcon g={g.geometry} />
                          <Box sx={{ minWidth: 0 }}>
                            <Stack direction="row" spacing={0.5} alignItems="center">
                              <Typography variant="body2" fontWeight={600} noWrap sx={{ maxWidth: 200 }}>{g.layer}</Typography>
                              {g.role && g.role !== "asset" && (
                                <Tooltip title={lay?.role_reason || ""}>
                                  <Chip size="small" label={ROLE[g.role].label} sx={{ height: 16, fontSize: 10, bgcolor: ROLE[g.role].bg, color: ROLE[g.role].fg }} />
                                </Tooltip>)}
                            </Stack>
                            {g.style && (
                              <Tooltip title="Drawing style — this layer holds several objects drawn differently; each style is mapped on its own">
                                <Chip size="small" variant="outlined" label={g.style} sx={{ height: 17, fontSize: 10, mt: 0.25, borderColor: C.navy2, color: C.navy2 }} />
                              </Tooltip>)}
                            <Typography variant="caption" noWrap component="div" sx={{ maxWidth: 240 }}>
                              {Object.entries(g.entity_types).map(([k, v]) => `${k} ${v}`).join(" · ")}
                              {g.blocks.length > 0 && ` · ${g.blocks.map((b) => b.name).join(", ")}`}
                            </Typography>
                          </Box>
                        </Stack>
                      </TableCell>
                      <TableCell align="right">{g.count}</TableCell>
                      <TableCell>
                        <Autocomplete size="small" disabled={ignored} options={opts.map((o) => o.name)} value={m.target}
                          groupBy={schema.layers.some((l) => l.asset) ? (o) => {
                            const l = schema.layers.find((x) => x.name === o);
                            return l?.asset ? `${l.asset} — ${l.template || ""}` : "";
                          } : undefined}
                          onChange={(_, v) => setRow(g.key, { target: v, role: "feature" })}
                          renderOption={(props, o) => {
                            const l = schema.layers.find((x) => x.name === o)!;
                            const alt = m.alternatives.find((a) => a.target === o);
                            return (
                              <Tooltip key={o} placement="right" title={l.description ? `${l.description}${l.legacy_names?.length ? " — also: " + l.legacy_names.slice(0, 6).join(", ") : ""}` : ""}>
                              <li {...props} key={o}>
                                <Stack direction="row" spacing={0.75} alignItems="center" sx={{ width: "100%" }}>
                                  <GeomIcon g={l.geometry_type} size={14} />
                                  <span style={{ flex: 1 }}>{o}</span>
                                  {l.asset && <Typography variant="caption" sx={{ fontWeight: 600 }}>{l.asset}</Typography>}
                                  {alt && <Typography variant="caption">{Math.round(alt.score * 100)}%</Typography>}
                                </Stack>
                              </li>
                              </Tooltip>
                            );
                          }}
                          renderInput={(params) => <TextField {...params} placeholder="— not mapped —" />} />
                        {!ignored && (m.keys?.length || Object.keys(m.attributes || {}).length) ? (
                          <Stack direction="row" flexWrap="wrap" gap={0.5} sx={{ mt: 0.5 }}>
                            {m.keys?.map((k) => <Tooltip key={k.code} title={k.description}><Chip size="small" label={k.code} sx={{ height: 18, fontSize: 10, bgcolor: C.navy, color: "#fff" }} /></Tooltip>)}
                            {Object.entries(m.attributes || {}).map(([f, a]) => (
                              <Tooltip key={f} title={`From CAD key: "${a.from}" (${a.match})`}>
                                <Chip size="small" label={`${f}=${a.value}`} sx={{ height: 18, fontSize: 10, bgcolor: C.greenSoft, color: C.green }} />
                              </Tooltip>))}
                          </Stack>) : null}
                        {!ignored && m.twins?.map((t) => (
                          <Tooltip key={t.target} title={`Same asset in another geometry (template requirement). ${t.reason}. Click to ${t.enabled ? "stop deriving it" : "derive it"}.`}>
                            <Chip size="small" clickable label={`${t.enabled ? "+" : "–"} ${t.target} (${t.convert === "centroid" ? "point" : "centreline"})`}
                              onClick={() => setRow(g.key, { twins: m.twins!.map((x) => x.target === t.target ? { ...x, enabled: !x.enabled } : x) })}
                              sx={{ mt: 0.5, mr: 0.5, height: 18, fontSize: 10, bgcolor: t.enabled ? C.blueSoft : "#EEF1F5", color: t.enabled ? C.navy : C.muted }} />
                          </Tooltip>))}
                        {!ignored && !m.target && m.suggested_role === "ignore" && (
                          <Typography variant="caption" component="div" sx={{ mt: 0.5, color: C.amber }}>Suggest: ignore — {m.suggestion_reason}</Typography>)}
                      </TableCell>
                      <TableCell>
                        {tl && tl.subtypes.length > 0 ? (
                          <Select size="small" value={m.subtype ?? ""} displayEmpty sx={{ minWidth: 120 }}
                            onChange={(e) => setRow(g.key, { subtype: e.target.value === "" ? null : Number(e.target.value) })}>
                            <MenuItem value=""><em>—</em></MenuItem>
                            {tl.subtypes.map((s) => <MenuItem key={s.code} value={s.code}>{s.code} · {s.name}</MenuItem>)}
                          </Select>
                        ) : <Typography variant="caption">—</Typography>}
                      </TableCell>
                      <TableCell><MapStatus status={ignored ? "IGNORED" : m.status} score={m.score} /></TableCell>
                      <TableCell padding="none" sx={{ whiteSpace: "nowrap", pr: 1 }}>
                        <Tooltip title={<Box>
                          {m.evidence.map((e, i) => <div key={i}>• {e}</div>)}
                          {lay?.text_samples.length ? <div style={{ marginTop: 6 }}>Labels on layer: {lay.text_samples.slice(0, 4).join(" | ")}</div> : null}
                          {g.attribute_tags.length ? <div style={{ marginTop: 6 }}>Block attributes: {g.attribute_tags.join(", ")}</div> : null}
                        </Box>}>
                          <IconButton size="small"><InfoOutlined sx={{ fontSize: 17 }} /></IconButton>
                        </Tooltip>
                        <Tooltip title={ignored ? "Include this layer" : "Ignore this layer (reported, not converted)"}>
                          <IconButton size="small" onClick={() => setRow(g.key, { role: ignored ? "feature" : "ignore" })}
                            sx={{ color: ignored ? C.red : C.muted }}><BlockOutlined sx={{ fontSize: 17 }} /></IconButton>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </Box>
        </Paper>
      </Grid>
      <Grid item xs={12} lg={4.5}>
        <Stack spacing={2}>
          <Box sx={{ height: 400 }}><MapView data={cad} mode="cad" highlightLayer={hover} onSelect={(p) => setHover(String(p.layer))} /></Box>
          {discovery.key_info && (
            <Alert severity={discovery.key_info.level === "layers" ? "info" : "success"} sx={{ py: 0.25 }}>
              <b>Drawing key: </b>{discovery.key_info.label}
              <Typography variant="caption" component="div">
                Searched in order: {discovery.key_info.tried.map((t) => `${({ layout_legend: "layout sheet legend", drawing_legend: "legend on drawing sheet", drawing_keynotes: "keynotes on drawing sheet" } as Record<string, string>)[t.level] || t.level} (${t.found})`).join(" → ")}
                {discovery.key_info.level === "layers" ? " → layers, blocks and geometry" : ""}
              </Typography>
            </Alert>
          )}
          <Paper sx={{ p: 2 }}>
            <SectionTitle right={<Button size="small" component="label" startIcon={<TravelExploreOutlined />} disabled={refBusy}>
              {mapping.reference ? "Replace" : "Upload"} reference GIS
              <input hidden type="file" accept=".zip,.gpkg,.geojson,.json" onChange={async (e) => {
                const f = e.target.files?.[0]; e.target.value = "";
                if (!f) return;
                setRefBusy(true);
                try {
                  const m = await api.attachReference(job.job_id, f);
                  onMapping(m);
                  onNotice(m.reference?.overlap ? `Spatial join: ${m.reference.matched_entities} CAD objects matched to the reference GIS — mapping re-scored.`
                    : "The reference data does not overlap the drawing — check the source coordinate system.");
                } catch (err: any) { onError(err.message); } finally { setRefBusy(false); }
              }} />
            </Button>}>Spatial join with existing GIS</SectionTitle>
            {!mapping.reference ? (
              <Typography variant="caption" component="div">
                Optional. Upload an approved GIS of the same area (zipped .gdb or shapefile, GeoPackage, GeoJSON). Drawn objects that fall on
                reference features — valve on valve, main on main — are mapped to that feature class, and their attribute values fill empty fields.
              </Typography>
            ) : (
              <Box>
                <Typography variant="body2"><b>{mapping.reference.file}</b> · {mapping.reference.summary.features} features ·{" "}
                  {mapping.reference.matched_entities} CAD objects matched
                  <Button size="small" sx={{ ml: 1 }} onClick={async () => {
                    try { onMapping(await api.removeReference(job.job_id)); onNotice("Reference GIS removed."); } catch (err: any) { onError(err.message); }
                  }}>Remove</Button>
                </Typography>
                {!mapping.reference.overlap && <Alert severity="warning" sx={{ py: 0, mt: 0.5 }}>No overlap with the drawing — check the source CRS.</Alert>}
                <Table size="small" sx={{ mt: 0.5 }}>
                  <TableBody>
                    {mapping.reference.summary.layers.map((l) => (
                      <TableRow key={l.name}>
                        <TableCell sx={{ py: 0.25 }}><GeomIcon g={l.geometry} size={13} /> {l.name}</TableCell>
                        <TableCell sx={{ py: 0.25 }} align="right">{l.count}</TableCell>
                        <TableCell sx={{ py: 0.25 }}>{l.target ? <b>{l.target}</b> : <Typography variant="caption" sx={{ color: C.amber }}>{l.how}</Typography>}</TableCell>
                      </TableRow>))}
                  </TableBody>
                </Table>
              </Box>
            )}
          </Paper>
          {(discovery.cleaning?.length ?? 0) > 0 && (
            <Paper sx={{ p: 2 }}>
              <SectionTitle right={<CleaningServicesOutlined sx={{ fontSize: 18, color: C.green }} />}>CAD clean-up before transfer</SectionTitle>
              <Stack spacing={0.5}>
                {discovery.cleaning!.map((c, i) => (
                  <Tooltip key={i} title={`${c.note}${c.note ? " — " : ""}handles: ${c.handles.slice(0, 12).join(", ")}${c.handles.length > 12 ? "…" : ""}`}>
                    <Typography variant="body2"><b>{c.count}</b> · {c.action}
                      <Typography variant="caption" component="span"> — {Object.entries(c.layers).map(([l, n]) => `${l} ${n}`).join(", ")}</Typography>
                    </Typography>
                  </Tooltip>))}
              </Stack>
            </Paper>)}
          {keys.length > 0 && (
            <Paper sx={{ p: 2 }}>
              <SectionTitle right={<Typography variant="caption">{keys.length} entries read from the drawing</Typography>}>CAD key (legend / keynotes)</SectionTitle>
              <Box sx={{ maxHeight: 260, overflow: "auto" }}>
                <Table size="small">
                  <TableBody>
                    {keys.map((k) => {
                      const grp = Object.entries(mapping.layers).find(([g]) => g.startsWith((k.best_layer || "§") + "::"));
                      return (
                        <TableRow key={k.code} hover onMouseEnter={() => k.best_layer && setHover(k.best_layer)}>
                          <TableCell sx={{ fontWeight: 700, whiteSpace: "nowrap" }}>{k.code}</TableCell>
                          <TableCell>{k.description}<Typography variant="caption" component="div">{k.best_layer || "no layer linked"}</Typography></TableCell>
                          <TableCell sx={{ whiteSpace: "nowrap" }}>{grp?.[1].role === "ignore" ? <Typography variant="caption">ignored</Typography> : grp?.[1].target ? <b>{grp[1].target}</b> : <Typography variant="caption" sx={{ color: C.amber }}>no class</Typography>}</TableCell>
                        </TableRow>);
                    })}
                  </TableBody>
                </Table>
              </Box>
            </Paper>)}
          <Paper sx={{ p: 2 }}>
            <SectionTitle right={<FormControlLabel sx={{ mr: 0 }} control={<Checkbox size="small" checked={labelSet === null}
              onChange={(e) => onPatch({ label_layers: e.target.checked ? null : textLayers.map((l) => l.name) })} />}
              label={<Typography variant="body2">Use all text layers</Typography>} />}>
              Label sources
            </SectionTitle>
            <Typography variant="caption" component="div" sx={{ mb: 1 }}>
              TEXT / MTEXT / leaders on these layers are parsed with {schema.label_rule_count} label rules and attached to the nearest aligned feature.
            </Typography>
            <Stack direction="row" flexWrap="wrap" gap={0.75}>
              {textLayers.map((l) => {
                const on = labelSet === null || labelSet.includes(l.name);
                return (
                  <Tooltip key={l.name} title={l.text_samples.slice(0, 5).join(" | ")}>
                    <Chip label={`${l.name} (${l.text_count})`} size="small" variant={on ? "filled" : "outlined"}
                      onClick={() => {
                        const cur = labelSet ?? textLayers.map((x) => x.name);
                        onPatch({ label_layers: on ? cur.filter((x) => x !== l.name) : [...cur, l.name] });
                      }}
                      sx={on ? { bgcolor: C.blueSoft, color: C.navy } : {}} />
                  </Tooltip>
                );
              })}
              {!textLayers.length && <Typography variant="caption">No text in this drawing.</Typography>}
            </Stack>
          </Paper>
        </Stack>
      </Grid>
      <SaveProfile open={saveOpen} onClose={() => setSaveOpen(false)} job={job} onNotice={onNotice} onError={onError} />
      <LoadProfile open={loadOpen} onClose={() => setLoadOpen(false)} job={job} onMapping={onMapping} onError={onError} onNotice={onNotice} />
    </Grid>
  );
}

function SaveProfile({ open, onClose, job, onNotice, onError }: { open: boolean; onClose: () => void; job: JobState; onNotice: (m: string) => void; onError: (m: string) => void }) {
  const [name, setName] = useState("");
  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ fontSize: 15 }}>Save mapping profile</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
          Saves layer, subtype and field mappings for {job.schema_name}. Confirmed layer mappings also become approved rules
          that is reused for future drawings.
        </Typography>
        <TextField autoFocus fullWidth label="Profile name" placeholder="e.g. Consultant A — sewer drawings" value={name} onChange={(e) => setName(e.target.value)} />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!name.trim()} onClick={async () => {
          try {
            const r = await api.saveProfile(job.job_id, name.trim());
            onNotice(r.rule_conflicts.length ? `Profile saved. ${r.rule_conflicts.length} existing approved rules were kept (overwrite requires admin authorisation).` : "Profile saved.");
            onClose();
          } catch (e: any) { onError(e.message); }
        }}>Save</Button>
      </DialogActions>
    </Dialog>
  );
}

function LoadProfile({ open, onClose, job, onMapping, onError, onNotice }: { open: boolean; onClose: () => void; job: JobState; onMapping: (m: Mapping) => void; onError: (m: string) => void; onNotice: (m: string) => void }) {
  const [list, setList] = useState<{ name: string; saved_at: string; saved_by: string; layer_count: number }[]>([]);
  useEffect(() => { if (open) api.profiles(job.schema_id).then(setList).catch((e) => onError(e.message)); }, [open]);
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ fontSize: 15 }}>Apply a saved mapping profile</DialogTitle>
      <DialogContent>
        {list.length === 0 && <Typography variant="body2" color="text.secondary">No profiles saved for this schema yet.</Typography>}
        <Table size="small">
          <TableBody>
            {list.map((p) => (
              <TableRow key={p.name} hover>
                <TableCell><b>{p.name.replace(/_/g, " ")}</b><br /><Typography variant="caption">{p.layer_count} layer groups · {p.saved_by} · {p.saved_at?.slice(0, 10)}</Typography></TableCell>
                <TableCell align="right">
                  <Button variant="outlined" onClick={async () => {
                    try { onMapping(await api.applyProfile(job.job_id, p.name)); onNotice(`Profile '${p.name}' applied.`); onClose(); }
                    catch (e: any) { onError(e.message); }
                  }}>Apply</Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  );
}
