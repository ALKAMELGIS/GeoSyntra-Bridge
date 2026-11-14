import type { ReactNode } from "react";
import { AppBar, Box, ButtonBase, Toolbar } from "@mui/material";
import { isAr, setLang, tr } from "../i18n";
import type { AuthUser } from "../types";
import { Brand, UserMenu } from "./UserMenu";

interface Props { me: AuthUser | null; mode: string; go: (r: string) => void; onLogout: () => void; active: "app" | "account" | "owner" | "templates"; right?: ReactNode }

/** Quiet paper bar shared by every signed-in page: brand, the three areas, credits and the account menu. */
export function AppHeader({ me, mode, go, onLogout, active, right }: Props) {
  const staff = me && (me.role === "owner" || me.role === "admin");
  const links: [string, string, Props["active"]][] = [["Conversions", "/app", "app"]];
  links.push(["ADM templates", "/templates", "templates"]);
  if (mode === "accounts") links.push(["Account & billing", "/account", "account"]);
  if (staff && mode === "accounts") links.push(["Owner console", "/owner", "owner"]);   // nothing to administer in local use
  return (
    <AppBar position="sticky" sx={{ bgcolor: "#FAFBFC", color: "#14181F", borderBottom: "1px solid #E3E6EA", top: 0, left: 0, right: 0 }}>
      <Toolbar sx={{ gap: 3, minHeight: "58px !important" }}>
        <Brand go={go} />
        <Box component="nav" sx={{ display: { xs: "none", md: "flex" }, gap: 0.5, alignSelf: "stretch" }}>
          {links.map(([label, to, key]) => (
            <ButtonBase key={key} onClick={() => go(to)} aria-current={active === key ? "page" : undefined}
              sx={{ px: 1.5, fontSize: 14, fontWeight: active === key ? 500 : 400, color: active === key ? "#14181F" : "#5D6573",
                    borderBottom: `2px solid ${active === key ? "#14181F" : "transparent"}`, "&:hover": { color: "#14181F" } }}>
              {tr(label.replace("&", "and"))}
            </ButtonBase>
          ))}
        </Box>
        <Box sx={{ flex: 1 }} />
        {right}
        <ButtonBase onClick={() => setLang(isAr() ? "en" : "ar")} aria-label={tr("Language")}
          sx={{ px: 1.25, height: 32, borderRadius: 2, fontSize: 13, color: "#5D6573", "&:hover": { color: "#14181F", bgcolor: "#EEF0F3" } }}>
          {isAr() ? "English" : "العربية"}
        </ButtonBase>
        {me && <UserMenu user={me} mode={mode} go={go} onLogout={onLogout} />}
      </Toolbar>
    </AppBar>
  );
}
