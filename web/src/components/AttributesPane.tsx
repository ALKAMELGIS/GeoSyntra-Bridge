/**
 * Attributes pane — the Review screen's equivalent of ArcGIS Pro's Attributes pane (Selection tab).
 *
 * Top: the selection view — the selected features grouped by layer. Click a feature to edit it; click a layer to edit
 * every selected feature of that layer at once; Ctrl / Shift + click to edit several; right-click for Zoom to, Unselect,
 * Only select this.
 * Bottom: Attributes | Geometry. Attributes is a compact field grid: a coloured bar says what each field needs
 * (red = required and empty, blue = automatic default, green = changed, grey = read-only); right-click a field for
 * Set to <Null>, Revert, Unique values, Copy / Paste. Auto Apply commits each field as it is left; otherwise Apply / Cancel.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Autocomplete, Box, Button, CircularProgress, Divider, IconButton, InputBase, LinearProgress, ListItemIcon, Menu, MenuItem, Stack, Switch,
  Tab, Tabs, Tooltip, Typography,
} from "@mui/material";
import ExpandMore from "@mui/icons-material/ExpandMore";
import ChevronRight from "@mui/icons-material/ChevronRight";
import ZoomInOutlined from "@mui/icons-material/ZoomInOutlined";
import RemoveCircleOutline from "@mui/icons-material/RemoveCircleOutline";
import FilterCenterFocusOutlined from "@mui/icons-material/FilterCenterFocusOutlined";
import ContentCopyOutlined from "@mui/icons-material/ContentCopyOutlined";
import ContentPasteOutlined from "@mui/icons-material/ContentPasteOutlined";
import BlockOutlined from "@mui/icons-material/BlockOutlined";
import UndoOutlined from "@mui/icons-material/UndoOutlined";
import FormatListBulletedOutlined from "@mui/icons-material/FormatListBulletedOutlined";
import TodayOutlined from "@mui/icons-material/TodayOutlined";
import NearMeOutlined from "@mui/icons-material/NearMeOutlined";
import SelectAllOutlined from "@mui/icons-material/SelectAllOutlined";
import { api } from "../api";
import type { AssignDetail, AssignField, AssignOverview, AssignRow, AssignState } from "../types";
import { nice } from "./FeatureEditor";
import { C } from "../theme";

const RED = "#DC2626", BLUE = "#3B82F6", GREEN = "#16A34A", GREY = "#C4C9D0";
const PLACEHOLDER = new Set(["N/A", "1900-01-01"]);
const NUM = ["INTEGER", "SMALLINTEGER", "DOUBLE", "FLOAT"];
const asText = (v: unknown, type: string) => {
  if (v === null || v === undefined) return "";
  const s = String(v);
  if (type === "DATE") { const m = s.match(/^(\d{4})[-/](\d{2})[-/](\d{2})/); return m ? `${m[1]}-${m[2]}-${m[3]}` : s; }
  return s;
};
let CLIP: Record<string, string> | null = null;            // Copy / Paste attributes, kept across features

type Node = { kind: "feature"; fid: number } | { kind: "layer"; cls: string } | { kind: "multi"; fids: number[] };

export interface PaneProps {
  jobId: string;
  ov: AssignOverview | null;
  rows: Record<number, AssignRow>;
  selection: number[];
  color: (cls: string) => string;
  busy: boolean;
  focus: string | null;
  setSelection: (fids: number[]) => void;
  zoomTo: (fids: number[]) => void;
  coordsOf: (fid: number) => number[][][] | null;
  geographic: boolean;
  onRows: (u: { fid: number; state: AssignState; missing: string[] }[]) => void;
  onNext: (from: number | null) => void;
  onError: (m: string) => void;
  onNotice: (m: string) => void;
  banner?: (fid: number | null) => React.ReactNode;
  onActive?: (fid: number | null) => void;
}

export function AttributesPane(p: PaneProps) {
  const { ov, rows, selection } = p;
  // ------------------------------------------------------------ selection view
  const groups = useMemo(() => {
    const g: Record<string, number[]> = {};
    selection.forEach((fid) => { const r = rows[fid]; if (r) (g[r.target] ||= []).push(fid); });
    return Object.entries(g);
  }, [selection, rows]);
  const [node, setNode] = useState<Node | null>(null);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const anchor = useRef<number | null>(null);
  useEffect(() => {                                  // a new selection on the map: first feature, or the layer when several
    if (!selection.length) { setNode(null); return; }
    if (selection.length === 1) setNode({ kind: "feature", fid: selection[0] });
    else if (groups.length === 1) setNode({ kind: "layer", cls: groups[0][0] });
    else setNode({ kind: "feature", fid: selection[0] });
  }, [selection.join(",")]);

  const fids = !node ? [] : node.kind === "feature" ? [node.fid] : node.kind === "multi" ? node.fids : groups.find(([k]) => k === node.cls)?.[1] || [];
  const cls = fids.length ? rows[fids[0]]?.target : null;
  const spec = cls && ov ? ov.classes[cls] : null;
  const single = node?.kind === "feature" ? node.fid : null;

  const clickItem = (fid: number, e: React.MouseEvent) => {
    guard(() => {
      if ((e.ctrlKey || e.metaKey) && node) {
        const cur = node.kind === "feature" ? [node.fid] : node.kind === "multi" ? node.fids : [];
        const same = cur.filter((x) => rows[x]?.target === rows[fid]?.target);
        const next = same.includes(fid) ? same.filter((x) => x !== fid) : [...same, fid];
        setNode(next.length === 1 ? { kind: "feature", fid: next[0] } : next.length ? { kind: "multi", fids: next } : null);
      } else if (e.shiftKey && anchor.current != null) {
        const list = groups.find(([k]) => k === rows[fid]?.target)?.[1] || [];
        const a = list.indexOf(anchor.current), b = list.indexOf(fid);
        if (a >= 0 && b >= 0) { const r = list.slice(Math.min(a, b), Math.max(a, b) + 1); setNode(r.length === 1 ? { kind: "feature", fid } : { kind: "multi", fids: r }); }
      } else { setNode({ kind: "feature", fid }); anchor.current = fid; }
    });
  };
  const [ctx, setCtx] = useState<{ x: number; y: number; fids: number[]; one?: number } | null>(null);

  // ------------------------------------------------------------ values
  const [detail, setDetail] = useState<AssignDetail | null>(null);
  const [initial, setInitial] = useState<Record<string, string>>({});
  const [form, setForm] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [errs, setErrs] = useState<Record<string, string>>({});
  const req = useRef(0);
  const nodeKey = node ? (node.kind === "feature" ? `f${node.fid}` : node.kind === "layer" ? `l${node.cls}:${fids.length}` : `m${node.fids.join(",")}`) : "";
  useEffect(() => {
    setErrs({});
    if (!node || !spec) { setDetail(null); setForm({}); setInitial({}); return; }
    if (node.kind !== "feature") { setDetail(null); setForm({}); setInitial({}); return; }   // several: only what is typed is applied
    const my = ++req.current;
    setLoading(true);
    api.assignDetail(p.jobId, node.fid).then((d) => {
      if (my !== req.current) return;
      setDetail(d);
      const src = (d.gdb?.values || d.attributes) as Record<string, unknown>;
      const v: Record<string, string> = {};
      spec.fields.forEach((x) => { const t = asText(src[x.name], x.type); v[x.name] = d.placeholders.includes(x.name) && PLACEHOLDER.has(t) ? "" : t; });
      setForm(v); setInitial(v);
    }).catch((e) => p.onError(String(e.message || e))).finally(() => { if (my === req.current) setLoading(false); });
  }, [nodeKey, spec]);

  const formRef = useRef(form); formRef.current = form;
  const initialRef = useRef(initial); initialRef.current = initial;
  const changed = (spec?.fields || []).filter((x) => !x.read_only && (form[x.name] ?? "") !== (initial[x.name] ?? ""));
  const dirty = changed.length > 0;
  const dirtyRef = useRef(false);
  dirtyRef.current = dirty;
  const guard = (go: () => void) => {
    if (!dirtyRef.current) { go(); return; }
    if (window.confirm("Apply the changed values first?\n\nOK = Apply · Cancel = Discard")) apply().then(go); else { setForm(initial); go(); }
  };

  const [auto, setAuto] = useState<boolean>(() => { try { return localStorage.getItem("gsb_auto_apply") === "1"; } catch { return false; } });
  const setAutoP = (v: boolean) => { setAuto(v); try { localStorage.setItem("gsb_auto_apply", v ? "1" : "0"); } catch { /* ignore */ } };

  const apply = async (only?: string[]): Promise<boolean> => {
    if (!spec || !fids.length || saving) return false;
    const fm = formRef.current, ini = initialRef.current;
    const ch: Record<string, unknown> = {};
    spec.fields.filter((x) => !x.read_only && (fm[x.name] ?? "") !== (ini[x.name] ?? "") && (!only || only.includes(x.name)))
      .forEach((x) => { ch[x.name] = fm[x.name] === "" ? null : fm[x.name]; });
    if (!Object.keys(ch).length) return true;
    setSaving(true); setErrs({});
    try {
      if (single != null) {
        const stillEmpty = (rows[single]?.missing || []).some((n) => !(detail?.placeholders || []).includes(n) && !String(fm[n] ?? "").trim());
        const r = await api.assignSave(p.jobId, single, ch, stillEmpty ? "draft" : "completed");
        p.onRows([{ fid: single, state: r.state, missing: r.missing }]);
        setInitial((s) => ({ ...s, ...Object.fromEntries(Object.keys(ch).map((k) => [k, fm[k] ?? ""])) }));
      } else {
        const r = await api.assignMany(p.jobId, fids, ch);
        p.onRows(r.features);
        p.onNotice(`${r.saved.toLocaleString()} saved` + (r.failed.length ? ` · ✕ ${r.failed.length}: ${r.failed[0].error}` : ""));
        setForm((s) => { const n = { ...s }; Object.keys(ch).forEach((k) => { delete n[k]; }); return n; });
      }
      return true;
    } catch (e: any) {
      const text = String(e?.message || e); p.onError(text);
      const e2: Record<string, string> = {}; spec.fields.forEach((x) => { if (text.includes(x.name) || text.includes(x.alias)) e2[x.name] = text; }); setErrs(e2);
      return false;
    } finally { setSaving(false); }
  };
  const cancel = () => { setForm(initial); setErrs({}); };
  const applyNext = async () => { if (await apply()) p.onNext(single); };

  // keyboard: Ctrl+Enter apply & next, Esc cancel (while typing in the pane)
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (!box.current?.contains(e.target as HTMLElement)) return;
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); applyNext(); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });

  // ------------------------------------------------------------ field context menu
  const [fmenu, setFmenu] = useState<{ x: number; y: number; f: AssignField } | null>(null);
  const [uniq, setUniq] = useState<{ value: string; count: number }[] | null>(null);
  const loadUnique = async (f: AssignField) => {
    if (!cls) return;
    setUniq([]);
    try { setUniq((await api.assignValues(p.jobId, cls, f.name)).values); } catch (e: any) { p.onError(String(e.message || e)); setUniq(null); }
  };
  const put = (name: string, v: string, commit = false) => {
    setForm((s) => ({ ...s, [name]: v }));
    if (commit && auto) setTimeout(() => applyField(name), 0);
  };
  const applyField = (name: string) => { if (auto) apply([name]); };


  // ------------------------------------------------------------ view
  const [tab, setTab] = useState<"attributes" | "geometry">("attributes");
  useEffect(() => { p.onActive?.(single); }, [single]);
  const [onlyTodo, setOnlyTodo] = useState(false);
  const missing = new Set(single != null ? rows[single]?.missing || [] : fids.flatMap((f) => rows[f]?.missing || []));
  const missCount = useMemo(() => {
    const m: Record<string, number> = {};
    if (single == null) fids.forEach((f) => (rows[f]?.missing || []).forEach((n) => { m[n] = (m[n] || 0) + 1; }));
    return m;
  }, [nodeKey, rows]);
  const ph = new Set(detail?.placeholders || []);
  const fields = (spec?.fields || []).filter((x) => !onlyTodo || missing.has(x.name) || ph.has(x.name) || errs[x.name]);
  const nTodo = (spec?.fields || []).filter((x) => missing.has(x.name) && !ph.has(x.name)).length;

  if (!selection.length) {
    const all = p.focus ? Object.values(rows).filter((r) => r.target === p.focus).map((r) => r.fid) : [];
    return (
      <Stack alignItems="center" justifyContent="center" spacing={1.25} sx={{ flex: 1, p: 4, color: C.muted, textAlign: "center" }}>
        <NearMeOutlined sx={{ fontSize: 30 }} />
        <Typography sx={{ fontSize: 13.5 }}>Select on the map</Typography>
        {p.focus && all.length > 0 && (
          <Button size="small" variant="outlined" startIcon={<SelectAllOutlined />} onClick={() => p.setSelection(all)}>
            Select all {ov?.classes[p.focus]?.alias} ({all.length.toLocaleString()})
          </Button>
        )}
        <Button size="small" onClick={() => p.onNext(null)}>Next to fill</Button>
      </Stack>
    );
  }

  return (
    <Box ref={box} sx={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}>
      {/* ---------------- selection view */}
      <Box sx={{ maxHeight: "22%", minHeight: 64, flex: "none", overflowY: "auto", overflowX: "hidden", borderBottom: `1px solid ${C.line}`, py: 0.5, "&::-webkit-scrollbar": { width: 8 }, "&::-webkit-scrollbar-thumb": { bgcolor: "#C4C9D0", borderRadius: 8, border: "2px solid #fff" }, "&::-webkit-scrollbar-track": { bgcolor: "#F2F4F7" }, scrollbarWidth: "thin", scrollbarColor: "#C4C9D0 #F2F4F7" }}>
        {groups.map(([k, list]) => {
          const c = ov?.classes[k];
          const layerOn = node?.kind === "layer" && node.cls === k;
          return (
            <Box key={k}>
              <Stack direction="row" alignItems="center" onClick={() => guard(() => setNode({ kind: "layer", cls: k }))}
                onContextMenu={(e) => { e.preventDefault(); setCtx({ x: e.clientX, y: e.clientY, fids: list }); }}
                sx={{ pl: 0.5, pr: 1.5, py: 0.4, cursor: "pointer", bgcolor: layerOn ? "#E6F1FB" : undefined, "&:hover": { bgcolor: layerOn ? "#E6F1FB" : "#F5F6F8" } }}>
                <IconButton size="small" aria-label={collapsed[k] ? "Expand" : "Collapse"} sx={{ p: 0.5 }} onClick={(e) => { e.stopPropagation(); setCollapsed((s) => ({ ...s, [k]: !s[k] })); }}>
                  {collapsed[k] ? <ChevronRight sx={{ fontSize: 18 }} /> : <ExpandMore sx={{ fontSize: 18 }} />}
                </IconButton>
                <Box sx={{ width: 10, height: 10, borderRadius: 0.75, bgcolor: p.color(k), mx: 0.75 }} />
                <Typography sx={{ fontSize: 13, fontWeight: 650, flex: 1 }} noWrap>{c?.alias || k}</Typography>
                <Typography sx={{ fontSize: 12, color: C.muted }}>({list.length.toLocaleString()})</Typography>
              </Stack>
              {!collapsed[k] && list.slice(0, 300).map((fid) => {
                const r = rows[fid];
                const on = node?.kind === "feature" ? node.fid === fid : node?.kind === "multi" ? node.fids.includes(fid) : layerOn;
                return (
                  <Stack key={fid} direction="row" alignItems="center" spacing={1} onClick={(e) => clickItem(fid, e)}
                    onContextMenu={(e) => { e.preventDefault(); setCtx({ x: e.clientX, y: e.clientY, fids: [fid], one: fid }); }}
                    sx={{ pl: 5, pr: 1.5, py: 0.3, cursor: "pointer", userSelect: "none", bgcolor: on ? "#CFE3F7" : undefined, "&:hover": { bgcolor: on ? "#CFE3F7" : "#F5F6F8" } }}>
                    <Typography sx={{ fontSize: 12.5, flex: 1 }} noWrap>#{r?.objectid ?? fid}<Box component="span" sx={{ color: C.muted, ml: 1 }}>{r?.handle}</Box></Typography>
                    <Dot s={r?.state} />
                  </Stack>
                );
              })}
              {!collapsed[k] && list.length > 300 && <Typography sx={{ pl: 5, py: 0.5, fontSize: 11.5, color: C.muted }}>+ {(list.length - 300).toLocaleString()} more — click the layer to edit them all</Typography>}
            </Box>
          );
        })}
      </Box>
      <Menu open={!!ctx} onClose={() => setCtx(null)} anchorReference="anchorPosition" anchorPosition={ctx ? { top: ctx.y, left: ctx.x } : undefined}>
        <MenuItem dense onClick={() => { p.zoomTo(ctx!.fids); setCtx(null); }}><ListItemIcon><ZoomInOutlined fontSize="small" /></ListItemIcon>Zoom to</MenuItem>
        {ctx?.one != null && <MenuItem dense onClick={() => { const f = ctx!.one!; setCtx(null); guard(() => p.setSelection([f])); }}><ListItemIcon><FilterCenterFocusOutlined fontSize="small" /></ListItemIcon>Only select this</MenuItem>}
        <MenuItem dense onClick={() => { const out = new Set(ctx!.fids); setCtx(null); guard(() => p.setSelection(selection.filter((x) => !out.has(x)))); }}>
          <ListItemIcon><RemoveCircleOutline fontSize="small" /></ListItemIcon>Unselect</MenuItem>
      </Menu>

      <Box sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflow: "hidden", bgcolor: "#fff" }}>
      {/* ---------------- header of the edited object */}
      {spec && (
        <Stack direction="row" alignItems="center" spacing={0.5} sx={{ px: 1.5, pt: 0.5, borderBottom: `1px solid ${C.line}` }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ minHeight: 36, flex: 1, "& .MuiTab-root": { minHeight: 36, py: 0, px: 1, minWidth: 0, textTransform: "none", fontSize: 13, fontWeight: 650 } }}>
            <Tab value="attributes" label="Attributes" />
            <Tab value="geometry" label="Geometry" disabled={single == null} />
          </Tabs>
          {tab === "attributes" && <>
            <Tooltip title="Only the fields that need a value"><Box component="button" onClick={() => setOnlyTodo(!onlyTodo)}
              sx={{ border: `1px solid ${onlyTodo ? C.ink : C.line}`, bgcolor: onlyTodo ? C.ink : "#fff", color: onlyTodo ? "#fff" : C.ink, borderRadius: 99, px: 1.25, py: 0.6, minHeight: 26, fontSize: 11.5, fontWeight: 650, cursor: "pointer" }}>
              To fill{nTodo ? ` ${nTodo}` : ""}</Box></Tooltip>
            <Tooltip title="Copy attributes"><span><IconButton size="small" aria-label="Copy attributes" disabled={single == null} onClick={() => { CLIP = { ...form }; p.onNotice("Copied"); }}><ContentCopyOutlined sx={{ fontSize: 17 }} /></IconButton></span></Tooltip>
            <Tooltip title={CLIP ? "Paste attributes (fields with the same name)" : "Copy attributes from a feature first"}><span>
              <IconButton size="small" aria-label="Paste attributes" disabled={!CLIP} onClick={() => {
                const v: Record<string, string> = {};
                spec.fields.forEach((x) => { if (!x.read_only && CLIP && CLIP[x.name] != null && CLIP[x.name] !== "") v[x.name] = CLIP[x.name]; });
                setForm((s) => ({ ...s, ...v }));
              }}><ContentPasteOutlined sx={{ fontSize: 17 }} /></IconButton></span></Tooltip>
          </>}
        </Stack>
      )}
      {(loading || saving) && <LinearProgress sx={{ height: 2 }} />}
      {p.banner?.(single)}
      {node && node.kind !== "feature" && spec && (
        <Typography sx={{ px: 1.5, py: 0.75, fontSize: 12, color: "#1E4E8C", bgcolor: "#EEF5FF", borderBottom: `1px solid ${C.line}` }}>
          <b>{fids.length.toLocaleString()} {spec.alias}</b> · one value for all
        </Typography>
      )}

      {/* ---------------- attributes grid */}
      {tab === "attributes" && spec && (
        <Box sx={{ flex: 1, minHeight: 0, overflowY: "scroll", overflowX: "hidden", opacity: loading ? 0.5 : 1, "&::-webkit-scrollbar": { width: 8 }, "&::-webkit-scrollbar-thumb": { bgcolor: "#C4C9D0", borderRadius: 8, border: "2px solid #fff" }, "&::-webkit-scrollbar-track": { bgcolor: "#F2F4F7" }, scrollbarWidth: "thin", scrollbarColor: "#C4C9D0 #F2F4F7" }}>
          {([["Required", fields.filter((f) => !f.read_only && f.required)], ["Optional", fields.filter((f) => !f.read_only && !f.required)],
             ["Automatic", fields.filter((f) => f.read_only)]] as [string, AssignField[]][]).filter(([, l]) => l.length).flatMap(([title, list]) => [
            <Stack key={"h" + title} direction="row" alignItems="center" sx={{ px: 1.5, pt: 1.25, pb: 0.5, position: "sticky", top: 0, zIndex: 1, bgcolor: "#fff" }}>
              <Typography sx={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.6, textTransform: "uppercase", color: C.muted, flex: 1 }}>{title}</Typography>
              <Typography sx={{ fontSize: 10.5, color: C.muted }}>{list.length}</Typography>
            </Stack>,
            ...list.map((f) => {
            const v = form[f.name] ?? "";
            const isCh = !f.read_only && v !== (initial[f.name] ?? "");
            const empty = single != null ? missing.has(f.name) && !ph.has(f.name) && !v.trim() : !!missCount[f.name] && !v.trim();
            const bar = errs[f.name] ? RED : isCh ? GREEN : f.read_only ? GREY : empty ? RED : ph.has(f.name) && !v ? BLUE : "transparent";
            return (
              <Box key={f.name} onContextMenu={(e) => { e.preventDefault(); setUniq(null); setFmenu({ x: e.clientX, y: e.clientY, f }); }}
                sx={{ display: "grid", gridTemplateColumns: "3px minmax(0, 1fr) minmax(0, 1.15fr)", minHeight: 38, borderBottom: "1px solid #F0F1F3",
                      bgcolor: errs[f.name] ? "#FEF2F2" : isCh ? "#F0FDF4" : undefined }}>
                <Box sx={{ bgcolor: bar }} />
                <Tooltip placement="left" title={<Box sx={{ fontSize: 12 }}><b>{f.name}</b> · {f.type}{f.length ? `(${f.length})` : ""}{f.required ? " · required" : ""}
                  {f.domain ? ` · list of ${f.domain.length}` : ""}{f.read_only ? " · automatic" : ""}{f.description ? <><br />{f.description}</> : null}
                  {errs[f.name] ? <><br />{errs[f.name]}</> : null}</Box>}>
                  <Stack direction="row" alignItems="center" sx={{ px: 1.25, py: 0.5, minWidth: 0 }}>
                    <Typography sx={{ fontSize: 12.5, lineHeight: 1.25, color: f.read_only ? C.muted : empty ? RED : "#344054", fontWeight: 500,
                                      display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden", wordBreak: "break-word" }}>
                      {nice(f)}{f.required && <Box component="span" sx={{ color: RED }}> *</Box>}
                    </Typography>
                    {single == null && missCount[f.name] ? <Typography sx={{ ml: "auto", pl: 0.5, fontSize: 10.5, color: RED, whiteSpace: "nowrap" }}>{missCount[f.name]} empty</Typography> : null}
                  </Stack>
                </Tooltip>
                <Cell f={f} value={v} auto={ph.has(f.name) && !v ? (single != null ? "default" : "") : ""} several={single == null}
                  onChange={(x, commit) => put(f.name, x, commit)} onCommit={() => applyField(f.name)} />
              </Box>
            );
          })])}
          {!fields.length && <Typography sx={{ p: 2, fontSize: 12.5, color: GREEN }}>Nothing to fill.</Typography>}
        </Box>
      )}
      <Menu open={!!fmenu} onClose={() => setFmenu(null)} anchorReference="anchorPosition" anchorPosition={fmenu ? { top: fmenu.y, left: fmenu.x } : undefined}
        PaperProps={{ sx: { maxHeight: 420, minWidth: 220 } }}>
        {fmenu && !fmenu.f.read_only && [
          <MenuItem key="n" dense disabled={fmenu.f.not_null} onClick={() => { put(fmenu.f.name, "", true); setFmenu(null); }}><ListItemIcon><BlockOutlined fontSize="small" /></ListItemIcon>Set to &lt;Null&gt;</MenuItem>,
          <MenuItem key="r" dense disabled={(form[fmenu.f.name] ?? "") === (initial[fmenu.f.name] ?? "")} onClick={() => { put(fmenu.f.name, initial[fmenu.f.name] ?? ""); setFmenu(null); }}><ListItemIcon><UndoOutlined fontSize="small" /></ListItemIcon>Revert</MenuItem>,
          <MenuItem key="u" dense onClick={() => loadUnique(fmenu.f)}><ListItemIcon><FormatListBulletedOutlined fontSize="small" /></ListItemIcon>Unique values</MenuItem>,
          ...(uniq === null ? [] : uniq.length === 0 ? [<MenuItem key="ul" dense disabled><CircularProgress size={14} sx={{ mr: 1 }} />…</MenuItem>]
            : uniq.map((u) => <MenuItem key={"u" + u.value} dense sx={{ pl: 6 }} onClick={() => { put(fmenu.f.name, u.value, true); setFmenu(null); }}>
                <Typography sx={{ fontSize: 13, flex: 1 }} noWrap>{u.value}</Typography><Typography sx={{ fontSize: 11, color: C.muted, ml: 2 }}>{u.count}</Typography></MenuItem>)),
          <Divider key="d" />,
        ]}
        {fmenu && <MenuItem dense onClick={() => { navigator.clipboard?.writeText(form[fmenu.f.name] ?? ""); setFmenu(null); }}><ListItemIcon><ContentCopyOutlined fontSize="small" /></ListItemIcon>Copy value</MenuItem>}
        {fmenu && !fmenu.f.read_only && <MenuItem dense onClick={async () => { const t = await navigator.clipboard?.readText?.().catch(() => ""); if (t) put(fmenu.f.name, t.trim(), true); setFmenu(null); }}>
          <ListItemIcon><ContentPasteOutlined fontSize="small" /></ListItemIcon>Paste value</MenuItem>}
      </Menu>

      {/* ---------------- geometry */}
      {tab === "geometry" && single != null && <GeometryTab parts={p.coordsOf(single)} geographic={p.geographic} />}

      </Box>

      {/* ---------------- footer */}
      {spec && (
        <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 1.5, py: 1, borderTop: `1px solid ${C.line}` }}>
          <Tooltip title="Apply each value as soon as you leave the field"><Stack direction="row" alignItems="center" sx={{ mr: "auto" }}>
            <Switch size="small" checked={auto} onChange={(e) => setAutoP(e.target.checked)} />
            <Typography sx={{ fontSize: 12, color: C.muted, whiteSpace: "nowrap" }}>Auto Apply</Typography>
          </Stack></Tooltip>
          {!auto && <Button size="small" disabled={!dirty || saving} onClick={cancel} sx={{ color: C.muted }}>Cancel</Button>}
          {!auto && <Button size="small" variant="outlined" disabled={!dirty || saving} onClick={() => apply()}>Apply</Button>}
          {single != null && (
            <Tooltip title="Apply, then the next feature to fill  (Ctrl+Enter)">
              <Button size="small" variant="contained" disabled={saving} onClick={applyNext} endIcon={<ChevronRight />}>Next</Button>
            </Tooltip>
          )}
        </Stack>
      )}
    </Box>
  );
}

function Dot({ s }: { s?: AssignState }) {
  const c = s === "completed" ? GREEN : s === "error" ? RED : s === "incomplete" ? "#F59E0B" : GREY;
  return <Box sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: c, flex: "none" }} />;
}

/** one value cell: borderless, full width; lists with search, dates with Today, numbers checked by the browser */
function Cell({ f, value, onChange, onCommit, auto, several }: {
  f: AssignField; value: string; onChange: (v: string, commit?: boolean) => void; onCommit: () => void; auto: string; several: boolean;
}) {
  const ph = several ? "" : auto ? `N/A · ${auto}` : "<Null>";
  const base = { fontSize: 12.5, px: 1.25, width: "100%", height: "100%", "& input::placeholder": { color: auto ? BLUE : "#9AA1AB", opacity: 1, fontStyle: "normal" } };
  if (f.read_only) return <Typography sx={{ fontSize: 12.5, px: 1.25, color: C.muted, alignSelf: "center" }} noWrap>{value || "automatic"}</Typography>;
  if (f.domain) {
    const free = f.name.toUpperCase().includes("NAME");
    const opts = f.domain;
    const label = (o: [string, string]) => (o[1] && o[1] !== o[0] ? `${o[0]} — ${o[1]}` : o[0]);
    return (
      <Autocomplete size="small" options={opts} freeSolo={free} autoHighlight openOnFocus
        value={opts.find((o) => o[0] === value) || (free && value ? ([value, value] as [string, string]) : null)}
        getOptionLabel={(o) => (typeof o === "string" ? o : label(o))} isOptionEqualToValue={(a, b) => a[0] === b[0]}
        filterOptions={(o, s) => { const q = s.inputValue.trim().toLowerCase(); return q ? o.filter((x) => label(x).toLowerCase().includes(q)) : o; }}
        onChange={(_, o) => onChange(o == null ? "" : typeof o === "string" ? o : o[0], true)}
        onInputChange={(_, v, r) => { if (free && r === "input") onChange(v); }}
        onBlur={() => free && onCommit()}
        sx={{ "& .MuiAutocomplete-inputRoot": { py: "0 !important", pr: "30px !important" } }}
        slotProps={{ paper: { sx: { fontSize: 13 } } }}
        renderInput={(params) => (
          <InputBase ref={params.InputProps.ref} inputProps={{ ...params.inputProps, dir: "auto" }} placeholder={several ? "—" : ph}
            sx={{ ...base, "& input": { p: 0, height: 32 } }} endAdornment={params.InputProps.endAdornment} />
        )} />
    );
  }
  const type = f.type === "DATE" ? "date" : NUM.includes(f.type) ? "number" : "text";
  return (
    <Stack direction="row" alignItems="center">
      <InputBase value={value} type={type} placeholder={several ? "—" : ph} onChange={(e) => onChange(e.target.value)}
        onBlur={onCommit} onKeyDown={(e) => { if (e.key === "Enter" && !e.ctrlKey) { (e.target as HTMLInputElement).blur(); } }}
        inputProps={{ maxLength: f.length || undefined, step: f.type === "DOUBLE" || f.type === "FLOAT" ? "any" : undefined, dir: "auto" }}
        sx={{ ...base, "& input": { p: 0, height: 32 } }} />
      {type === "date" && !value && <Tooltip title="Today"><IconButton size="small" onClick={() => onChange(new Date().toISOString().slice(0, 10), true)}><TodayOutlined sx={{ fontSize: 16 }} /></IconButton></Tooltip>}
    </Stack>
  );
}

function GeometryTab({ parts, geographic }: { parts: number[][][] | null; geographic: boolean }) {
  if (!parts) return <Typography sx={{ p: 2, fontSize: 12.5, color: C.muted }}>No geometry</Typography>;
  const dec = geographic ? 7 : 3;
  const n = parts.reduce((a, p) => a + p.length, 0);
  const copy = () => navigator.clipboard?.writeText(parts.map((pt, i) => pt.map((c, j) => `${i + 1}\t${j + 1}\t${c[0].toFixed(dec)}\t${c[1].toFixed(dec)}`).join("\n")).join("\n"));
  return (
    <Box sx={{ flex: 1, overflow: "auto" }}>
      <Stack direction="row" alignItems="center" sx={{ px: 1.5, py: 0.75, borderBottom: `1px solid ${C.line}` }}>
        <Typography sx={{ fontSize: 12, color: C.muted, flex: 1 }}>{n} vertices{parts.length > 1 ? ` · ${parts.length} parts` : ""} · {geographic ? "longitude / latitude" : "drawing units"}</Typography>
        <Button size="small" startIcon={<ContentCopyOutlined sx={{ fontSize: 15 }} />} onClick={copy}>Copy</Button>
      </Stack>
      <Box component="table" sx={{ width: "100%", borderCollapse: "collapse", fontSize: 12, fontVariantNumeric: "tabular-nums",
        "& td, & th": { px: 1.25, py: 0.5, borderBottom: "1px solid #EEF0F2", textAlign: "right" }, "& th": { color: C.muted, fontWeight: 600, position: "sticky", top: 0, bgcolor: "#fff" } }}>
        <thead><tr><th style={{ textAlign: "left" }}>#</th><th>{geographic ? "Lon" : "X"}</th><th>{geographic ? "Lat" : "Y"}</th></tr></thead>
        <tbody>
          {parts.flatMap((pt, i) => pt.slice(0, 2000).map((c, j) => (
            <tr key={`${i}-${j}`}><td style={{ textAlign: "left", color: C.muted }}>{parts.length > 1 ? `${i + 1}.` : ""}{j + 1}</td><td>{c[0].toFixed(dec)}</td><td>{c[1].toFixed(dec)}</td></tr>
          )))}
        </tbody>
      </Box>
    </Box>
  );
}
