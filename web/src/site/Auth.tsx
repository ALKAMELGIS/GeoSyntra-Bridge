import { FormEvent, useEffect, useState } from "react";
import "./site.css";
import "./auth.css";
import { AuthLines, AuthPipeline } from "./AuthPipeline";
import { auth } from "../api";
import { LogoMark } from "../components/Logo";
import type { AuthConfig, AuthUser } from "../types";
import { dict } from "./i18n";
import { useLang } from "./Landing";

type Mode = "login" | "signup" | "forgot" | "reset";

interface Props {
  mode: Mode; params: URLSearchParams; cfg: AuthConfig | null;
  go: (r: string) => void; onSignedIn: (u: AuthUser) => void;
}

const ICON: Record<string, JSX.Element> = {
  google: (<svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true"><path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/><path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z"/><path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z"/></svg>),
  github: (<svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true"><path fill="currentColor" d="M8 0C3.6 0 0 3.6 0 8c0 3.5 2.3 6.5 5.5 7.6.4.1.5-.2.5-.4v-1.5c-2.2.5-2.7-1-2.7-1-.4-.9-.9-1.2-.9-1.2-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.3 1.9.9 2.3.7.1-.5.3-.9.5-1.1-1.8-.2-3.6-.9-3.6-4 0-.9.3-1.6.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8.6-.2 1.3-.3 2-.3s1.4.1 2 .3c1.5-1 2.2-.8 2.2-.8.4 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 3.1-1.9 3.7-3.7 3.9.3.3.6.8.6 1.5v2.2c0 .2.1.5.6.4C13.7 14.5 16 11.5 16 8c0-4.4-3.6-8-8-8z"/></svg>),
};

export function Auth({ mode, params, cfg, go, onSignedIn }: Props) {
  const [lang, setLang] = useLang();
  const t = dict(lang);
  const [f, setF] = useState({ email: "", password: "", name: "", company: "" });
  const [err, setErr] = useState(params.get("error") || "");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { setErr(params.get("error") || ""); setOk(""); }, [mode]);
  const next = params.get("plan") ? `/account?plan=${params.get("plan")}` : "/app";
  const providers = Object.entries(cfg?.providers || {});
  const closed = mode === "signup" && cfg && !cfg.signup_open;

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(""); setOk(""); setBusy(true);
    try {
      if (mode === "login") { const r = await auth.login(f.email, f.password); onSignedIn(r.user); go(next); }
      else if (mode === "signup") { const r = await auth.signup(f); onSignedIn(r.user); go(next); }
      else if (mode === "forgot") { const r = await auth.forgot(f.email); setOk(r.message); }
      else { const r = await auth.reset(params.get("token") || "", f.password); onSignedIn(r.user); go("/app"); }
    } catch (x: any) { setErr(x.message); }
    finally { setBusy(false); }
  };
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const title = { login: t.a_signin_t, signup: t.a_signup_t, forgot: t.a_forgot_t, reset: t.a_reset_t }[mode];

  const ar = lang === "ar";
  const theme = (() => { try { const v = localStorage.getItem("gsb_theme"); if (v === "light" || v === "dark") return v; } catch { /* */ } return matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"; })();
  const sub = mode === "login" ? t.a_sub_login : mode === "signup" ? t.a_sub_signup : "";
  const show = (mode === "login" || mode === "signup") && !closed;

  return (
    <div className="ax" data-theme={theme} dir={ar ? "rtl" : "ltr"} lang={lang}>
      <aside className="ax-side">
        <a className="ax-brand" href="#/" onClick={(e) => { e.preventDefault(); go("/"); }}>
          <LogoMark size={28} />GeoSyntra<sup>Bridge</sup>
        </a>
        <div className="ax-stage"><AuthPipeline ar={ar} /></div>
        <AuthLines />
      </aside>
      <main className="ax-main">
        <div className="ax-top">
          <a href="#/" onClick={(e) => { e.preventDefault(); go("/"); }} className="ax-home">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"><path d={ar ? "M9 6l6 6-6 6" : "M15 6l-6 6 6 6"} /></svg>{t.a_home}
          </a>
          <button className="ax-lang" onClick={() => setLang(ar ? "en" : "ar")} lang={ar ? "en" : "ar"}>{t.lang}</button>
        </div>
        <form className="ax-form" onSubmit={submit} noValidate>
          <h1>{title}</h1>
          {sub && <p className="ax-sub">{sub}</p>}
          {mode === "signup" && cfg && cfg.trial_credits > 0 && !closed && <div className="ax-note">{t.a_trial(cfg.trial_credits)}</div>}
          {closed && <div className="ax-err">{t.a_closed}</div>}
          {err && <div className="ax-err" role="alert">{err}</div>}
          {ok && <div className="ax-ok" role="status">{ok}</div>}
          {show && providers.length > 0 && (
            <>
              {providers.map(([id, label]) => <a key={id} className="ax-btn" href={auth.oauthUrl(id)}>{ICON[id]}{t.a_with(label)}</a>)}
              <div className="ax-or"><span>{t.a_or}</span></div>
            </>
          )}
          {mode === "signup" && !closed && <label className="ax-f"><span>{t.a_name}</span><input autoComplete="name" value={f.name} onChange={set("name")} required /></label>}
          {mode !== "reset" && !closed && (
            <label className="ax-f"><span>{t.a_email}</span>
              <div className="ax-in"><i aria-hidden="true">@</i><input type="email" autoComplete="email" placeholder="you@company.ae" value={f.email} onChange={set("email")} required dir="ltr" /></div>
            </label>
          )}
          {mode === "signup" && !closed && <label className="ax-f"><span>{t.a_company}</span><input autoComplete="organization" value={f.company} onChange={set("company")} /></label>}
          {(mode === "login" || mode === "signup" || mode === "reset") && !closed && (
            <label className="ax-f">
              <span>{mode === "reset" ? t.a_new_pw : t.a_password}{mode === "login" && <a href="#/forgot" className="ax-forgot">{t.a_forgot}</a>}</span>
              <input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={f.password} onChange={set("password")} required dir="ltr" />
              {mode !== "login" && <small>{t.a_pw_hint}</small>}
            </label>
          )}
          {!closed && <button className="ax-btn primary" type="submit" disabled={busy}>{busy ? "…" : { login: t.a_signin, signup: t.a_signup, forgot: t.a_send, reset: t.a_save_pw }[mode]}</button>}
          <div className="ax-alt">
            {mode === "login" && (!cfg || cfg.signup_open) && <>{t.a_no_acc} <a href="#/signup">{t.start}</a></>}
            {mode === "signup" && <>{t.a_have_acc} <a href="#/login">{t.signin}</a></>}
            {(mode === "forgot" || mode === "reset") && <a href="#/login">{t.a_back}</a>}
          </div>
          {mode === "signup" && !closed && <p className="ax-terms">{t.a_terms}</p>}
        </form>
      </main>
    </div>
  );
}
