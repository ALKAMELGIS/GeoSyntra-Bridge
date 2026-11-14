import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert, Box, Button, ButtonBase, Chip, CircularProgress, IconButton, LinearProgress, ListItemText, Menu, MenuItem, Snackbar, Stack, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
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
import CheckRounded from "@mui/icons-material/CheckRounded";
import { AppHeader } from "./components/AppHeader";
import ArrowBackOutlined from "@mui/icons-material/ArrowBackOutlined";
import type { AuthUser } from "./types";
import { tr } from "./i18n";

const STEPS = ["Upload", "Layers", "Review", "Deliver"];
// old 7-step numbers (still used by a few panels) → the 4 steps
const FROM_OLD = (n: number) => (n <= 1 ? n : n >= 6 ? 3 : 2);

function jobFromHash(): string | null {
  const m = window.location.hash.match(/job=([0-9a-f]{32})/);
  return m ? m[1] : null;
}

interface AppProps { me?: AuthUser | null; mode?: string; go?: (r: string) => void; onLogout?: () => void; refreshMe?: () => void }

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
    // a conversion that cannot be opened says why and goes back to the list, never to an empty upload page
    if (jid) api.job(jid).then((j) => { setJob(j); setSchemaId(j.schema_id); })
      .catch((e: any) => {
        try { sessionStorage.setItem("gsb_open_error", `Could not open this conversion: ${e?.message || "not found"}`); } catch { /* ignore */ }
        window.location.hash = "/app";
      });
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
  const goAfterCheck = useRef<number | null>(null);   // Next pressed before the checks ran: open this step when they finish
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
          if (goAfterCheck.current != null) { setStep(goAfterCheck.current); goAfterCheck.current = null; }
          const comp = await api.completeness(job.job_id).catch(() => null);
          if (comp && !comp.complete && comp.open_groups.length) note("Checked · layers to review");
          else note("Checked");
        } else if (auto === "generating" && ["completed"].includes(job.status)) {
          setAuto(null); refreshMe();
        }
      } catch (e: any) { setAuto(null); goAfterCheck.current = null; err(e.message); }
    })();
  }, [auto, job?.status, job?.job_id]);
  useEffect(() => {
    if (auto === "validating" && job?.status === "failed") { setAuto(null); goAfterCheck.current = null; err(`The check stopped: ${job.error?.message || job.message || "unknown error"}`); }
  }, [auto, job?.status]);

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

      <Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          {/* ---- one line: the drawing, where it stands, the next action */}
          <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: { xs: 2, md: 3 }, height: 60, bgcolor: "#fff", borderBottom: `1px solid ${C.line}`, position: "sticky", top: 0, zIndex: 6 }}>
            <Tooltip title="All conversions"><IconButton size="small" onClick={() => go("/app")} sx={{ color: C.muted }}><ArrowBackOutlined fontSize="small" /></IconButton></Tooltip>
            <Typography sx={{ fontWeight: 700, fontSize: 15, minWidth: 0, maxWidth: 260, display: { xs: "none", lg: "block" } }} noWrap>{job ? job.file.name : tr("New conversion")}</Typography>
            {/* ---- the four steps, numbered, in one line */}
            <Stack component="nav" aria-label="Steps" direction="row" alignItems="center" sx={{ flex: 1, justifyContent: "center", minWidth: 0 }}>
              {STEPS.map((t, i) => {
                const done = [!!discovery, !!visited[1] && !!mapping && unresolved === 0, !!visited[2] && !!results && !nErrors && !topoOpen, job?.status === "completed"][i];
                const warn = [0, mapping ? unresolved : 0, results ? nErrors + topoOpen : 0, 0][i];
                const on = step === i;
                return (
                  <Stack key={t} direction="row" alignItems="center">
                    {i > 0 && <Box sx={{ width: { xs: 10, md: 28 }, height: 1.5, bgcolor: done || on ? C.ink : C.line, mx: 0.5 }} />}
                    <ButtonBase disabled={!enabled[i] || (!!busy && i !== step)} onClick={() => setStep(i)} aria-current={on ? "step" : undefined}
                      sx={{ px: 1.25, py: 0.75, borderRadius: 99, gap: 0.9, bgcolor: on ? C.ink : "transparent", color: on ? "#fff" : enabled[i] ? C.ink : "#B8BDC5",
                            "&:hover": { bgcolor: on ? C.ink : "#F1F2F4" } }}>
                      <Box sx={{ width: 20, height: 20, borderRadius: "50%", display: "grid", placeItems: "center", fontSize: 11.5, fontWeight: 700, flex: "none",
                                 bgcolor: on ? "#fff" : done ? C.green : "transparent", color: on ? C.ink : done ? "#fff" : "inherit",
                                 border: on || done ? "none" : `1.5px solid ${enabled[i] ? C.ink : C.line}` }}>
                        {done && !on ? <CheckRounded sx={{ fontSize: 14 }} /> : i + 1}
                      </Box>
                      <Typography sx={{ fontSize: 13.5, fontWeight: on ? 700 : 600, display: { xs: on ? "block" : "none", sm: "block" } }}>{tr(t)}</Typography>
                      {warn > 0 && <Box sx={{ fontSize: 11, fontWeight: 700, px: 0.75, borderRadius: 99, bgcolor: "#FEF3C7", color: "#92400E" }}>{warn}</Box>}
                    </ButtonBase>
                  </Stack>
                );
              })}
            </Stack>
            {(busy || auto) && <Typography sx={{ fontSize: 12, color: C.muted, maxWidth: 200 }} noWrap>{job?.message || tr("working…")}</Typography>}
            {step === 1 && workflowModel && (
              <ToggleButtonGroup size="small" exclusive value={view === "workflow" ? "workflow" : view === "board" ? "board" : "steps"} onChange={(_, v) => v && setView(v)} aria-label="View">
                <Tooltip title={tr("Connections")}><ToggleButton value="board" aria-label="Connections"><CableOutlined fontSize="small" /></ToggleButton></Tooltip>
                <Tooltip title={tr("Workspace")}><ToggleButton value="workflow" aria-label="Workspace"><AccountTreeOutlined fontSize="small" /></ToggleButton></Tooltip>
                <Tooltip title={tr("List")}><ToggleButton value="steps" aria-label="List"><ViewAgendaOutlined fontSize="small" /></ToggleButton></Tooltip>
              </ToggleButtonGroup>
            )}
            {step < 3 && (
              (() => {
                // Layers → Review needs the checks; if they have not run yet, Next runs them and then opens Review
                const needsCheck = step === 1 && ready && !results;
                const checking = auto === "validating" || (!!busy && needsCheck);
                return (
                  <Tooltip title={checking ? tr("Checking the data against the template — Review opens when it is done")
                    : !enabled[step + 1] && !needsCheck ? (step === 0 ? tr("Upload a drawing first") : tr("Connect the layers first")) : ""}>
                    <span>
                      <Button variant="contained" disabled={checking || !!busy || (!enabled[step + 1] && !needsCheck)}
                        endIcon={checking ? <CircularProgress size={14} color="inherit" /> : <ArrowForwardOutlined />}
                        onClick={async () => {
                          if (!needsCheck || !job) { setStep(step + 1); return; }
                          try { goAfterCheck.current = step + 1; setAuto("validating"); setJob(await api.validate(job.job_id)); }
                          catch (e: any) { setAuto(null); goAfterCheck.current = null; err(e.message); }
                        }}>
                        {checking ? tr("Checking…") : tr("Next")}
                      </Button>
                    </span>
                  </Tooltip>
                );
              })()
            )}
            {step === 3 && job?.status === "completed" && <Button variant="outlined" onClick={reset}>{tr("New conversion")}</Button>}
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
        {toast ? <Alert severity={toast.sev} variant="filled" onClose={() => setToast(null)} sx={{ fontSize: 12.5 }}>{tr(toast.msg)}</Alert> : undefined}
      </Snackbar>
    </Box>
  );
}
