import { useCallback, useEffect, useState } from "react";
import { Box, CircularProgress } from "@mui/material";
import App from "./App";
import { auth } from "./api";
import type { AuthConfig, AuthUser } from "./types";
import { Landing } from "./site/Landing";
import { Auth } from "./site/Auth";
import { Account } from "./pages/Account";
import { Owner } from "./pages/Owner";
import { Workspace } from "./pages/Workspace";

/** Hash routes: #/ website · #/login #/signup #/forgot #/reset · #/app (and #job=…) converter · #/account · #/owner */
function parse(): { path: string; params: URLSearchParams } {
  const h = window.location.hash.replace(/^#/, "");
  if (/^job=/.test(h)) return { path: "/app/job", params: new URLSearchParams() };
  const [p, q] = h.split("?");
  return { path: p || "/", params: new URLSearchParams(q || "") };
}

export default function Root() {
  const [route, setRoute] = useState(parse());
  const [cfg, setCfg] = useState<AuthConfig | null>(null);
  const [me, setMe] = useState<AuthUser | null>(null);
  const [mode, setMode] = useState<string>("");
  const [ready, setReady] = useState(false);

  const refreshMe = useCallback(() => auth.me().then((r) => { setMe(r.user); setMode(r.mode); }).catch(() => setMe(null)), []);
  useEffect(() => {
    Promise.all([auth.config().then(setCfg).catch(() => null), refreshMe()]).finally(() => setReady(true));
    const onHash = () => setRoute(parse());
    const onUnauth = () => { setMe(null); window.location.hash = "/login"; };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("gsb-unauthorized", onUnauth);
    return () => { window.removeEventListener("hashchange", onHash); window.removeEventListener("gsb-unauthorized", onUnauth); };
  }, []);
  useEffect(() => { window.scrollTo(0, 0); if (ready) refreshMe(); }, [route.path]);   // balance and plan stay current between pages

  const go = useCallback((r: string) => { window.location.hash = r.startsWith("/") ? r : `/${r}`; }, []);
  const logout = useCallback(async () => { try { await auth.logout(); } finally { setMe(null); go("/"); } }, [go]);

  if (!ready) return <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center", bgcolor: "#1B1E23" }}><CircularProgress sx={{ color: "#d3d7de" }} /></Box>;

  const accounts = mode === "accounts";
  const { path, params } = route;
  const signedIn = !!me;

  // without accounts (local / platform-integrated installs) the converter is the home page
  if (!accounts && (path === "/" || path === "")) {
    return <Workspace me={me} mode={mode} go={go} onLogout={logout} />;
  }
  if (path === "/home" || path === "/") return <Landing cfg={cfg} user={me} go={go} />;
  if (["/login", "/signup", "/forgot", "/reset"].includes(path)) {
    if (signedIn && path !== "/reset") { setTimeout(() => go(params.get("plan") ? `/account?plan=${params.get("plan")}` : "/app")); return null; }
    return <Auth mode={path.slice(1) as any} params={params} cfg={cfg} go={go} onSignedIn={(u) => { setMe(u); setMode("accounts"); }} />;
  }
  if (accounts && !signedIn) { setTimeout(() => go("/login")); return null; }
  if (path === "/account" && me) return <Account me={me} mode={mode} go={go} onLogout={logout} refreshMe={refreshMe} params={params} />;
  if (path === "/owner" && me && (me.role === "owner" || me.role === "admin")) return <Owner me={me} mode={mode} go={go} onLogout={logout} />;
  if (path === "/app" || path === "/app/") return <Workspace me={me} mode={mode} go={go} onLogout={logout} />;
  return <App me={me} mode={mode} go={go} onLogout={logout} refreshMe={refreshMe} />;
}
