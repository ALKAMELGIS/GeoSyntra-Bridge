/**
 * Hero: the transfer, as one machine on a plate (procedural three.js — no models, no images).
 * A CAD sheet feeds raw entities onto a belt → a scanner reads the layers → a mapper gives each entity its
 * feature class (it takes the template's colour) → the DMT gate checks it (green flash) → it is lifted into
 * the geodatabase, whose counter climbs. Drag to turn it; it rests, sways and pauses when off screen.
 */
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

export interface HeroLabels { dwg: string; read: string; map: string; qc: string; gdb: string; features: string; layers: string }

export default function HeroMachine({ labels, centered }: { labels: HeroLabels; centered?: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const tags = useRef<(HTMLDivElement | null)[]>([]);
  const lab = useRef(labels); lab.current = labels;
  useEffect(() => {
    const el = host.current; if (!el) return;
    let off = () => {};
    try { off = init(el, tags.current, () => lab.current, !!centered); } catch { el.classList.add("hm-fail"); }
    return () => off();
  }, []);
  const names = [labels.dwg, labels.read, labels.map, labels.qc, labels.gdb];
  return (
    <div className="hm" ref={host} aria-hidden="true">
      {names.map((n, i) => <div key={i} className="hm-tag" ref={(d) => { tags.current[i] = d; }}><i>0{i + 1}</i>{n}</div>)}
    </div>
  );
}

const TEMPLATE_COLORS = [0x2f7f8c, 0x3d3e42, 0xb07a2a, 0x4a5f86, 0xb3a795, 0x76825d, 0x8a6d4f];

function init(el: HTMLDivElement, tagEls: (HTMLDivElement | null)[], L: () => HeroLabels, centered: boolean) {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const W = () => el.clientWidth || 800, H = () => el.clientHeight || 600;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0, 0);
  renderer.setPixelRatio(Math.min(devicePixelRatio, W() < 900 ? 1.5 : 1.75));
  renderer.setSize(W(), H());
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.domElement.className = "hm-canvas";
  el.prepend(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(28, W() / H(), 1, 200);
  const pm = new THREE.PMREMGenerator(renderer), room = new RoomEnvironment();
  scene.environment = pm.fromScene(room, 0.04).texture; room.dispose(); pm.dispose();
  scene.add(new THREE.HemisphereLight(0xf4f0e8, 0x3b342c, 1.3));
  const key = new THREE.DirectionalLight(0xfff2dc, 2.8);
  key.position.set(-5, 12, 8); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -8, right: 8, top: 6, bottom: -6, near: 1, far: 40 });
  key.shadow.bias = -0.0003; key.shadow.normalBias = 0.03; scene.add(key);
  const rim = new THREE.DirectionalLight(0xdde6f2, 1.2); rim.position.set(6, 5, -9); scene.add(rim);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.07; controls.enableZoom = false; controls.enablePan = false;
  controls.minPolarAngle = 0.5; controls.maxPolarAngle = 1.25; controls.rotateSpeed = 0.45;
  controls.minAzimuthAngle = -0.9; controls.maxAzimuthAngle = 1.3;
  if (matchMedia("(pointer:coarse)").matches) controls.enableRotate = false;

  // ------------------------------------------------------------------ materials & helpers
  const mat = (color: number, metal = 0.1, rough = 0.5, x: THREE.MeshStandardMaterialParameters = {}) =>
    new THREE.MeshStandardMaterial({ color, metalness: metal, roughness: rough, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1, ...x });
  const M = {
    plate: mat(0xd9d5ce, 0.35, 0.42), edge: mat(0xbdb8b0, 0.6, 0.3), body: mat(0xeceae6, 0.2, 0.35), dark: mat(0x2b2d31, 0.55, 0.35),
    belt: mat(0x232428, 0.1, 0.8), chrome: mat(0xc9ccd0, 0.95, 0.18), paper: mat(0xf8f6f1, 0, 0.85),
    cyan: mat(0x5ec4d6, 0.1, 0.3, { emissive: 0x3fb6cc, emissiveIntensity: 1.6 }), green: mat(0x9fe0b4, 0.1, 0.3, { emissive: 0x3fae74, emissiveIntensity: 0.4 }),
    raw: mat(0x8d9097, 0.3, 0.45), glass: mat(0xa9b3b9, 0.4, 0.1, { transparent: true, opacity: 0.22, depthWrite: false }),
    warm: mat(0xfff1d8, 0.1, 0.3, { emissive: 0xffd9a0, emissiveIntensity: 1.2 }),
  };
  const geos = new Map<string, THREE.BufferGeometry>();
  const rbox = (w: number, h: number, d: number, r = 0.04) => {
    const k = `${w},${h},${d},${r}`;
    if (!geos.has(k)) geos.set(k, r ? new RoundedBoxGeometry(w, h, d, 2, Math.min(r, w / 3, h / 3, d / 3)) : new THREE.BoxGeometry(w, h, d));
    return geos.get(k)!;
  };
  const box = (p: THREE.Object3D, w: number, h: number, d: number, x: number, y: number, z: number, m: THREE.Material = M.body, r = 0.04) => {
    const o = new THREE.Mesh(rbox(w, h, d, r), m); o.position.set(x, y, z); o.castShadow = o.receiveShadow = true; p.add(o); return o;
  };
  const cyl = (p: THREE.Object3D, r: number, h: number, x: number, y: number, z: number, m: THREE.Material = M.chrome, r2 = r, seg = 32) => {
    const o = new THREE.Mesh(new THREE.CylinderGeometry(r, r2, h, seg), m); o.position.set(x, y, z); o.castShadow = o.receiveShadow = true; p.add(o); return o;
  };
  const canvasTex = (w: number, h: number, draw: (c: CanvasRenderingContext2D) => void) => {
    const c = document.createElement("canvas"); c.width = w; c.height = h; const ctx = c.getContext("2d")!; draw(ctx);
    const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; return { t, ctx, c };
  };
  const screen = (p: THREE.Object3D, w: number, h: number, x: number, y: number, z: number, t: THREE.Texture, ry = 0) => {
    const o = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map: t, toneMapped: false })); o.position.set(x, y, z); o.rotation.y = ry; p.add(o); return o;
  };
  const font = getComputedStyle(el).fontFamily || "sans-serif";

  const machine = new THREE.Group(); scene.add(machine);
  // the plate
  box(machine, 12.6, 0.34, 4.6, 0, -0.17, 0, M.plate, 0.14);
  box(machine, 12.4, 0.04, 4.42, 0, 0.02, 0, M.edge, 0.06);
  for (const x of [-6, 6]) for (const z of [-2, 2]) cyl(machine, 0.07, 0.03, x, 0.05, z, M.chrome, 0.07, 16);
  // the belt (stripes scroll)
  const stripes = canvasTex(256, 32, (c) => { c.fillStyle = "#232428"; c.fillRect(0, 0, 256, 32); c.fillStyle = "#34363b"; for (let x = 0; x < 256; x += 16) c.fillRect(x, 0, 6, 32); });
  stripes.t.wrapS = THREE.RepeatWrapping; stripes.t.repeat.set(10, 1);
  const beltMat = mat(0xffffff, 0.1, 0.85, { map: stripes.t });
  box(machine, 10.6, 0.1, 0.95, 0.2, 0.12, 0, beltMat, 0.04);
  for (const x of [-5.1, 5.5]) { const r = cyl(machine, 0.09, 1.0, x, 0.12, 0, M.chrome, 0.09, 20); r.rotation.x = Math.PI / 2; }
  box(machine, 10.8, 0.06, 0.06, 0.2, 0.2, 0.52, M.edge, 0.02); box(machine, 10.8, 0.06, 0.06, 0.2, 0.2, -0.52, M.edge, 0.02);

  // 01 — the CAD sheet on an easel, entities drop from its tray
  const X0 = -4.6, X1 = -2.2, X2 = 0.2, X3 = 2.5, X4 = 4.7;
  const sheet = canvasTex(512, 360, (c) => {
    c.fillStyle = "#fbfaf6"; c.fillRect(0, 0, 512, 360); c.strokeStyle = "#d9d6cf"; c.lineWidth = 1;
    for (let x = 0; x < 512; x += 32) { c.beginPath(); c.moveTo(x, 0); c.lineTo(x, 360); c.stroke(); }
    for (let y = 0; y < 360; y += 32) { c.beginPath(); c.moveTo(0, y); c.lineTo(512, y); c.stroke(); }
    c.lineWidth = 4; c.strokeStyle = "#e2504c"; c.beginPath(); c.moveTo(20, 250); c.lineTo(170, 220); c.lineTo(330, 226); c.lineTo(492, 180); c.stroke();
    c.lineWidth = 2.5; c.strokeStyle = "#d9a520"; [[170, 220, 185, 90], [330, 226, 345, 80], [250, 224, 236, 330]].forEach(([a, b, d, e]) => { c.beginPath(); c.moveTo(a, b); c.lineTo(d, e); c.stroke(); });
    c.strokeStyle = "#3aa7bd"; [[170, 220], [330, 226]].forEach(([x, y]) => c.strokeRect(x - 9, y - 9, 18, 18));
    c.strokeStyle = "#4b9c52"; [[185, 120], [190, 150], [340, 110], [343, 140], [240, 300]].forEach(([x, y]) => { c.beginPath(); c.arc(x, y, 6, 0, 7); c.stroke(); });
    c.strokeStyle = "#1b1c1f"; c.lineWidth = 2; c.strokeRect(6, 6, 500, 348); c.strokeRect(372, 300, 128, 48);
    c.fillStyle = "#1b1c1f"; c.font = `600 15px ${font}`; c.fillText("IR_As-Built.dwg", 380, 328);
  });
  const easel = new THREE.Group(); easel.position.set(X0, 0, -0.15); machine.add(easel);
  box(easel, 0.08, 1.9, 0.08, -0.85, 0.95, -0.5, M.dark, 0.02); box(easel, 0.08, 1.9, 0.08, 0.85, 0.95, -0.5, M.dark, 0.02);
  const board = new THREE.Group(); board.position.set(0, 1.45, -0.42); board.rotation.x = -0.32; easel.add(board);
  box(board, 1.95, 1.4, 0.05, 0, 0, -0.03, M.dark, 0.02);
  screen(board, 1.86, 1.31, 0, 0, 0.001, sheet.t);
  const hopper = box(easel, 1.3, 0.22, 0.95, 0, 0.42, 0.15, M.body, 0.06);
  box(easel, 0.95, 0.06, 0.7, 0, 0.55, 0.15, M.dark, 0.03); void hopper;

  // 02 — scanner arch with a laser curtain and a layer counter
  const arch = new THREE.Group(); arch.position.set(X1, 0, 0); machine.add(arch);
  box(arch, 0.22, 1.5, 0.3, 0, 0.75, -0.75, M.body, 0.06); box(arch, 0.22, 1.5, 0.3, 0, 0.75, 0.75, M.body, 0.06);
  box(arch, 0.36, 0.3, 1.9, 0, 1.55, 0, M.body, 0.08);
  box(arch, 0.38, 0.06, 1.92, 0, 1.38, 0, M.dark, 0.02);
  const laser = new THREE.Mesh(new THREE.PlaneGeometry(1.3, 1.2), new THREE.MeshBasicMaterial({ color: 0x55d0e4, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false, toneMapped: false }));
  laser.position.set(0, 0.8, 0); laser.rotation.y = Math.PI / 2; arch.add(laser);
  const beam = box(arch, 0.04, 0.03, 1.3, 0, 0.3, 0, M.cyan, 0.01);
  const scr1 = canvasTex(256, 128, () => {}); screen(arch, 0.62, 0.31, 0.19, 1.55, 0, scr1.t, Math.PI / 2);

  // 03 — the mapper: a turret that tints each entity with its feature class colour
  const mapper = new THREE.Group(); mapper.position.set(X2, 0, 0); machine.add(mapper);
  box(mapper, 0.3, 1.2, 0.3, 0, 0.6, -0.85, M.body, 0.06);
  box(mapper, 0.3, 0.24, 1.0, 0, 1.25, -0.45, M.body, 0.06);
  const head = new THREE.Group(); head.position.set(0, 1.0, 0); mapper.add(head);
  cyl(head, 0.32, 0.22, 0, 0, 0, M.chrome, 0.36, 7);
  TEMPLATE_COLORS.forEach((c, i) => { const a = (i / 7) * Math.PI * 2; const n = cyl(head, 0.055, 0.08, Math.cos(a) * 0.3, -0.13, Math.sin(a) * 0.3, mat(c, 0.2, 0.4, { emissive: c, emissiveIntensity: 0.6 }), 0.055, 12); n.userData.c = c; });
  const nozzle = cyl(head, 0.08, 0.28, 0, -0.22, 0, M.dark, 0.04, 16); void nozzle;

  // 04 — the DMT gate: a ring that flashes green on each pass
  const gate = new THREE.Group(); gate.position.set(X3, 0.75, 0); machine.add(gate);
  const ringMat = mat(0xdfe6e0, 0.6, 0.25, { emissive: 0x3fae74, emissiveIntensity: 0 });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.72, 0.07, 18, 64), ringMat); ring.rotation.y = Math.PI / 2; ring.castShadow = true; gate.add(ring);
  box(machine, 0.5, 0.12, 0.5, X3, 0.06, -0.85, M.dark, 0.04); box(machine, 0.12, 0.85, 0.12, X3, 0.45, -0.85, M.body, 0.03);
  const check = canvasTex(128, 128, (c) => { c.strokeStyle = "#3fae74"; c.lineWidth = 12; c.lineCap = "round"; c.beginPath(); c.moveTo(30, 66); c.lineTo(55, 90); c.lineTo(98, 40); c.stroke(); });
  const checkMesh = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.42), new THREE.MeshBasicMaterial({ map: check.t, transparent: true, opacity: 0, depthWrite: false, toneMapped: false }));
  checkMesh.position.set(X3, 1.85, 0); machine.add(checkMesh);

  // 05 — the geodatabase: three discs, a lift and a counter
  const db = new THREE.Group(); db.position.set(X4, 0, -0.1); machine.add(db);
  const discs: THREE.Mesh[] = [];
  for (let i = 0; i < 3; i++) { discs.push(cyl(db, 0.62, 0.36, 0, 0.42 + i * 0.44, -0.6, M.body, 0.62, 48)); cyl(db, 0.625, 0.05, 0, 0.62 + i * 0.44, -0.6, M.dark, 0.625, 48); }
  const dbTop = cyl(db, 0.5, 0.02, 0, 1.73, -0.6, M.green, 0.5, 48);
  const scr2 = canvasTex(256, 128, () => {}); screen(db, 0.8, 0.4, 0.0, 0.62, 0.035, scr2.t);
  box(db, 0.86, 0.46, 0.06, 0, 0.62, 0.0, M.dark, 0.03);

  // ------------------------------------------------------------------ entities on the belt
  const kinds = [new RoundedBoxGeometry(0.42, 0.08, 0.12, 2, 0.03), new THREE.SphereGeometry(0.1, 16, 12), new RoundedBoxGeometry(0.26, 0.06, 0.26, 2, 0.03)];
  const N = 14, SPAN = X4 - (X0 + 0.3);
  const items = Array.from({ length: N }, (_, i) => {
    const m = new THREE.MeshStandardMaterial({ color: 0x8d9097, metalness: 0.3, roughness: 0.45, emissive: 0x3fae74, emissiveIntensity: 0 });
    const o = new THREE.Mesh(kinds[i % 3], m); o.castShadow = true; machine.add(o);
    return { o, m, u: i / N, c: TEMPLATE_COLORS[(i * 3) % 7] };
  });
  const tmp = new THREE.Color(), raw = new THREE.Color(0x8d9097);

  // ------------------------------------------------------------------ screens
  let features = 0, flash = 0, scanned = 0;
  const paint = () => {
    const l = L();
    const s1 = scr1.ctx; s1.fillStyle = "#16181b"; s1.fillRect(0, 0, 256, 128); s1.fillStyle = "#7fd3e2"; s1.font = `600 20px ${font}`; s1.fillText(l.layers, 18, 42);
    s1.fillStyle = "#f2f0eb"; s1.font = `600 44px ${font}`; s1.fillText(String(23 + (scanned % 9)), 18, 98);
    for (let i = 0; i < 6; i++) { s1.fillStyle = i < (scanned % 7) ? "#5ec4d6" : "#3a3d42"; s1.fillRect(150 + i * 16, 70, 10, 28); }
    scr1.t.needsUpdate = true;
    const s2 = scr2.ctx; s2.fillStyle = "#16181b"; s2.fillRect(0, 0, 256, 128); s2.fillStyle = "#9fe0b4"; s2.font = `600 20px ${font}`; s2.fillText(l.features, 18, 42);
    s2.fillStyle = "#f2f0eb"; s2.font = `600 44px ${font}`; s2.fillText((1200 + features).toLocaleString("en-US"), 18, 98);
    scr2.t.needsUpdate = true;
  };
  paint();

  // ------------------------------------------------------------------ camera
  const goalTgt = new THREE.Vector3(0.2, 0.7, 0);
  const layout = () => {
    renderer.setSize(W(), H()); camera.aspect = W() / H();
    const wide = W() > 900 && !centered, a = W() / H(), tan = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    // wide hero: the machine sits in the right ~60 %, the headline keeps the left
    const rtl = !!el.closest("[dir=rtl]");
    camera.setViewOffset(W(), H(), wide ? W() * 0.18 * (rtl ? 1 : -1) : 0, wide ? -H() * 0.02 : H() * 0.02, W(), H());
    const d = Math.max(13.2 / (2 * tan * a * (wide ? 0.5 : 0.96)), 6 / (2 * tan * (wide ? 0.62 : 0.8)));
    camera.position.copy(new THREE.Vector3(7.5, 6.2, 11.5).normalize().multiplyScalar(d).add(goalTgt));
    controls.target.copy(goalTgt); camera.updateProjectionMatrix(); controls.update();
  };
  layout();
  const ro = new ResizeObserver(layout); ro.observe(el);
  let visible = true; const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }); io.observe(el);
  let interacting = false;
  controls.addEventListener("start", () => { interacting = true; }); controls.addEventListener("end", () => { interacting = false; });
  const anchors = [new THREE.Vector3(X0, 0, 1.25), new THREE.Vector3(X1, 0, 1.25), new THREE.Vector3(X2, 0, 1.25), new THREE.Vector3(X3, 0, 1.25), new THREE.Vector3(X4, 0, 1.25)];
  const v = new THREE.Vector3();

  let raf = 0, last = performance.now(), t = 0, nextPaint = 0, az0 = controls.getAzimuthalAngle();
  const frame = (now: number) => {
    raf = requestAnimationFrame(frame);
    if (!visible) { last = now; return; }
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!still) t += dt;
    const speed = 0.055;
    stripes.t.offset.x = -t * 1.2;
    laser.material.opacity = 0.12 + 0.08 * Math.sin(t * 6);
    beam.position.y = 0.3 + 0.9 * (0.5 + 0.5 * Math.sin(t * 2.2));
    head.rotation.y = t * 1.4;
    flash = Math.max(0, flash - dt * 2.2);
    ringMat.emissiveIntensity = flash * 1.4; (checkMesh.material as THREE.MeshBasicMaterial).opacity = flash; checkMesh.position.y = 1.85 + (1 - flash) * 0.25;
    (dbTop.material as THREE.MeshStandardMaterial).emissiveIntensity = 0.35 + flash * 0.8;
    discs.forEach((d, i) => d.position.y = 0.42 + i * 0.44 + Math.sin(t * 2 + i) * 0.006);

    for (const it of items) {
      const prev = it.u;
      it.u = (it.u + dt * speed) % 1;
      if (it.u < prev) { it.m.color.copy(raw); it.c = TEMPLATE_COLORS[Math.floor(Math.random() * 7)]; scanned++; }
      const x = X0 + 0.3 + it.u * SPAN;
      let y = 0.24, z = 0, s = 1;
      if (x < X0 + 0.9) { y = 0.62 - (x - (X0 + 0.3)) / 0.6 * 0.38; }          // drop from the tray
      if (x > X4 - 0.45) {                                                    // lifted into the geodatabase
        const k = Math.min(1, (x - (X4 - 0.45)) / 0.45);
        y = 0.24 + k * 1.6; z = -0.6 * k - 0.1 * k; s = 1 - k * 0.85;
        if (k > 0.98 && !it.o.userData.stored) { it.o.userData.stored = true; features += 1; }
      } else it.o.userData.stored = false;
      const tint = THREE.MathUtils.clamp((x - X2 + 0.15) / 0.3, 0, 1);       // mapped: takes the class colour
      tmp.copy(raw).lerp(new THREE.Color(it.c), tint); it.m.color.copy(tmp);
      const g = Math.max(0, 1 - Math.abs(x - X3) / 0.35);                     // checked at the gate
      it.m.emissiveIntensity = g * 0.9;
      if (g > 0.95 && !it.o.userData.passed) { it.o.userData.passed = true; flash = 1; }
      if (g === 0) it.o.userData.passed = false;
      it.o.position.set(x, y, z); it.o.scale.setScalar(s);
      it.o.rotation.y = x < X2 ? 0.3 * Math.sin(it.u * 40) : 0;                // loose before mapping, aligned after
    }
    if (now > nextPaint) { paint(); nextPaint = now + 400; }
    if (!interacting && !still) {                                             // a slow sway when left alone
      const target = az0 + Math.sin(t * 0.18) * 0.18;
      const cur = controls.getAzimuthalAngle(); const dAz = (target - cur) * 0.02;
      const off = camera.position.clone().sub(controls.target); off.applyAxisAngle(new THREE.Vector3(0, 1, 0), dAz); camera.position.copy(controls.target).add(off);
    }
    controls.update();
    anchors.forEach((a, i) => {
      const tag = tagEls[i]; if (!tag) return;
      v.copy(a).project(camera);
      tag.style.transform = `translate(${((v.x + 1) / 2) * W()}px, ${((1 - v.y) / 2) * H()}px) translate(-50%, 0)`;
    });
    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(frame);
  az0 = controls.getAzimuthalAngle();

  return () => {
    cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); controls.dispose();
    scene.traverse((o) => { const m = o as THREE.Mesh; m.geometry?.dispose?.(); const mm = m.material as THREE.Material | THREE.Material[] | undefined; (Array.isArray(mm) ? mm : mm ? [mm] : []).forEach((x) => x.dispose()); });
    renderer.dispose(); renderer.domElement.remove();
  };
}
