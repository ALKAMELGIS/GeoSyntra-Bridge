import { useEffect, useMemo, useState } from "react";
import {
  Box, Button, Chip, Collapse, FormControlLabel, MenuItem, Paper, Select, Stack, Switch, TextField, Tooltip, Typography,
} from "@mui/material";
import EditOutlined from "@mui/icons-material/EditOutlined";
import type { Discovery, FieldSpec, JobState, Mapping, SchemaDetail, SchemaField } from "../types";
import { DemPanel, ELEV_FIELDS, ELEV_VALUES } from "./DemPanel";
import { RequiredValues } from "./RequiredValues";
import { C } from "../theme";
import { Empty, GeomIcon } from "./ui";

interface Props {
  job: JobState; schema: SchemaDetail; discovery: Discovery; mapping: Mapping; onPatch: (p: unknown) => Promise<void>;
  onMapping: (m: Mapping) => void; onError: (m: string) => void; onNotice: (m: string) => void; onValidate?: () => void;
  /** values the engine wrote where the drawing had none (class -> field -> value), from the last validation */
  autoVals?: Record<string, Record<string, string>>;
}

const SOURCES: { v: string; label: string }[] = [
  { v: "label", label: "CAD label (rule)" },
  { v: "block_attr", label: "Block attribute" },
  { v: "constant", label: "Constant value" },
  { v: "elevation", label: "DEM elevation" },
  { v: "today", label: "Today's date (automatic)" },
  { v: "geometry", label: "Geometry" },
  { v: "cad", label: "CAD property" },
  { v: "none", label: "— not populated —" },
];
const GEOM = ["x", "y", "z", "length", "area", "start_z", "end_z"];
const CADP = ["layer", "handle", "block_name", "color", "linetype", "rotation", "elevation", "entity_type"];

export function FieldsStep({ job, schema, discovery, mapping, onPatch, onMapping, onError, onNotice, onValidate, autoVals = {} }: Props) {
  const demOn = !!mapping.elevation?.enabled && !!mapping.dem;
  const applyAll = (f: string, spec: FieldSpec) => onPatch({ fields: { "*": { [f]: spec } } });
  const mapped = useMemo(() => {
    const counts: Record<string, number> = {};
    discovery.groups.forEach((g) => {
      const m = mapping.layers[g.key];
      if (m?.target && m.role !== "ignore") counts[m.target] = (counts[m.target] || 0) + g.count;
    });
    return counts;
  }, [discovery, mapping]);
  const targets = Object.keys(mapped).sort();
  const [sel, setSel] = useState<string | null>(targets[0] || null);
  useEffect(() => { if (!sel || !mapped[sel]) setSel(targets[0] || null); }, [targets.join("|")]);

  const layer = schema.layers.find((l) => l.name === sel);
  const tags = useMemo(() => {
    const s = new Set<string>();
    discovery.groups.forEach((g) => mapping.layers[g.key]?.target === sel && g.attribute_tags.forEach((t) => s.add(t)));
    return [...s].sort();
  }, [sel, discovery, mapping]);
  const fmap = (sel && mapping.fields[sel]) || {};
  const set = (f: string, spec: FieldSpec) => onPatch({ fields: { [sel!]: { [f]: spec } } });

  if (!targets.length) return <Paper><Empty>Map at least one CAD layer to a feature class first.</Empty></Paper>;

  return (
    <>
    <DemPanel job={job} mapping={mapping} onPatch={onPatch} onMapping={onMapping} onError={onError} onNotice={onNotice} />
    {onValidate && <RequiredValues job={job} onPatch={onPatch} onValidate={onValidate} namesOnly />}
    {/* feature classes as tabs: the whole width goes to the form */}
    <Box sx={{ display: "grid", gap: 1, gridTemplateColumns: { xs: "1fr", sm: "repeat(auto-fill, minmax(220px, 1fr))" }, mb: 2 }}>
      {targets.map((t) => {
        const l = schema.layers.find((x) => x.name === t)!;
        const fm = withToday(l, mapping.fields[t] || {});
        const todo = l.fields.filter((f) => needsValue(f, fm[f.name]) && !(autoVals[t] || {})[f.name]).length;
        const filled = l.fields.filter((f) => isFilled(fm[f.name])).length;
        const on = t === sel;
        return (
          <Box key={t} component="button" onClick={() => setSel(t)} aria-pressed={on}
            sx={{ textAlign: "left", font: "inherit", cursor: "pointer", p: 1.5, borderRadius: 2, bgcolor: "#fff",
                  border: `1.5px solid ${on ? C.ink : C.line}`, boxShadow: on ? "0 2px 8px rgba(16,24,40,.08)" : "none" }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <GeomIcon g={l.geometry_type} />
              <Typography sx={{ fontWeight: 700, fontSize: 14, flex: 1 }} noWrap>{t}</Typography>
              {todo > 0 ? <Chip size="small" label={`${todo} to fill`} sx={{ bgcolor: C.amberSoft, color: "#92400E", fontWeight: 700, height: 22 }} />
                : <Chip size="small" label="complete" sx={{ bgcolor: C.greenSoft, color: C.green, fontWeight: 700, height: 22 }} />}
            </Stack>
            <Typography variant="caption" component="div" sx={{ mt: 0.5 }}>{mapped[t].toLocaleString()} features · {filled}/{l.fields.length} fields</Typography>
            <Box sx={{ mt: 0.75, height: 4, borderRadius: 2, bgcolor: "#ECEEF1", overflow: "hidden" }}>
              <Box sx={{ height: 4, width: `${(filled / l.fields.length) * 100}%`, bgcolor: todo ? C.amber : C.green }} /></Box>
          </Box>
        );
      })}
    </Box>
    {layer && <ClassFields layer={layer} fmap={fmap} schema={schema} tags={tags} demOn={demOn} count={mapped[sel!] || 0} autos={autoVals[sel!] || {}}
      multi={targets.length > 1} set={set} applyAll={applyAll} />}
    </>
  );
}

const withToday = (l: SchemaDetail["layers"][number], fm: Record<string, FieldSpec>) =>
  Object.fromEntries(l.fields.map((f) => [f.name, f.type === "DATE" && (!fm[f.name] || fm[f.name].source === "none") ? { source: "today" } : fm[f.name]])) as Record<string, FieldSpec>;

/** WATER_REQUIREMENT_SUMMER -> "Water requirement summer"; keeps the template alias when it is a real one. */
const human = (f: SchemaField) => {
  if (f.alias && f.alias.toUpperCase() !== f.name.toUpperCase()) return f.alias;
  const known: Record<string, string> = { PROJECTID: "Project ID", ASSETCATEGORY: "Asset category", UNITTYPE: "Unit type", UNITID: "Unit ID",
    IRRIGATIONSYSTEMTYPE: "Irrigation system type", IRRIGATIONWATERSOURCE: "Irrigation water source", PLANTTYPE: "Plant type",
    XCOORD: "X coordinate", YCOORD: "Y coordinate", ZCOORD: "Elevation (Z)", ASSETID: "Asset ID" };
  if (known[f.name.toUpperCase()]) return known[f.name.toUpperCase()];
  const t = f.name.toLowerCase().replace(/_/g, " ").replace(/\bsummr\b/, "summer").replace(/\baverae\b/, "average")
    .replace(/^mainassetname$/, "main asset name").replace(/^subassetname$/, "sub asset name").replace(/^maint ownership$/, "maintenance ownership");
  return t.charAt(0).toUpperCase() + t.slice(1);
};
export { human };
const SUFFIX = /_(SUMMER|WINTER|AVERAGE|AVG|MIN|MAX|START|END|FROM|TO|EN|AR)$/i;

const AUTO_LABEL = (spec: FieldSpec, f: SchemaField): string => {
  switch (spec.source) {
    case "geometry": return `geometry ${spec.value ?? ""}`.trim();
    case "elevation": return "DEM / CAD level";
    case "label": return "CAD label";
    case "block_attr": return `block attribute ${spec.value || f.name}`;
    case "constant": return `= ${String(spec.value ?? "").slice(0, 24)}`;
    case "today": return `today (${new Date().toISOString().slice(0, 10)})`;
    case "cad": return `CAD ${spec.value ?? ""}`;
    case "subtype": return "subtype";
    case "network": return "network analysis";
    case "computed": return "computed";
    case "system": return "automatic";
    default: return "";
  }
};
const isFilled = (spec?: FieldSpec) => !!spec && spec.source !== "none" && !(spec.source === "constant" && (spec.value == null || spec.value === ""));
const ELEV = /^(ZCOORD|ZCOORD_START|ZCOORD_END|GROUND_LEVEL|AVG_GLEVEL|COVER_LEVEL|INVERT_LEVEL|BOTTOM_LEVEL|TOP_LEVEL|BASE_LEVEL|Z|ELEVATION)$/i;
const needsValue = (f: SchemaField, spec?: FieldSpec) => !isFilled(spec) && (f.nullable === false || !!f.contractor_mandatory) && f.type !== "DATE" && !ELEV.test(f.name);

/** One feature class: what still needs a value (grouped form), what is filled automatically (summary), what stays empty. */
function ClassFields({ layer, fmap: fmap0, schema, tags, demOn, count, multi, set, applyAll, autos }: {
  layer: SchemaDetail["layers"][number]; fmap: Record<string, FieldSpec>; schema: SchemaDetail; tags: string[]; demOn: boolean; autos: Record<string, string>;
  count: number; multi: boolean; set: (f: string, s: FieldSpec) => void; applyAll: (f: string, s: FieldSpec) => void;
}) {
  const [q, setQ] = useState("");
  const [showOpt, setShowOpt] = useState(false);
  const [showAuto, setShowAuto] = useState(false);
  const [showDef, setShowDef] = useState(false);
  const [edit, setEdit] = useState<string | null>(null);
  const [everywhere, setEverywhere] = useState(true);
  const fmap = withToday(layer, fmap0);
  const match = (f: SchemaField) => !q || `${f.name} ${f.alias || ""} ${human(f)}`.toLowerCase().includes(q.toLowerCase());
  const fields = layer.fields.filter(match);
  const todo = fields.filter((f) => needsValue(f, fmap[f.name]));
  const realTodo = todo.filter((f) => !autos[f.name]);       // automatic values are shown, but not counted as missing
  const auto = fields.filter((f) => isFilled(fmap[f.name]));
  const empty = fields.filter((f) => !isFilled(fmap[f.name]) && !needsValue(f, fmap[f.name]));
  const put = (f: SchemaField, spec: FieldSpec) => (everywhere && multi ? applyAll(f.name, spec) : set(f.name, spec));
  const isList = (f: SchemaField) => !!schema.domains.find((d) => d.name === f.domain)?.values;
  // group the form: lists first, then sets of related numbers (summer / winter / average …), then free text
  const sets: Record<string, SchemaField[]> = {};
  const single: SchemaField[] = [];
  const defaults = todo.filter((f) => autos[f.name]);         // required, given a default by the converter — editable, not asked for
  realTodo.forEach((f) => { const b = f.name.replace(SUFFIX, ""); if (b !== f.name) (sets[b] = sets[b] || []).push(f); else single.push(f); });
  Object.keys(sets).forEach((b) => { if (sets[b].length < 2) { single.push(...sets[b]); delete sets[b]; } });
  const lists = single.filter(isList), texts = single.filter((f) => !isList(f));
  const bySource: Record<string, number> = {};
  auto.forEach((f) => { const k = AUTO_KIND(fmap[f.name]); bySource[k] = (bySource[k] || 0) + 1; });

  return (
    <Paper sx={{ p: { xs: 2, md: 3 } }}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={2} alignItems={{ sm: "flex-end" }} sx={{ mb: 3 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h5">{layer.name}</Typography>
          <Typography variant="body2" sx={{ color: C.muted }}>{count.toLocaleString()} features</Typography>
        </Box>
        <TextField size="small" placeholder="Find a field" value={q} onChange={(e) => setQ(e.target.value)} sx={{ width: { sm: 220 } }} />
      </Stack>

      {/* 1 — the form */}
      <Section title="Fill in" n={realTodo.length} tone={realTodo.length ? "amber" : "green"}
        hint={realTodo.length ? "Saved as you go." : undefined}
        right={multi && realTodo.length > 0 ? (
          <FormControlLabel sx={{ mr: 0 }} control={<Switch size="small" checked={everywhere} onChange={(e) => setEverywhere(e.target.checked)} />}
            label={<Typography variant="caption">Same value in every feature class</Typography>} />) : null}>
        {realTodo.length === 0 && <Typography variant="body2" sx={{ color: C.green }}>Nothing to fill — every required field has a value.</Typography>}
        {lists.length > 0 && <FormGrid title="Choose from the template's lists">{lists.map((f) => (
          <Labeled key={f.name} f={f} auto={autos[f.name]}><QuickValue f={f} schema={schema} onSet={(v) => put(f, { source: "constant", value: v })} /></Labeled>))}</FormGrid>}
        {Object.entries(sets).map(([b, fs]) => (
          <FormGrid key={b} title={human({ ...fs[0], name: b, alias: null } as SchemaField)} cols={fs.length}>
            {fs.map((f) => (
              <Labeled key={f.name} f={f} auto={autos[f.name]} short={f.name.match(SUFFIX)?.[1]}>
                <QuickValue f={f} schema={schema} onSet={(v) => put(f, { source: "constant", value: v })} /></Labeled>))}
          </FormGrid>))}
        {texts.length > 0 && <FormGrid title="Other values">{texts.map((f) => (
          <Labeled key={f.name} f={f} auto={autos[f.name]}><QuickValue f={f} schema={schema} onSet={(v) => put(f, { source: "constant", value: v })} /></Labeled>))}</FormGrid>}
      </Section>

      {/* 2 — required fields the drawing does not carry, given a default: one quiet list, change any of them */}
      {defaults.length > 0 && (
        <Section title="Default values" n={defaults.length} tone="green" hint="Required, not in the drawing — a default is written. Change any you know."
          right={<Button size="small" onClick={() => setShowDef(!showDef)}>{showDef || q ? "Hide" : "Change"}</Button>}>
          {!(showDef || q) && (
            <Typography variant="body2" sx={{ color: C.muted }} noWrap>
              {defaults.slice(0, 6).map((f) => `${human(f)}: ${autos[f.name]}`).join(" · ")}{defaults.length > 6 ? ` · +${defaults.length - 6}` : ""}
            </Typography>
          )}
          <Collapse in={showDef || !!q} unmountOnExit>
            <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, columnGap: 3, rowGap: 1.25 }}>
              {defaults.map((f) => (
                <Box key={f.name} sx={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1.2fr)", gap: 1.5, alignItems: "center" }}>
                  <Tooltip placement="top-start" title={`${f.name}${f.description ? " — " + f.description : ""}`}>
                    <Typography sx={{ fontSize: 13, fontWeight: 600 }} noWrap>{human(f)}</Typography>
                  </Tooltip>
                  <QuickValue f={f} schema={schema} hint={autos[f.name]} onSet={(v) => set(f.name, { source: "constant", value: v })} />
                </Box>
              ))}
            </Box>
          </Collapse>
        </Section>
      )}

      {/* 3 — filled automatically: a summary, details on demand */}
      <Section title="Filled automatically" n={auto.length} tone="green"
        right={<Button size="small" onClick={() => setShowAuto(!showAuto)}>{showAuto || q ? "Hide" : "Review"}</Button>}>
        <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
          {Object.entries(bySource).map(([k, n]) => <Chip key={k} size="small" label={`${k} · ${n}`} sx={{ bgcolor: C.greenSoft, color: C.green, fontWeight: 600 }} />)}
        </Stack>
        <Collapse in={showAuto || !!q}>
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, columnGap: 3, mt: 1 }}>
            {auto.map((f) => {
              const spec = fmap[f.name];
              return edit === f.name ? (
                <Box key={f.name} sx={{ gridColumn: "1 / -1" }}>
                  <Advanced f={f} spec={spec} layer={layer} schema={schema} tags={tags} demOn={demOn} onChange={(sp) => set(f.name, sp)} onDone={() => setEdit(null)} />
                </Box>
              ) : (
                <Stack key={f.name} component="button" direction="row" alignItems="center" spacing={1} onClick={() => setEdit(f.name)}
                  sx={{ font: "inherit", textAlign: "left", bgcolor: "transparent", border: 0, borderBottom: `1px solid ${C.line}`, py: 0.9, px: 0,
                        cursor: "pointer", "&:hover .ed": { opacity: 1 } }}>
                  <Typography sx={{ fontSize: 13, fontWeight: 600, flex: 1, minWidth: 0 }} noWrap title={f.name}>{human(f)}</Typography>
                  <Typography sx={{ fontSize: 12, color: C.green, fontWeight: 600 }} noWrap>{AUTO_LABEL(spec, f)}</Typography>
                  <EditOutlined className="ed" sx={{ fontSize: 15, color: C.muted, opacity: 0.35 }} />
                </Stack>
              );
            })}
          </Box>
        </Collapse>
      </Section>

      {/* 4 — optional */}
      <Section title="Optional — left empty" n={empty.length} tone="muted" last
        right={<Button size="small" onClick={() => setShowOpt(!showOpt)}>{showOpt || q ? "Hide" : "Show"}</Button>}>
        <Collapse in={showOpt || !!q}>
          <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, columnGap: 3 }}>
            {empty.map((f) => edit === f.name ? (
              <Box key={f.name} sx={{ gridColumn: "1 / -1" }}>
                <Advanced f={f} spec={fmap[f.name] || { source: "none" }} layer={layer} schema={schema} tags={tags} demOn={demOn}
                  onChange={(sp) => set(f.name, sp)} onDone={() => setEdit(null)} /></Box>
            ) : (
              <Stack key={f.name} direction="row" alignItems="center" sx={{ py: 0.5, borderBottom: `1px solid ${C.line}` }}>
                <Typography sx={{ fontSize: 13, color: C.muted, flex: 1, minWidth: 0 }} noWrap title={f.name}>{human(f)}</Typography>
                <Button size="small" onClick={() => setEdit(f.name)}>Add</Button>
              </Stack>
            ))}
          </Box>
        </Collapse>
      </Section>
    </Paper>
  );
}

const AUTO_KIND = (spec: FieldSpec): string => ({
  geometry: "Geometry", elevation: "Elevation", label: "CAD labels", block_attr: "Block attributes", constant: "Fixed values",
  today: "Today's date", cad: "CAD properties", subtype: "Subtype", network: "Network", computed: "Computed", system: "Automatic",
} as Record<string, string>)[spec.source] || spec.source;

function Section({ title, n, tone, right, hint, last, children }: { title: string; n: number; tone: "amber" | "green" | "muted";
  right?: React.ReactNode; hint?: string; last?: boolean; children: React.ReactNode }) {
  const col = tone === "amber" ? C.amber : tone === "green" ? C.green : C.muted;
  return (
    <Box component="section" sx={{ pb: last ? 0 : 3, mb: last ? 0 : 3, borderBottom: last ? 0 : `1px solid ${C.line}` }}>
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 1.5 }}>
        <Box sx={{ minWidth: 26, height: 22, px: 0.75, borderRadius: 99, bgcolor: col, color: "#fff", display: "grid", placeItems: "center", fontSize: 12, fontWeight: 700 }}>{n}</Box>
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ fontWeight: 700, fontSize: 15 }}>{title}</Typography>
          {hint && <Typography variant="caption">{hint}</Typography>}
        </Box>
        {right}
      </Stack>
      {children}
    </Box>
  );
}

function FormGrid({ title, cols, children }: { title: string; cols?: number; children: React.ReactNode }) {
  return (
    <Box component="fieldset" sx={{ border: `1px solid ${C.line}`, borderRadius: 2, p: 2, pt: 1, m: 0, mb: 1.5 }}>
      <Box component="legend" sx={{ px: 0.75, fontSize: 12, fontWeight: 700, color: C.muted, letterSpacing: ".02em" }}>{title}</Box>
      <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: cols ? `repeat(${Math.min(cols, 3)}, minmax(0,1fr))` : "repeat(2, minmax(0,1fr))" } }}>
        {children}
      </Box>
    </Box>
  );
}

function Labeled({ f, short, children, auto }: { f: SchemaField; short?: string; children: React.ReactNode; auto?: string }) {
  return (
    <Box>
      <Tooltip placement="top-start" title={f.description || f.name}>
        <Typography component="label" sx={{ display: "block", fontSize: 13, fontWeight: 600, mb: 0.5 }}>
          {short ? short.charAt(0) + short.slice(1).toLowerCase() : human(f)}
          <Box component="span" sx={{ ml: 0.75, fontFamily: "'IBM Plex Mono', monospace", fontWeight: 400, fontSize: 10.5, color: C.muted }}>{f.name}</Box>
          {auto != null && <Box component="span" sx={{ ml: 1, px: 0.75, py: 0.1, borderRadius: 1, bgcolor: C.greenSoft, color: C.green, fontSize: 11, fontWeight: 650 }}>automatic: {auto}</Box>}
        </Typography>
      </Tooltip>
      {children}
    </Box>
  );
}

/** One control: a list for domain fields, a box for the rest. Saves on choose / on leaving the box. */
function QuickValue({ f, schema, onSet, hint }: { f: SchemaField; schema: SchemaDetail; onSet: (v: string) => void; hint?: string }) {
  const [txt, setTxt] = useState("");
  const d = schema.domains.find((x) => x.name === f.domain);
  if (d?.values)
    return (
      <Select size="small" fullWidth value="" displayEmpty inputProps={{ "aria-label": f.name }}
        renderValue={() => <Box component="span" sx={{ color: hint ? C.green : C.muted, fontStyle: "normal" }}>{hint ? `${hint} · default` : "Choose…"}</Box>} onChange={(e) => onSet(String(e.target.value))}>
        <MenuItem value=""><em>{hint ? `${hint} (default)` : "Choose…"}</em></MenuItem>
        {Object.entries(d.values).map(([k, v]) => <MenuItem key={k} value={k} dense>{k === v ? k : `${k} — ${v}`}</MenuItem>)}
      </Select>
    );
  const num = /INT|DOUBLE|FLOAT|SINGLE/i.test(f.type);
  return <TextField size="small" fullWidth value={txt} placeholder={hint ? `${hint} · default` : num ? "Number" : "Type a value"} inputProps={{ inputMode: num ? "decimal" : "text", "aria-label": f.name }}
    onChange={(e) => setTxt(num ? e.target.value.replace(/[^0-9.\-]/g, "") : e.target.value)}
    onBlur={() => txt.trim() && onSet(txt.trim())} onKeyDown={(e) => { if (e.key === "Enter" && txt.trim()) onSet(txt.trim()); }} />;
}

function Advanced({ f, spec, layer, schema, tags, demOn, onChange, onDone }: {
  f: SchemaField; spec: FieldSpec; layer: SchemaDetail["layers"][number]; schema: SchemaDetail; tags: string[]; demOn: boolean;
  onChange: (s: FieldSpec) => void; onDone: () => void;
}) {
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "minmax(0,1fr) 200px 240px auto" }, gap: 1, alignItems: "center", py: 1, px: 1,
      my: 0.5, borderRadius: 1.5, bgcolor: "#F6F8FA", border: `1px solid ${C.line}` }}>
      <Box sx={{ minWidth: 0 }}>
        <Typography sx={{ fontSize: 13.5, fontWeight: 600 }} noWrap>{f.alias && f.alias !== f.name ? f.alias : f.name}</Typography>
        <Typography variant="caption" sx={{ fontFamily: "'IBM Plex Mono', monospace" }}>{f.name}</Typography>
      </Box>
      <Select size="small" fullWidth value={spec.source} onChange={(e) => onChange({ source: String(e.target.value), value: null, fallback: spec.fallback })}>
        {SOURCES.filter((s) => (s.v !== "label" || layer.label_fields.includes(f.name) || spec.source === "label")
          && (s.v !== "elevation" || f.name.toUpperCase() in ELEV_FIELDS || spec.source === "elevation")
          && (s.v !== "today" || f.type === "DATE")).map((s) => <MenuItem key={s.v} value={s.v} dense>{s.label}</MenuItem>)}
      </Select>
      <ValueEditor spec={spec} f={f} tags={tags} schema={schema} demOn={demOn} onChange={onChange} />
      <Button size="small" onClick={onDone}>Done</Button>
    </Box>
  );
}

export function DomainChip({ f, schema }: { f: SchemaField; schema: SchemaDetail }) {
  if (!f.domain) return <Typography variant="caption">—</Typography>;
  const d = schema.domains.find((x) => x.name === f.domain);
  const vals = d?.values ? Object.entries(d.values) : [];
  return (
    <Tooltip title={d?.type === "range" ? `${d.min} … ${d.max}` : vals.slice(0, 25).map(([k, v]) => `${k} = ${v}`).join("\n") + (vals.length > 25 ? `\n… ${vals.length - 25} more` : "")}
      componentsProps={{ tooltip: { sx: { whiteSpace: "pre-line" } } }}>
      <Chip size="small" variant="outlined" label={`${f.domain}${vals.length ? ` (${vals.length})` : ""}`} />
    </Tooltip>
  );
}

function ValueEditor({ spec, f, tags, schema, demOn, onChange }: {
  spec: FieldSpec; f: SchemaField; tags: string[]; schema: SchemaDetail; demOn: boolean; onChange: (s: FieldSpec) => void;
}) {
  const [txt, setTxt] = useState(String(spec.value ?? ""));
  useEffect(() => setTxt(String(spec.value ?? "")), [spec.value]);
  if (spec.source === "elevation")
    return (
      <Stack direction="row" spacing={0.5} alignItems="center">
        <Select fullWidth value={String(spec.value ?? ELEV_FIELDS[f.name.toUpperCase()] ?? "z")} onChange={(e) => onChange({ ...spec, value: e.target.value })}>
          {Object.entries(ELEV_VALUES).map(([k, l]) => <MenuItem key={k} value={k} dense>{l}</MenuItem>)}
        </Select>
        <Tooltip title={demOn ? "DEM prepared — the value is sampled per feature" : "No DEM selected yet: only real CAD levels can fill this field (choose a DEM above)"}>
          <Chip size="small" label={demOn ? "DEM" : "no DEM"} sx={{ height: 18, fontSize: 10, bgcolor: demOn ? C.greenSoft : C.amberSoft, color: demOn ? C.green : C.amber }} />
        </Tooltip>
      </Stack>
    );
  if (spec.source === "label")
    return <Typography variant="caption">Extracted from nearby labels{spec.fallback ? ` · fallback ${spec.fallback.startsWith("elevation") ? "DEM elevation" : spec.fallback}` : ""}
      {(spec as any).derived ? ` · else ${(spec as any).derived === "network" ? "from junctions" : (spec as any).derived}` : ""}</Typography>;
  if (spec.source === "today") return <Typography variant="caption">The date of the conversion ({new Date().toISOString().slice(0, 10)})</Typography>;
  if (spec.source === "network") return <Typography variant="caption">Filled from connected junctions / flow analysis</Typography>;
  if (spec.source === "computed") return <Typography variant="caption" sx={{ fontFamily: "monospace" }}>= {String(spec.value)}</Typography>;
  if (spec.source === "block_attr")
    return (
      <Select fullWidth value={String(spec.value ?? "")} displayEmpty onChange={(e) => onChange({ ...spec, value: e.target.value })}>
        <MenuItem value=""><em>{f.name}</em></MenuItem>
        {tags.map((t) => <MenuItem key={t} value={t} dense>{t}</MenuItem>)}
      </Select>
    );
  if (spec.source === "geometry")
    return (
      <Select fullWidth value={String(spec.value ?? "x")} onChange={(e) => onChange({ ...spec, value: e.target.value })}>
        {GEOM.map((t) => <MenuItem key={t} value={t} dense>{t}</MenuItem>)}
      </Select>
    );
  if (spec.source === "cad")
    return (
      <Select fullWidth value={String(spec.value ?? "layer")} onChange={(e) => onChange({ ...spec, value: e.target.value })}>
        {CADP.map((t) => <MenuItem key={t} value={t} dense>{t}</MenuItem>)}
      </Select>
    );
  if (spec.source === "constant") {
    const d = schema.domains.find((x) => x.name === f.domain);
    if (d?.values)
      return (
        <Select fullWidth value={String(spec.value ?? "")} displayEmpty onChange={(e) => onChange({ ...spec, value: e.target.value })}>
          <MenuItem value=""><em>select…</em></MenuItem>
          {Object.entries(d.values).map(([k, v]) => <MenuItem key={k} value={k} dense>{k === v ? k : `${k} — ${v}`}</MenuItem>)}
        </Select>
      );
    return <TextField fullWidth value={txt} onChange={(e) => setTxt(e.target.value)} onBlur={() => onChange({ ...spec, value: txt })}
      placeholder={f.type === "DATE" ? "YYYY-MM-DD" : "value"} />;
  }
  return <Stack />;
}
