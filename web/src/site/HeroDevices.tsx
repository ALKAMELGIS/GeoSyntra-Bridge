/**
 * Hero visual: a blueprint drawing itself across the page, a laptop showing the CAD drawing and a monitor
 * showing the GIS map. Entities leave the CAD screen, fly to the map and land as features; the layer panel
 * counts them in. Interactive: the whole composition follows the pointer (parallax), hovering a layer in the
 * panel highlights it on the map, clicking its box hides or shows it. Original artwork — no images.
 */
import { useEffect, useMemo, useRef, useState } from "react";

const LAYERS = [
  { id: "RD", en: "Roads", ar: "الطرق", color: "#E8C547", total: 46 },
  { id: "IR", en: "Irrigation", ar: "الري", color: "#4FC3D6", total: 128 },
  { id: "SW", en: "Storm Water", ar: "تصريف الأمطار", color: "#6C8EF0", total: 37 },
  { id: "SL", en: "Street Lighting", ar: "إنارة الشوارع", color: "#F29A4A", total: 64 },
  { id: "PC", en: "Plots", ar: "قطع الأراضي", color: "#C9CED6", total: 22 },
];

const CMD = "GSBEXPORT  Layers: 23  Entities: 1,284  →  IR_ADM_V10.gdb";
const rnd = (seed: number) => () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };

/** Orange architectural linework for the page background. */
function Blueprint() {
  const d = useMemo(() => {
    const p: string[] = [], dims: { x: number; y: number; t: string; r: number }[] = [];
    const wall = (x0: number, y0: number, x1: number, y1: number, t = 10) => { p.push(`M${x0} ${y0}H${x1}V${y1}H${x0}Z`, `M${x0 + t} ${y0 + t}H${x1 - t}V${y1 - t}H${x0 + t}Z`); };
    wall(980, -40, 1660, 520, 14); wall(1040, 30, 1300, 250); wall(1320, 30, 1600, 250); wall(1040, 280, 1600, 470);
    for (let i = 0; i < 9; i++) p.push(`M${1060 + i * 26} 300V450`);                                   // stair treads
    p.push("M1300 140a60 60 0 0 1 60 60", "M1320 250a50 50 0 0 0 -50 -50", "M1100 250a46 46 0 0 1 46 -46");
    wall(-80, 520, 520, 980, 12); wall(-40, 560, 260, 760); wall(280, 560, 480, 760);
    p.push("M260 640a70 70 0 0 1 70 70", "M120 760a60 60 0 0 0 60 60");
    for (let x = 560; x < 960; x += 40) p.push(`M${x} 900V${860 - ((x / 40) % 3) * 20}`);
    p.push("M0 160H420M0 200H420M420 160V200", "M60 120V240M140 120V240M220 120V240M300 120V240");      // grid & axis
    [60, 140, 220, 300].forEach((x) => p.push(`M${x - 14} 104a14 14 0 1 0 28 0a14 14 0 1 0 -28 0`));
    p.push("M980 560H1660", "M980 552V568M1300 552V568M1660 552V568");                                    // dimension lines
    dims.push({ x: 1140, y: 548, t: "3200", r: 0 }, { x: 1480, y: 548, t: "3600", r: 0 }, { x: 966, y: 200, t: "900", r: -90 }, { x: 966, y: 400, t: "1250", r: -90 });
    p.push("M940 -20V520", "M932 0H948M932 250H948M932 520H948", "M540 40L760 40L760 300", "M560 60L740 60L740 280L560 280Z");
    for (let i = 0; i < 6; i++) p.push(`M${560 + i * 30} 60V280`);
    dims.push({ x: 650, y: 28, t: "2020", r: 0 }, { x: 380, y: 512, t: "6000", r: 0 }, { x: 600, y: 846, t: "450", r: 0 });
    return { path: p, dims };
  }, []);
  return (
    <svg className="hd-bp" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
        {d.path.map((x, i) => <path key={i} d={x} pathLength={1} style={{ animationDelay: `${0.15 + (i % 14) * 0.08}s` }} />)}
      </g>
      <g className="hd-bp-t" fill="currentColor">{d.dims.map((x, i) => <text key={i} x={x.x} y={x.y} transform={`rotate(${x.r} ${x.x} ${x.y})`}>{x.t}</text>)}</g>
    </svg>
  );
}

/** Terrain for the map: dark imagery tones with faint contour rings, painted once on a canvas. */
function useTerrain(ref: React.RefObject<HTMLCanvasElement>) {
  useEffect(() => {
    const c = ref.current; if (!c) return;
    const w = (c.width = 640), h = (c.height = 400), g = c.getContext("2d")!, r = rnd(7);
    g.fillStyle = "#3a4236"; g.fillRect(0, 0, w, h);
    for (let i = 0; i < 260; i++) {
      const x = r() * w, y = r() * h, s = 10 + r() * 60;
      const gr = g.createRadialGradient(x, y, 0, x, y, s);
      const tone = ["#4b5543", "#2e352c", "#57604c", "#3f4a3e", "#615e4c"][Math.floor(r() * 5)];
      gr.addColorStop(0, tone + "cc"); gr.addColorStop(1, tone + "00"); g.fillStyle = gr; g.fillRect(x - s, y - s, s * 2, s * 2);
    }
    g.strokeStyle = "rgba(232,120,140,.32)"; g.lineWidth = 0.7;
    for (let k = 0; k < 7; k++) {
      const cx = r() * w, cy = r() * h;
      for (let j = 1; j < 9; j++) { g.beginPath(); g.ellipse(cx, cy, j * 16 + r() * 6, j * 11 + r() * 5, r() * 3, 0, 7); g.stroke(); }
    }
    g.fillStyle = "rgba(210,205,190,.10)";
    for (let i = 0; i < 40; i++) g.fillRect(380 + r() * 140, 90 + r() * 90, 6 + r() * 12, 6 + r() * 10);
  }, []);
}

type Feat = { layer: string; d: string; kind: "line" | "pt" | "poly" };
function buildFeatures(): Feat[] {
  const r = rnd(21), f: Feat[] = [];
  // runway-like crossing roads + taxiways
  f.push({ layer: "RD", kind: "line", d: "M40 230 L560 60" }, { layer: "RD", kind: "line", d: "M120 40 L520 330" });
  f.push({ layer: "RD", kind: "line", d: "M230 170 C280 140 320 150 360 120 S430 100 470 90" }, { layer: "RD", kind: "line", d: "M300 140 L330 210 L400 230" });
  for (let i = 0; i < 6; i++) { const x = 200 + i * 40; f.push({ layer: "PC", kind: "poly", d: `M${x} ${96 - i * 4}h28v20h-28z` }); }
  for (let i = 0; i < 5; i++) { const x = 240 + i * 36; f.push({ layer: "PC", kind: "poly", d: `M${x} ${196 + i * 6}h26v18h-26z` }); }
  f.push({ layer: "IR", kind: "line", d: "M60 300 C160 280 260 300 340 270 S480 230 600 250" }, { layer: "IR", kind: "line", d: "M340 270 L360 340" }, { layer: "IR", kind: "line", d: "M200 290 L190 360" });
  f.push({ layer: "SW", kind: "line", d: "M80 100 L240 160 L380 200 L590 300" }, { layer: "SW", kind: "line", d: "M240 160 L230 70" });
  for (let i = 0; i < 12; i++) { const t = i / 11; f.push({ layer: "SL", kind: "pt", d: `${(60 + 480 * t + r() * 8).toFixed(1)} ${(222 - 160 * t + r() * 8).toFixed(1)}` }); }
  for (let i = 0; i < 8; i++) f.push({ layer: "IR", kind: "pt", d: `${(80 + r() * 480).toFixed(1)} ${(270 + r() * 70).toFixed(1)}` });
  return f;
}

export default function HeroDevices({ lang }: { lang: "en" | "ar" }) {
  const box = useRef<HTMLDivElement>(null);
  const terrain = useRef<HTMLCanvasElement>(null);
  const wires = useRef<SVGSVGElement>(null);
  const feats = useMemo(buildFeatures, []);
  const [shown, setShown] = useState(0);                         // features landed on the map
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [hot, setHot] = useState<string | null>(null);
  const [scan, setScan] = useState(0);
  const [xy, setXY] = useState<[number, number]>([0.42, 0.55]);
  useTerrain(terrain);

  // the transfer loop: a packet leaves the CAD screen, lands on the map, the next feature appears
  useEffect(() => {
    const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (still) { setShown(feats.length); return; }
    let raf = 0, t0 = performance.now(), n = 0, pause = 0, visible = true;
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }); if (box.current) io.observe(box.current);
    const svg = wires.current!;
    const dots = Array.from(svg.querySelectorAll<SVGCircleElement>(".hd-pk"));
    const path = svg.querySelector<SVGPathElement>(".hd-route")!;
    const L = path.getTotalLength();
    const lanes = dots.map((_, i) => ({ start: -i * 0.33, color: LAYERS[i % LAYERS.length].color }));
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (!visible) { t0 = now; return; }
      const dt = (now - t0) / 1000; t0 = now;
      setScan((s) => (s + dt * 0.35) % 1);
      if (pause > 0) { pause -= dt; if (pause <= 0) { n = 0; setShown(0); } return; }
      lanes.forEach((ln, i) => {
        ln.start += dt * 0.9;
        const u = ln.start;
        const dot = dots[i];
        if (u < 0 || u > 1) { dot.style.opacity = "0"; }
        else {
          const p = path.getPointAtLength(u * L);
          dot.setAttribute("cx", String(p.x)); dot.setAttribute("cy", String(p.y));
          dot.style.opacity = String(Math.sin(u * Math.PI) * 1.2);
          dot.setAttribute("fill", ln.color);
        }
        if (u > 1) {
          ln.start -= 1.0; ln.color = LAYERS[(n + i) % LAYERS.length].color;
          n += 1; setShown(Math.min(feats.length, n));
          if (n >= feats.length) pause = 3.2;
        }
      });
    };
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); io.disconnect(); };
  }, [feats.length]);

  // parallax: the composition leans toward the pointer
  useEffect(() => {
    const hero = box.current?.closest(".lx-hero") as HTMLElement | null; if (!hero) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const move = (e: PointerEvent) => {
      const b = hero.getBoundingClientRect();
      hero.style.setProperty("--mx", (((e.clientX - b.left) / b.width) * 2 - 1).toFixed(3));
      hero.style.setProperty("--my", (((e.clientY - b.top) / b.height) * 2 - 1).toFixed(3));
    };
    const leave = () => { hero.style.setProperty("--mx", "0"); hero.style.setProperty("--my", "0"); };
    const scroll = () => hero.style.setProperty("--sy", String(Math.min(1, window.scrollY / Math.max(1, hero.offsetHeight))));
    hero.addEventListener("pointermove", move); hero.addEventListener("pointerleave", leave); window.addEventListener("scroll", scroll, { passive: true });
    return () => { hero.removeEventListener("pointermove", move); hero.removeEventListener("pointerleave", leave); window.removeEventListener("scroll", scroll); };
  }, []);

  const counts = LAYERS.map((l) => {
    const mine = feats.map((f, i) => [f, i] as const).filter(([f]) => f.layer === l.id);
    const landed = mine.filter(([, i]) => i < shown).length;
    return Math.round((landed / Math.max(1, mine.length)) * l.total);
  });
  const color = (id: string) => LAYERS.find((l) => l.id === id)!.color;
  const ar = lang === "ar";

  return (
    <div className="hd" ref={box} aria-hidden="true">
      <Blueprint />
      <div className="hd-rig">
        {/* monitor — a GIS desktop (light ribbon, Contents pane, map view, status bar) */}
        <div className="hd-mon">
          <div className="hd-scr gis">
            <div className="g-title"><i className="g-dots" /><span>IR_ADM_V10 — Map</span><i className="g-win" /></div>
            <div className="g-tabs">{["Project", "Map", "Insert", "Analysis", "View", "Edit", "Imagery", "Share"].map((x, i) => <span key={x} className={i === 1 ? "on" : i === 0 ? "proj" : ""}>{x}</span>)}</div>
            <div className="g-ribbon">
              {[["Explore", 2], ["Basemap", 1], ["Add Data", 2], ["Select", 3], ["Inquiry", 2], ["Labeling", 2]].map(([g, n]) => (
                <div key={g as string} className="g-grp"><div className="g-ic">{Array.from({ length: n as number }, (_, i) => <i key={i} className={`k${(g as string).length % 4}${i}`} />)}</div><small>{g}</small></div>
              ))}
            </div>
            <div className="g-body">
              <div className="g-pane" dir={ar ? "rtl" : "ltr"}>
                <div className="g-ph">{ar ? "المحتويات" : "Contents"}<span>×</span></div>
                <div className="g-search" />
                <div className="g-tree">
                  <div className="g-map">▾ <b>Map</b></div>
                  {LAYERS.map((l, k) => (
                    <div key={l.id} className={`g-row${hot === l.id ? " hot" : ""}${hidden[l.id] ? " off" : ""}`}
                      onPointerEnter={() => setHot(l.id)} onPointerLeave={() => setHot(null)}
                      onClick={() => setHidden((h) => ({ ...h, [l.id]: !h[l.id] }))}>
                      <span className={`g-cb${hidden[l.id] ? "" : " on"}`} />
                      <span className="g-sym" style={{ background: l.id === "SL" || l.id === "PC" ? "transparent" : l.color, borderColor: l.color, borderRadius: l.id === "SL" ? "50%" : 2 }} />
                      <span className="g-ln">{ar ? l.ar : l.en}</span><span className="g-ct">{counts[k]}</span>
                    </div>
                  ))}
                  <div className="g-row base"><span className="g-cb on" /><span className="g-sym img" /><span className="g-ln">World Imagery</span></div>
                </div>
              </div>
              <div className="g-view">
                <div className="g-mtab"><span>Map</span><span className="x">×</span></div>
                <div className="hd-map" onPointerMove={(e) => { const b = e.currentTarget.getBoundingClientRect(); setXY([(e.clientX - b.left) / b.width, (e.clientY - b.top) / b.height]); }}>
                  <canvas ref={terrain} />
                  <svg viewBox="0 0 640 400" preserveAspectRatio="xMidYMid slice">
                    {feats.map((f, i) => {
                      const vis = i < shown && !hidden[f.layer];
                      const dim = hot && hot !== f.layer;
                      const c = color(f.layer);
                      const cls = `hd-f${vis ? " in" : ""}${dim ? " dim" : ""}${hot === f.layer ? " hot" : ""}`;
                      if (f.kind === "pt") { const [x, y] = f.d.split(" ").map(Number); return <circle key={i} className={cls} cx={x} cy={y} r={3.2} fill={c} stroke="#111" strokeWidth={0.6} />; }
                      return <path key={i} className={cls} d={f.d} pathLength={1} fill={f.kind === "poly" ? c + "33" : "none"} stroke={c}
                        strokeWidth={f.layer === "RD" && i < 2 ? 9 : f.kind === "poly" ? 1.4 : 2.4} strokeLinecap="round" strokeLinejoin="round" />;
                    })}
                  </svg>
                  <div className="g-nav"><i /><i /><i /></div>
                </div>
                <div className="g-status"><span>1:5,000</span><span>{(54.3602 + xy[0] * 0.0214).toFixed(4)}°E {(24.4687 - xy[1] * 0.0133).toFixed(4)}°N</span><span className="g-sel">{shown} features</span></div>
              </div>
            </div>
          </div>
          <div className="hd-neck" /><div className="hd-foot" />
        </div>
        {/* laptop — a CAD desktop (dark ribbon, model space, ViewCube, UCS, command line) */}
        <div className="hd-lap">
          <div className="hd-lscr cad">
            <div className="c-top"><i className="c-a" /><span>IR_As-Built.dwg</span></div>
            <div className="c-tabs">{["Home", "Insert", "Annotate", "Parametric", "View", "Manage", "Output"].map((x, i) => <span key={x} className={i === 0 ? "on" : ""}>{x}</span>)}</div>
            <div className="c-ribbon">
              <div className="c-grp"><div className="c-big">⟋</div><div className="c-big">⌒</div><div className="c-big">○</div><small>Draw</small></div>
              <div className="c-grp"><div className="c-sm">{Array.from({ length: 6 }, (_, i) => <i key={i} />)}</div><small>Modify</small></div>
              <div className="c-grp wide"><div className="c-layer"><i style={{ background: "#e8504c" }} />IR-PIPE-MAIN ▾</div><small>Layers</small></div>
            </div>
            <div className="c-space">
              <svg viewBox="0 0 400 200" preserveAspectRatio="xMidYMid slice">
                <defs><pattern id="cg" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" stroke="#2c333c" strokeWidth=".6" /></pattern></defs>
                <rect width="400" height="200" fill="#212830" /><rect width="400" height="200" fill="url(#cg)" />
                <g fill="none" strokeWidth="0.9">
                  <path d="M-10 50 L410 -20 M-10 120 L410 50 M60 210 L170 -10" stroke="#d8dde4" />
                  {Array.from({ length: 30 }, (_, i) => {
                    const r = (i * 37) % 11, x = 24 + (i % 10) * 37 + r, y = 46 + Math.floor(i / 10) * 44 - (i % 10) * 7;
                    return <g key={i} transform={`rotate(-11 ${x} ${y})`}>
                      <rect x={x} y={y} width={27} height={20} stroke={i % 5 === 0 ? "#e8c547" : "#d8dde4"} />
                      <rect x={x + 4} y={y + 4} width={19} height={12} stroke="#4fc3d6" opacity={0.75} />
                      {i % 3 === 0 && <path d={`M${x + 4} ${y + 16}l19 -12`} stroke="#4fc3d6" opacity={0.6} />}
                    </g>;
                  })}
                  <path d="M10 190 L110 165 L220 178 L390 140" stroke="#e8504c" strokeWidth="1.6" />
                </g>
                <rect className="hd-scan" x="0" y={scan * 200 - 12} width="400" height="12" />
                <g transform="translate(14 186)" strokeWidth="1.4"><path d="M0 0H18" stroke="#e8504c" /><path d="M0 0V-18" stroke="#5fc46a" /><text x="20" y="3" fontSize="6" fill="#e8504c">X</text><text x="-3" y="-21" fontSize="6" fill="#5fc46a">Y</text></g>
              </svg>
              <div className="c-cube"><span>TOP</span></div>
            </div>
            <div className="c-cmd"><span>Command:</span> {CMD.slice(0, Math.floor(scan * (CMD.length + 12))).slice(0, CMD.length)}<i className="c-caret" /></div>
            <div className="c-status"><span className="on">Model</span><span>Layout1</span><span className="r">MODEL ⊞ ⊥ ∠ ⊡</span></div>
          </div>
          <div className="hd-base" />
        </div>
        {/* the route packets travel on, from the CAD screen to the map */}
        <svg className="hd-wires" ref={wires} viewBox="0 0 1000 700" preserveAspectRatio="none">
          <path className="hd-route" d="M800 520 C760 380 640 420 560 300" fill="none" />
          {Array.from({ length: 3 }, (_, i) => <circle key={i} className="hd-pk" r="5" cx="-20" cy="-20" />)}
        </svg>
      </div>
    </div>
  );
}
