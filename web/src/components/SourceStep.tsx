import { SubmissionPanel } from "./SubmissionPanel";
import { RequiredValues } from "./RequiredValues";
import { CleanupPanel } from "./CleanupPanel";
import { TemplateUpdates } from "./TemplateUpdates";
import { useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, ButtonBase, Chip, Divider, Grid, InputAdornment, LinearProgress, ListItemText, MenuItem,
  Paper, Select, Stack, Table, Collapse, TableBody, TableCell, TableHead, TableRow, TextField, Tooltip, Typography,
} from "@mui/material";
import FileUploadOutlined from "@mui/icons-material/FileUploadOutlined";
import UploadFileOutlined from "@mui/icons-material/UploadFileOutlined";
import InsertDriveFileOutlined from "@mui/icons-material/InsertDriveFileOutlined";
import SearchOutlined from "@mui/icons-material/SearchOutlined";
import WarningAmberOutlined from "@mui/icons-material/WarningAmberOutlined";
import ExpandMoreOutlined from "@mui/icons-material/ExpandMoreOutlined";
import ErrorOutlineRounded from "@mui/icons-material/ErrorOutlineRounded";
import AutoFixHighOutlined from "@mui/icons-material/AutoFixHighOutlined";
import PublicOutlined from "@mui/icons-material/PublicOutlined";
import BadgeOutlined from "@mui/icons-material/BadgeOutlined";
import { api } from "../api";
import { CoordinatePanel } from "./CoordinatePanel";
import type { Capabilities, Discovery, JobState, Mapping, SchemaDetail, SchemaSummary } from "../types";
import { C } from "../theme";
import { fmtBytes, GeomIcon, KV, SectionTitle } from "./ui";

interface Props {
  caps: Capabilities | null;
  schemas: SchemaSummary[];
  schemaId: string;
  onSchema: (id: string) => void;
  schema: SchemaDetail | null;
  job: JobState | null;
  discovery: Discovery | null;
  mapping: Mapping | null;
  onUploaded: (j: JobState) => void;
  onPatch: (p: unknown) => Promise<void>;
  onNext: () => void;
  onError: (m: string) => void;
  onSchemaUpdated?: () => void;
  onValidate?: () => void;
  onGoElevation?: () => void;
  onAddSheets?: (files: File[]) => void;
}

export function SourceStep(p: Props) {
  const busy = p.job && (p.job.status === "queued" || p.job.status === "running");
  const fresh = !p.job || (p.job.status === "failed" && p.job.stage === "discovery");
  if (fresh) {
    return (
      <Stack spacing={3} alignItems="center" sx={{ py: { xs: 2, md: 5 } }}>
        <TemplatePicker {...p} />
        <DropZone {...p} />
      </Stack>
    );
  }
  const full = !!(p.discovery && p.mapping);   // the page itself is the surface — no card inside it
  return (
    <Stack spacing={full ? 0 : 2}>
      {p.discovery?.schema_ranking && p.job && p.job.schema_id !== "adm_all" && <Box sx={full ? { px: { xs: 2, md: 3 }, pt: 2 } : undefined}><SchemaFit {...p} /></Box>}
      <Paper sx={full ? { overflow: "hidden", borderRadius: 0, border: 0, boxShadow: "none", bgcolor: "#FBFAF8", minHeight: p.job?.schema_id === "adm_all" ? "calc(100vh - 118px)" : undefined } : { overflow: "hidden" }}>
        <FileCard {...p} />
        {p.discovery && p.mapping && p.job && (
          <Box>
            {p.onValidate && (
              <RequiredValues job={p.job} onPatch={p.onPatch} onValidate={p.onValidate} onGoElevation={p.onGoElevation}
                wrap={(body) => <Row icon={<ErrorOutlineRounded />} tone={C.red} title="Blocked"
                  action={<ExplodeButton job={p.job!} onDone={p.onUploaded} onError={p.onError} />}>{body}</Row>} />
            )}
            <Row icon={<AutoFixHighOutlined />} title="Clean-up"><CleanupPanel job={p.job} onApplied={(j) => p.onUploaded(j)} inline /></Row>
            <Row icon={<PublicOutlined />} title="Projection"><CoordinatePanel job={p.job} mapping={p.mapping} schema={p.schema} onPatch={p.onPatch} /></Row>
            <Row icon={<BadgeOutlined />} title="Submission"><SubmissionPanel job={p.job} mapping={p.mapping} onPatch={p.onPatch} compact embedded /></Row>
          </Box>
        )}
      </Paper>
      {p.schema && p.job?.schema_id !== "adm_all" && <Box sx={full ? { px: { xs: 2, md: 3 }, py: 3 } : undefined}><SchemaPanel {...p} locked={!!busy} /></Box>}
    </Stack>
  );
}

/** one slim line of the post-upload checklist: quiet icon + label on the left, the content on the right */
/** Explode: re-read the drawing with every untagged block broken into its own lines, points and areas — values
 *  and levels hidden inside blocks become features the template can use. Tagged blocks keep their attributes. */
function ExplodeButton({ job, onDone, onError }: { job: JobState; onDone: (j: JobState) => void; onError: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <Tooltip title="Re-read the drawing with every block exploded into its own lines, points and areas (blocks with data tags keep them)">
      <span><Button size="small" variant="outlined" disabled={busy} sx={{ whiteSpace: "nowrap", height: 32, bgcolor: "#fff" }}
        onClick={async () => {
          setBusy(true);
          try { onDone(await api.setCleanup(job.job_id, { decisions: {}, settings: { explode_blocks: true }, apply: true })); }
          catch (e: any) { onError(e.message); } finally { setBusy(false); }
        }}>{busy ? "Exploding…" : "Explode blocks"}</Button></span>
    </Tooltip>
  );
}

function Row({ icon, title, children, tone, action }: { icon: React.ReactElement; title: string; children: React.ReactNode; tone?: string; action?: React.ReactNode }) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "168px 1fr" }, gap: { xs: 0.75, md: 2 }, alignItems: "start",
               px: { xs: 2, md: 3 }, py: 1.5, minHeight: 56, borderTop: `1px solid ${C.line}`, bgcolor: tone ? "#FFF7F7" : undefined }}>
      <Stack direction="row" spacing={1.25} alignItems="center" sx={{ minHeight: 32 }}>
        <Box sx={{ display: "grid", color: tone || "#8A919C", "& svg": { fontSize: 19 } }}>{icon}</Box>
        <Typography sx={{ fontSize: 13.5, fontWeight: 600, color: tone || C.ink }}>{title}</Typography>
        {action && <Box sx={{ ml: "auto !important", display: { md: "none" } }}>{action}</Box>}
      </Stack>
      {action && <Box sx={{ display: { xs: "none", md: "block" }, gridColumn: 1, gridRow: 2, mt: -0.5 }}>{action}</Box>}
      <Box sx={{ minWidth: 0 }}>{children}</Box>
    </Box>
  );
}

const ASSET_CODE = (id: string) => (id.match(/^adm_([a-z]+)/)?.[1] || "").toUpperCase();
const ASSET_NAME = (n: string) => n.replace(/^ADM\s+/, "").replace(/\s+—.*$/, "").replace(/\s*\([A-Z0-9]+\)\s*$/, "");

function DropZone({ caps, schemaId, job, onUploaded, onError, onAddSheets }: Props) {
  const [drag, setDrag] = useState(false);
  const [up, setUp] = useState<number | null>(null);
  const [name, setName] = useState("");
  const input = useRef<HTMLInputElement>(null);
    const sendAll = (list: FileList | File[] | null | undefined) => {
    const files = Array.from(list || []);
    if (!files.length) return;
    send(files[0], files.slice(1));
  };
  const send = async (f: File | undefined, more: File[] = []) => {
    if (!f) return;
    const ext = f.name.toLowerCase().split(".").pop();
    if (!["dxf", "dwg", "dgn", "zip"].includes(ext || "")) return onError("Accepted: .dwg, .dxf, .dgn (MicroStation V7), or a .zip with the drawing and its xrefs.");
    if (ext === "dwg" && caps && !caps.dwg.available)
      return onError("This DWG could not be read. Save it as AutoCAD 2010 or newer, or as DXF, and upload it again.");
    if (caps && f.size > caps.max_upload_mb * 1024 * 1024) return onError(`File exceeds the ${caps.max_upload_mb} MB limit.`);
    if (!schemaId) return onError("Choose the asset type first.");
    try {
      setName(more.length ? `${f.name} + ${more.length} more` : f.name); setUp(0);
      onUploaded(await api.upload(schemaId, f, setUp));
      if (more.length) onAddSheets?.(more);
    }
    catch (e: any) { onError(e.message); }
    finally { setUp(null); }
  };
  return (
    <Box onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); sendAll(e.dataTransfer.files); }}
      onClick={() => up == null && input.current?.click()} role="button" tabIndex={0} aria-label="Upload drawing"
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      sx={{ width: "100%", maxWidth: 560, border: `1.5px dashed ${drag ? C.cyan : "#D3D7DD"}`, bgcolor: drag ? C.cyanSoft : "#fff",
            borderRadius: 4, py: { xs: 6, md: 7 }, px: 3, textAlign: "center", cursor: up == null ? "pointer" : "default",
            transition: "all .15s", "&:hover": { borderColor: "#9AA1AB" }, "&:focus-visible": { outline: `2px solid ${C.cyan}`, outlineOffset: 3 } }}>
      <FileStack active={drag} />
      {up == null ? (
        <>
          <Typography sx={{ fontWeight: 650, fontSize: 16, mt: 2.5 }}>Click to upload or drop files</Typography>
          <Typography sx={{ color: C.muted, mt: 0.5, fontSize: 13.5 }}>DWG, DXF, DGN or ZIP</Typography>
          <Button variant="outlined" size="small" startIcon={<FileUploadOutlined />} onClick={(e) => { e.stopPropagation(); input.current?.click(); }}
            sx={{ mt: 2.5, borderRadius: 99, px: 2, color: C.ink, borderColor: "#D3D7DD", fontWeight: 500, "&:hover": { borderColor: "#9AA1AB", bgcolor: "#F7F8FA" } }}>
            Browse files
          </Button>
        </>
      ) : (
        <Box sx={{ maxWidth: 360, mx: "auto", mt: 2.5 }}>
          <Typography sx={{ fontWeight: 650 }} noWrap>{name}</Typography>
          <LinearProgress variant="determinate" value={up * 100} sx={{ mt: 1.5, height: 4, borderRadius: 2, "& .MuiLinearProgress-bar": { bgcolor: C.ink } }} />
          <Typography variant="caption">{Math.round(up * 100)}%</Typography>
        </Box>
      )}
      <input ref={input} type="file" hidden multiple accept=".dxf,.dwg,.dgn,.zip" onChange={(e) => { sendAll(e.target.files); e.target.value = ""; }} />
      {job?.status === "failed" && job.stage === "discovery" && <Alert severity="error" sx={{ mt: 2.5, mx: "auto", maxWidth: 480, textAlign: "left" }}>{job.message}</Alert>}
    </Box>
  );
}

/** three soft cards fanned out, the upload sign on the front one */
function FileStack({ active }: { active: boolean }) {
  const card = (rot: number, x: number, z: number, front = false) => (
    <Box sx={{ position: "absolute", left: "50%", top: 0, width: 44, height: 44, ml: `${-22 + x}px`, borderRadius: 2.5, bgcolor: "#fff",
               border: `1.5px solid ${front ? (active ? C.cyan : "#D3D7DD") : "#E6E9ED"}`, transform: `rotate(${rot}deg)`, zIndex: z,
               boxShadow: front ? "0 2px 6px rgba(16,24,40,.08)" : "none", display: "grid", placeItems: "center" }}>
      {front && <UploadFileOutlined sx={{ fontSize: 22, color: active ? C.cyan : "#5B6370" }} />}
    </Box>
  );
  return <Box sx={{ position: "relative", height: 46, width: 110, mx: "auto" }}>{card(-12, -24, 1)}{card(12, 24, 1)}{card(0, 0, 2, true)}</Box>;
}

/** template: one row of compact choices — Automatic first (all seven ADM templates) */
function TemplatePicker({ schemas, schemaId, onSchema, job }: Props) {
  const adm = schemas.filter((s) => s.status === "approved" && /^adm_/.test(s.schema_id) && s.schema_id !== "adm_all");
  const all = schemas.find((s) => s.schema_id === "adm_all");
  const other = schemas.filter((s) => !adm.includes(s) && s.schema_id !== "adm_all");
  const locked = !!job && (job.status === "queued" || job.status === "running");
  const chip = (id: string, label: string, title: string) => {
    const on = id === schemaId;
    return (
      <Tooltip key={id} title={title}>
        <ButtonBase disabled={locked} onClick={() => onSchema(id)} aria-pressed={on}
          sx={{ px: 1.5, height: 32, borderRadius: 99, fontSize: 13, fontWeight: on ? 700 : 500, bgcolor: on ? C.ink : "#fff", color: on ? "#fff" : C.ink,
                border: `1px solid ${on ? C.ink : "#D3D7DD"}`, "&:hover": { borderColor: on ? C.ink : "#9AA1AB" } }}>
          {label}
        </ButtonBase>
      </Tooltip>
    );
  };
  return (
    <Stack direction="row" alignItems="center" justifyContent="center" sx={{ gap: 0.75, flexWrap: "wrap", maxWidth: 760 }}>
      {all && chip(all.schema_id, "Automatic", "All 7 ADM templates — each layer goes to its own geodatabase")}
      {adm.map((s) => chip(s.schema_id, ASSET_CODE(s.schema_id), ASSET_NAME(s.display_name)))}
      {other.length > 0 && (
        <Select size="small" displayEmpty value={other.some((o) => o.schema_id === schemaId) ? schemaId : ""} onChange={(e) => onSchema(String(e.target.value))}
          renderValue={(v) => other.find((o) => o.schema_id === v)?.display_name || "Other"} sx={{ height: 32, borderRadius: 99, fontSize: 13, minWidth: 90 }}>
          {other.map((o) => <MenuItem key={o.schema_id} value={o.schema_id} dense>{o.display_name}</MenuItem>)}
        </Select>
      )}
    </Stack>
  );
}

function FileCard({ job, discovery, onAddSheets }: Props) {
  const add = useRef<HTMLInputElement>(null);
  const j = job!;
  const d = discovery;
  const busy = j.status === "queued" || j.status === "running";
  const spans = Object.entries(d?.schema_ranking?.find((x) => x.schema_id === "adm_all")?.templates || {})
    .filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const ignored = d ? [
    ...Object.entries(d.artifacts || {}).map(([k, v]) => `${v.toLocaleString()} ${k}`),
  ] : [];
  // objects the converter could not read as geometry (annotation types are not assets and are left out)
  const ANNOT = /^(DIMENSION|ARC_DIMENSION|LARGE_RADIAL_DIMENSION|LEADER|TOLERANCE|VIEWPORT|IMAGE|WIPEOUT|OLE2?FRAME|ATTDEF|ATTRIB|SEQEND|VERTEX|RAY|XLINE|.*UNDERLAY|LIGHT|SUN|(ACAD_)?TABLE)$/;
  const notRead = d ? Object.entries(d.skipped_types).filter(([k]) => !ANNOT.test(k)) : [];
  const nNotRead = notRead.reduce((a, [, v]) => a + v, 0);
  const recovered = d ? Object.entries(d.recovered_types || {}) : [];
  const meta = [
    [j.file, ...(j.extra_files || [])].length > 1 ? [j.file, ...(j.extra_files || [])].map((f) => f.name).join(" · ")
      : `${j.file.format.toUpperCase()} · ${fmtBytes(j.file.size)}${d?.converted_from_dwg ? ` · ${d.dxf_version}` : ""}`,
  ];
  const num = (v: number, k: string) => (
    <Box component="span" sx={{ whiteSpace: "nowrap" }}><Box component="b" sx={{ color: C.ink, fontWeight: 650 }}>{v.toLocaleString()}</Box> {k}</Box>
  );
  return (
    <Box sx={{ px: { xs: 2, md: 3 }, py: 2 }}>
      <Stack direction={{ xs: "column", md: "row" }} spacing={{ xs: 1.5, md: 3 }} alignItems={{ md: "center" }}>
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ flex: 1, minWidth: 0 }}>
          <Box sx={{ width: 40, height: 40, borderRadius: 2, bgcolor: "#F1F3F5", display: "grid", placeItems: "center", flex: "none" }}>
            <InsertDriveFileOutlined sx={{ color: C.ink, fontSize: 20 }} />
          </Box>
          <Box sx={{ minWidth: 0 }}>
            <Typography sx={{ fontWeight: 650, fontSize: 16 }} noWrap>{j.file.name}{j.extra_files?.length ? <Box component="span" sx={{ color: C.muted, fontWeight: 500 }}> + {j.extra_files.length} more</Box> : null}</Typography>
            <Stack direction="row" sx={{ columnGap: 1.5, rowGap: 0.25, flexWrap: "wrap", color: C.muted, fontSize: 12.5 }}>
              <span>{meta[0]}</span>
              {d && <>{num(d.layer_count, "layers")}{num(d.feature_entity_count, "objects")}{num(d.text_count, "labels")}</>}
            </Stack>
          </Box>
        </Stack>
        {d && spans.length > 0 && (
          <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" useFlexGap>
            <Typography variant="caption" sx={{ color: C.muted }}>→</Typography>
            {spans.map(([id, n]) => (
              <Tooltip key={id} title={`${n} layer${n === 1 ? "" : "s"} go to this template`}>
                <Chip size="small" label={ASSET_CODE(id) || id} sx={{ height: 24, bgcolor: "#F1F3F5", fontWeight: 650 }} />
              </Tooltip>
            ))}
          </Stack>
        )}
        {!busy && onAddSheets && (
          <>
            <Button size="small" variant="text" onClick={() => add.current?.click()} sx={{ color: C.muted, whiteSpace: "nowrap" }}>+ Add sheet</Button>
            <input ref={add} type="file" hidden multiple accept=".dxf,.dwg,.dgn"
              onChange={(e) => { const fs = Array.from(e.target.files || []); e.target.value = ""; if (fs.length) onAddSheets(fs); }} />
          </>
        )}
      </Stack>
      {busy && (
        <Box sx={{ mt: 1.5 }}>
          <LinearProgress variant="determinate" value={j.progress * 100} sx={{ height: 4, borderRadius: 2, "& .MuiLinearProgress-bar": { bgcolor: C.cyan } }} />
          <Typography variant="caption">{j.message}</Typography>
        </Box>
      )}
      {d && nNotRead > 0 && (
        <Alert severity="warning" sx={{ mt: 1.5, py: 0 }}>
          <b>{nNotRead.toLocaleString()} object{nNotRead === 1 ? "" : "s"} not read</b> — {notRead.map(([k, v]) => `${k} ×${v}`).join(", ")}.
          {" "}EXPLODE them in AutoCAD (or Export to AutoCAD from Civil 3D) and upload again.
        </Alert>
      )}
      {d && (recovered.length > 0 || ignored.length > 0) && (
        <Typography variant="caption" component="div" sx={{ mt: 1, color: C.muted }}>
          {recovered.length > 0 && <>Read from drawn geometry: {recovered.map(([k, v]) => `${k} ×${v}`).join(", ")}</>}
          {recovered.length > 0 && ignored.length > 0 && " · "}
          {ignored.length > 0 && (
            <Tooltip title={[...ignored, ...d.read_warnings.slice(0, 3)].join(" · ")}>
              <Box component="span" sx={{ cursor: "help", textDecoration: "underline dotted" }}>{ignored.length} kind{ignored.length === 1 ? "" : "s"} of clutter ignored</Box>
            </Tooltip>
          )}
        </Typography>
      )}
    </Box>
  );
}

function SchemaPanel({ schemas, schemaId, onSchema, schema, locked, caps, onSchemaUpdated, onError, job }: Props & { locked: boolean }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const layers = useMemo(() => (schema?.layers || []).filter((l) =>
    !q || l.name.toLowerCase().includes(q.toLowerCase()) || (l.alias || "").toLowerCase().includes(q.toLowerCase())), [schema, q]);
  return (
    <Paper sx={{ p: 2 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle2">{schema?.display_name}</Typography>
          <Typography variant="caption" component="div" noWrap>{[schema?.source_template, `${schema?.feature_class_count} feature classes`, `${schema?.domain_count} domains`].filter(Boolean).join(" · ")}</Typography>
        </Box>
        <Button size="small" onClick={() => setOpen(!open)} endIcon={<ExpandMoreOutlined sx={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .2s" }} />}>
          {open ? "Hide contents" : "Show contents"}
        </Button>
      </Stack>
      <Collapse in={open}>
      {job && <Select fullWidth sx={{ mt: 1.5 }} value={schemaId} disabled={locked} onChange={(e) => onSchema(String(e.target.value))}
        renderValue={(v) => schemas.find((s) => s.schema_id === v)?.display_name || "Select a schema"} displayEmpty>
        {schemas.map((s) => (
          <MenuItem key={s.schema_id} value={s.schema_id} dense>
            <ListItemText primary={s.display_name} secondary={`${s.feature_class_count} feature classes · ${s.domain_count} domains · v${s.version}`}
              primaryTypographyProps={{ fontSize: 13, fontWeight: 600 }} secondaryTypographyProps={{ fontSize: 11.5 }} />
            <Chip size="small" label={s.status} sx={{ ml: 1, bgcolor: s.status === "approved" ? C.greenSoft : C.amberSoft,
              color: s.status === "approved" ? C.green : C.amber }} />
          </MenuItem>
        ))}
      </Select>}
      {schema && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5, mb: 1 }}>{schema.description}</Typography>
          {schema.status !== "approved" && (
            <Alert severity="warning" sx={{ mb: 1, py: 0 }}>Draft schema — replace with the approved template GDB via schema import.</Alert>
          )}
          <Grid container columnSpacing={2}>
            <Grid item xs={6}>
              <KV k="Feature datasets" v={schema.feature_datasets.length || "—"} hint={schema.feature_datasets.join(", ")} />
              <KV k="Feature classes" v={schema.feature_class_count} />
              <KV k="Domains" v={schema.domain_count} />
              <KV k="Subtype classes" v={schema.subtype_layers || "—"} />
            </Grid>
            <Grid item xs={6}>
              <KV k="Coordinate system" v={schema.spatial_reference.epsg ? `EPSG:${schema.spatial_reference.epsg}` : "undefined"}
                hint={schema.spatial_reference.details?.name || schema.spatial_reference.name} />
              <KV k="Label rules" v={schema.label_rule_count} />
              <KV k="Network" v={schema.network_enabled ? schema.network_flow : "—"} />
              <KV k="Relationships" v={schema.relationship_count || "—"} />
            </Grid>
          </Grid>
          {schema.schema_id !== "adm_all" && <TemplateUpdates schema={schema} caps={caps} onUpdated={() => onSchemaUpdated?.()} onError={onError} />}
          {!schema.spatial_reference.verified && (
            <Typography variant="caption" sx={{ display: "flex", alignItems: "center", gap: 0.5, mt: 0.75, color: C.amber }}>
              <WarningAmberOutlined sx={{ fontSize: 14 }} /> Target CRS assumed (UTM 40N) — confirm against the approved template.
            </Typography>
          )}
          <Divider sx={{ my: 1.5 }} />
          <TextField fullWidth placeholder="Search feature classes" value={q} onChange={(e) => setQ(e.target.value)}
            InputProps={{ startAdornment: <InputAdornment position="start"><SearchOutlined sx={{ fontSize: 18 }} /></InputAdornment> }} />
          <Box sx={{ mt: 1, maxHeight: 380, overflow: "auto", border: `1px solid ${C.line}`, borderRadius: 1 }}>
            <Table size="small" stickyHeader>
              <TableHead><TableRow><TableCell>Feature class</TableCell><TableCell>Dataset</TableCell><TableCell align="right">Fields</TableCell></TableRow></TableHead>
              <TableBody>
                {layers.map((l) => (
                  <TableRow key={l.name} hover>
                    <TableCell>
                      <Stack direction="row" spacing={0.75} alignItems="center">
                        <GeomIcon g={l.geometry_type} />
                        <Tooltip title={l.alias && l.alias !== l.name ? l.alias : ""}><span>{l.name}</span></Tooltip>
                        {l.subtypes.length > 0 && <Chip size="small" label={`${l.subtypes.length} subtypes`} variant="outlined" />}
                        {l.network_role && <Chip size="small" label={l.network_role} variant="outlined" />}
                      </Stack>
                    </TableCell>
                    <TableCell sx={{ color: C.muted }}>{l.feature_dataset || "—"}</TableCell>
                    <TableCell align="right">{l.field_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Box>
        </>
      )}
      </Collapse>
    </Paper>
  );
}

function SchemaFit({ discovery, job, onSchema }: Props) {
  const all = discovery!.schema_ranking!.find((x) => x.schema_id === "adm_all");
  const r = discovery!.schema_ranking!.filter((x) => x.schema_id !== "adm_all").slice(0, 3);
  const best = r[0];
  const spans = Object.entries(all?.templates || {}).filter(([, n]) => n > 0);
  const current = r.find((x) => x.schema_id === job!.schema_id);
  const mismatch = best && best.schema_id !== job!.schema_id && best.coverage > (current?.coverage || 0) + 0.1;
  return (
    <Paper sx={{ p: 2 }}>
      <SectionTitle>Schema fit for this drawing</SectionTitle>
      {r.map((x) => (
        <Stack key={x.schema_id} direction="row" alignItems="center" spacing={1} sx={{ py: 0.4 }}>
          <Typography variant="body2" sx={{ width: 260, fontWeight: x.schema_id === job!.schema_id ? 700 : 400 }} noWrap>{x.display_name}</Typography>
          <Box sx={{ flex: 1, height: 6, bgcolor: "#EEF1F5", borderRadius: 3 }}>
            <Box sx={{ width: `${Math.round(x.coverage * 100)}%`, height: 6, bgcolor: x.schema_id === best.schema_id ? C.green : C.navy2, borderRadius: 3 }} />
          </Box>
          <Typography variant="caption" sx={{ width: 120, textAlign: "right" }}>{Math.round(x.coverage * 100)}% · {x.mapped_groups}/{x.groups} groups</Typography>
        </Stack>
      ))}
      {spans.length >= 2 && job!.schema_id !== "adm_all" && (
        <Alert severity="info" sx={{ mt: 1, py: 0 }} action={<Button size="small" onClick={() => onSchema("adm_all")}>Use all</Button>}>
          This drawing shows several asset types ({spans.map(([k, n]) => `${k.replace(/^adm_/, "").split("_")[0].toUpperCase()} ${n}`).join(", ")} layer groups).
          Choose <b>Several asset types</b> to deliver each feature class in its own template geodatabase.
        </Alert>
      )}
      {mismatch && spans.length < 2 && (
        <Alert severity="info" sx={{ mt: 1, py: 0 }} action={<Button size="small" onClick={() => onSchema(best.schema_id)}>Switch</Button>}>
          This drawing matches <b>{best.display_name}</b> better than the selected schema.
        </Alert>
      )}
    </Paper>
  );
}
