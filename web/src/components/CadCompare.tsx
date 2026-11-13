/**
 * CAD tab — the selected feature as it is in the drawing, next to what goes to the geodatabase,
 * so the engineer can confirm each value: entity, block attributes, labels read from the drawing, and a
 * CAD ↔ GIS comparison (✓ same, ≠ different, — not in the drawing).
 */
import { useEffect, useState } from "react";
import { Box, LinearProgress, Stack, Tooltip, Typography } from "@mui/material";
import NearMeOutlined from "@mui/icons-material/NearMeOutlined";
import { api } from "../api";
import type { AssignDetail, AssignOverview } from "../types";
import { C } from "../theme";

const GREEN = "#16A34A", RED = "#DC2626";
const num = (v: unknown) => (v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v));
const fmt = (v: unknown, d = 3) => { const n = num(v); return n === null ? (v == null || v === "" ? "—" : String(v)) : String(Math.round(n * 10 ** d) / 10 ** d); };
const norm = (v: unknown) => String(v ?? "").trim().toUpperCase().replace(/\s+/g, " ");

export function CadCompare({ jobId, fid, ov, embedded }: { jobId: string; fid: number | null; ov: AssignOverview | null; embedded?: boolean }) {
  const [d, setD] = useState<AssignDetail | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (fid == null) { setD(null); return; }
    let live = true;
    setLoading(true);
    api.assignDetail(jobId, fid).then((x) => { if (live) setD(x); }).catch(() => live && setD(null)).finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [jobId, fid]);

  if (fid == null) {
    return (
      <Stack alignItems="center" justifyContent="center" spacing={1} sx={{ flex: 1, p: 4, color: C.muted }}>
        <NearMeOutlined sx={{ fontSize: 30 }} /><Typography sx={{ fontSize: 13.5 }}>Select one feature</Typography>
      </Stack>
    );
  }
  if (!d) return <LinearProgress sx={{ height: 2 }} />;

  const s = d.source as Record<string, any>;
  const gis = (d.gdb?.values || d.attributes) as Record<string, unknown>;
  const fields = ov?.classes[d.target]?.fields || [];
  const alias = (n: string) => fields.find((f) => f.name === n)?.alias || n;
  const lv = ((d as any).label_values || {}) as Record<string, { value: unknown; text: string; handle: string }[]>;
  const rejected = ((d as any).rejected || {}) as Record<string, string>;
  const battrs = (s.attributes || {}) as Record<string, string>;
  const byValue = (v: unknown) => Object.keys(gis).filter((k) => norm(gis[k]) && norm(gis[k]) === norm(v));
  const zField = Object.keys(gis).find((k) => /^ZCOORD$|GROUND_LEVEL|COVER_LEVEL/i.test(k));

  // CAD ↔ GIS: the values that can be compared directly
  const cmp: { cad: string; cv: unknown; gf: string | null; tol?: number }[] = [
    { cad: "X", cv: s.x, gf: Object.keys(gis).find((k) => /^XCOORD$/i.test(k)) || null, tol: 0.01 },
    { cad: "Y", cv: s.y, gf: Object.keys(gis).find((k) => /^YCOORD$/i.test(k)) || null, tol: 0.01 },
    { cad: "Z", cv: s.z, gf: zField || null, tol: 0.01 },
    ...(s.length != null ? [{ cad: "Length", cv: s.length, gf: Object.keys(gis).find((k) => /LENGTH$/i.test(k) && !/SHAPE/i.test(k)) || null, tol: 0.05 }] : []),
    ...(s.area != null ? [{ cad: "Area", cv: s.area, gf: Object.keys(gis).find((k) => /AREA$/i.test(k) && !/SHAPE/i.test(k)) || null, tol: 0.05 }] : []),
  ];
  const state = (cv: unknown, gv: unknown, tol?: number): "same" | "diff" | "none" => {
    if (cv == null || cv === "" || gv == null || gv === "") return "none";
    const a = num(cv), b = num(gv);
    if (a !== null && b !== null) return Math.abs(a - b) <= (tol ?? 0) ? "same" : "diff";
    return norm(cv) === norm(gv) ? "same" : "diff";
  };

  return (
    <Box sx={{ flex: embedded ? undefined : 1, overflow: "auto", opacity: loading ? 0.5 : 1 }}>
      {loading && <LinearProgress sx={{ height: 2 }} />}
      {!embedded && <Box sx={{ px: 1.5, py: 1, borderBottom: `1px solid ${C.line}` }}>
        <Typography sx={{ fontSize: 13.5, fontWeight: 700 }} noWrap>{s.layer}</Typography>
        <Typography sx={{ fontSize: 11.5, color: C.muted }} noWrap>{s.type}{s.block ? ` · ${s.block}` : ""} · {s.handle}</Typography>
      </Box>}

      {!embedded && <Sec title="CAD ↔ GIS" />}
      <Grid3 head={["CAD", "", "GIS"]} />
      {cmp.map((r) => {
        const gv = r.gf ? gis[r.gf] : undefined;
        const st = state(r.cv, gv, r.tol);
        return <Grid3 key={r.cad} row={[`${r.cad}  ${fmt(r.cv)}`, st, r.gf ? `${alias(r.gf)}  ${fmt(gv)}` : "—"]} />;
      })}
      {s.block && <Grid3 row={[`Block  ${s.block}`, byValue(s.block).length ? "same" : "none", byValue(s.block).map(alias).join(", ") || "—"]} />}

      {Object.keys(battrs).length > 0 && (<>
        <Sec title="Block attributes" />
        {Object.entries(battrs).map(([k, v]) => {
          const hit = byValue(v);
          return <Grid3 key={k} row={[`${k}  ${v || "—"}`, v ? (hit.length ? "same" : "diff") : "none", hit.map(alias).join(", ") || "not used"]} />;
        })}
      </>)}

      {Object.keys(lv).length > 0 && (<>
        <Sec title="Labels read from the drawing" />
        {Object.entries(lv).map(([k, xs]) => (
          <Grid3 key={k} row={[`“${xs[0].text}”`, state(xs[0].value, gis[k]), `${alias(k)}  ${fmt(gis[k])}`]} />
        ))}
      </>)}

      {Object.keys(rejected).length > 0 && (<>
        <Sec title="Not accepted by the template" tone={RED} />
        {Object.entries(rejected).map(([k, v]) => <Grid3 key={k} row={[v, "diff", `${alias(k)}  ${fmt(gis[k])}`]} />)}
      </>)}

      {!embedded && <Sec title="Entity" />}
      {!embedded && [["Handle", s.handle], ["Layer", s.layer], ["Type", s.type], ["Block", s.block], ["Text", s.text], ["X", fmt(s.x)], ["Y", fmt(s.y)], ["Z", fmt(s.z)],
        ["Vertices", s.vertex_count], ["Length", s.length != null ? fmt(s.length) : null], ["Area", s.area != null ? fmt(s.area) : null],
        ["Rotation", s.rotation], ["Color", s.color], ["Linetype", s.linetype], ["Lineweight", s.lineweight]]
        .filter(([, v]) => v !== null && v !== undefined && v !== "" && v !== "—")
        .map(([k, v]) => (
          <Stack key={String(k)} direction="row" sx={{ px: 1.5, py: 0.6, borderBottom: "1px solid #EEF0F2" }}>
            <Typography sx={{ fontSize: 12.5, color: C.muted, width: 92, flex: "none" }}>{k}</Typography>
            <Typography sx={{ fontSize: 12.5, minWidth: 0, wordBreak: "break-word" }} dir="auto">{String(v)}</Typography>
          </Stack>
        ))}
    </Box>
  );
}

function Sec({ title, tone }: { title: string; tone?: string }) {
  return <Typography sx={{ px: 1.5, pt: 1.5, pb: 0.5, fontSize: 11, fontWeight: 700, letterSpacing: 0.4, textTransform: "uppercase", color: tone || C.muted }}>{title}</Typography>;
}

function Grid3({ head, row }: { head?: string[]; row?: [string, string, string] }) {
  if (head) return (
    <Box sx={{ display: "grid", gridTemplateColumns: "1fr 22px 1fr", px: 1.5, py: 0.4, bgcolor: "#F7F8FA", fontSize: 11, fontWeight: 700, color: C.muted }}>
      <span>{head[0]}</span><span /><span>{head[2]}</span>
    </Box>
  );
  const [a, st, b] = row!;
  const mark = st === "same" ? <Tooltip title="Same"><span style={{ color: GREEN, fontWeight: 700 }}>✓</span></Tooltip>
    : st === "diff" ? <Tooltip title="Different"><span style={{ color: RED, fontWeight: 700 }}>≠</span></Tooltip>
    : <span style={{ color: "#B8BDC5" }}>·</span>;
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: "1fr 22px 1fr", alignItems: "center", px: 1.5, py: 0.6, borderBottom: "1px solid #EEF0F2",
               bgcolor: st === "diff" ? "#FEF7F7" : undefined }}>
      <Typography sx={{ fontSize: 12.5, minWidth: 0, wordBreak: "break-word" }} dir="auto">{a}</Typography>
      <Box sx={{ textAlign: "center", fontSize: 13 }}>{mark}</Box>
      <Typography sx={{ fontSize: 12.5, minWidth: 0, wordBreak: "break-word" }} dir="auto">{b}</Typography>
    </Box>
  );
}
