/**
 * The sign-in page's left panel: one example job through the conversion pipeline. Packets run from the drawing
 * through layer matching into the engine and out to the deliverables, and the log steps through the job.
 * Pure SVG + CSS (SMIL for the packets), no libraries. Facts in the footer are the product's own rules.
 */
import { useEffect, useState } from "react";

const LOG = [
  'Received: "IR_As-Built_Phase2.dwg" (14.2 MB, 23 layers)',
  "Reading entities → 12,480 lines · 3,912 blocks · 1,204 texts",
  "Layer match: 21 of 23 layers mapped from the dictionary",
  "2 layers uncertain → sent to the engineer for confirmation",
  "Engine: coordinates checked against Abu Dhabi grid (EPSG:32640)",
  "Writing IRRPRESSUREMAIN · IRRIGATION_VALVE · SPRINKLERS",
  "DMT checks: schema ✓  coded values ✓  topology ✓",
  "Package ready: ADM_IR_V10.gdb + metadata + QC report",
  "Done. Files are deleted after 72 hours",
  "Idle. Waiting for the next drawing…",
];

const P = {
  a: "M116,88 L158,88",
  b: "M268,88 L306,88",
  c: "M411,88 C425,88 435,50 448,50",
  d: "M411,88 L448,88",
  e: "M411,88 C425,88 435,126 448,126",
};

function Dot({ path, dur, delay, r, o }: { path: string; dur: number; delay: number; r: number; o: number }) {
  return <circle r={r} className="ap-dot" opacity={o}><animateMotion dur={`${dur}s`} begin={`${delay}s`} repeatCount="indefinite" path={path} /></circle>;
}

export function AuthPipeline({ ar }: { ar: boolean }) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const a = setInterval(() => setI((x) => (x + 1) % LOG.length), 2700);
    return () => clearInterval(a);
  }, []);
  const node = (x: number, w: number, top: string, main: string, sub: string) => (
    <g>
      <rect x={x} y={66} width={w} height={44} rx={8} className="ap-node" />
      <text x={x + w / 2} y={83} className="ap-k">{top}</text>
      <text x={x + w / 2} y={100} className="ap-v">{main}</text>
      <text x={x + w / 2} y={124} className="ap-s">{sub}</text>
    </g>
  );
  const out = (y: number, label: string, state: "ok" | "run", d = 0) => (
    <g>
      <rect x={448} y={y} width={116} height={30} rx={7} className="ap-out" />
      <text x={498} y={y + 18.5} className="ap-o">{label}</text>
      <circle cx={552} cy={y + 15} r={3} className={`ap-st ${state}`} style={{ animationDelay: `${d}s` }} />
    </g>
  );
  return (
    <div className="ap" dir="ltr">
      <div className="ap-head">
        <span><i className="ap-file" />IR_As-Built_Phase2.dwg</span>
        <span>{ar ? "مهمة مثال" : "Example job"}</span>
      </div>
      <svg viewBox="0 0 580 160" className="ap-svg" role="img" aria-label={ar ? "مسار تحويل المخطط إلى قاعدة بيانات" : "Drawing to geodatabase pipeline"}>
        <defs>
          <marker id="apm" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto">
            <path d="M2 1.5L7.5 5L2 8.5" fill="none" className="ap-arrow" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </marker>
        </defs>
        <path d={P.a} className="ap-wire" markerEnd="url(#apm)" />
        <path d={P.b} className="ap-wire" markerEnd="url(#apm)" />
        <path d={P.c} className="ap-wire faint" /><path d={P.d} className="ap-wire faint" /><path d={P.e} className="ap-wire faint" />
        <Dot path={P.a} dur={1.05} delay={0} r={2.5} o={1} /><Dot path={P.a} dur={1.05} delay={0.35} r={1.8} o={0.65} /><Dot path={P.a} dur={1.05} delay={0.7} r={1.3} o={0.35} />
        <Dot path={P.b} dur={0.88} delay={0.18} r={2.5} o={1} /><Dot path={P.b} dur={0.88} delay={0.62} r={1.8} o={0.65} />
        <Dot path={P.c} dur={1.3} delay={0.08} r={2.2} o={0.9} /><Dot path={P.c} dur={1.3} delay={0.65} r={1.5} o={0.55} />
        <Dot path={P.d} dur={1.15} delay={0.28} r={2.2} o={0.9} /><Dot path={P.d} dur={1.15} delay={0.85} r={1.5} o={0.55} />
        <Dot path={P.e} dur={1.4} delay={0.45} r={2.2} o={0.9} /><Dot path={P.e} dur={1.4} delay={1.0} r={1.5} o={0.55} />
        {node(16, 100, "Input", "CAD drawing", "DWG · DXF · DGN")}
        {node(158, 110, "Dictionary", "Layer match", "21 of 23 layers")}
        <g>
          <rect x={306} y={53} width={105} height={70} rx={10} className="ap-core" />
          <text x={358} y={80} className="ap-k core">Engine</text>
          <text x={358} y={99} className="ap-v core">Converting</text>
          <text x={358} y={114} className="ap-s core in">ADM template V10</text>
        </g>
        {out(35, "Feature classes", "ok")}
        {out(73, "DMT checks", "run")}
        {out(111, "GDB package", "run", 0.35)}
      </svg>
      <div className="ap-log"><span>›</span><div key={i} className="ap-msg">{LOG[i]}</div></div>
      <div className="ap-stats">
        <div><small>{ar ? "القوالب" : "Templates"}</small><b>7</b></div>
        <div><small>{ar ? "تعديل على القالب" : "Template changes"}</small><b>0</b></div>
        <div><small>{ar ? "مدة حفظ الملفات" : "Files kept"}</small><b>72 h</b></div>
        <div className="r"><small>{ar ? "المخرجات" : "Output"}</small><span>File Geodatabase</span></div>
      </div>
    </div>
  );
}

/** Contour-like lines sweeping across the bottom of the panel. */
export function AuthLines() {
  const lines = Array.from({ length: 34 }, (_, k) => {
    const o = k * 7;
    return `M-40 ${330 + o * 0.6} C 160 ${250 + o}, 330 ${260 + o * 0.8}, 520 ${420 + o * 1.1} S 760 ${620 + o}, 900 ${560 + o * 0.7}`;
  });
  return (
    <svg className="ax-lines" viewBox="0 0 800 700" preserveAspectRatio="xMidYMax slice" aria-hidden="true">
      <g fill="none" strokeWidth="1">{lines.map((d, k) => <path key={k} d={d} style={{ animationDelay: `${k * 0.04}s`, opacity: 0.25 + (k % 5) * 0.08 }} />)}</g>
    </svg>
  );
}
