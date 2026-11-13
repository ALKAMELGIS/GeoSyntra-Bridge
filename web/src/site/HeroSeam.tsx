/**
 * Hero: one street, two worlds. The same plan drawn as the CAD sheet (left) and as the ADM geodatabase it becomes
 * (right); drag the seam to move between them. Hovering a template highlights its feature class.
 */
import { useEffect, useRef, useState } from "react";
import type { Lang } from "./i18n";

export interface SeamLayer { id: string; name: string; color: string }

// --- one plan, shared by both sides (viewBox 1000 x 620) -----------------------------------------
const ROAD = { y0: 288, y1: 372 };
const POLES = [90, 250, 410, 570, 730, 890];
const MH = [170, 410, 650, 890];
const TREES = [60, 160, 260, 360, 460, 560, 660, 760, 860, 960];
const IR = "M -20 468 C 180 468 260 452 420 456 S 700 476 1020 462";
const IR_LAT = [140, 340, 540, 740, 940].map((x) => `M ${x} 462 L ${x} 506`);
const SW = `M -20 330 L 1020 330`;
const ITC = "M -20 244 L 1020 244";
const BLDG = [
  [40, 70, 230, 150], [320, 52, 210, 168], [600, 76, 170, 146], [820, 60, 160, 160],
];
const PLAZA = "M 0 392 L 1000 392 L 1000 430 L 0 430 Z";
const WALK = "M 0 250 L 1000 250 L 1000 288 L 0 288 Z";

const CAD = { bg: "#17191d", grid: "rgba(255,255,255,.045)", road: "#e8e8e8", lane: "#e8d44a", ir: "#3fd3e0", sw: "#6f8dff",
  sl: "#ffae3d", sf: "#46c25a", it: "#e070e0", hl: "#b9b2a4", bldg: "#9aa0a8" };

export default function HeroSeam({ lang, layers, labels }: { lang: Lang; layers: SeamLayer[]; labels: { cad: string; gis: string; drag: string } }) {
  const [x, setX] = useState(0.92);
  const [hot, setHot] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef(false);
  const col = (id: string) => layers.find((l) => l.id === id)?.color || "#888";
  const dim = (id: string) => (hot && hot !== id ? 0.18 : 1);

  useEffect(() => {          // one sweep on arrival shows that the picture has two sides
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) { setX(0.5); return; }
    let raf = 0; const t0 = performance.now();
    const step = (t: number) => {
      const k = Math.min(1, (t - t0 - 500) / 1400);
      if (k > 0) setX(0.92 - 0.42 * (1 - Math.pow(1 - k, 3)));
      if (k < 1 && !drag.current) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, []);

  const move = (cx: number) => {
    const r = box.current?.getBoundingClientRect(); if (!r) return;
    setX(Math.max(0.03, Math.min(0.97, (cx - r.left) / r.width)));
  };

  const gis = (
    <svg viewBox="0 0 1000 620" preserveAspectRatio="xMidYMid slice" className="hs-svg">
      <rect width="1000" height="620" fill="#e6dfd0" />
      <g opacity={dim("HL")}>
        <path d={WALK} fill={col("HL")} opacity=".55" /><path d={PLAZA} fill={col("HL")} opacity=".55" />
        {BLDG.map(([bx, by, w, h], i) => <rect key={i} x={bx} y={by} width={w} height={h} rx="3" fill="#d6cdbc" stroke="#b5aa95" />)}
      </g>
      <g opacity={dim("RD")}>
        <rect x="0" y={ROAD.y0} width="1000" height={ROAD.y1 - ROAD.y0} fill={col("RD")} />
        <path d={`M0 ${(ROAD.y0 + ROAD.y1) / 2} H1000`} stroke="#f3f1ec" strokeWidth="2" strokeDasharray="22 18" />
        <path d={`M0 ${ROAD.y0} H1000 M0 ${ROAD.y1} H1000`} stroke="#c9c2b4" strokeWidth="3" />
      </g>
      <g opacity={dim("IT")}><path d={ITC} stroke={col("IT")} strokeWidth="3" fill="none" strokeDasharray="10 6" /></g>
      <g opacity={dim("SW")}>
        <path d={SW} stroke={col("SW")} strokeWidth="4" fill="none" opacity=".9" />
        {MH.map((m) => <rect key={m} x={m - 8} y="322" width="16" height="16" rx="2" fill="#fff" stroke={col("SW")} strokeWidth="3" />)}
      </g>
      <g opacity={dim("SF")}>
        {TREES.map((t, i) => <g key={t}><circle cx={t} cy={540 + (i % 2) * 22} r="26" fill={col("SF")} opacity=".85" />
          <circle cx={t} cy={540 + (i % 2) * 22} r="26" fill="none" stroke="#56603f" strokeWidth="2" /></g>)}
      </g>
      <g opacity={dim("IR")}>
        <path d={IR} stroke={col("IR")} strokeWidth="5" fill="none" />
        {IR_LAT.map((d) => <path key={d} d={d} stroke={col("IR")} strokeWidth="2.5" />)}
      </g>
      <g opacity={dim("SL")}>
        {POLES.map((p, i) => <g key={p}><circle cx={p} cy={i % 2 ? 380 : 280} r="11" fill={col("SL")} opacity=".25" />
          <circle cx={p} cy={i % 2 ? 380 : 280} r="5.5" fill={col("SL")} stroke="#fff" strokeWidth="2" /></g>)}
      </g>
    </svg>
  );

  const cad = (
    <svg viewBox="0 0 1000 620" preserveAspectRatio="xMidYMid slice" className="hs-svg">
      <defs><pattern id="hs-g" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke={CAD.grid} /></pattern></defs>
      <rect width="1000" height="620" fill={CAD.bg} /><rect width="1000" height="620" fill="url(#hs-g)" />
      <g fill="none" strokeWidth="1.3">
        {BLDG.map(([bx, by, w, h], i) => <rect key={i} x={bx} y={by} width={w} height={h} stroke={CAD.bldg} />)}
        <path d={`M0 250 H1000 M0 430 H1000`} stroke={CAD.hl} />
        <path d={`M0 ${ROAD.y0} H1000 M0 ${ROAD.y1} H1000`} stroke={CAD.road} />
        <path d={`M0 ${(ROAD.y0 + ROAD.y1) / 2} H1000`} stroke={CAD.lane} strokeDasharray="22 18" />
        <path d={ITC} stroke={CAD.it} strokeDasharray="10 6" />
        <path d={SW} stroke={CAD.sw} />
        {MH.map((m) => <g key={m} stroke={CAD.sw}><rect x={m - 8} y="322" width="16" height="16" /><path d={`M${m - 8} 322 l16 16 m0 -16 l-16 16`} /></g>)}
        <path d={IR} stroke={CAD.ir} />
        {IR_LAT.map((d) => <path key={d} d={d} stroke={CAD.ir} />)}
        {TREES.map((t, i) => <g key={t} stroke={CAD.sf}><circle cx={t} cy={540 + (i % 2) * 22} r="26" />
          <path d={`M${t - 26} ${540 + (i % 2) * 22} h52 M${t} ${514 + (i % 2) * 22} v52`} /></g>)}
        {POLES.map((p, i) => <g key={p} stroke={CAD.sl}><circle cx={p} cy={i % 2 ? 380 : 280} r="6" /><path d={`M${p - 10} ${i % 2 ? 380 : 280} h20`} /></g>)}
      </g>
      <g className="hs-cadtxt">
        <text x="46" y="240" fill={CAD.it}>ITC-DUCT</text><text x="236" y="322" fill={CAD.sw}>SW-PIPE-600</text>
        <text x="470" y="448" fill={CAD.ir}>IR-MAIN-110</text><text x="676" y="276" fill={CAD.sl}>SL-POLE</text>
        <text x="600" y="606" fill={CAD.sf}>SF-TREE</text><text x="52" y="90" fill={CAD.bldg}>A-BLDG</text>
      </g>
    </svg>
  );

  return (
    <div className="hs" dir="ltr">
      <div className="hs-win">
        <div className="hs-bar"><i /><i /><i /><span>{labels.cad}</span><b>→</b><span>{labels.gis}</span></div>
        <div ref={box} className="hs-view" style={{ ["--x" as any]: `${x * 100}%` }}
          onPointerDown={(e) => { drag.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); move(e.clientX); }}
          onPointerMove={(e) => drag.current && move(e.clientX)}
          onPointerUp={() => { drag.current = false; }} onPointerCancel={() => { drag.current = false; }}>
          {gis}
          <div className="hs-cad">{cad}</div>
          <div className="hs-tag hs-tag-l">DWG · 23 layers</div>
          <div className="hs-tag hs-tag-r">GDB · 7 feature classes</div>
          <div className="hs-pop" style={{ opacity: x < 0.6 && (!hot || hot === "IR") ? 1 : 0 }}>
            <b>IR_PIPE</b><span>Diameter</span><em>110 mm</em><span>Material</span><em>HDPE</em><span>Status</span><em>Existing</em>
          </div>
          <div className="hs-seam">
            <button className="hs-knob" aria-label={labels.drag} role="slider" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(x * 100)}
              onKeyDown={(e) => { if (e.key === "ArrowLeft") setX(Math.max(0.03, x - 0.05)); if (e.key === "ArrowRight") setX(Math.min(0.97, x + 0.05)); }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M9 6l-6 6 6 6M15 6l6 6-6 6" /></svg>
            </button>
          </div>
        </div>
      </div>
      <ul className="hs-chips" dir={lang === "ar" ? "rtl" : "ltr"}>
        {layers.map((l) => (
          <li key={l.id} tabIndex={0} className={hot === l.id ? "on" : hot ? "dim" : ""}
            onPointerEnter={() => { setHot(l.id); if (x > 0.7) setX(0.35); }} onPointerLeave={() => setHot(null)}
            onFocus={() => setHot(l.id)} onBlur={() => setHot(null)}>
            <i style={{ background: l.color }} />{l.name}
          </li>))}
      </ul>
    </div>
  );
}
