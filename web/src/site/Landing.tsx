import { useEffect, useState } from "react";
import "./site.css";
import "./landing.css";
import { lazy, Suspense } from "react";
import type { ViewMode } from "./UtilityScene";
const UtilityScene = lazy(() => import("./UtilityScene"));
const HeroMachine = lazy(() => import("./HeroMachine"));
import HeroSeam from "./HeroSeam";
import { LogoMark } from "../components/Logo";
import { billing } from "../api";
import type { AuthConfig, AuthUser, Plan } from "../types";
import { dict, getLang, Lang, saveLang } from "./i18n";

export function money(minor: number, cur: string, lang: Lang = "en") {
  try {
    return new Intl.NumberFormat(lang === "ar" ? "ar-AE" : "en-AE", { style: "currency", currency: cur.toUpperCase(), maximumFractionDigits: 0 })
      .format(minor / 100);
  } catch { return `${(minor / 100).toFixed(0)} ${cur.toUpperCase()}`; }
}

interface Props { cfg: AuthConfig | null; user: AuthUser | null; go: (route: string) => void }

export function useLang(): [Lang, (l: Lang) => void] {
  const [lang, setLang] = useState<Lang>(getLang());
  return [lang, (l: Lang) => { saveLang(l); setLang(l); }];
}

export function Nav({ lang, setLang, user, go, minimal }: { lang: Lang; setLang: (l: Lang) => void; user: AuthUser | null; go: (r: string) => void; minimal?: boolean }) {
  const t = dict(lang);
  return (
    <nav className="gs-nav" aria-label="Main">
      <div className="gs-wrap">
        <a className="gs-brand" href="#/" onClick={(e) => { e.preventDefault(); go("/"); }}>
          <img src="./brand-mark.png" alt="" /><span>GeoSyntra Bridge</span>
        </a>
        <div className="gs-links">
          {!minimal && <>
            <a href="#how" onClick={(e) => { e.preventDefault(); document.getElementById("how")?.scrollIntoView({ behavior: "smooth" }); }}>{t.nav_how}</a>
            <a href="#standards" onClick={(e) => { e.preventDefault(); document.getElementById("standards")?.scrollIntoView({ behavior: "smooth" }); }}>{t.nav_std}</a>
            <a href="#pricing" onClick={(e) => { e.preventDefault(); document.getElementById("pricing")?.scrollIntoView({ behavior: "smooth" }); }}>{t.nav_price}</a>
            <a href="#faq" onClick={(e) => { e.preventDefault(); document.getElementById("faq")?.scrollIntoView({ behavior: "smooth" }); }}>{t.nav_faq}</a>
          </>}
        </div>
        <div className="gs-actions">
          <button className="gs-lang" onClick={() => setLang(lang === "en" ? "ar" : "en")} lang={lang === "en" ? "ar" : "en"}>{t.lang}</button>
          {user ? (
            <button className="gs-btn primary small" onClick={() => go("/app")}>{t.open_app}</button>
          ) : (
            <>
              <button className="gs-btn ghost small" onClick={() => go("/login")}>{t.signin}</button>
              <button className="gs-btn primary small" onClick={() => go("/signup")}>{t.start}</button>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}


const TEMPLATES: { id: string; en: string; ar: string; motif: string; color: string; note: [string, string] }[] = [
  { id: "IR", en: "Irrigation", ar: "الري", motif: "pipes", color: "#2F7F8C", note: ["Mains · valves · sprinklers", "خطوط · محابس · رشاشات"] },
  { id: "RD", en: "Roads", ar: "الطرق", motif: "roads", color: "#3D3E42", note: ["Carriageway · kerbs · markings", "الطريق · الأرصفة الحجرية · الخطوط"] },
  { id: "SL", en: "Street Lighting", ar: "إنارة الشوارع", motif: "lights", color: "#B07A2A", note: ["Poles · lanterns · cables", "أعمدة · كشافات · كابلات"] },
  { id: "SW", en: "Storm Water", ar: "تصريف الأمطار", motif: "drain", color: "#4A5F86", note: ["Pipes · manholes · gullies", "أنابيب · غرف تفتيش · مصائد"] },
  { id: "HL", en: "Hard Landscaping", ar: "التنسيق الصلب", motif: "paving", color: "#B3A795", note: ["Paving · plazas · furniture", "بلاط · ساحات · أثاث"] },
  { id: "SF", en: "Soft Landscaping", ar: "التنسيق الزراعي", motif: "trees", color: "#76825D", note: ["Trees · shrubs · lawns", "أشجار · شجيرات · مسطحات"] },
  { id: "IT", en: "ITC", ar: "الاتصالات ITC", motif: "ducts", color: "#8A6D4F", note: ["Duct banks · handholes", "مجاري كابلات · غرف سحب"] },
];

function getTheme(): "light" | "dark" {
  try { const v = localStorage.getItem("gsb_theme"); if (v === "light" || v === "dark") return v; } catch { /* */ }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function Landing({ cfg, user, go }: Props) {
  const [lang, setLang] = useLang();
  const t = dict(lang);
  const ar = lang === "ar";
  const [theme, setTheme] = useState(getTheme);
  const dark = theme === "dark";
  const [plans, setPlans] = useState<Plan[]>([]);
  const [view, setView] = useState<ViewMode>("3d");
  const [hot, setHot] = useState<string | null>(null);

  useEffect(() => { billing.plans().then(setPlans).catch(() => setPlans([])); }, []);
  useEffect(() => { document.documentElement.lang = lang; }, [lang]);
  useEffect(() => { try { localStorage.setItem("gsb_theme", theme); } catch { /* */ } }, [theme]);
  // reveal on scroll
  useEffect(() => {
    const io = new IntersectionObserver((es) => es.forEach((e) => e.isIntersecting && e.target.classList.add("in")), { threshold: 0.15 });
    document.querySelectorAll(".lx .rv").forEach((n) => io.observe(n));
    return () => io.disconnect();
  }, [plans.length, lang]);

  const trial = cfg?.trial_credits ?? 50;
  const main = plans.filter((p) => p.kind !== "pack");
  const packs = plans.filter((p) => p.kind === "pack");
  const choose = (p: Plan) => {
    if (p.kind === "enterprise") { window.location.href = `mailto:${cfg?.support_email || ""}?subject=GeoSyntra%20Bridge%20Enterprise`; return; }
    go(user ? `/account?plan=${p.id}` : `/signup?plan=${p.id}`);
  };
  const to = (id: string) => (e: React.MouseEvent) => { e.preventDefault(); document.getElementById(id)?.scrollIntoView({ behavior: "smooth" }); };
  const start = () => go(user ? "/app" : "/signup");
  const Arrow = () => <span className="ar" aria-hidden="true">{ar ? "←" : "→"}</span>;

  return (
    <div className="lx" data-theme={theme} dir={ar ? "rtl" : "ltr"} lang={lang}>
      <nav className="lx-nav" aria-label="Main">
        <a className="lx-brand" href="#/" onClick={(e) => { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
          <LogoMark size={28} />GeoSyntra<sup>Bridge</sup>
        </a>
        <div className="lx-links">
          <a href="#platform" onClick={to("platform")}>{t.l_bridge_e}</a>
          <a href="#templates" onClick={to("templates")}>{t.l_tpl_e}</a>
          <a href="#pricing" onClick={to("pricing")}>{t.nav_price}</a>
        </div>
        <div className="lx-act">
          <button className="lx-ico" aria-label={t.l_theme} onClick={() => setTheme(dark ? "light" : "dark")}>
            {dark ? <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="4.5" /><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.9 4.9l1.8 1.8M17.3 17.3l1.8 1.8M4.9 19.1l1.8-1.8M17.3 6.7l1.8-1.8" /></svg>
              : <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" /></svg>}
          </button>
          <button className="lx-lang" onClick={() => setLang(ar ? "en" : "ar")} lang={ar ? "en" : "ar"}>{t.lang}</button>
          {user ? <button className="lx-pill" onClick={() => go("/app")}>{t.open_app}</button>
            : <><button className="lx-txt" onClick={() => go("/login")}>{t.signin}</button><button className="lx-pill" onClick={() => go("/signup")}>{t.start}</button></>}
        </div>
      </nav>

      <header className="lx-hero2" id="home">
        <div className="lx-hero-txt">
          <p className="lx-eye">{t.l_eyebrow}</p>
          <h1>{t.l_h1a}<br /><em>{t.l_h1b}</em></h1>
          <p className="lx-lead">{t.l_lead}</p>
          <div className="lx-ctas">
            <button className="lx-pill lg" onClick={start}>{user ? t.open_app : t.cta_trial(trial)} <Arrow /></button>
            <a className="lx-link" href="#platform" onClick={to("platform")}>{t.l_more} <Arrow /></a>
          </div>
        </div>
        <HeroSeam lang={lang} labels={t.l_seam} layers={TEMPLATES.map((x) => ({ id: x.id, name: ar ? x.ar : x.en, color: x.color }))} />
      </header>

      <section className="lx-sec lx-bridge lx-page" id="platform">
        <p className="lx-eye rv">{t.l_bridge_e}</p>
        <h2 className="lx-big rv">{t.l_bridge_a} <em>{t.l_bridge_b}</em></h2>
        <div className="lx-bridge-viz rv"><Suspense fallback={null}><HeroMachine labels={t.l_hm} centered /></Suspense></div>
        <ol className="lx-bridge-p lx-steps4 rv">{t.l_bridge_p.map((x, i) => { const [a, ...b] = x.split(" — "); return <li key={x}><i>{i + 1}</i><span><b>{a}</b> — {b.join(" — ")}</span></li>; })}</ol>
        <div className="lx-stats lx-stats-in">{t.l_stats.map(([n, l]) => <div key={l} className="rv"><b>{n}</b><span>{l}</span></div>)}</div>
      </section>

      <section className="lx-sec lx-city lx-page" id="templates">
        <div className="lx-head rv">
          <div><p className="lx-eye">{t.l_tpl_e}</p><h2>{t.l_tpl_a} <em>{t.l_tpl_b}</em></h2></div>
          <div className="lx-seg" role="tablist" aria-label="View">
            {(["3d", "layers", "plan"] as ViewMode[]).map((m) => (
              <button key={m} role="tab" aria-selected={view === m} className={view === m ? "on" : ""} onClick={() => setView(m)}>
                {m === "3d" ? "3D" : m === "plan" ? "2D" : (ar ? "الطبقات" : "Layers")}</button>))}
          </div>
        </div>
        <div className="lx-stage rv">
          <Suspense fallback={<div className="us-host" />}>
            <UtilityScene mode={view} dark={dark} hover={hot} onHover={setHot}
              layers={TEMPLATES.map((x) => ({ id: x.id, name: ar ? x.ar : x.en, note: x.note[ar ? 1 : 0], color: x.color }))} />
          </Suspense>
          <ul className="lx-legend">
            {TEMPLATES.map((x) => (
              <li key={x.id} className={hot === x.id ? "on" : hot ? "dim" : ""} onPointerEnter={() => setHot(x.id)} onPointerLeave={() => setHot(null)}
                onFocus={() => setHot(x.id)} onBlur={() => setHot(null)} tabIndex={0}>
                <i style={{ background: x.color }} /><b>{ar ? x.ar : x.en}</b><span>{x.note[ar ? 1 : 0]}</span>
              </li>))}
          </ul>
        </div>
      </section>

      <section className="lx-sec lx-page" id="pricing">
        <div className="lx-head rv"><div><p className="lx-eye">{t.nav_price}</p><h2>{t.price_t}</h2></div>
          <p className="lx-sub">{t.price_lead(cfg?.pricing.generate_base_credits ?? 10, cfg?.pricing.generate_credits_per_1000_features ?? 5)}</p></div>
        {main.length > 0 && <div className="lx-plans">
          {main.map((p) => (
            <article key={p.id} className={`lx-plan rv${p.highlight ? " hl" : ""}`}>
              <header><h3>{p.name}</h3>{p.highlight && <span>{t.popular}</span>}</header>
              <div className="amt">{p.kind === "enterprise" ? <em>{t.contact}</em> : <>{money(p.price_amount, p.currency, lang)}<small>{t.per_month}</small></>}</div>
              <ul>{p.kind === "subscription" && <li>{t.credits_month(p.credits)}</li>}
                {p.features.filter((_f, i) => !(p.kind === "subscription" && i === 0)).map((f) => <li key={f}>{f}</li>)}</ul>
              <button className={p.highlight ? "lx-pill" : "lx-out"} onClick={() => choose(p)}>{p.kind === "enterprise" ? t.contact : `${t.choose} ${p.name}`}</button>
            </article>))}
        </div>}
        {packs.length > 0 && <div className="lx-packs rv"><span>{t.packs_t} · {t.packs_lead}</span>
          {packs.map((p) => <button key={p.id} className="lx-out" onClick={() => choose(p)}>{p.name} · {money(p.price_amount, p.currency, lang)}</button>)}</div>}
        <div className="lx-faq2">
          <h3 className="rv">{t.faq_t}</h3>
          <div className="lx-faq">{t.faq.map(([q, a]) => <details key={q} className="rv"><summary>{q}</summary><p>{a}</p></details>)}</div>
        </div>
      </section>


      <footer className="lx-foot">
        <span>GeoSyntra Bridge — {t.foot}</span>
        {cfg?.support_email && <a href={`mailto:${cfg.support_email}`}>{cfg.support_email}</a>}
        <span>© {new Date().getFullYear()} {cfg?.company_name || "GeoSyntra"}. {t.rights}</span>
      </footer>
    </div>
  );
}
