import { useEffect, useState } from "react";
import {
  Alert, Box, Button, Checkbox, Chip, CircularProgress, Collapse, FormControlLabel, Grid, MenuItem, Paper, Stack, TextField, Typography,
} from "@mui/material";
import TerrainOutlined from "@mui/icons-material/TerrainOutlined";
import UploadFileOutlined from "@mui/icons-material/UploadFileOutlined";
import ExpandMoreOutlined from "@mui/icons-material/ExpandMoreOutlined";
import { api } from "../api";
import type { DemSourceInfo, ElevationOptions, ElevationReport, JobState, Mapping } from "../types";
import { C } from "../theme";

/** Elevation fields of the official templates that can take the DEM value (ground surface only). */
export const ELEV_FIELDS: Record<string, string> = {
  ZCOORD: "z", Z: "z", ZCOORD_START: "start_z", ZCOORD_END: "end_z",
  GROUND_LEVEL: "dem_ground", AVG_GLEVEL: "dem_ground", COVER_LEVEL: "dem_ground",
};
export const ELEV_VALUES: Record<string, string> = {
  z: "Elevation of the feature (point Z · line/area mean)", start_z: "Elevation at the line's first vertex",
  end_z: "Elevation at the line's last vertex", dem_ground: "DEM ground level",
};
const VREF: Record<string, string> = {
  ras_ghumays: "Ras Ghumays (DMT standard)", source: "As delivered by the DEM", "EPSG:3855": "EGM2008 geoid", "EPSG:5773": "EGM96 geoid", "EPSG:4979": "WGS 84 ellipsoid",
};

interface Props {
  job: JobState; mapping: Mapping; onPatch: (p: unknown) => Promise<void>; onMapping: (m: Mapping) => void;
  onError: (m: string) => void; onNotice: (m: string) => void;
}

/** Compact DEM source + options shown inside Field mapping, where the elevation fields are. */
export function DemPanel({ job, mapping, onPatch, onMapping, onError, onNotice }: Props) {
  const [sources, setSources] = useState<Record<string, DemSourceInfo>>({});
  const [defaults, setDefaults] = useState<ElevationOptions>({});
  const [keyCfg, setKeyCfg] = useState(false);
  const [src, setSrc] = useState<string>(mapping.elevation?.source || "copernicus_glo30");
  const [apiKey, setApiKey] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [upV, setUpV] = useState("unknown");
  const [upCrs, setUpCrs] = useState("");
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const o: ElevationOptions = { ...defaults, ...(mapping.elevation || {}) };
  const dem = mapping.dem;
  const on = !!o.enabled && !!dem;

  useEffect(() => {
    api.demSources().then((r) => { setSources(r.sources); setDefaults(r.defaults); setKeyCfg(r.opentopography_key_configured); })
      .catch((e) => onError(e.message));
  }, []);

  const set = (patch: ElevationOptions) => onPatch({ elevation: patch });
  const prepare = async () => {
    setBusy(true);
    try {
      if (src === "upload" && !file) throw new Error("choose a GeoTIFF first");
      const m = src === "upload" ? await api.uploadDem(job.job_id, file!, upCrs.trim(), upV) : await api.prepareDem(job.job_id, src, apiKey || undefined);
      onMapping(m);
      onNotice(`DEM ready: ${m.dem?.label} — elevation fields will be filled from it.`);
    } catch (e: any) { onError(e.message); } finally { setBusy(false); }
  };

  return (
    <Paper sx={{ p: 1.5, mb: 2, border: `1px solid ${on ? C.green : C.line}` }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <TerrainOutlined sx={{ color: on ? C.green : C.muted }} />
        <Typography variant="subtitle2" sx={{ mr: 1 }}>Elevation fields from DEM</Typography>
        <TextField select size="small" value={src} onChange={(e) => setSrc(e.target.value)} sx={{ minWidth: 250 }}>
          {Object.entries(sources).map(([k, s]) => <MenuItem key={k} value={k} dense>{s.label} — {s.resolution}</MenuItem>)}
        </TextField>
        {src === "alos_aw3d30" && !keyCfg && (
          <TextField size="small" placeholder="OpenTopography API key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} sx={{ width: 220 }} />)}
        {src === "upload" && (
          <>
            <Button size="small" component="label" variant="outlined" startIcon={<UploadFileOutlined />}>
              {file ? file.name : "GeoTIFF…"}<input hidden type="file" accept=".tif,.tiff" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </Button>
            <TextField select size="small" value={upV} onChange={(e) => setUpV(e.target.value)} sx={{ width: 170 }} label="Vertical ref.">
              {["ras_ghumays", "unknown", "EPSG:3855", "EPSG:5773", "EPSG:4979"].map((v) => <MenuItem key={v} value={v} dense>{v === "unknown" ? "Unknown / local" : v === "ras_ghumays" ? "Ras Ghumays" : VREF[v]}</MenuItem>)}
            </TextField>
            <TextField size="small" label="CRS if missing" placeholder="EPSG:32640" value={upCrs} onChange={(e) => setUpCrs(e.target.value)} sx={{ width: 140 }} />
          </>
        )}
        <Button size="small" variant="contained" color="secondary" disabled={busy} onClick={prepare}
          startIcon={busy ? <CircularProgress size={13} color="inherit" /> : undefined}>
          {dem && dem.source === src ? "Refresh DEM" : "Use this DEM"}
        </Button>
        {o.enabled && <Button size="small" disabled={busy} onClick={async () => { try { onMapping(await api.disableDem(job.job_id)); } catch (e: any) { onError(e.message); } }}>Turn off</Button>}
        <Box sx={{ flex: 1 }} />
        <Button size="small" endIcon={<ExpandMoreOutlined sx={{ transform: more ? "rotate(180deg)" : "none" }} />} onClick={() => setMore(!more)}>Options</Button>
      </Stack>
      <Typography variant="caption" component="div" sx={{ mt: 0.75 }}>
        {on ? <>
          <Chip size="small" label="ON" sx={{ height: 17, fontSize: 10, bgcolor: C.greenSoft, color: C.green, mr: 0.75 }} />
          {dem!.label} · {dem!.resolution_m.join(" × ")} m · {dem!.vertical_label} · {dem!.min} … {dem!.max} m in the area · NoData {dem!.nodata_pct ?? 0}%
          {" — "}fills ZCOORD, ZCOORD_START / ZCOORD_END, GROUND_LEVEL, AVG_GLEVEL, COVER_LEVEL where the template has them and the drawing gives no level.
        </> : mapping.elevation?.missing_policy === "nodata_value"
          ? `Off — features without a level are written with the no-data level ${mapping.elevation?.nodata_value} (reported). Choose a DEM to replace it.`
          : "Off — the template's elevation fields (ZCOORD …) stay empty and NOT NULL ones block generation. 0 is never written as an elevation."}
      </Typography>
      <Collapse in={more}>
        <Grid container spacing={1.25} sx={{ mt: 0.5 }}>
          <Grid item xs={6} md={3}>
            <TextField select fullWidth size="small" label="Existing CAD Z" value={o.mode || "fill_missing"} onChange={(e) => set({ mode: e.target.value as any })}>
              <MenuItem value="fill_missing" dense>Preserve — DEM fills missing</MenuItem><MenuItem value="replace" dense>Replace with DEM</MenuItem>
            </TextField>
          </Grid>
          <Grid item xs={6} md={2}>
            <TextField select fullWidth size="small" label="Interpolation" value={o.interpolation || "bilinear"} onChange={(e) => set({ interpolation: e.target.value as any })}>
              <MenuItem value="nearest" dense>Nearest</MenuItem><MenuItem value="bilinear" dense>Bilinear</MenuItem><MenuItem value="bicubic" dense>Bicubic</MenuItem>
            </TextField>
          </Grid>
          <Grid item xs={6} md={2.5}>
            <TextField select fullWidth size="small" label="Output vertical reference" value={o.vertical_target || "source"} onChange={(e) => set({ vertical_target: e.target.value })}>
              {Object.entries(VREF).map(([k, l]) => <MenuItem key={k} value={k} dense>{l}</MenuItem>)}
            </TextField>
          </Grid>
          <Grid item xs={6} md={1.5}>
            {(o.vertical_target || "ras_ghumays") === "ras_ghumays" ? (
              <TextField fullWidth size="small" type="number" label="→ Ras Ghumays offset (m)" value={o.rg_offset ?? ""}
                onChange={(e) => set({ rg_offset: e.target.value === "" ? null : Number(e.target.value) })}
                helperText="from a benchmark: RG = DEM + offset" />
            ) : (
              <TextField fullWidth size="small" type="number" label="Offset (m)" value={o.vertical_offset ?? 0} onChange={(e) => set({ vertical_offset: Number(e.target.value) })} />
            )}
          </Grid>
          <Grid item xs={6} md={3}>
            <Stack direction="row" spacing={1}>
              <TextField select fullWidth size="small" label="No elevation" value={o.missing_policy || "block"} onChange={(e) => set({ missing_policy: e.target.value as any })}>
                <MenuItem value="block" dense>Block & report</MenuItem><MenuItem value="exclude" dense>Exclude feature</MenuItem>
                <MenuItem value="nodata_value" dense>Explicit no-data value</MenuItem>
              </TextField>
              {o.missing_policy === "nodata_value" && (
                <TextField size="small" type="number" label="Value" value={o.nodata_value ?? -9999} sx={{ width: 100 }}
                  onChange={(e) => set({ missing_policy: "nodata_value", nodata_value: Number(e.target.value) })} />)}
            </Stack>
          </Grid>
          <Grid item xs={12}>
            {src === "upload" && (
              <TextField size="small" type="number" label="Declared DTM accuracy (m, std. dev.)" value={o.dem_accuracy_m ?? ""} sx={{ width: 260, mr: 2 }}
                onChange={(e) => set({ dem_accuracy_m: e.target.value === "" ? null : Number(e.target.value) })}
                helperText="compared with DMT Table 2 (e.g. < 0.30 m in a city)" />)}
            <FormControlLabel control={<Checkbox size="small" checked={o.interpolate_gaps !== false} onChange={(e) => set({ interpolate_gaps: e.target.checked })} />}
              label={<Typography variant="body2">Interpolate vertices without elevation along their feature</Typography>} />
            <FormControlLabel control={<Checkbox size="small" checked={o.cad_zero_is_missing !== false} onChange={(e) => set({ cad_zero_is_missing: e.target.checked })} />}
              label={<Typography variant="body2">CAD drawn flat at Z = 0 has no elevation</Typography>} />
            {dem?.license && <Typography variant="caption" component="div">{dem.license}</Typography>}
          </Grid>
        </Grid>
      </Collapse>
    </Paper>
  );
}

/** One-line elevation result shown in Validate. */
export function ElevationSummary({ rep }: { rep?: ElevationReport }) {
  if (!rep) return null;
  if (rep.status === "disabled")
    return <Alert severity="warning" sx={{ py: 0.25 }}>No DEM — levels from CAD only ({rep.by_status?.cad || 0} features).</Alert>;
  if (rep.status !== "done") return null;
  const miss = rep.missing_features || 0;
  const std = rep.ras_ghumays === false || rep.accuracy?.meets === false || rep.accuracy?.meets === null;
  return (
    <Alert severity={miss || std ? "warning" : "success"} sx={{ py: 0.25 }}>
      <b>Elevation ({rep.dem?.label}): </b>
      {Object.entries(rep.by_status || {}).map(([k, v]) => `${k} ${v}`).join(" · ")} features
      {rep.z_range ? ` · Z ${rep.z_range.min} … ${rep.z_range.max} m (mean ${rep.z_range.mean})` : ""}
      {rep.vertical && !rep.drawing_levels ? ` · ${rep.vertical.result}${rep.vertical.message.includes("unavailable") ? " (conversion unavailable — kept)" : ""}` : ""}
      {rep.drawing_levels ? "" : ` · ${rep.vertices?.interpolated || 0} vertices interpolated`}
      {miss ? ` · ${miss} feature(s) without elevation (${rep.missing_policy})` : ""}
      {rep.validation ? ` · checks: ${rep.validation.spikes} spikes, ${rep.validation.out_of_range} out of range` : ""}
      {rep.drawing_levels ? ` · heights follow the drawing's ${rep.drawing_levels.control_points} surveyed levels (DEM aligned ${rep.drawing_levels.offset_m > 0 ? "+" : ""}${rep.drawing_levels.offset_m} m, ${rep.drawing_levels.interpolated_vertices} vertices from nearby levels)`
        : rep.ras_ghumays === false ? " · NOT Ras Ghumays (standard 3.5)" : rep.ras_ghumays ? " · Ras Ghumays" : ""}
      {rep.accuracy && rep.accuracy.meets === false ? ` · DEM ±${rep.accuracy.dem_std_m} m > ${rep.accuracy.required_std_m} m required (Table 2)` : ""}
    </Alert>
  );
}
