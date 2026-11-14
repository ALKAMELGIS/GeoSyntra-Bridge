import { useState } from "react";
import { Avatar, Box, Divider, ListItemIcon, Menu, MenuItem, Tooltip, Typography } from "@mui/material";
import AccountCircleOutlined from "@mui/icons-material/AccountCircleOutlined";
import AdminPanelSettingsOutlined from "@mui/icons-material/AdminPanelSettingsOutlined";
import LogoutOutlined from "@mui/icons-material/LogoutOutlined";
import HomeOutlined from "@mui/icons-material/HomeOutlined";
import TransformOutlined from "@mui/icons-material/TransformOutlined";
import type { AuthUser } from "../types";
import { LogoMark } from "./Logo";

interface Props { user: AuthUser | null; mode: string; go: (r: string) => void; onLogout: () => void }

/** Credits + account menu shown in every signed-in header. */
export function UserMenu({ user, mode, go, onLogout }: Props) {
  const [el, setEl] = useState<HTMLElement | null>(null);
  if (!user) return null;
  const staff = user.role === "owner" || user.role === "admin";
  const accounts = mode === "accounts";
  return (
    <>
      {accounts && user.credits !== null && (
        <Tooltip title="Credits — a geodatabase costs credits, everything else is free">
          <Box component="button" onClick={() => go("/account")}
            sx={{ display: "flex", alignItems: "center", gap: 0.75, border: 0, bgcolor: "transparent", cursor: "pointer", font: "inherit", fontSize: 13,
                  color: "#5D6573", px: 1, height: 32, borderRadius: 1, "&:hover": { bgcolor: "#F3F4F6" } }}>
            <Box component="span" sx={{ width: 6, height: 6, borderRadius: "50%", bgcolor: !staff && user.credits < 15 ? "#9A5B0F" : "#2F7D55" }} />
            {staff ? "Unlimited" : `${user.credits} credits`}
          </Box>
        </Tooltip>
      )}
      <Box component="button" onClick={(e: React.MouseEvent<HTMLElement>) => setEl(e.currentTarget)}
        sx={{ display: "flex", alignItems: "center", gap: 1, bgcolor: "#FAFBFC", border: "1px solid #E3E6EA", color: "#14181F", cursor: "pointer",
              py: 0.4, pl: 0.4, pr: 1.25, borderRadius: 2, font: "inherit", "&:hover": { borderColor: "#C5CBD3" } }}
        aria-label="Account menu">
        <Avatar src={user.avatar_url || undefined} sx={{ width: 28, height: 28, fontSize: 11.5, fontWeight: 500, bgcolor: "#14181F", color: "#FAFBFC" }}>
          {(user.name || user.email || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase()}
        </Avatar>
        <Typography noWrap sx={{ fontSize: 13.5, maxWidth: 200, display: { xs: "none", md: "block" } }}>{user.name || user.email}</Typography>
      </Box>
      <Menu anchorEl={el} open={!!el} onClose={() => setEl(null)} anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}>
        <Box sx={{ px: 2, py: 1 }}>
          <Typography variant="body2" fontWeight={600}>{user.name || user.email}</Typography>
          <Typography variant="caption">{user.email} · {user.role}</Typography>
        </Box>
        <Divider />
        <MenuItem onClick={() => { setEl(null); go("/app"); }}><ListItemIcon><TransformOutlined fontSize="small" /></ListItemIcon>Converter</MenuItem>
        {accounts && <MenuItem onClick={() => { setEl(null); go("/account"); }}><ListItemIcon><AccountCircleOutlined fontSize="small" /></ListItemIcon>Account & billing</MenuItem>}
        {staff && <MenuItem onClick={() => { setEl(null); go("/owner"); }}><ListItemIcon><AdminPanelSettingsOutlined fontSize="small" /></ListItemIcon>Owner console</MenuItem>}
        <MenuItem onClick={() => { setEl(null); go("/"); }}><ListItemIcon><HomeOutlined fontSize="small" /></ListItemIcon>Website</MenuItem>
        {accounts && <Divider />}
        {accounts && <MenuItem onClick={() => { setEl(null); onLogout(); }}><ListItemIcon><LogoutOutlined fontSize="small" /></ListItemIcon>Sign out</MenuItem>}
      </Menu>
    </>
  );
}

export function Brand({ go, subtitle }: { go: (r: string) => void; subtitle?: string }) {
  return (
    <Box component="a" href="#/" onClick={(e: React.MouseEvent) => { e.preventDefault(); go("/"); }}
      sx={{ display: "flex", alignItems: "center", gap: 1, color: "#14181F", textDecoration: "none", whiteSpace: "nowrap" }}>
      <LogoMark size={28} />
      <Typography sx={{ fontWeight: 600, fontSize: 16, letterSpacing: "-0.02em" }}>GeoSyntra</Typography>
      <Typography sx={{ fontSize: 16, color: "#5D6573", letterSpacing: "-0.01em" }}>Bridge</Typography>
      {subtitle && <Typography sx={{ fontSize: 13.5, opacity: 0.7, display: { xs: "none", sm: "block" } }}>{subtitle}</Typography>}
    </Box>
  );
}
