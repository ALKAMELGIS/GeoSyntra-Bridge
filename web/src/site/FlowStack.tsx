/**
 * "Less redrawing" — three app windows stacked in perspective: the conversion flow, the DMT checks and the
 * geodatabase schema. Interactive: the stack follows the pointer, a window comes forward when clicked,
 * Run plays the flow node by node, the branch goes to "You confirm" when a layer is uncertain, and the
 * checks and the schema fill in as the flow reaches them.
 */
import { useEffect, useRef, useState } from "react";

type L = "en" | "ar";
const TX = {
  en: { flow: "Conversion flow", ex: "Example job", qc: "DMT checks", schema: "From drawing to schema", human: "Designed with human control",
    run: "Run", again: "Run again", uncertain: "Uncertain layer", rules: ["Schema = template", "Coded values", "NOT NULL fields", "Topology rules", "Elevation (DEM)"],
    nodes: [["Drawing in", "DWG · DXF · DGN"], ["Layer match", "Dictionary + learned rules"], ["Generate GDB", "Template filled"], ["You confirm", "Only what is uncertain"], ["Package", "Named per the standard"]],
    cols: ["Feature class", "Features", "Status"], st: { wait: "waiting", draft: "draft", ok: "passed" } },
  ar: { flow: "مسار التحويل", ex: "مهمة مثال", qc: "فحوص DMT", schema: "من المخطط إلى الاسكيما", human: "مصمَّم ليبقى القرار لك",
    run: "تشغيل", again: "أعد التشغيل", uncertain: "طبقة غير مؤكدة", rules: ["الاسكيما = القالب", "القيم المرمّزة", "الحقول الإلزامية", "قواعد التوبولوجي", "المناسيب (DEM)"],
    nodes: [["المخطط", "DWG · DXF · DGN"], ["مطابقة الطبقات", "القاموس + قواعد متعلَّمة"], ["إنتاج GDB", "القالب معبّأ"], ["تأكيدك", "غير المؤكد فقط"], ["الحزمة", "باسم مطابق للمعيار"]],
    cols: ["الطبقة", "العناصر", "الحالة"], st: { wait: "بانتظار", draft: "مسودة", ok: "ناجح" } },
};
const ROWS: [string, number][] = [["IRRPRESSUREMAIN", 38], ["IRRIGATION_VALVE", 64], ["SPRINKLERS", 412], ["VALVECHAMBER", 64]];
const TONE = [["#EAF3FC", "#7FB2E5"], ["#F1ECFC", "#A88BE3"], ["#E8F6EE", "#6CC394"], ["#FDF1E3", "#E9A85C"], ["#E8F6EE", "#6CC394"]];
const Ico = ({ k }: { k: number }) => {
  const p = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">{[
    <g {...p}><path d="M4 18 9 9l4 6 3-4 4 7Z" /><path d="M4 5h16" /></g>,
    <g {...p}><rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" /><path d="M11 7.5h3.5a2 2 0 0 1 2 2V13" /></g>,
    <g {...p}><ellipse cx="12" cy="6" rx="7" ry="2.5" /><path d="M5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5" /></g>,
    <g {...p}><circle cx="12" cy="8" r="3.5" /><path d="M5 20c1-3.5 3.8-5.5 7-5.5s6 2 7 5.5" /></g>,
    <g {...p}><path d="M4 8 12 4l8 4-8 4-8-4Z" /><path d="M4 8v8l8 4 8-4V8M12 12v8" /></g>][k]}</svg>;
};

export function FlowStack({ lang }: { lang: L }) {
  const t = TX[lang];
  const [front, setFront] = useState(0);                 // which window is in front: 0 flow, 1 QC, 2 schema
  const [step, setStep] = useState(-1);                  // -1 idle … 5 done
  const [unsure, setUnsure] = useState(true);
  const unsureRef = useRef(true); unsureRef.current = unsure;
  const [tilt, setTilt] = useState({ x: 0, y: 0 });
  const box = useRef<HTMLDivElement>(null);
  const timer = useRef<number>();
  const still = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  const run = () => {
    window.clearInterval(timer.current); setStep(0); setFront(0);
    let s = 0;
    timer.current = window.setInterval(() => {
      s += 1;
      if (s === 3 && !unsureRef.current) s = 4;                     // confident: skip "You confirm"… the branch still shows
      setStep(s);
      if (s === 4) setFront(1);
      if (s >= 5) { window.clearInterval(timer.current); setFront(2); }
    }, still ? 300 : 900);
  };
  // start once, when the stack first comes into view
  useEffect(() => {
    const el = box.current; if (!el) return;
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { run(); io.disconnect(); } }, { threshold: 0.4 });
    io.observe(el);
    return () => { io.disconnect(); window.clearInterval(timer.current); };
  }, []);

  const move = (e: React.PointerEvent) => {
    if (still || e.pointerType === "touch") return;
    const b = box.current!.getBoundingClientRect();
    setTilt({ x: ((e.clientX - b.left) / b.width - 0.5) * 2, y: ((e.clientY - b.top) / b.height - 0.5) * 2 });
  };
  const on = (i: number) => step >= i;
  const lit = (i: number) => step === i;
  const done = step >= 5;
  const checks = Math.max(0, Math.min(t.rules.length, (step - 3) * 3));
  const order = [0, 1, 2].sort((a, b) => (a === front ? -1 : b === front ? 1 : a - b));   // front first

  const win = (i: number, body: React.ReactNode, title: string, chip: string) => {
    const depth = order.indexOf(i);
    return (
      <div key={i} className={`fs-win d${depth}`} style={{ zIndex: 3 - depth }} onClick={() => setFront(i)}
        role="button" tabIndex={0} aria-label={title} onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && setFront(i)}>
        <header><span className="fs-dot" /><b>{title}</b><span className="fs-chip">{chip}</span><span className="fs-more">•••</span></header>
        <div className="fs-body">{body}</div>
        <footer><span>{t.human}</span>
          <button onClick={(e) => { e.stopPropagation(); run(); }} aria-label={done ? t.again : t.run} tabIndex={depth ? -1 : 0}>
            <svg width="11" height="11" viewBox="0 0 12 12"><path d="M3 1.8v8.4L10 6Z" fill="currentColor" /></svg>{done ? t.again : t.run}</button>
        </footer>
      </div>
    );
  };

  const node = (i: number, cls: string) => (
    <div className={`fs-node ${cls}${on(i) ? " on" : ""}${lit(i) ? " lit" : ""}${i === 3 && !unsure ? " skip" : ""}`}
      style={{ ["--bg" as string]: TONE[i][0], ["--bd" as string]: TONE[i][1] }}>
      <Ico k={i} /><b>{t.nodes[i][0]}</b><small>{t.nodes[i][1]}</small>
    </div>
  );

  return (
    <div className="fs" ref={box} onPointerMove={move} onPointerLeave={() => setTilt({ x: 0, y: 0 })} dir="ltr"
      style={{ ["--tx" as string]: tilt.x, ["--ty" as string]: tilt.y }}>
      <div className="fs-stack">
        {win(2, (
          <table className="fs-table"><thead><tr>{t.cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>{ROWS.map(([fc, n]) => (
              <tr key={fc}><td>{fc}</td><td>{on(2) ? n : "—"}</td>
                <td><span className={`fs-st ${done ? "ok" : on(2) ? "draft" : ""}`}>{done ? t.st.ok : on(2) ? t.st.draft : t.st.wait}</span></td></tr>))}
            </tbody></table>), t.schema, "ADM_IR_V10")}
        {win(1, (
          <div className="fs-qc">
            {node(4, "solo")}
            <ul>{t.rules.map((r, k) => <li key={r} className={k < checks ? "ok" : ""}><i />{r}</li>)}</ul>
          </div>), t.qc, done ? "0 errors" : "…")}
        {win(0, (
          <div className="fs-flow">
            <svg className="fs-wires" viewBox="0 0 600 300" preserveAspectRatio="none" aria-hidden="true">
              <path id="w1" className={on(1) ? "on" : ""} d="M150 170 C175 170 180 170 205 170" />
              <path id="w2" className={on(2) ? "on" : ""} d="M335 160 C380 160 380 90 420 90" />
              <path id="w3" className={`${unsure && on(3) ? "on" : ""}${unsure ? "" : " off"}`} d="M335 180 C380 180 380 240 420 240" />
              {!still && on(1) && !done && <circle r="4" className="fs-spark"><animateMotion dur="1.4s" repeatCount="indefinite" path={on(2) ? "M335 160 C380 160 380 90 420 90" : "M150 170 C175 170 180 170 205 170"} /></circle>}
            </svg>
            {node(0, "n0")}{node(1, "n1")}{node(2, "n2")}{node(3, "n3")}
            <button className="fs-toggle" onClick={(e) => { e.stopPropagation(); setUnsure(!unsure); }} aria-pressed={unsure}>
              <span className={unsure ? "on" : ""} />{t.uncertain}</button>
          </div>), t.flow, t.ex)}
      </div>
    </div>
  );
}
