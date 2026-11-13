import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, ButtonBase, Chip, IconButton, LinearProgress, ListItemText, Menu, MenuItem, Snackbar, Stack, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from "@mui/material";
import AccountTreeOutlined from "@mui/icons-material/AccountTreeOutlined";
import ViewAgendaOutlined from "@mui/icons-material/ViewAgendaOutlined";
import { WorkflowCanvas } from "./workflow/WorkflowCanvas";
import { MappingBoard } from "./components/MappingBoard";
import CableOutlined from "@mui/icons-material/CableOutlined";
import { buildModel } from "./workflow/model";
import ArticleOutlined from "@mui/icons-material/ArticleOutlined";
import ArrowForwardOutlined from "@mui/icons-material/ArrowForwardOutlined";
import { api } from "./api";
import type { Capabilities, Discovery, GeoJSON, JobState, Mapping, Results, SchemaDetail, SchemaSummary } from "./types";
import { C } from "./theme";
import { SourceStep } from "./components/SourceStep";
import { LayersStep } from "./components/LayersStep";
import { FieldsStep } from "./components/FieldsStep";
import { ValidateStep } from "./components/ValidateStep";
import { GenerateStep } from "./components/GenerateStep";
import { ReviewStep, DetailsDrawer } from "./components/ReviewStep";
import FileUploadOutlined from "@mui/icons-material/FileUploadOutlined";
import LayersOutlined from "@mui/icons-material/LayersOutlined";
import MapOutlined from "@mui/icons-material/MapOutlined";
import DownloadOutlined from "@mui/icons-material/DownloadOutlined";
import { AppHeader } from "./components/AppHeader";
import ArrowBackOutlined from "@mui/icons-material/ArrowBackOutlined";
import type { AuthUser } from "./types";

const STEPS = ["Upload", "Layers", "Review", "Deliver"];
const STEP_ICONS = [FileUploadOutlined, LayersOutlined, MapOutlined, DownloadOutlined];
const NEXT = ["Layers", "Review", "Deliver"];
// old 7-step numbers (still used by a few panels) → the 4 steps
const FROM_OLD = (n: number) => (n <= 1 ? n : n >= 6 ? 3 : 2);

function jobFromHash(): string | null {
  const m = window.location.hash.match(/job=([0-9a-f]{32})/);
  return m ? m[1] : null;
}

interface AppProps { me?: AuthUser | null; mode?: string; go?: (r: string) => void; onLogout?: () => void; refreshMe?: () => void }

function Pill({ tone, children }: { tone: "green" | "amber" | "red" | "muted"; children: React.ReactNode }) {
  const [bg, c] = tone === "green" ? ["#DCFCE7", "#15803D"] : tone === "red" ? ["#FEE2E2", "#B91C1C"] : tone === "muted" ? ["#F1F2F4", "#646B76"] : ["#FEF3C7", "#92400E"];
  return <Box sx={{ px: 1.1, py: 0.3, borderRadius: 99, bgcolor: bg, color: c, fontSize: 12, fontWeight: 700, whiteSpace: "nowrap" }}>{children}</Box>;
}

export default function App({ me = null, mode = "none", go = () => undefined, onLogout = () => undefined, refreshMe = () => undefined }: AppProps) {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [schemas, setSchemas] = useState<SchemaSummary[]>([]);
  const [schemaId, setSchemaId] = useState("");
  const [schema, setSchema] = useState<SchemaDetail | null>(null);
  const [job, setJob] = useState<JobState | null>(null);
  const [discovery, setDiscovery] = useState<Discovery | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [preview, setPreview] = useState<GeoJSON | null>(null);
  const [step, setStepState] = useState(0);
  // a step shows as done only once the user has been there (no ticks on steps nobody opened)
  const [visited, setVisited] = useState<Record<number, boolean>>({});
  const setStep = (n: number) => { setStepState(n); setVisited((v) => (v[n] ? v : { ...v, [n]: true })); };
  type View = "board" | "workflow" | "steps" | "map";
  const [view, setViewState] = useState<View>(() => { try { const v = localStorage.getItem("gsb_view2") as View; return v || "board"; } catch { return "board"; } });
  const setView = (v: View) => { setViewState(v); try { localStorage.setItem("gsb_view2", v); } catch { /* ignore */ } };
  const [toast, setToast] = useState<{ msg: string; sev: "error" | "success" | "info" } | null>(null);
  const [recent, setRecent] = useState<null | HTMLElement>(null);
  const [recentJobs, setRecentJobs] = useState<Partial<JobState>[]>([]);
  const lastStatus = useRef<string>("");

  const err = useCallback((msg: string) => setToast({ msg, sev: "error" }), []);
  const note = useCallback((msg: string) => setToast({ msg, sev: "success" }), []);

  useEffect(() => {
    api.capabilities().then(setCaps).catch((e) => err(`Server not reachable: ${e.message}`));
    const t = setInterval(() => { api.capabilities().then(setCaps).catch(() => undefined); }, 15000);
    api.schemas().then((s) => {
      setSchemas(s);
      if (!jobFromHash() && s.length) setSchemaId((s.find((x) => x.schema_id === "adm_all") || s[0]).schema_id);
    }).catch((e) => err(e.message));
    const jid = jobFromHash();
    if (jid) api.job(jid).then((j) => { setJob(j); setSchemaId(j.schema_id); }).catch(() => (window.location.hash = ""));
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!schemaId) return;
    api.schema(schemaId).then(setSchema).catch((e) => err(e.message));
  }, [schemaId]);

  // load artefacts whenever the job reaches a new state
  useEffect(() => {
    if (!job) return;
    const key = `${job.job_id}:${job.status}:${job.stage}`;
    if (key === lastStatus.current) return;
    lastStatus.current = key;
    const id = job.job_id;
    if (["ready", "validated", "completed"].includes(job.status) || (job.status === "failed" && job.stage !== "discovery")) {
      api.discovery(id).then(setDiscovery).catch(() => undefined);
      api.mapping(id).then(setMapping).catch(() => undefined);
    }
    if (["validated", "completed"].includes(job.status) || (job.status === "failed" && job.stage === "generate")) {
      api.results(id).then(setResults).catch(() => undefined);
      api.preview(id).then(setPreview).catch(() => undefined);
    }
    if (job.status === "completed") note("Generated");
  }, [job]);

  // poll while the server is working
  useEffect(() => {
    if (!job || !["queued", "running"].includes(job.status)) return;
    const t = setInterval(() => api.job(job.job_id).then(setJob).catch(() => undefined), 700);
    return () => clearInterval(t);
  }, [job?.job_id, job?.status]);

  // automatic conversion: upload -> route every layer -> validate -> (values the drawing lacks?) -> geodatabases
  const [auto, setAuto] = useState<null | "mapping" | "validating" | "generating">(null);
  const pendingSheets = useRef<File[]>([]);
  const jobRef = useRef<JobState | null>(null);
  jobRef.current = job;
  const addSheets = async (files: File[]) => {
    const j = jobRef.current;
    setAuto("mapping");
    if (!j || ["queued", "running"].includes(j.status)) { pendingSheets.current.push(...files); return; }
    const [f, ...rest] = files;
    pendingSheets.current.push(...rest);
    try { setDiscovery(null); setResults(null); setJob(await api.addDrawing(j.job_id, f)); }
    catch (e: any) { setAuto(null); err(e.message); }
  };
  const onUploaded = (j: JobState) => {
    setJob(j); setDiscovery(null); setMapping(null); setResults(null); setPreview(null);
    window.location.hash = `job=${j.job_id}`;
    setAuto("mapping");
  };
  useEffect(() => {
    if (!auto || !job) return;
    if (job.status === "failed") { setAuto(null); return; }
    (async () => {
      try {
        if (auto === "mapping" && job.status === "ready" && pendingSheets.current.length) {
          const f = pendingSheets.current.shift()!;
          setDiscovery(null); setResults(null);
          setJob(await api.addDrawing(job.job_id, f));
          return;
        }
        if (auto === "mapping" && job.status === "ready") {
          setAuto("validating");
          let mm = await api.acceptSuggestions(job.job_id, 0.45);
          // project memory: the DEM used for earlier drawings is fetched for this one too
          const mem = await api.memory().catch(() => null) as any;
          if (mem?.dem_source && !mm.dem) mm = await api.prepareDem(job.job_id, mem.dem_source).catch(() => mm);
          setMapping(mm);
          setJob(await api.validate(job.job_id));
        } else if (auto === "validating" && job.status === "validated") {
          // checks done in the background — the user goes through the steps with Next, one by one
          setAuto(null);
          const comp = await api.completeness(job.job_id).catch(() => null);
          if (comp && !comp.complete && comp.open_groups.length) note("Checked · layers to review");
          else note("Checked");
        } else if (auto === "generating" && ["completed"].includes(job.status)) {
          setAuto(null); refreshMe();
        }
      } catch (e: any) { setAuto(null); err(e.message); }
    })();
  }, [auto, job?.status, job?.job_id]);

  const onSchema = async (id: string) => {
    setSchemaId(id);
    if (job && ["ready", "validated", "completed"].includes(job.status)) {
      try {
        const j = await api.changeSchema(job.job_id, id);
        setJob(j); setResults(null); setPreview(null);
        setMapping(await api.mapping(job.job_id));
        note("Template changed");
      } catch (e: any) { err(e.message); }
    }
  };

  const onPatch = useCallback(async (patch: unknown) => {
    if (!job) return;
    try {
      setMapping(await api.patchMapping(job.job_id, patch));
      const j = await api.job(job.job_id);
      setJob(j);
    } catch (e: any) { err(e.message); }
  }, [job?.job_id]);

  const run = async (kind: "validate" | "generate") => {
    if (!job) return;
    try {
      setJob(await (kind === "validate" ? api.validate(job.job_id) : api.generate(job.job_id)));
      if (kind === "generate") refreshMe();
    }
    catch (e: any) { err(e.message); if (kind === "generate") refreshMe(); }
  };

  const reset = () => {
    setJob(null); setDiscovery(null); setMapping(null); setResults(null); setPreview(null); setStep(0); setVisited({});
    lastStatus.current = ""; window.location.hash = "/app/new";
  };

  void setRecentJobs;

  const ready = !!mapping && !!discovery;
  const enabled = [true, ready, ready && !!results, ready && !!results];
  const [details, setDetails] = useState<null | "attributes" | "issues">(null);
  const busy = job && ["queued", "running"].includes(job.status);
  const [cadGeo, setCadGeo] = useState<{ id: string; geo: GeoJSON } | null>(null);
  useEffect(() => {
    const cid = job && discovery ? `${job.job_id}:${discovery.entity_count}` : "";
    if (!job?.job_id || !discovery || view !== "workflow" || cadGeo?.id === cid) return;
    api.cadPreview(job.job_id).then((geo) => setCadGeo({ id: cid, geo })).catch(() => undefined);
  }, [job?.job_id, discovery, view]);
  const workflowModel = useMemo(() => (job && schema && discovery && mapping
    ? (() => { try { return buildModel(job, discovery, mapping, schema, results, cadGeo?.id === `${job.job_id}:${discovery.entity_count}` ? cadGeo.geo : null); } catch { return null; } })() : null),
    [job?.status, job?.job_id, schema, discovery, mapping, results, cadGeo]);
  const showWorkflow = view === "workflow" && step === 1 && !!workflowModel;
  const showBoard = view === "board" && step === 1 && !!(job && schema && discovery && mapping);
  // same selection as the Error Inspector of the Topology step
  const TOPO_GEOM = ["z_spike", "z_not_finite", "self_intersection", "invalid_geometry"];
  const topoOpen = results ? results.issues.filter((i) => (["topology", "topology_standard", "network"].includes(i.category) || TOPO_GEOM.includes(i.code))
    && !["rule_not_evaluated", "geometry_repaired"].includes(i.code) && !i.message.includes("marked as exception")).length : 0;
  const nErrors = results ? results.summary.issues_by_severity.error : 0;
  const autoVals = useMemo(() => {
    const m: Record<string, Record<string, string>> = {};
    (results?.issues || []).forEach((i) => {
      if (i.code !== "not_null_placeholder" || !i.target_layer || !i.field) return;
      const v = i.message.match(/: '(.*)' (filled automatically|written)/);
      if (v) (m[i.target_layer] = m[i.target_layer] || {})[i.field] = v[1];
    });
    return m;
  }, [results]);
  const unresolved = mapping ? Object.values(mapping.layers).filter((m) => m.role !== "ignore" && (!m.target || ["SUGGESTED", "MANUAL"].includes(m.status))).length : 0;

  return (
    <Box sx={{ minHeight: "100vh", bgcolor: "background.default" }}>
      <AppHeader me={me} mode={mode} go={go} onLogout={onLogout} active="app" right={<>
          {caps && !caps.filegdb && <Chip size="small" label="File GDB unavailable" sx={{ bgcolor: C.redSoft, color: C.red }} />}
          {job && <Tooltip title="Processing log"><IconButton size="small" sx={{ color: "#9AA1AC" }} href={`./api/jobs/${job.job_id}/log`} target="_blank"><ArticleOutlined fontSize="small" /></IconButton></Tooltip>}
          {!me && caps && <Typography sx={{ fontSize: 12, opacity: 0.7 }}>{caps.user}</Typography>}
        </>} />

      <Box sx={{ display: "flex", alignItems: "stretch" }}>
        {/* ---- steps: one slim icon rail */}
        <Box component="nav" aria-label="Steps" sx={{ width: 76, flex: "none", bgcolor: "#fff", borderRight: `1px solid ${C.line}`, position: "sticky", top: 0,
                   alignSelf: "flex-start", height: "calc(100vh - 56px)", display: "flex", flexDirection: "column", alignItems: "center", pt: 1.5, gap: 0.75 }}>
          {STEPS.map((t, i) => {
            const Icon = STEP_ICONS[i];
            const done = [!!discovery, !!visited[1] && !!mapping && unresolved === 0, !!visited[2] && !!results && !nErrors && !topoOpen, job?.status === "completed"][i];
            const warn = [false, !!mapping && unresolved > 0, !!results && (nErrors > 0 || topoOpen > 0), false][i];
            const on = step === i;
            return (
              <Tooltip key={t} placement="right" title={[job ? `${discovery?.feature_entity_count.toLocaleString() || ""} objects read` : "Choose the template and the drawing",
                mapping ? (unresolved ? `${unresolved} layer${unresolved > 1 ? "s" : ""} to check` : "All layers connected") : "",
                results ? `${nErrors + topoOpen} to fix` : "", job?.status === "completed" ? "Ready to download" : "One geodatabase per template"][i] || t}>
                <span>
                  <ButtonBase disabled={!enabled[i] || (!!busy && i !== step)} onClick={() => setStep(i)} aria-current={on ? "step" : undefined}
                    sx={{ width: 60, py: 1, borderRadius: 2, flexDirection: "column", gap: 0.4, position: "relative", color: on ? C.cyan : enabled[i] ? C.ink : "#B8BDC5",
                          bgcolor: on ? C.cyanSoft : "transparent", "&:hover": { bgcolor: on ? C.cyanSoft : "#F4F5F7" } }}>
                    <Icon sx={{ fontSize: 22 }} />
                    <Typography sx={{ fontSize: 11, fontWeight: on ? 700 : 500, lineHeight: 1 }}>{t}</Typography>
                    {(done || warn) && <Box sx={{ position: "absolute", top: 6, right: 12, width: 8, height: 8, borderRadius: "50%", bgcolor: warn ? "#F59E0B" : C.green, border: "1.5px solid #fff" }} />}
                  </ButtonBase>
                </span>
              </Tooltip>
            );
          })}
        </Box>

        <Box sx={{ flex: 1, minWidth: 0 }}>
          {/* ---- one line: the drawing, where it stands, the next action */}
          <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: { xs: 2, md: 3 }, height: 60, bgcolor: "#fff", borderBottom: `1px solid ${C.line}`, position: "sticky", top: 0, zIndex: 6 }}>
            <Tooltip title="All conversions"><IconButton size="small" onClick={() => go("/app")} sx={{ color: C.muted }}><ArrowBackOutlined fontSize="small" /></IconButton></Tooltip>
            <Typography sx={{ fontWeight: 700, fontSize: 15.5, minWidth: 0, maxWidth: 420 }} noWrap>{job ? job.file.name : "New conversion"}</Typography>
            {results && step > 0 && <>
              <Pill tone={results.summary.features_imported ? "green" : "muted"}>{results.summary.features_imported.toLocaleString()} ready</Pill>
              {results.summary.features_failed > 0 && <Pill tone="red">{results.summary.features_failed.toLocaleString()} blocked</Pill>}
              {(nErrors + topoOpen) > 0 && <Pill tone="amber">{(nErrors + topoOpen).toLocaleString()} to fix</Pill>}
            </>}
            {(busy || auto) && <Typography sx={{ fontSize: 12, color: C.muted }} noWrap>{job?.message || "working…"}</Typography>}
            <Box sx={{ flex: 1 }} />
            {step === 1 && workflowModel && (
              <ToggleButtonGroup size="small" exclusive value={view === "workflow" ? "workflow" : view === "board" ? "board" : "steps"} onChange={(_, v) => v && setView(v)} aria-label="View">
                <Tooltip title="Connections"><ToggleButton value="board" aria-label="Connections"><CableOutlined fontSize="small" /></ToggleButton></Tooltip>
                <Tooltip title="Workspace"><ToggleButton value="workflow" aria-label="Workspace"><AccountTreeOutlined fontSize="small" /></ToggleButton></Tooltip>
                <Tooltip title="List"><ToggleButton value="steps" aria-label="List"><ViewAgendaOutlined fontSize="small" /></ToggleButton></Tooltip>
              </ToggleButtonGroup>
            )}
            {step > 0 && <Button onClick={() => setStep(step - 1)} sx={{ color: C.muted, minWidth: 0 }}>Back</Button>}
            {step < 3 && (
              <Button variant="contained" endIcon={<ArrowForwardOutlined />} disabled={!enabled[step + 1] || !!busy} onClick={() => setStep(step + 1)}>
                {NEXT[step]}
              </Button>
            )}
            {step === 3 && job?.status === "completed" && <Button variant="outlined" onClick={reset}>New conversion</Button>}
          </Stack>

          <Box sx={(showWorkflow || showBoard || (step === 0 && !!discovery && !!mapping)) ? { p: 0 } : { px: { xs: 1.5, md: step === 2 ? 1.5 : 3 }, pt: step === 2 ? 1.5 : 3, pb: 4, maxWidth: step === 2 ? "none" : 1600, mx: "auto" }}>
            {caps && caps.update_pending !== false && (
              <Alert severity="warning" sx={{ mb: 2 }}>
                <b>The update is installed but the server is still running the previous version.</b> Close the server window and run
                start_windows.bat again — needed once; later updates restart by themselves.
                <Box dir="rtl" sx={{ mt: 0.5 }}>تم تثبيت التحديث لكن الخادم ما زال يعمل بالنسخة السابقة: أغلق نافذة الخادم وشغّل start_windows.bat مرة واحدة — التحديثات التالية تُطبَّق تلقائياً.</Box>
              </Alert>
            )}
            {auto && step === 0 && <LinearProgress sx={{ mb: 2, height: 3, borderRadius: 2 }} />}
            {showWorkflow && workflowModel && <WorkflowCanvas model={workflowModel} />}
            {showBoard && job && schema && discovery && mapping &&
              <MappingBoard job={job} schema={schema} discovery={discovery} mapping={mapping} onPatch={onPatch} onMapping={setMapping} onError={err} />}
            {step === 0 && <SourceStep caps={caps} schemas={schemas} schemaId={schemaId} onSchema={onSchema} schema={schema} job={job}
                onSchemaUpdated={async () => { setSchemas(await api.schemas()); setSchema(await api.schema(schemaId)); if (job) { setJob(await api.job(job.job_id)); setMapping(await api.mapping(job.job_id)); } note("Template updated"); }}
              discovery={discovery} mapping={mapping} onUploaded={onUploaded} onPatch={onPatch} onNext={() => setStep(1)} onError={err}
              onValidate={() => { setAuto("validating"); run("validate"); }} onGoElevation={() => { if (results) { setStep(2); setDetails("attributes"); } }} onAddSheets={addSheets} />}
            {!showWorkflow && !showBoard && step === 1 && job && schema && discovery && mapping &&
              <LayersStep job={job} schema={schema} discovery={discovery} mapping={mapping} onPatch={onPatch} onMapping={setMapping} onError={err} onNotice={note} />}
            {step === 2 && job && <ReviewStep job={job} preview={preview} results={results} busy={!!busy}
              onValidate={() => run("validate")} onError={err} onNotice={note} onPatch={onPatch} onJob={setJob} openDetails={setDetails} />}
            {step === 3 && job && schema && <GenerateStep job={job} schema={schema} results={results} onGenerate={() => run("generate")} onNew={reset}
              onGoStep={(n) => setStep(FROM_OLD(n))} mapping={mapping} onPatch={onPatch} />}
          </Box>
        </Box>
      </Box>

      <DetailsDrawer open={details === "attributes"} title="Attribute sources & elevation" onClose={() => setDetails(null)}>
        {job && schema && discovery && mapping && <FieldsStep job={job} schema={schema} discovery={discovery} mapping={mapping} onPatch={onPatch} autoVals={autoVals}
          onValidate={() => { setDetails(null); run("validate"); }} onMapping={setMapping} onError={err} onNotice={note} />}
      </DetailsDrawer>
      <DetailsDrawer open={details === "issues"} title="All checks" onClose={() => setDetails(null)}>
        {job && mapping && <ValidateStep job={job} mapping={mapping} results={results} preview={preview} onPatch={onPatch} onRun={() => run("validate")} onError={err} />}
      </DetailsDrawer>

      <Menu anchorEl={recent} open={!!recent} onClose={() => setRecent(null)} PaperProps={{ sx: { width: 380, maxHeight: 420 } }}>
        <MenuItem onClick={() => { setRecent(null); reset(); }} dense><b>+ New conversion</b></MenuItem>
        {recentJobs.map((j) => (
          <MenuItem key={j.job_id} dense onClick={() => {
            setRecent(null); reset();
            window.location.hash = `job=${j.job_id}`;
            api.job(j.job_id!).then((x) => { setJob(x); setSchemaId(x.schema_id); }).catch((e) => err(e.message));
          }}>
            <ListItemText primary={j.file?.name} secondary={`${j.schema_name} · ${j.status} · ${j.created_at?.slice(0, 16).replace("T", " ")}`}
              primaryTypographyProps={{ fontSize: 12.5, fontWeight: 600, noWrap: true }} secondaryTypographyProps={{ fontSize: 11 }} />
          </MenuItem>
        ))}
        {!recentJobs.length && <MenuItem disabled dense>No previous conversions</MenuItem>}
      </Menu>

      <Snackbar open={!!toast} autoHideDuration={toast?.sev === "error" ? 8000 : 4000} onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        {toast ? <Alert severity={toast.sev} variant="filled" onClose={() => setToast(null)} sx={{ fontSize: 12.5 }}>{toast.msg}</Alert> : undefined}
      </Snackbar>
    </Box>
  );
}
