import { useEffect, useState } from "react";
import { Button,
  Alert, Box, MenuItem, Paper, Stack, TextField, Tooltip, Typography,
} from "@mui/material";
import AssignmentOutlined from "@mui/icons-material/AssignmentOutlined";
import { api } from "../api";
import type { JobState, Mapping, SubmissionStatus } from "../types";
import { C } from "../theme";

interface Props {
  job: JobState; mapping: Mapping | null; onPatch: (p: unknown) => Promise<void>;
  compact?: boolean; showBoundary?: boolean; embedded?: boolean;
}

const GROUPS: [string, string][] = [["naming", "File name"], ["project", "Project"], ["metadata", "Metadata"]];

/** DMT As-Built Data Submission Standards — project information, file name, project boundary. */
export function SubmissionPanel({ job, mapping, onPatch, compact, showBoundary, embedded }: Props) {
  const [st, setSt] = useState<SubmissionStatus | null>(null);
  const [open, setOpen] = useState<string | null>(compact ? null : "naming");
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [show, setShow] = useState(!compact);             // compact: one line until the user opens the form

  const load = () => api.submission(job.job_id).then((s) => { setSt(s); setDraft(s.info); }).catch(() => setSt(null));
  useEffect(() => { load(); }, [job.job_id, mapping?.project]);

  if (!st) return null;
  const save = async (k: string, v: any) => { await onPatch({ project: { [k]: v } }); };
  const nMissing = st.missing.length + st.errors.length;

  const isEmpty = (k: string) => draft[k] === undefined || draft[k] === null || String(draft[k]).trim() === "";
  const req = st.fields.filter((f) => f.m);
  const filled = req.filter((f) => !isEmpty(f.key)).length;
  const missingIn = (g: string) => st.fields.filter((f) => f.group === g && f.m && isEmpty(f.key)).length;
  const tab = open || "naming";
  const sectionOf = (f: any) => f.kind === "date" ? "Dates" : /cost/i.test(f.key + f.label) ? "Costs"
    : /consult|contract(or)?_|contractor|representative/i.test(f.key + " " + f.label) && !/type/i.test(f.key) ? "Parties" : "Details";
  const groupFields = st.fields.filter((f) => f.group === tab);
  const sections = ["Details", "Parties", "Dates", "Costs"].map((n) => [n, groupFields.filter((f) => sectionOf(f) === n)] as const).filter(([, fs]) => fs.length);
  const field = (f: any) => {
    const v = draft[f.key] ?? "";
    const missing = f.m && String(v).trim() === "";
    const common = {
      fullWidth: true, size: "small" as const, value: v, placeholder: f.placeholder || (f.kind === "choice" ? undefined : ""),
      onChange: (e: any) => setDraft({ ...draft, [f.key]: e.target.value }),
      inputProps: { "aria-label": f.label },
      sx: { "& .MuiOutlinedInput-root": { bgcolor: "#fff", height: f.key.includes("abstract") ? "auto" : 40, fontSize: 13.5 } },
    };
    return (
      <Box key={f.key} sx={{ display: "flex", flexDirection: "column", gap: 0.75, minWidth: 0,
                             gridColumn: f.key.includes("abstract") || f.key === "remarks" ? "1 / -1" : undefined }}>
        <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minHeight: 18 }}>
          <Typography component="label" sx={{ fontSize: 12.5, fontWeight: 500, color: C.ink }} noWrap>{f.label}</Typography>
          {f.m && <Box component="span" title="Required" sx={{ width: 6, height: 6, borderRadius: "50%", flex: "none", bgcolor: missing ? C.amber : C.green }} />}
          {f.help && <Tooltip title={f.help}><Typography component="span" sx={{ fontSize: 12, color: C.muted, cursor: "help" }}>?</Typography></Tooltip>}
        </Stack>
        {f.kind === "choice" ? (
          <TextField select {...common} SelectProps={{ displayEmpty: true }}
            onChange={(e) => { setDraft({ ...draft, [f.key]: e.target.value }); save(f.key, e.target.value); }}>
            <MenuItem value="" dense disabled><Box component="span" sx={{ color: C.muted }}>Choose</Box></MenuItem>
            {Object.entries(f.choices || {}).map(([k, l]) => <MenuItem key={k} value={k} dense>{k === l ? k : `${k} — ${l}`}</MenuItem>)}
          </TextField>
        ) : (
          <TextField {...common} type={f.kind === "date" ? "date" : f.kind === "number" || f.kind === "int" ? "number" : "text"}
            multiline={f.key.includes("abstract")} minRows={f.key.includes("abstract") ? 3 : undefined}
            onBlur={() => String(draft[f.key] ?? "") !== String(st.info[f.key] ?? "") && save(f.key, draft[f.key])} />
        )}
      </Box>
    );
  };

  return (
    <Paper elevation={0} sx={embedded ? { p: 0, border: 0, bgcolor: "transparent" } : { p: 2, border: `1px solid ${C.line}` }}>
      {/* the result: the file name the standard builds, and how far the form is */}
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" useFlexGap>
        {!embedded && <AssignmentOutlined sx={{ color: st.name ? C.green : C.muted }} />}
        <Box sx={{ px: 1.25, py: 0.5, borderRadius: 1.5, bgcolor: st.name ? C.greenSoft : "#F3F2EF", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12.5,
                   color: st.name ? C.green : C.muted, maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {st.name ? `${st.name}.gdb` : "File name builds as you fill the form"}
        </Box>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ ml: "auto" }}>
          <Box sx={{ width: 120, height: 4, borderRadius: 2, bgcolor: "#E3E6EA", overflow: "hidden" }}>
            <Box sx={{ height: "100%", width: `${req.length ? (filled / req.length) * 100 : 100}%`, bgcolor: nMissing ? C.amber : C.green, transition: "width .3s" }} />
          </Box>
          <Typography sx={{ fontSize: 12.5, color: nMissing ? C.amber : C.green, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
            {nMissing ? `${filled} of ${req.length} required` : "Complete"}{st.errors.length ? ` · ${st.errors.length} invalid` : ""}
          </Typography>
          {compact && <Button size="small" variant={show ? "text" : "outlined"} onClick={() => setShow(!show)} sx={{ minWidth: 0, color: show ? C.muted : undefined }}>
            {show ? "Close" : nMissing ? "Fill in" : "Edit"}</Button>}
        </Stack>
      </Stack>
      {show && <>

      {/* three parts of the standard, one at a time */}
      <Stack direction="row" role="tablist" aria-label="Submission" sx={{ mt: 1.5, p: 0.5, gap: 0.5, borderRadius: 2, bgcolor: "#F3F4F6", width: "fit-content", maxWidth: "100%", overflowX: "auto" }}>
        {GROUPS.map(([g, label]) => {
          const m = missingIn(g), on = tab === g;
          return (
            <Box key={g} component="button" role="tab" aria-selected={on} onClick={() => setOpen(g)}
              sx={{ display: "flex", alignItems: "center", gap: 1, height: 34, px: 1.5, border: 0, borderRadius: 1.5, cursor: "pointer", font: "inherit",
                    fontSize: 13.5, fontWeight: on ? 500 : 400, whiteSpace: "nowrap", color: on ? C.ink : C.muted,
                    bgcolor: on ? "#fff" : "transparent", boxShadow: on ? "0 1px 2px rgba(20,24,31,.08)" : "none" }}>
              {label}
              {m > 0 ? <Box component="span" sx={{ fontSize: 11.5, color: C.amber, fontVariantNumeric: "tabular-nums" }}>{m} to fill</Box>
                : <Box component="span" sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: C.green }} />}
            </Box>
          );
        })}
      </Stack>

      <Stack spacing={2.25} sx={{ mt: 2 }}>
        {sections.map(([name, fs]) => (
          <Box key={name}>
            {sections.length > 1 && <Typography sx={{ fontSize: 12, color: C.muted, mb: 1 }}>{name}</Typography>}
            <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, minmax(0, 1fr))", lg: "repeat(3, minmax(0, 1fr))" }, columnGap: 2, rowGap: 1.75 }}>
              {fs.map(field)}
            </Box>
          </Box>
        ))}
      </Stack>
      </>}
      {showBoundary && (
        <Box sx={{ mt: 1.5 }}>
          <Typography variant="body2" fontWeight={600}>Project boundary — PROJECT_BOUND (standard 3.9)</Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 0.75 }} alignItems="center">
            <TextField select size="small" sx={{ minWidth: 320 }} label="Boundary from"
              value={draft.boundary?.mode === "layer" ? `layer:${draft.boundary.layer}` : "derived"}
              onChange={(e) => {
                const v = String(e.target.value);
                const b = v === "derived" ? { mode: "derived", buffer_m: draft.boundary?.buffer_m ?? 5 } : { mode: "layer", layer: v.slice(6) };
                setDraft({ ...draft, boundary: b }); save("boundary", b);
              }}>
              {st.boundary_candidates.map((c) => <MenuItem key={c.layer} value={`layer:${c.layer}`} dense>CAD layer {c.layer} ({c.polygons} closed outline(s))</MenuItem>)}
              <MenuItem value="derived" dense>Derived: envelope of the delivered features (verify)</MenuItem>
            </TextField>
          </Stack>
          <Typography variant="caption" component="div">
            Delivered as its own geodatabase in the submission package — the official template is not altered (the DMT QC tool compares it with its template).
          </Typography>
        </Box>
      )}
      {st.errors.length > 0 && <Alert severity="warning" sx={{ mt: 1, py: 0 }}>{st.errors.join(" · ")}</Alert>}
    </Paper>
  );
}
