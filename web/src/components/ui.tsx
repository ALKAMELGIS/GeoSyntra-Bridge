import { Box, Chip, Stack, Tooltip, Typography } from "@mui/material";
import PlaceOutlined from "@mui/icons-material/PlaceOutlined";
import TimelineOutlined from "@mui/icons-material/TimelineOutlined";
import CropSquareOutlined from "@mui/icons-material/CropSquareOutlined";
import type { ReactNode } from "react";
import { C } from "../theme";

export function GeomIcon({ g, size = 16 }: { g: string; size?: number }) {
  const sx = { fontSize: size, color: C.muted, verticalAlign: "middle" };
  if (g === "point") return <PlaceOutlined sx={sx} titleAccess="Point" />;
  if (g === "line") return <TimelineOutlined sx={sx} titleAccess="Polyline" />;
  return <CropSquareOutlined sx={sx} titleAccess="Polygon" />;
}

const STATUS: Record<string, { label: string; fg: string; bg: string }> = {
  AUTO: { label: "Auto", fg: C.green, bg: C.greenSoft },
  USER: { label: "Confirmed", fg: C.navy, bg: C.blueSoft },
  PROFILE: { label: "Profile", fg: C.navy, bg: C.blueSoft },
  SUGGESTED: { label: "Review", fg: C.amber, bg: C.amberSoft },
  MANUAL: { label: "Low", fg: C.red, bg: C.redSoft },
  UNRESOLVED: { label: "Unmapped", fg: C.muted, bg: "#EEF1F5" },
  IGNORED: { label: "Ignored", fg: C.muted, bg: "#EEF1F5" },
};

export function MapStatus({ status, score }: { status: string; score?: number }) {
  const s = STATUS[status] || STATUS.UNRESOLVED;
  return (
    <Chip size="small" label={score != null && status !== "USER" && status !== "IGNORED" ? `${s.label} ${Math.round(score * 100)}%` : s.label}
      sx={{ color: s.fg, bgcolor: s.bg }} />
  );
}

export const SEV = {
  error: { fg: C.red, bg: C.redSoft },
  warning: { fg: C.amber, bg: C.amberSoft },
  info: { fg: C.blue, bg: C.blueSoft },
} as const;

export function SevChip({ s }: { s: keyof typeof SEV }) {
  return <Chip size="small" label={s} sx={{ color: SEV[s].fg, bgcolor: SEV[s].bg, textTransform: "capitalize", minWidth: 62 }} />;
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1, minHeight: 28 }}>
      <Typography variant="subtitle2">{children}</Typography>
      {right}
    </Stack>
  );
}

export function KV({ k, v, hint }: { k: string; v: ReactNode; hint?: string }) {
  return (
    <Stack direction="row" justifyContent="space-between" sx={{ py: 0.4, borderBottom: `1px dashed ${C.line}` }}>
      <Typography variant="body2" color="text.secondary">{k}</Typography>
      <Tooltip title={hint || ""}><Typography variant="body2" fontWeight={600} sx={{ textAlign: "right" }}>{v}</Typography></Tooltip>
    </Stack>
  );
}

export function Stat({ label, value, tone = "navy", onClick, active }: {
  label: string; value: number | string; tone?: "navy" | "green" | "red" | "amber" | "blue" | "muted";
  onClick?: () => void; active?: boolean;
}) {
  const color = { navy: C.navy, green: C.green, red: C.red, amber: C.amber, blue: C.blue, muted: C.muted }[tone];
  return (
    <Box onClick={onClick} sx={{
      px: 1.5, py: 1, border: `1px solid ${active ? color : C.line}`, borderRadius: 1.5, bgcolor: "#fff",
      cursor: onClick ? "pointer" : "default", minWidth: 0, transition: "border-color .15s",
      boxShadow: active ? `inset 0 0 0 1px ${color}` : "none", "&:hover": onClick ? { borderColor: color } : {},
    }}>
      <Typography sx={{ fontSize: 20, fontWeight: 700, color, lineHeight: 1.2 }}>{value}</Typography>
      <Typography variant="caption" noWrap component="div">{label}</Typography>
    </Box>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <Box sx={{ p: 3, textAlign: "center", color: C.muted, fontSize: 12.5 }}>{children}</Box>;
}

export function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 ** 2).toFixed(1)} MB`;
}
