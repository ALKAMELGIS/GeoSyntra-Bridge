import { useEffect, useState } from "react";
import { Alert, Box, Button, Collapse, Drawer, IconButton, LinearProgress, Paper, Stack, Table, TableBody, TableCell, TableRow, Tooltip, Typography } from "@mui/material";
import DescriptionOutlined from "@mui/icons-material/DescriptionOutlined";
import Inventory2Outlined from "@mui/icons-material/Inventory2Outlined";
import ArrowForwardRounded from "@mui/icons-material/ArrowForwardRounded";
import RefreshOutlined from "@mui/icons-material/RefreshOutlined";
import CloseOutlined from "@mui/icons-material/CloseOutlined";
import StorageOutlined from "@mui/icons-material/StorageOutlined";
import CheckCircle from "@mui/icons-material/CheckCircle";
import Cancel from "@mui/icons-material/Cancel";
import ErrorOutline from "@mui/icons-material/ErrorOutline";
import FileDownloadOutlined from "@mui/icons-material/FileDownloadOutlined";
import { api, billing } from "../api";
import { DeliveryTable } from "./RequiredValues";
import type { Completeness, JobState, Mapping, Results, SchemaDetail } from "../types";
import { SubmissionPanel } from "./SubmissionPanel";
import { C } from "../theme";
import { fmtBytes } from "./ui";

interface Props {
  job: JobState; schema: SchemaDetail; results: Results | null; onGenerate: () => void; onNew: () => void;
  mapping: Mapping | null; onPatch: (p: unknown) => Promise<void>;
  /** opens the step where a blocker is fixed */
  onGoStep?: (step: number, label: string) => void;
}

const LABEL: Record<string, string> = { IR: "Irrigation", HL: "Hard Landscaping", RD: "Roads", SL: "Soft Landscaping",
  SW: "Storm Water", STL: "Street Lighting", ITC: "ITC" };
const codeOf = (name: string) => (/ADM_([A-Z]+)/i.exec(name)?.[1] || "").toUpperCase();

/** Deliver: one status, one button, one card per geodatabase. Details stay folded. */
export function GenerateStep({ job, schema, results, onGenerate, mapping, onPatch, onGoStep }: Props) {
  const busy = job.status === "queued" || job.status === "running";
  const [comp, setComp] = useState<Completeness | null>(null);
  const [showVer, setShowVer] = useState(false);
  const [showMap, setShowMap] = useState(false);
  useEffect(() => { api.completeness(job.job_id).then(setComp).catch(() => setComp(null)); }, [job.status, job.stage, results]);
  const [quote, setQuote] = useState<{ enforced: boolean; cost?: number; balance?: number; enough?: boolean; already_paid?: boolean; features?: number } | null>(null);
  useEffect(() => { billing.quote(job.job_id).then(setQuote).catch(() => setQuote(null)); }, [job.status, results]);
  const s = results?.summary;
  const done = job.status === "completed" || (job.status === "failed" && job.stage === "generate" && !!job.outputs?.gdb_zip);
  const ver = results?.verification;
  const gdbs: any[] = (results?.gdb as any)?.geodatabases?.length ? (results!.gdb as any).geodatabases
    : results?.gdb ? [{ name: results.gdb.name, counts: results.gdb.counts, size: results.gdb.size, single: true }] : [];
  const blockers = comp ? [
    ...comp.open_groups.map((g) => `${g.group.split("::")[0]} — ${g.target ? `confirm ${g.target}` : "choose a feature class"}`),
    ...comp.blocking_errors.map((b) => b.replace(/ is NOT NULL in the (schema|template) but /, ": ").replace(/ and the domain has no neutral code/, "")),
    ...(!comp.validation_current ? ["validate again after the last change"] : []),
  ] : [];
  const blocked = !!comp && !comp.complete;
  const cantPay = !!quote?.enforced && quote.enough === false;
  const status = done && job.status === "failed" ? { t: "Written — check verification", c: C.red, bg: C.redSoft }
    : done ? { t: "Delivered", c: C.green, bg: C.greenSoft }
    : !results ? { t: "Validate first", c: C.muted, bg: "#F3F4F6" }
    : blocked ? { t: `${blockers.length} thing${blockers.length === 1 ? "" : "s"} to fix`, c: "#92400E", bg: "#FEF3C7" }
    : { t: "Ready to generate", c: C.green, bg: C.greenSoft };

  // what goes where: one geodatabase per template, the feature classes and counts in each
  const byClass = (s?.features_by_class || {}) as Record<string, number>;
  const planned = (() => {
    const m = new Map<string, { code: string; template: string; classes: [string, number][] }>();
    Object.entries(byClass).filter(([, n]) => n > 0).forEach(([fc, n]) => {
      const t = schema.layers.find((l) => l.name === fc)?.template || schema.display_name || "";
      const code = codeOf(t) || t;
      if (!m.has(code)) m.set(code, { code, template: t, classes: [] });
      m.get(code)!.classes.push([fc, n]);
    });
    return [...m.values()];
  })();
  const cards = done && gdbs.length
    ? gdbs.map((g, i) => ({ code: codeOf(g.name), name: g.name as string, size: g.size as number | undefined,
                            classes: Object.entries((g.counts || {}) as Record<string, number>).filter(([, v]) => v > 0),
                            href: api.downloadUrl(job.job_id, g.single ? "gdb_zip" : `gdb_${i + 1}`) }))
    : planned.map((p) => ({ code: p.code, name: p.template, size: undefined, classes: p.classes, href: undefined as string | undefined }));
  const [fill, setFill] = useState(false);
  const [sub, setSub] = useState<{ missing: number; name: string | null } | null>(null);
  useEffect(() => { api.submission(job.job_id).then((x) => setSub({ missing: x.missing.length, name: x.name })).catch(() => setSub(null)); }, [job.status, mapping, fill]);

  return (
    <Stack spacing={2} sx={{ maxWidth: 1180, mx: "auto" }}>
      {/* ---------------- one line: where it stands + the one action */}
      <Stack direction="row" alignItems="center" spacing={1.5} flexWrap="wrap" useFlexGap>
        <Box sx={{ px: 1.5, py: 0.6, borderRadius: 99, bgcolor: status.bg, color: status.c, fontWeight: 700, fontSize: 13.5 }}>{status.t}</Box>
        {s && <Typography sx={{ color: C.muted, fontSize: 14 }}>
          <b style={{ color: C.ink }}>{s.features_imported.toLocaleString()}</b> features
          {s.features_failed ? <> · <b style={{ color: C.red }}>{s.features_failed.toLocaleString()}</b> blocked</> : null}
        </Typography>}
        <Box sx={{ flex: 1 }} />
        {done && job.outputs?.package
          ? <>
              <Button disabled={busy || blocked || cantPay} onClick={onGenerate} startIcon={<RefreshOutlined />} sx={{ color: C.muted }}>Generate again</Button>
              <Button variant="contained" size="large" startIcon={<FileDownloadOutlined />} href={api.downloadUrl(job.job_id, "package")}>Download package</Button>
            </>
          : <Button variant="contained" size="large" startIcon={<StorageOutlined />} disabled={busy || !results || blocked || cantPay} onClick={onGenerate}>
              {done ? "Generate again" : "Generate"}</Button>}
      </Stack>
      {busy && <Box><LinearProgress variant="determinate" value={job.progress * 100} sx={{ height: 4, borderRadius: 2 }} /><Typography variant="caption">{job.message}</Typography></Box>}
      {job.status === "failed" && job.stage === "generate" && <Alert severity="error">{job.message}</Alert>}
      {quote?.enforced && results && !done && (
        <Typography variant="body2" sx={{ color: cantPay ? C.amber : C.muted }}>
          {quote.already_paid ? "Already paid" : <>Costs <b>{quote.cost}</b> credits · you have {quote.balance}{cantPay ? <> — <a href="#/account">add credits</a></> : null}</>}
        </Typography>
      )}

      {/* ---------------- the flow: drawing → geodatabase(s) → package */}
      <Paper sx={{ p: { xs: 2, md: 3 } }}>
        <Box sx={{ display: "grid", alignItems: "center", gap: { xs: 1.5, md: 2 },
                   gridTemplateColumns: { xs: "1fr", md: "minmax(150px, 0.7fr) 28px minmax(0, 2fr) 28px minmax(170px, 0.8fr)" } }}>
          {/* drawing */}
          <Node icon={<DescriptionOutlined />} tone="#475467" bg="#F2F4F7" title={job.file.name} sub={`${job.file.format.toUpperCase()} · CAD`} />
          <Arrow />
          {/* geodatabases */}
          <Stack spacing={1.25}>
            {cards.length === 0 && <Typography sx={{ color: C.muted, fontSize: 13.5 }}>Validate to see the geodatabase</Typography>}
            {cards.map((g) => {
              const n = g.classes.reduce((a, [, v]) => a + v, 0);
              return (
                <Box key={g.name + g.code} sx={{ border: `1.5px solid ${done ? "#A7E3BE" : C.line}`, borderRadius: 2.5, p: 1.75, bgcolor: done ? "#F6FEF9" : "#fff" }}>
                  <Stack direction="row" alignItems="center" spacing={1.5}>
                    <Box sx={{ width: 44, height: 44, borderRadius: 2, bgcolor: "#FDF1E3", color: "#B4651B", display: "grid", placeItems: "center", fontWeight: 800, fontSize: 13, flex: "none" }}>
                      {g.code || "GDB"}</Box>
                    <Box sx={{ minWidth: 0, flex: 1 }}>
                      <Typography sx={{ fontWeight: 700, fontSize: 16 }} noWrap>{LABEL[g.code] || g.code || "Geodatabase"}</Typography>
                      <Typography sx={{ fontSize: 12, color: C.muted, fontFamily: "ui-monospace, Menlo, Consolas, monospace" }} noWrap title={g.name}>{done ? g.name : `template ${g.name || "—"}`}</Typography>
                    </Box>
                    <Box sx={{ textAlign: "right" }}>
                      <Typography sx={{ fontWeight: 800, fontSize: 20, lineHeight: 1 }}>{n.toLocaleString()}</Typography>
                      <Typography sx={{ fontSize: 11, color: C.muted }}>features</Typography>
                    </Box>
                    {g.href && <Tooltip title={`Download${g.size ? ` · ${fmtBytes(g.size)}` : ""}`}>
                      <IconButton href={g.href} sx={{ border: `1px solid ${C.line}`, borderRadius: 1.5 }}><FileDownloadOutlined fontSize="small" /></IconButton></Tooltip>}
                  </Stack>
                  <Stack direction="row" spacing={0.75} sx={{ mt: 1.25 }} flexWrap="wrap" useFlexGap>
                    {g.classes.map(([k, v]) => (
                      <Box key={k} sx={{ px: 1, py: 0.25, borderRadius: 1, bgcolor: "#F2F4F7", fontSize: 12 }}>{k} <b>{v.toLocaleString()}</b></Box>
                    ))}
                  </Stack>
                </Box>
              );
            })}
          </Stack>
          <Arrow />
          {/* package */}
          <Box sx={{ border: `1.5px dashed ${done ? "#A7E3BE" : C.line}`, borderRadius: 2.5, p: 1.75 }}>
            <Stack direction="row" spacing={1.25} alignItems="center">
              <Inventory2Outlined sx={{ color: done ? C.green : C.muted }} />
              <Box sx={{ minWidth: 0 }}>
                <Typography sx={{ fontWeight: 700, fontSize: 14 }}>Submission package</Typography>
                <Typography sx={{ fontSize: 11.5, color: C.muted, fontFamily: "ui-monospace, Menlo, Consolas, monospace" }} noWrap title={sub?.name || ""}>{sub?.name || "ZIP"}</Typography>
              </Box>
            </Stack>
            <Stack spacing={0.4} sx={{ mt: 1.25, fontSize: 12, color: C.muted }}>
              <span>▸ {cards.length || 1} geodatabase{cards.length > 1 ? "s" : ""}</span>
              <span>▸ Report · Excel / PDF</span>
              <span>▸ Metadata</span>
              <span>▸ Project boundary</span>
            </Stack>
          </Box>
        </Box>
      </Paper>

      {/* ---------------- what is left before delivery (only when something is) */}
      {(blocked || (sub && sub.missing > 0)) && !busy && (
        <Paper sx={{ overflow: "hidden" }}>
          {blockers.slice(0, 5).map((b, i) => {
            const go = /layer|mapped|decision|feature class/i.test(b) ? [1, "Layers"] : [2, "Review"];
            return (
              <Row key={i} tone={C.red} text={b}>
                {onGoStep && <Button size="small" onClick={() => onGoStep(go[0] as number, go[1] as string)}>Fix</Button>}
              </Row>
            );
          })}
          {blockers.length > 5 && <Row tone={C.red} text={`+ ${blockers.length - 5} more`} />}
          {sub && sub.missing > 0 && (
            <Row tone="#D97706" text={`DMT submission details · ${sub.missing} to fill`}>
              <Button size="small" variant="outlined" onClick={() => setFill(true)}>Fill</Button>
            </Row>
          )}
        </Paper>
      )}

      {/* ---------------- files + verification */}
      {done && (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap alignItems="center">
          <FileBtn href={api.downloadUrl(job.job_id, "xlsx")} label="Excel" />
          <FileBtn href={api.downloadUrl(job.job_id, "pdf")} label="PDF" />
          {job.outputs?.metadata && <FileBtn href={api.downloadUrl(job.job_id, "metadata")} label="Metadata" />}
          {job.outputs?.boundary_zip && <FileBtn href={api.downloadUrl(job.job_id, "boundary_zip")} label="Boundary" />}
          {results?.dmt_qc && <FileBtn href={api.downloadUrl(job.job_id, "qc_csv")} label={`QC errors · ${results.dmt_qc.errors.length}`} />}
          <Box sx={{ flex: 1 }} />
          {ver && (
            <Button size="small" onClick={() => setShowVer(!showVer)} startIcon={ver.passed ? <CheckCircle sx={{ color: C.green }} /> : <Cancel sx={{ color: C.red }} />}
              sx={{ color: ver.passed ? C.green : C.red, fontWeight: 700 }}>
              {ver.passed ? "Verified" : "Not verified"} · {ver.checks.length}</Button>
          )}
          <Button size="small" onClick={() => setFill(true)} sx={{ color: C.muted }}>Submission details</Button>
          <Button size="small" onClick={() => setShowMap(!showMap)} sx={{ color: C.muted }}>Layers</Button>
        </Stack>
      )}
      <Collapse in={showVer} unmountOnExit>
        {ver && (
          <Paper sx={{ p: 1 }}>
            <Table size="small"><TableBody>
              {ver.checks.map((c) => (
                <TableRow key={c.check}>
                  <TableCell sx={{ width: 28, pr: 0 }}>
                    {c.passed ? <CheckCircle sx={{ fontSize: 18, color: C.green }} /> : c.severity === "warning" ? <ErrorOutline sx={{ fontSize: 18, color: C.amber }} /> : <Cancel sx={{ fontSize: 18, color: C.red }} />}
                  </TableCell>
                  <TableCell><b>{c.check}</b></TableCell>
                  <TableCell sx={{ color: C.muted }}>{c.detail}</TableCell>
                </TableRow>
              ))}
            </TableBody></Table>
          </Paper>
        )}
      </Collapse>
      <Collapse in={showMap} unmountOnExit><DeliveryTable jobId={job.job_id} status={job.status} /></Collapse>
      {!done && results && <Button size="small" onClick={() => setShowMap(!showMap)} sx={{ color: C.muted, alignSelf: "flex-start" }}>{showMap ? "Hide" : "Layers"}</Button>}

      <Drawer anchor="right" open={fill} onClose={() => setFill(false)} PaperProps={{ sx: { width: { xs: "100%", md: 620 }, bgcolor: "#F7F8FA" } }}>
        <Stack direction="row" alignItems="center" sx={{ px: 2.5, py: 1.5, bgcolor: "#fff", borderBottom: `1px solid ${C.line}` }}>
          <Typography sx={{ fontWeight: 700, fontSize: 16, flex: 1 }}>Submission details</Typography>
          <IconButton onClick={() => setFill(false)}><CloseOutlined /></IconButton>
        </Stack>
        <Box sx={{ p: 2 }}><SubmissionPanel job={job} mapping={mapping} onPatch={onPatch} showBoundary /></Box>
      </Drawer>
    </Stack>
  );
}

function Node({ icon, title, sub, tone, bg }: { icon: React.ReactNode; title: string; sub: string; tone: string; bg: string }) {
  return (
    <Stack direction="row" spacing={1.25} alignItems="center" sx={{ minWidth: 0 }}>
      <Box sx={{ width: 44, height: 44, borderRadius: 2, bgcolor: bg, color: tone, display: "grid", placeItems: "center", flex: "none" }}>{icon}</Box>
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ fontWeight: 650, fontSize: 14 }} noWrap title={title}>{title}</Typography>
        <Typography sx={{ fontSize: 12, color: C.muted }}>{sub}</Typography>
      </Box>
    </Stack>
  );
}
const Arrow = () => (
  <Box sx={{ color: "#B8BDC5", display: "grid", placeItems: "center", transform: { xs: "rotate(90deg)", md: "none" } }}><ArrowForwardRounded /></Box>
);
function Row({ tone, text, children }: { tone: string; text: string; children?: React.ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" spacing={1.25} sx={{ px: 2, py: 1.1, borderBottom: `1px solid ${C.line}`, "&:last-child": { borderBottom: 0 } }}>
      <Box sx={{ width: 8, height: 8, borderRadius: "50%", bgcolor: tone, flex: "none" }} />
      <Typography sx={{ fontSize: 13.5, flex: 1 }}>{text}</Typography>
      {children}
    </Stack>
  );
}
function FileBtn({ href, label }: { href: string; label: string }) {
  return <Button size="small" href={href} startIcon={<FileDownloadOutlined />} sx={{ color: C.ink, bgcolor: "#fff", border: `1px solid ${C.line}`, px: 1.25 }}>{label}</Button>;
}

