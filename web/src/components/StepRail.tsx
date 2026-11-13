import { Box, ButtonBase, Typography } from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import { C } from "../theme";

export interface RailStep { title: string; note?: string; noteTone?: "ok" | "warn" | "muted"; done: boolean; enabled: boolean }

/** The five stages of a conversion: a segmented rail, each segment a button with its own state line. */
export function StepRail({ steps, current, onSelect }: { steps: RailStep[]; current: number; onSelect: (i: number) => void }) {
  return (
    <Box component="nav" aria-label="Conversion steps" sx={{ display: "grid", gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))`, gap: { xs: 0.5, md: 1.25 } }}>
      {steps.map((s, i) => {
        const active = i === current;
        const bar = active ? C.cyan : s.done ? C.ink : C.line;
        return (
          <ButtonBase key={s.title} disabled={!s.enabled} onClick={() => onSelect(i)} aria-current={active ? "step" : undefined}
            sx={{ display: "block", textAlign: "start", borderRadius: 1, px: 0.25, pt: 1, pb: 1.1, opacity: s.enabled ? 1 : 0.45,
                  "&:hover .rail-t": { color: s.enabled ? C.ink : undefined }, "&.Mui-focusVisible": { outline: `2px solid ${C.cyan}` } }}>
            <Box sx={{ height: 3, borderRadius: 2, bgcolor: bar, mb: 1.1, transition: "background-color .25s" }} />
            <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <Box sx={{ width: 22, height: 22, flex: "none", borderRadius: "50%", display: "grid", placeItems: "center", fontSize: 12, fontWeight: 700,
                         bgcolor: active ? C.cyan : s.done ? C.ink : "transparent", color: active || s.done ? "#fff" : C.muted,
                         border: active || s.done ? "none" : `1.5px solid ${C.line}` }}>
                {s.done && !active ? <CheckRounded sx={{ fontSize: 15 }} /> : i + 1}
              </Box>
              <Typography className="rail-t" noWrap sx={{ fontSize: 13.5, fontWeight: active ? 700 : 600, color: active ? C.ink : s.done ? C.ink : C.muted }}>
                {s.title}
              </Typography>
            </Box>
            <Typography noWrap sx={{ display: { xs: "none", md: "block" }, fontSize: 12, mt: 0.4, ml: "30px",
                                     color: s.noteTone === "ok" ? C.green : s.noteTone === "warn" ? C.amber : C.muted }}>
              {s.note || " "}
            </Typography>
          </ButtonBase>
        );
      })}
    </Box>
  );
}
