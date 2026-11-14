import { useEffect, useState } from "react";
import { Badge, Box, ButtonBase, IconButton, Popover, Stack, Tooltip, Typography } from "@mui/material";
import NotificationsNoneOutlined from "@mui/icons-material/NotificationsNoneOutlined";
import { api } from "../api";
import { C } from "../theme";
import { tr } from "../i18n";

type Note = { id: string; tone: "red" | "amber" | "blue"; title: string; body?: string; href?: string };

/** Every notice the system has for the user, in one place: the bell in the header. Nothing is shown as a banner. */
export function Notifications({ admin }: { admin: boolean }) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [seen, setSeen] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("gsb_seen_notes") || "[]"); } catch { return []; } });

  useEffect(() => {
    let alive = true;
    const load = async () => {
      const out: Note[] = [];
      const caps = await api.capabilities().catch(() => null);
      if (caps && caps.update_pending !== false) out.push({ id: "update", tone: "amber", title: tr("Restart the server to finish the update"),
        body: tr("Close the server window and run start_windows.bat once. Later updates restart by themselves.") });
      if (caps && !caps.filegdb) out.push({ id: "filegdb", tone: "red", title: tr("File geodatabase writer unavailable"),
        body: tr("Deliveries cannot be generated until it is installed.") });
      const jobs = await api.jobs().catch(() => [] as any[]);
      jobs.filter((j: any) => j.status === "failed").slice(0, 6).forEach((j: any) => out.push({
        id: `job:${j.job_id}:${j.updated_at}`, tone: "red", title: j.file?.name || tr("Conversion"),
        body: (j.message || tr("Stopped — open to see why")).slice(0, 120), href: `#job=${j.job_id}` }));
      if (admin) {
        const schemas = await api.schemas().catch(() => [] as any[]);
        for (const s of schemas.filter((x: any) => x.schema_id !== "adm_all")) {
          const h = await api.templateUpdates(s.schema_id).catch(() => null);
          const p = h?.updates.find((u) => u.status === "pending");
          if (p) out.push({ id: `tpl:${p.update_id}`, tone: "blue", title: tr("New template release waiting"),
            body: `${p.template} · ${p.current_version} → ${p.new_version}`, href: `#/templates?schema=${s.schema_id}` });
        }
      }
      if (alive) setNotes(out);
    };
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [admin]);

  const unseen = notes.filter((n) => !seen.includes(n.id)).length;
  const openIt = (e: React.MouseEvent<HTMLElement>) => {
    setEl(e.currentTarget);
    const all = Array.from(new Set([...seen, ...notes.map((n) => n.id)])).slice(-200);
    setSeen(all); try { localStorage.setItem("gsb_seen_notes", JSON.stringify(all)); } catch { /* ignore */ }
  };
  const dot = { red: C.red, amber: C.amber, blue: C.cyan };

  return (
    <>
      <Tooltip title={tr("Notifications")}>
        <IconButton onClick={openIt} aria-label={tr("Notifications")} sx={{ color: unseen ? C.ink : "#5D6573" }}>
          <Badge badgeContent={unseen} max={9} sx={{ "& .MuiBadge-badge": { bgcolor: C.ink, color: "#fff", fontSize: 10, height: 16, minWidth: 16 } }}>
            <NotificationsNoneOutlined fontSize="small" />
          </Badge>
        </IconButton>
      </Tooltip>
      <Popover open={!!el} anchorEl={el} onClose={() => setEl(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }} transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 360, maxWidth: "calc(100vw - 32px)", borderRadius: 3, mt: 1, boxShadow: "0 8px 28px rgba(20,24,31,.16)" } } }}>
        <Typography sx={{ px: 2, pt: 1.75, pb: 1, fontSize: 14, fontWeight: 500 }}>{tr("Notifications")}</Typography>
        {notes.length === 0 && <Typography sx={{ px: 2, pb: 2, fontSize: 13, color: C.muted }}>{tr("Nothing new")}</Typography>}
        <Box sx={{ maxHeight: 420, overflow: "auto", pb: 0.5 }}>
          {notes.map((n) => (
            <ButtonBase key={n.id} disabled={!n.href} onClick={() => { if (n.href) { window.location.hash = n.href.slice(1); setEl(null); } }}
              sx={{ width: "100%", justifyContent: "flex-start", textAlign: "start", gap: 1.25, px: 2, py: 1.25, borderTop: `1px solid #EEF0F3`,
                    "&:hover": { bgcolor: n.href ? "#F5F6F8" : undefined }, "&.Mui-disabled": { opacity: 1 } }}>
              <Box sx={{ width: 6, height: 6, mt: 0.9, flex: "none", borderRadius: "50%", bgcolor: dot[n.tone], alignSelf: "flex-start" }} />
              <Stack sx={{ minWidth: 0 }}>
                <Typography sx={{ fontSize: 13, fontWeight: 500 }} noWrap>{n.title}</Typography>
                {n.body && <Typography sx={{ fontSize: 12.5, color: C.muted }}>{n.body}</Typography>}
              </Stack>
            </ButtonBase>
          ))}
        </Box>
      </Popover>
    </>
  );
}
