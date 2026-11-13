import type { ReactNode } from "react";
import { AppBar, Box, ButtonBase, Toolbar } from "@mui/material";
import type { AuthUser } from "../types";
import { Brand, UserMenu } from "./UserMenu";

interface Props { me: AuthUser | null; mode: string; go: (r: string) => void; onLogout: () => void; active: "app" | "account" | "owner"; right?: ReactNode }

/** Quiet paper bar shared by every signed-in page: brand, the three areas, credits and the account menu. */
export function AppHeader({ me, mode, go, onLogout, active, right }: Props) {
  const staff = me && (me.role === "owner" || me.role === "admin");
  const links: [string, string, Props["active"]][] = [["Conversions", "/app", "app"]];
  if (mode === "accounts") links.push(["Account & billing", "/account", "account"]);
  if (staff && mode === "accounts") links.push(["Owner console", "/owner", "owner"]);   // nothing to administer in local use
  return (
    <AppBar position="sticky" sx={{ bgcolor: "#FBFAF8", color: "#16171A", borderBottom: "1px solid #E4E2DD", top: 0, left: 0, right: 0 }}>
      <Toolbar sx={{ gap: 3, minHeight: "58px !important" }}>
        <Brand go={go} />
        <Box component="nav" sx={{ display: { xs: "none", md: "flex" }, gap: 0.5, alignSelf: "stretch" }}>
          {links.map(([label, to, key]) => (
            <ButtonBase key={key} onClick={() => go(to)} aria-current={active === key ? "page" : undefined}
              sx={{ px: 1.5, fontSize: 14, fontWeight: active === key ? 500 : 400, color: active === key ? "#16171A" : "#5F5D58",
                    borderBottom: `2px solid ${active === key ? "#16171A" : "transparent"}`, "&:hover": { color: "#16171A" } }}>
              {label.replace("&", "and")}
            </ButtonBase>
          ))}
        </Box>
        <Box sx={{ flex: 1 }} />
        {right}
        {me && <UserMenu user={me} mode={mode} go={go} onLogout={onLogout} />}
      </Toolbar>
    </AppBar>
  );
}
