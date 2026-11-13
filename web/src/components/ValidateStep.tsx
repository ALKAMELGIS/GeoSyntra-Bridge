import { useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, Chip, Collapse, Divider, Drawer, FormControlLabel, Grid, IconButton, LinearProgress, MenuItem, Paper, Select,
  Stack, Switch, Table, TableBody, TableCell, TableRow, TextField, Typography,
} from "@mui/material";
import { DataGrid, GridColDef } from "@mui/x-data-grid";
import PlayArrowOutlined from "@mui/icons-material/PlayArrowOutlined";
import CloseOutlined from "@mui/icons-material/CloseOutlined";
import { api } from "../api";
import type { FeatureDetail, GeoJSON, Issue, JobState, Mapping, Results } from "../types";
import { C } from "../theme";
import { MapHandle, MapView } from "./MapView";
import { SectionTitle, SevChip } from "./ui";
import { ElevationSummary } from "./DemPanel";

interface Props {
  job: JobState; mapping: Mapping; results: Results | null; preview: GeoJSON | null;
  onPatch: (p: unknown) => Promise<void>; onRun: () => void; onError: (m: string) => void;
}

const RANK: Record<string, number> = { error: 0, warning: 1, info: 2 };

const TILES: { key: string; label: string; cats: string[]; tone: "red" | "amber" | "blue" }[] = [
  { key: "failed", label: "Failed features", cats: ["failed"], tone: "red" },
  { key: "missing", label: "Missing attributes", cats: ["missing_attribute"], tone: "amber" },
  { key: "dup", label: "Duplicate records", cats: ["duplicate"], tone: "amber" },
  { key: "labels", label: "Unmatched labels", cats: ["unmatched_label"], tone: "amber" },
  { key: "ambig", label: "Ambiguous labels", cats: ["ambiguous_label"], tone: "amber" },
  { key: "mapping", label: "Uncertain / unmapped", cats: ["uncertain_mapping", "unmatched_feature"], tone: "amber" },
  { key: "geom", label: "Geometry & topology", cats: ["geometry", "topology"], tone: "red" },
  { key: "network", label: "Network connectivity", cats: ["network"], tone: "blue" },
  { key: "stdtopo", label: "Standard topology (info)", cats: ["topology_standard"], tone: "blue" },
  { key: "domain", label: "Domain / value errors", cats: ["domain", "crs"], tone: "red" },
];

export function ValidateStep({ job, mapping, results, preview, onPatch, onRun, onError }: Props) {
  const busy = job.status === "queued" || job.status === "running";
  const [tile, setTile] = useState<string | null>(null);
  const [opts, setOpts] = useState(false);
  const [byZ, setByZ] = useState(false);
  const [sev, setSev] = useState<string>("all");
  const [sel, setSel] = useState<Issue | null>(null);
  const [detail, setDetail] = useState<FeatureDetail | null>(null);
  const mapRef = useRef<MapHandle>(null);
  const stale = job.stage === "mapping" && results;

  const issues = useMemo(() => {
    let x = results?.issues || [];
    if (tile) {
      const cats = TILES.find((t) => t.key === tile)!.cats;
      x = x.filter((i) => cats.includes(i.category));
    }
    if (sev !== "all") x = x.filter((i) => i.severity === sev);
    return x;
  }, [results, tile, sev]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    TILES.forEach((t) => (c[t.key] = (results?.issues || []).filter((i) => t.cats.includes(i.category)).length));
    return c;
  }, [results]);

  const openFeature = async (fid: number | null | undefined, issue?: Issue) => {
    if (issue) mapRef.current?.zoomTo(issue.lonlat, issue.feature_id);
    if (fid == null) { setDetail(null); return; }
    try { setDetail(await api.feature(job.job_id, fid)); } catch (e: any) { onError(e.message); }
  };

  const cols: GridColDef<Issue>[] = [
    { field: "severity", headerName: "Severity", width: 92, renderCell: (p) => <SevChip s={p.value} />,
      sortComparator: (a: string, b: string) => RANK[a] - RANK[b] },
    { field: "message", headerName: "Issue", flex: 1, minWidth: 320, renderCell: (p) => (
      <Box sx={{ py: 0.5, lineHeight: 1.4, whiteSpace: "normal" }}>
        <Box sx={{ fontWeight: 500, color: C.ink }}>{p.row.message}</Box>
        {p.row.suggestion && <Box sx={{ color: C.muted, fontSize: 11.5, mt: 0.25 }}>→ {p.row.suggestion}</Box>}
      </Box>) },
    { field: "target_layer", headerName: "Feature class", width: 150 },
    { field: "feature_id", headerName: "Feature", width: 76, type: "number" },
  ];

  const s = results?.summary;
  const big = (v: number, label: string, color: string) => (
    <Box sx={{ minWidth: 110 }}>
      <Typography sx={{ fontSize: 30, fontWeight: 600, lineHeight: 1.1, color }}>{v.toLocaleString()}</Typography>
      <Typography variant="caption">{label}</Typography>
    </Box>
  );
  return (
    <Stack spacing={2}>
      <Paper sx={{ p: { xs: 2, md: 2.5 } }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={3} alignItems={{ md: "center" }}>
          {s ? (
            <Stack direction="row" spacing={4} sx={{ flex: 1 }} flexWrap="wrap" useFlexGap>
              {big(s.features_imported, "ready to write", C.green)}
              {big(s.features_failed, "blocked", s.features_failed ? C.red : C.muted)}
              {big(s.issues_by_severity.error || 0, "errors", s.issues_by_severity.error ? C.red : C.muted)}
              {big(s.issues_by_severity.warning || 0, "warnings", s.issues_by_severity.warning ? C.amber : C.muted)}
            </Stack>
          ) : <Typography sx={{ flex: 1, color: C.muted }}>Not validated yet.</Typography>}
          <Button size="small" onClick={() => setOpts(!opts)} sx={{ color: C.muted }}>{opts ? "Hide settings" : "Settings"}</Button>
          <Button variant="contained" startIcon={<PlayArrowOutlined />} disabled={busy} onClick={onRun}>
            {results ? "Validate again" : "Validate"}
          </Button>
        </Stack>
        <Collapse in={opts}>
        <Grid container spacing={2} alignItems="center" sx={{ mt: 1 }}>
          <Grid item xs={12}>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap alignItems="center">
              <TextField label="Label search radius (m)" type="number" sx={{ width: 170 }} value={mapping.options.label_search_radius}
                onChange={(e) => onPatch({ options: { label_search_radius: Number(e.target.value) } })} />
              {mapping.options.snap_tolerance != null && (
                <TextField label="Network snap tolerance (m)" type="number" sx={{ width: 190 }} value={mapping.options.snap_tolerance}
                  onChange={(e) => onPatch({ options: { snap_tolerance: Number(e.target.value) } })} />
              )}
              <Select value={mapping.options.duplicate_policy} onChange={(e) => onPatch({ options: { duplicate_policy: e.target.value } })} sx={{ width: 200 }}>
                <MenuItem value="flag">Duplicates: write & flag</MenuItem>
                <MenuItem value="skip">Duplicates: skip & report</MenuItem>
              </Select>
              <Select value={mapping.options.non_nullable_policy || "placeholder"} onChange={(e) => onPatch({ options: { non_nullable_policy: e.target.value } })} sx={{ width: 250 }}>
                <MenuItem value="placeholder">NOT NULL gaps: placeholder & report</MenuItem>
                <MenuItem value="reject">NOT NULL gaps: reject feature</MenuItem>
              </Select>
              {([["make_valid_polygons", "Repair invalid polygons"], ["close_near_closed_rings", "Close near-closed rings"],
                 ["snap_line_endpoints", "Snap line ends to nodes"]] as const).map(([k, l]) => (
                <FormControlLabel key={k} control={<Switch size="small" checked={!!mapping.options.repair[k]}
                  onChange={(e) => onPatch({ options: { repair: { [k]: e.target.checked } } })} />}
                  label={<Typography variant="body2">{l}</Typography>} />
              ))}
            </Stack>
          </Grid>
        </Grid>
        </Collapse>
        {busy && <Box sx={{ mt: 1.5 }}><LinearProgress variant="determinate" value={job.progress * 100} color="secondary" /><Typography variant="caption">{job.message}</Typography></Box>}
        {stale && !busy && <Alert severity="info" sx={{ mt: 1.5, py: 0 }}>Changed since the last check — validate again.</Alert>}
        {job.status === "failed" && job.error?.stage === "validation" && <Alert severity="error" sx={{ mt: 1.5 }}>{job.message}</Alert>}
      </Paper>

      {s && (
        <>
          <ElevationSummary rep={results?.elevation} />
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
            <Typography variant="caption" sx={{ mr: 0.5 }}>Show</Typography>
            <Chip label="All issues" onClick={() => setTile(null)} variant={!tile ? "filled" : "outlined"} sx={{ fontWeight: 600 }} />
            {TILES.filter((t) => counts[t.key]).map((t) => (
              <Chip key={t.key} label={`${t.label} · ${counts[t.key].toLocaleString()}`} onClick={() => setTile(tile === t.key ? null : t.key)}
                variant={tile === t.key ? "filled" : "outlined"}
                sx={{ borderColor: t.tone === "red" ? C.red : t.tone === "amber" ? C.amber : C.line,
                      color: tile === t.key ? "#fff" : t.tone === "red" ? C.red : t.tone === "amber" ? "#92400E" : C.ink,
                      bgcolor: tile === t.key ? (t.tone === "red" ? C.red : t.tone === "amber" ? C.amber : C.ink) : "transparent" }} />
            ))}
          </Stack>
          <Grid container spacing={2}>
            <Grid item xs={12} lg={7}>
              <Paper sx={{ p: 1.5, height: 560, display: "flex", flexDirection: "column" }}>
                <SectionTitle right={
                  <Select size="small" value={sev} onChange={(e) => setSev(String(e.target.value))} sx={{ width: 130 }}>
                    <MenuItem value="all">All severities</MenuItem><MenuItem value="error">Errors</MenuItem>
                    <MenuItem value="warning">Warnings</MenuItem><MenuItem value="info">Info</MenuItem>
                  </Select>}>
                  {issues.length} issues{tile ? ` · ${TILES.find((t) => t.key === tile)?.label}` : ""}
                </SectionTitle>
                <Box sx={{ flex: 1, minHeight: 0 }}>
                  <DataGrid rows={issues} columns={cols} density="compact" disableColumnMenu
                    initialState={{ pagination: { paginationModel: { pageSize: 50 } }, sorting: { sortModel: [{ field: "severity", sort: "asc" }] } }}
                    pageSizeOptions={[25, 50, 100]} rowSelectionModel={sel ? [sel.id] : []}
                    onRowClick={(p) => { setSel(p.row); openFeature(p.row.feature_id, p.row); }}
                    getRowHeight={() => "auto"}
                    sx={{ border: 0, fontSize: 12, "& .MuiDataGrid-cell": { py: 0.6, alignItems: "center", display: "flex" },
                          "& .MuiDataGrid-columnHeaders": { bgcolor: "#FAFBFC" }, "& .MuiDataGrid-row": { cursor: "pointer" } }} />
                </Box>
              </Paper>
            </Grid>
            <Grid item xs={12} lg={5}>
              <Box sx={{ height: 560, position: "relative" }}>
                {results?.elevation?.status === "done" && (
                  <Button size="small" variant={byZ ? "contained" : "outlined"} onClick={() => setByZ(!byZ)}
                    sx={{ position: "absolute", zIndex: 2, right: 48, top: 8, bgcolor: byZ ? undefined : "#fff", py: 0.1 }}>
                    Colour by elevation
                  </Button>)}
                <MapView ref={mapRef} data={preview} mode={byZ ? "elevation" : "result"} selectedFid={detail?.fid ?? sel?.feature_id ?? null}
                  onSelect={(p) => p.fid != null && openFeature(Number(p.fid))} />
              </Box>
            </Grid>
          </Grid>
          <FeatureClassSummary results={results!} />
        </>
      )}

      <Drawer anchor="right" open={!!detail} onClose={() => setDetail(null)} PaperProps={{ sx: { width: 440, p: 2 } }}>
        {detail && <FeatureInspector d={detail} onClose={() => setDetail(null)} />}
      </Drawer>
    </Stack>
  );
}

function FeatureClassSummary({ results }: { results: Results }) {
  const rows = Object.entries(results.summary.features_by_class);
  const failedBy: Record<string, number> = {};
  results.features.filter((f) => f.status === "failed").forEach((f) => (failedBy[f.target] = (failedBy[f.target] || 0) + 1));
  return (
    <Paper sx={{ p: 2 }}>
      <SectionTitle>Features per target class</SectionTitle>
      <Stack direction="row" flexWrap="wrap" gap={1}>
        {rows.map(([k, v]) => (
          <Box key={k} sx={{ px: 1.25, py: 0.5, border: `1px solid ${C.line}`, borderRadius: 1 }}>
            <Typography variant="body2"><b>{v}</b> {k}{failedBy[k] ? <span style={{ color: C.red }}> · {failedBy[k]} failed</span> : null}</Typography>
          </Box>
        ))}
        {!rows.length && <Typography variant="caption">No features would be written.</Typography>}
      </Stack>
    </Paper>
  );
}

function FeatureInspector({ d, onClose }: { d: FeatureDetail; onClose: () => void }) {
  const attrs = Object.entries(d.attributes).filter(([, v]) => v !== null && v !== "");
  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between">
        <Typography variant="h6">{d.target} #{d.fid}</Typography>
        <IconButton size="small" onClick={onClose}><CloseOutlined /></IconButton>
      </Stack>
      <Typography variant="caption">
        {d.status === "failed" ? <span style={{ color: C.red }}>Not imported — {d.failure}</span> : `${d.geometry_type}${d.subtype != null ? ` · subtype ${d.subtype}` : ""}${d.status === "skipped" ? " · skipped (duplicate)" : ""}`}
      </Typography>
      <Divider sx={{ my: 1.5 }} />
      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Source CAD entity</Typography>
      <Table size="small"><TableBody>
        {Object.entries(d.source).filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => (
          <TableRow key={k}><TableCell sx={{ color: C.muted, width: 110 }}>{k}</TableCell>
            <TableCell sx={{ wordBreak: "break-word" }}>{typeof v === "object" ? JSON.stringify(v) : typeof v === "number" ? Number(v).toFixed(3).replace(/\.?0+$/, "") : String(v)}</TableCell></TableRow>
        ))}
      </TableBody></Table>
      {d.labels.length > 0 && <>
        <Typography variant="subtitle2" sx={{ mt: 1.5, mb: 0.5 }}>Associated labels</Typography>
        {d.labels.map((l, i) => <Typography key={i} variant="body2" sx={{ fontFamily: "monospace", fontSize: 12 }}>{l}</Typography>)}
      </>}
      <Typography variant="subtitle2" sx={{ mt: 1.5, mb: 0.5 }}>Target attributes</Typography>
      <Table size="small"><TableBody>
        {attrs.map(([k, v]) => <TableRow key={k}><TableCell sx={{ color: C.muted, width: 140 }}>{k}</TableCell><TableCell sx={{ wordBreak: "break-word" }}>{String(v)}</TableCell></TableRow>)}
        {!attrs.length && <TableRow><TableCell colSpan={2}><Typography variant="caption">No attributes populated</Typography></TableCell></TableRow>}
      </TableBody></Table>
      {d.issues.length > 0 && <>
        <Typography variant="subtitle2" sx={{ mt: 1.5, mb: 0.5 }}>Issues ({d.issues.length})</Typography>
        <Stack spacing={0.75}>
          {d.issues.map((i) => (
            <Box key={i.id} sx={{ p: 1, border: `1px solid ${C.line}`, borderRadius: 1 }}>
              <Stack direction="row" spacing={1} alignItems="center"><SevChip s={i.severity} /><Typography variant="caption">{i.code}</Typography></Stack>
              <Typography variant="body2" sx={{ mt: 0.5 }}>{i.message}</Typography>
              {i.suggestion && <Typography variant="caption" component="div" sx={{ color: C.green }}>→ {i.suggestion}</Typography>}
            </Box>
          ))}
        </Stack>
      </>}
    </Box>
  );
}

