/**
 * The seven ADM templates as one street corner, built procedurally with three.js (no models, no images):
 * roads, paving, planting, street lighting above ground; irrigation, ITC ducts and storm water below.
 * Three views — 3D (a section cut reveals what is under the road), Layers (exploded, one template per level)
 * and 2D (top-down plan, drawn like CAD). Hover a layer or a legend entry to isolate it.
 */
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

export type ViewMode = "3d" | "layers" | "plan";
export interface LayerDef { id: string; name: string; note: string; color: string }

type V3 = [number, number, number];
interface Built { group: THREE.Group; mats: THREE.Material[]; lines: THREE.LineBasicMaterial[]; anchor: THREE.Vector3 }

// stacking order in the Layers view, bottom → top
const ORDER = ["SW", "IT", "IR", "RD", "HL", "SF", "SL"];

export default function UtilityScene({ layers, mode, dark, onHover, hover }: {
  layers: LayerDef[]; mode: ViewMode; dark: boolean; hover: string | null; onHover: (id: string | null) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const api = useRef<{ setMode: (m: ViewMode) => void; setHover: (id: string | null) => void } | null>(null);
  const labels = useRef<Record<string, HTMLDivElement | null>>({});
  const [failed, setFailed] = useState(false);
  const hoverCb = useRef(onHover); hoverCb.current = onHover;

  useEffect(() => {
    const el = host.current; if (!el) return;
    let dispose = () => {};
    try { const r = init(el, layers, labels.current, (id) => hoverCb.current(id)); api.current = r; dispose = r.dispose; }
    catch { setFailed(true); }
    return () => dispose();
  }, []);
  useEffect(() => { api.current?.setMode(mode); }, [mode]);
  useEffect(() => { api.current?.setHover(hover); }, [hover]);
  void dark;

  if (failed) return <div className="us-fail">3D view needs WebGL.</div>;
  return (
    <div className="us-host" ref={host}>
      {layers.map((l) => (
        <div key={l.id} className="us-label" ref={(d) => { labels.current[l.id] = d; }} aria-hidden="true">
          <i style={{ background: l.color }} />{l.name}
        </div>
      ))}
    </div>
  );
}

function init(el: HTMLDivElement, defs: LayerDef[], labelEls: Record<string, HTMLDivElement | null>, onHover: (id: string | null) => void) {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const W = () => el.clientWidth || 800, H = () => el.clientHeight || 600;
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0, 0);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));
  renderer.setSize(W(), H());
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.localClippingEnabled = true;
  renderer.domElement.className = "us-canvas";
  el.prepend(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, W() / H(), 1, 200);
  const pm = new THREE.PMREMGenerator(renderer), room = new RoomEnvironment();
  scene.environment = pm.fromScene(room, 0.04).texture; room.dispose(); pm.dispose();
  scene.add(new THREE.HemisphereLight(0xf3efe7, 0x3a332b, 1.4));
  const key = new THREE.DirectionalLight(0xfff3df, 2.6);
  key.position.set(-6, 14, 9); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
  Object.assign(key.shadow.camera, { left: -9, right: 9, top: 9, bottom: -9, near: 1, far: 40 });
  key.shadow.bias = -0.0003; key.shadow.normalBias = 0.03; scene.add(key);
  const rim = new THREE.DirectionalLight(0xdfe7f2, 1.1); rim.position.set(8, 6, -10); scene.add(rim);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true; controls.dampingFactor = 0.07; controls.enablePan = false; controls.enableZoom = false;
  controls.minPolarAngle = 0.02; controls.maxPolarAngle = Math.PI * 0.46; controls.rotateSpeed = 0.5;
  controls.autoRotate = !still; controls.autoRotateSpeed = 0.35;
  if (matchMedia("(pointer:coarse)").matches) controls.enableRotate = false;

  // ------------------------------------------------------------------ materials & helpers
  const cut = new THREE.Plane(new THREE.Vector3(0, 0, -1), 20);           // keeps z < constant
  const INK = 0x1d1e21;
  const built: Record<string, Built> = {};
  const all: THREE.Mesh[] = [];
  let current: Built = null as unknown as Built;
  const begin = (id: string) => { current = { group: new THREE.Group(), mats: [], lines: [], anchor: new THREE.Vector3(-6.6, 0, 0) }; current.group.userData.layer = id; built[id] = current; scene.add(current.group); };
  const mat = (color: number, o: { rough?: number; metal?: number; clip?: boolean; emissive?: number; flat?: boolean } = {}) => {
    const m = new THREE.MeshStandardMaterial({ color, roughness: o.rough ?? 0.85, metalness: o.metal ?? 0, flatShading: o.flat,
      emissive: o.emissive ?? 0, emissiveIntensity: o.emissive ? 1.2 : 0, transparent: false,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      clippingPlanes: o.clip ? [cut] : undefined, side: o.clip ? THREE.DoubleSide : THREE.FrontSide });
    m.userData.base = 1; current.mats.push(m); return m;
  };
  const lineMat = (opacity = 0.55, clip = false) => {
    const m = new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity, clippingPlanes: clip ? [cut] : undefined });
    m.userData.base = opacity; current.lines.push(m); return m;
  };
  const add = (geo: THREE.BufferGeometry, m: THREE.Material, p: V3, edges = 0, clip = false) => {
    const mesh = new THREE.Mesh(geo, m); mesh.position.set(...p); mesh.castShadow = true; mesh.receiveShadow = true;
    mesh.userData.layer = current.group.userData.layer; current.group.add(mesh); all.push(mesh);
    geo.computeBoundingBox(); const bb = geo.boundingBox!; mesh.userData.cz = p[2] + (bb.min.z + bb.max.z) / 2;
    mesh.userData.above = ["SL", "SF", "HL"].includes(mesh.userData.layer) && !(m as THREE.Material).clippingPlanes?.length;
    if (edges) { const e = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 30), lineMat(edges, clip)); e.position.copy(mesh.position); e.rotation.copy(mesh.rotation); current.group.add(e); mesh.userData.edge = e; }
    return mesh;
  };
  // a box by its extents (x0..x1, y0..y1, z0..z1)
  const slab = (x0: number, x1: number, y0: number, y1: number, z0: number, z1: number, m: THREE.Material, edges = 0.5, clip = false) =>
    add(new THREE.BoxGeometry(x1 - x0, y1 - y0, z1 - z0), m, [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2], edges, clip);
  const pipe = (pts: V3[], r: number, m: THREE.Material) => {
    const curve = new THREE.CatmullRomCurve3(pts.map((p) => new THREE.Vector3(...p)), false, "catmullrom", 0.02);
    return add(new THREE.TubeGeometry(curve, Math.max(8, pts.length * 12), r, 10, false), m, [0, 0, 0]);
  };
  const drawLines = (segs: [V3, V3][], opacity = 0.5, clip = false) => {
    const g = new THREE.BufferGeometry().setFromPoints(segs.flatMap(([a, b]) => [new THREE.Vector3(...a), new THREE.Vector3(...b)]));
    current.group.add(new THREE.LineSegments(g, lineMat(opacity, clip)));
  };
  const C = (hex: string) => new THREE.Color(hex).getHex();
  const col = Object.fromEntries(defs.map((d) => [d.id, C(d.color)]));

  // ------------------------------------------------------------------ the corner (x −6..6, z −4..4, ground y = 0)
  // Storm water — trunk under the road, manholes, gullies at the kerb
  begin("SW");
  const swm = mat(col.SW, { rough: 0.5, metal: 0.2 }), conc = mat(0xb9b4ab, { rough: 0.9 });
  pipe([[-6, -0.95, 0.15], [6, -0.82, 0.15]], 0.13, swm);
  [-3.8, 0, 3.8].forEach((x) => {
    add(new THREE.CylinderGeometry(0.26, 0.26, 0.95, 24, 1, true), conc, [x, -0.47, 0.15], 0);
    add(new THREE.CylinderGeometry(0.3, 0.3, 0.05, 24), mat(0x55585e, { metal: 0.6, rough: 0.4 }), [x, 0.045, 0.15], 0.6);
  });
  [-5, -1.9, 1.9, 5].forEach((x, i) => {
    const z = i % 2 ? -0.98 : 0.98;
    slab(x - 0.16, x + 0.16, -0.35, 0.03, z - 0.1, z + 0.1, conc, 0.6);
    pipe([[x, -0.32, z], [x + (i < 2 ? 0.5 : -0.5), -0.7, 0.15]], 0.045, swm);
  });
  current.anchor.set(-6.6, -0.6, 0);

  // ITC — a duct bank of four under the north footway, handholes
  begin("IT");
  const itm = mat(col.IT, { rough: 0.45, metal: 0.1 });
  [[-0.05, -0.05], [0.05, -0.05], [-0.05, 0.05], [0.05, 0.05]].forEach(([dy, dz]) =>
    pipe([[-6, -0.5 + dy, 1.75 + dz], [1.2, -0.5 + dy, 1.75 + dz], [1.6, -0.5 + dy, 2.2 + dz], [1.6, -0.5 + dy, 4 + dz]], 0.035, itm));
  [-4.6, -1.2, 1.6].forEach((x, i) => {
    const z = i === 2 ? 3.1 : 1.75;
    slab(x - 0.28, x + 0.28, -0.65, 0.11, z - 0.2, z + 0.2, mat(0xa59c90), 0.7);
    slab(x - 0.3, x + 0.3, 0.11, 0.13, z - 0.22, z + 0.22, mat(0x6b6359, { metal: 0.4, rough: 0.5 }), 0.7);
  });
  current.anchor.set(-6.6, -0.4, 0);

  // Irrigation — main along the south verge, laterals, valve boxes, sprinklers
  begin("IR");
  const irm = mat(col.IR, { rough: 0.45 });
  pipe([[-6, -0.3, -3.1], [6, -0.3, -3.1]], 0.05, irm);
  pipe([[3.5, -0.3, -3.1], [3.5, -0.3, -1.9], [3.5, -0.3, 1.1], [4.8, -0.3, 2.4], [4.8, -0.3, 4]], 0.035, irm);
  pipe([[-6, -0.28, 3.1], [-1.4, -0.28, 3.1]], 0.035, irm);
  [-4.6, -1.4, 1.8, 4.6].forEach((x) => {
    pipe([[x, -0.3, -3.1], [x, -0.12, -2.6], [x, -0.05, -2.45]], 0.025, irm);
    slab(x - 0.14, x + 0.14, -0.22, 0.07, -3.32, -3.0, mat(0x2f3a33, { rough: 0.6 }), 0.6);
  });
  for (let x = -5.6; x <= 5.6; x += 1.4) add(new THREE.CylinderGeometry(0.035, 0.05, 0.14, 10), mat(0x2a2d31, { metal: 0.3 }), [x, 0.1, -3.55], 0);
  for (let x = -5.4; x <= -1.8; x += 1.2) add(new THREE.CylinderGeometry(0.035, 0.05, 0.14, 10), mat(0x2a2d31, { metal: 0.3 }), [x, 0.12, 3.5], 0);
  current.anchor.set(-6.6, -0.2, 0);

  // Roads — carriageway, side road, kerbs, markings, crossing
  begin("RD");
  const asph = mat(0x3d3e42, { rough: 0.95, clip: true }), kerb = mat(0xcfcac2, { rough: 0.8, clip: true }), paint = mat(0xf2efe8, { rough: 0.6, clip: true });
  slab(-6, 6, -0.12, 0.02, -1.1, 1.1, asph, 0.5, true);
  slab(1.6, 3.4, -0.12, 0.02, 1.1, 4, asph, 0.5, true);
  [[-6, 1.6, 1.1], [3.4, 6, 1.1]].forEach(([a, b, z]) => slab(a, b, -0.05, 0.12, z, z + 0.1, kerb, 0.6, true));
  slab(-6, 6, -0.05, 0.12, -1.2, -1.1, kerb, 0.6, true);
  slab(1.5, 1.6, -0.05, 0.12, 1.1, 4, kerb, 0.6, true); slab(3.4, 3.5, -0.05, 0.12, 1.1, 4, kerb, 0.6, true);
  for (let x = -5.8; x < 6; x += 0.9) if (x < 0.2 || x > 1.4) slab(x, x + 0.45, 0.02, 0.04, -0.03, 0.03, paint, 0, true);
  for (let z = -0.95; z < 1.0; z += 0.3) slab(0.35, 1.2, 0.02, 0.04, z, z + 0.15, paint, 0, true);
  current.anchor.set(-6.6, 0, 0);

  // Hard landscaping — footways with joints, plaza, benches
  begin("HL");
  const pav = mat(0xd9d3c9, { rough: 0.9, clip: true }), pav2 = mat(0xc7beb1, { rough: 0.9, clip: true });
  slab(-6, 1.5, -0.08, 0.1, 1.2, 2.3, pav, 0.45, true); slab(3.5, 6, -0.08, 0.1, 1.2, 2.3, pav, 0.45, true);
  slab(-6, 6, -0.08, 0.1, -2.3, -1.2, pav, 0.45, true);
  slab(-6, -1.4, -0.08, 0.1, 2.3, 4, pav2, 0.45, true);
  const joints: [V3, V3][] = [];
  for (let x = -5.5; x < 6; x += 0.5) { joints.push([[x, 0.108, -2.3], [x, 0.108, -1.2]]); if (x < 1.5 || x > 3.5) joints.push([[x, 0.108, 1.2], [x, 0.108, 2.3]]); }
  for (let x = -5.6; x < -1.4; x += 0.4) joints.push([[x, 0.108, 2.3], [x, 0.108, 4]]);
  for (let z = 2.7; z < 4; z += 0.4) joints.push([[-6, 0.108, z], [-1.4, 0.108, z]]);
  drawLines(joints, 0.18, true);
  [-5, -3.2].forEach((x) => slab(x, x + 0.9, 0.1, 0.32, 3.55, 3.8, mat(0x8b7258, { rough: 0.7 }), 0.6));
  current.anchor.set(-6.6, 0.1, 0);

  // Soft landscaping — verges, beds, trees, shrubs
  begin("SF");
  const grass = mat(0xa3a884, { rough: 1, clip: true }), leaf = mat(0x76825d, { rough: 0.9, flat: true }), leaf2 = mat(0x8d9467, { rough: 0.9, flat: true }), bark = mat(0x5c4a3a);
  slab(-6, 6, -0.1, 0.06, -4, -2.3, grass, 0.4, true);
  slab(3.5, 6, -0.1, 0.06, 2.3, 4, grass, 0.4, true);
  add(new THREE.CylinderGeometry(0.75, 0.75, 0.16, 40), grass, [-3.6, 0.12, 3.1], 0.4);
  const tree = (x: number, z: number, s = 1, m = leaf) => {
    add(new THREE.CylinderGeometry(0.04 * s, 0.06 * s, 0.7 * s, 8), bark, [x, 0.35 * s + 0.05, z], 0);
    add(new THREE.IcosahedronGeometry(0.42 * s, 1), m, [x, 0.95 * s + 0.05, z], 0.18);
  };
  [-5.2, -3.4, -1.6, 0.2, 2, 3.8, 5.4].forEach((x, i) => tree(x, -3.2 + (i % 2) * 0.35, 1 + (i % 3) * 0.1, i % 2 ? leaf2 : leaf));
  [-5.3, -2.2].forEach((x, i) => tree(x, 3.15, 1.15, i ? leaf : leaf2));
  tree(-3.6, 3.1, 1.25, leaf2);
  [[4.2, 3.2], [5.2, 2.8], [4.9, 3.6]].forEach(([x, z], i) => tree(x, z, 0.85 + i * 0.1, i % 2 ? leaf : leaf2));
  for (let x = -5.6; x < 6; x += 0.7) add(new THREE.IcosahedronGeometry(0.13, 0), leaf2, [x, 0.14, -2.5], 0);
  current.anchor.set(-6.6, 0.5, 0);

  // Street lighting — poles, arms, lanterns, the cable below, a feeder pillar
  begin("SL");
  const steel = mat(0x2c2e33, { metal: 0.7, rough: 0.35 }), lamp = mat(0xfff4dc, { emissive: 0xffd9a0, rough: 0.3 }), cab = mat(col.SL, { rough: 0.5 });
  const pole = (x: number, z: number, dir: number) => {
    add(new THREE.CylinderGeometry(0.035, 0.05, 2.2, 12), steel, [x, 1.1, z], 0);
    pipe([[x, 2.15, z], [x, 2.3, z + dir * 0.25], [x, 2.32, z + dir * 0.6]], 0.022, steel);
    add(new THREE.BoxGeometry(0.34, 0.06, 0.16), lamp, [x, 2.28, z + dir * 0.62], 0.5);
  };
  const north = [-5.4, -2.9, -0.4, 4.8], south = [-4.2, -1.7, 0.8, 3.3, 5.6];
  north.forEach((x) => pole(x, 1.35, -1)); south.forEach((x) => pole(x, -1.35, 1));
  pipe([[-6, -0.22, 1.42], [6, -0.22, 1.42]], 0.02, cab);
  pipe([[-6, -0.22, -1.42], [6, -0.22, -1.42]], 0.02, cab);
  slab(5.3, 5.7, 0.1, 0.75, 1.75, 2.0, mat(0x6d7178, { metal: 0.3, rough: 0.5 }), 0.6);
  current.anchor.set(-6.6, 1.2, 0);

  // the ground block: soil seen in the section cut
  const soilGroup = new THREE.Group(); scene.add(soilGroup);
  const soilMat = new THREE.MeshStandardMaterial({ color: 0xcfc4b3, roughness: 1, transparent: false, polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 2, clippingPlanes: [cut], side: THREE.DoubleSide });
  const soil = new THREE.Mesh(new THREE.BoxGeometry(12, 1.25, 8), soilMat); soil.position.set(0, -0.745, 0); soil.receiveShadow = true; soilGroup.add(soil);
  const soilEdge = new THREE.LineSegments(new THREE.EdgesGeometry(soil.geometry), new THREE.LineBasicMaterial({ color: INK, transparent: true, opacity: 0.35, clippingPlanes: [cut] }));
  soilEdge.position.copy(soil.position); soilGroup.add(soilEdge);
  // a soft contact shadow under everything
  const shadowTex = (() => { const c = document.createElement("canvas"); c.width = c.height = 128; const g = c.getContext("2d")!;
    const gr = g.createRadialGradient(64, 64, 10, 64, 64, 64); gr.addColorStop(0, "rgba(0,0,0,.28)"); gr.addColorStop(1, "rgba(0,0,0,0)"); g.fillStyle = gr; g.fillRect(0, 0, 128, 128); return new THREE.CanvasTexture(c); })();
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(22, 16), new THREE.MeshBasicMaterial({ map: shadowTex, transparent: true, depthWrite: false }));
  void floor;

  // ------------------------------------------------------------------ state, camera, animation
  let mode: ViewMode = "3d", hovered: string | null = null, spread = 0, plan = 0, cutZ = 0.3;
  const goalPos = new THREE.Vector3(), goalTgt = new THREE.Vector3();
  let animating = false, fovGoal = 30, t0 = 0;
  const fromPos = new THREE.Vector3(), fromTgt = new THREE.Vector3(); let fromFov = 30;
  const startTween = () => { fromPos.copy(camera.position); fromTgt.copy(controls.target); fromFov = camera.fov; t0 = performance.now(); animating = true; };
  // distance that fits a w × h footprint on screen (the legend takes the right ~240 px on wide frames)
  const fitDist = (w: number, h: number, fov: number) => {
    const a = W() / H(), tan = Math.tan(THREE.MathUtils.degToRad(fov / 2)), wide = W() > 900;
    return Math.max(w / (2 * tan * a * (wide ? 0.74 : 0.96)), h / (2 * tan * 0.9));
  };
  const setGoal = () => {
    const wide = W() > 900;
    camera.setViewOffset(W(), H(), wide ? W() * 0.075 * (document.dir === "rtl" || el.closest("[dir=rtl]") ? -1 : 1) : 0, 0, W(), H());
    if (mode === "plan") { goalTgt.set(0, 0, 0); fovGoal = 26; goalPos.set(0, fitDist(12.6, 8.6, 26), 0.004); }
    else if (mode === "layers") { goalTgt.set(0, 3.2, 0); fovGoal = 30; goalPos.set(10.5, 8.2, 12).normalize().multiplyScalar(fitDist(15.5, 13.5, 30)).add(goalTgt); }
    else { goalTgt.set(0, -0.3, 0); fovGoal = 30; goalPos.set(10.5, 7.2, 12).normalize().multiplyScalar(fitDist(15.5, 8.5, 30)).add(goalTgt); }
  };
  const setMode = (m: ViewMode) => {
    mode = m; setGoal(); startTween();
    controls.enableRotate = m !== "plan" && !matchMedia("(pointer:coarse)").matches;
    controls.autoRotate = !still && m !== "plan";
    if (still) { camera.position.copy(goalPos); controls.target.copy(goalTgt); camera.fov = fovGoal; camera.updateProjectionMatrix(); }
  };
  setGoal(); camera.position.copy(goalPos); controls.target.copy(goalTgt); controls.update();
  controls.addEventListener("start", () => { animating = false; });

  const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
  let ptrIn = false, cand: string | null = null, candT = 0;
  const onMove = (e: PointerEvent) => { const b = renderer.domElement.getBoundingClientRect(); ptr.set(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1); ptrIn = true; };
  const onLeave = () => { ptrIn = false; if (hovered) { hovered = null; onHover(null); } };
  renderer.domElement.addEventListener("pointermove", onMove); renderer.domElement.addEventListener("pointerleave", onLeave);

  const ro = new ResizeObserver(() => { renderer.setSize(W(), H()); camera.aspect = W() / H(); camera.updateProjectionMatrix(); setGoal(); startTween(); });
  ro.observe(el);
  let visible = true;
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; }); io.observe(el);

  let raf = 0, last = performance.now(), frameN = 0;
  const v = new THREE.Vector3();
  const tick = (now: number) => {
    raf = requestAnimationFrame(tick);
    if (!visible) return;
    const dt = Math.min(0.25, (now - last) / 1000); last = now;
    const s = still ? 1 : 1 - Math.exp(-dt * 4);
    spread += ((mode === "layers" ? 1 : 0) - spread) * s;
    plan += ((mode === "plan" ? 1 : 0) - plan) * s;
    cutZ += ((mode === "3d" ? 0.25 : 9) - cutZ) * s;
    cut.constant = cutZ;
    for (const m of all) if (m.userData.above) { const hide = cutZ < 5 && m.userData.cz > cutZ; m.visible = !hide; if (m.userData.edge) m.userData.edge.visible = !hide; }
    ORDER.forEach((id, i) => { const b = built[id]; if (b) b.group.position.y = spread * (i * 1.05 + (i < 3 ? 0.4 + (2 - i) * 0.25 : 1.2)); });
    soilGroup.position.y = 0; soilMat.opacity = Math.max(0, 1 - spread * 1.6 - plan * 0.85);
    { const tr = soilMat.opacity < 0.999; if (tr !== soilMat.transparent) { soilMat.transparent = tr; soilMat.depthWrite = !tr; soilMat.needsUpdate = true; } } soilEdge.material.opacity = 0.35 * (1 - spread) * (1 - plan);
    soilGroup.visible = soilMat.opacity > 0.02;
    // hover: raycast (throttled)
    if (ptrIn && frameN++ % 3 === 0) {
      ray.setFromCamera(ptr, camera);
      const hit = ray.intersectObjects(all.filter((m) => m.visible), false).find((h) => {
        const m = (h.object as THREE.Mesh).material as THREE.Material;
        return !(m.clippingPlanes && m.clippingPlanes.length && h.point.z > cut.constant);
      });
      const id = hit ? (hit.object.userData.layer as string) : null;
      if (id !== cand) { cand = id; candT = now; }
      else if (id !== hovered && now - candT > (id ? 450 : 250)) { hovered = id; onHover(id); }
    }
    // fades: isolate the hovered layer; in plan, surfaces become see-through so the utilities read like CAD
    for (const id of ORDER) {
      const b = built[id]; if (!b) continue;
      const iso = hovered && hovered !== id ? 0.12 : 1;
      const surface = id === "RD" || id === "HL" || id === "SF";
      const op = iso * (surface ? 1 - plan * 0.62 : 1);
      b.mats.forEach((m) => {
        m.opacity = Math.abs(op - m.opacity) < 0.004 ? op : m.opacity + (op - m.opacity) * s;
        const tr = m.opacity < 0.999;                       // switch state only when it really changes
        if (tr !== m.transparent) { m.transparent = tr; m.depthWrite = !tr; m.needsUpdate = true; }
      });
      b.lines.forEach((m) => { const target = (m.userData.base as number) * (hovered && hovered !== id ? 0.15 : 1) * (1 + plan * 0.9); m.opacity += (Math.min(1, target) - m.opacity) * s; });
      // layer labels, projected to the screen (Layers view only)
      const lab = labelEls[id];
      if (lab) {
        v.copy(b.anchor).add(b.group.position).project(camera);
        lab.style.transform = `translate(${((v.x + 1) / 2) * W()}px, ${((1 - v.y) / 2) * H()}px) translate(-100%, -50%)`;
        lab.style.opacity = String(Math.max(0, spread - 0.5) * 2 * (hovered && hovered !== id ? 0.35 : 1));
      }
    }
    if (animating) {                                        // time-based, eased: the same at any frame rate
      const u = still ? 1 : Math.min(1, (now - t0) / 1100), e = u < 0.5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
      camera.position.lerpVectors(fromPos, goalPos, e); controls.target.lerpVectors(fromTgt, goalTgt, e);
      camera.fov = fromFov + (fovGoal - fromFov) * e; camera.updateProjectionMatrix();
      if (u >= 1) animating = false;
    }
    controls.update();
    renderer.render(scene, camera);
  };
  raf = requestAnimationFrame(tick);

  return {
    setMode,
    setHover: (id: string | null) => { hovered = id; },
    dispose: () => {
      cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); controls.dispose();
      renderer.domElement.removeEventListener("pointermove", onMove); renderer.domElement.removeEventListener("pointerleave", onLeave);
      scene.traverse((o) => { const m = o as THREE.Mesh; m.geometry?.dispose?.(); const mm = m.material as THREE.Material | THREE.Material[] | undefined; (Array.isArray(mm) ? mm : mm ? [mm] : []).forEach((x) => x.dispose()); });
      renderer.dispose(); renderer.domElement.remove();
    },
  };
}
