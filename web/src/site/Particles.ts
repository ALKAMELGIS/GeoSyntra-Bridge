/**
 * A small WebGL2 particle field. Every particle has up to four target positions (one per "shape");
 * the vertex shader blends between them by `stage`, adds a slow drift, lets "stream" particles flow from
 * loose CAD noise (left) to ordered GIS points (right), and pushes particles away from the pointer like a liquid.
 * No simulation on the CPU: one draw call per frame.
 */
export type Shape = Float32Array; // N * 3

const VS = `#version 300 es
precision highp float;
in vec3 aP0; in vec3 aP1; in vec3 aP2; in vec3 aP3; in vec4 aR;
uniform float uStage, uTime, uAspect, uFlow, uPush, uDark, uScale, uRot, uSway;
uniform vec2 uMouse;
out float vA; out vec3 vC;
vec3 rotY(vec3 p, float a){ float c=cos(a), s=sin(a); return vec3(c*p.x+s*p.z, p.y, -s*p.x+c*p.z); }
vec3 rotX(vec3 p, float a){ float c=cos(a), s=sin(a); return vec3(p.x, c*p.y-s*p.z, s*p.y+c*p.z); }
void main(){
  float s0 = clamp(uStage, 0.0, 3.0);
  // stagger: mid-morph each particle runs ahead or behind, so a shape is drawn, not cross-faded
  float s = clamp(s0 + (aR.x - 0.5) * 1.6 * abs(s0 - floor(s0 + 0.5)), 0.0, 3.0);
  float w0 = max(0.0, 1.0-abs(s-0.0)), w1 = max(0.0, 1.0-abs(s-1.0)), w2 = max(0.0, 1.0-abs(s-2.0)), w3 = max(0.0, 1.0-abs(s-3.0));
  // stagger: each particle travels on its own delay, so a morph looks like a swarm, not a cross-fade
  vec3 p = aP0*w0 + aP1*w1 + aP2*w2 + aP3*w3;
  float t = uTime;
  bool stream = aR.w > 0.5;
  if (stream && uFlow > 0.0) {
    float x = mod(p.x + t*0.045*(0.6+aR.x) + 2.4, 4.8) - 2.4;
    float order = smoothstep(-0.2, 1.2, x);                       // 0 = CAD noise, 1 = GIS grid
    vec2 jit = vec2(sin(t*0.7+aR.y*40.0), cos(t*0.6+aR.z*40.0)) * 0.05 * (1.0-order);
    p = vec3(x, mix(p.y + jit.x, floor(p.y*7.0+0.5)/7.0, order), mix(p.z + jit.y, floor(p.z*5.0+0.5)/5.0, order));
  } else {
    p += (uSway > 0.5 ? 0.003 : 0.012)*vec3(sin(t*0.9+aR.x*30.0), cos(t*0.8+aR.y*30.0), sin(t*0.7+aR.z*30.0));
    if (uSway > 0.5) {                                  // the CAD sheet faces you; the model turns slowly
      float k = smoothstep(0.0, 1.0, s0);
      p = rotX(rotY(p, k * (0.55 + 0.45 * sin(t * 0.18))), 0.32 * k);
    } else p = rotX(rotY(p, uRot + t*0.06), 0.35);
  }
  p *= uScale;
  float z = 3.4 - p.z;
  vec2 q = p.xy * (2.2 / z);
  q.x /= uAspect;
  // liquid push away from the pointer, with a little swirl
  vec2 d = vec2((q.x - uMouse.x)*uAspect, q.y - uMouse.y);
  float r2 = dot(d, d);
  float f = uPush * exp(-r2 / 0.035);
  vec2 dir = normalize(d + 1e-5);
  q += vec2(dir.x/uAspect, dir.y) * f * (0.12 + 0.1*aR.x) + vec2(-dir.y/uAspect, dir.x) * f * 0.05 * (aR.y-0.5);
  gl_Position = vec4(q, 0.0, 1.0);
  float size = (1.1 + 2.2*aR.z) * (3.0 / z);
  gl_PointSize = size * (uDark > 0.5 ? 1.0 : 1.15) * min(2.0, max(1.0, uAspect > 1.0 ? 1.0 : 1.0));
  // palette: graphite / bronze / silver on paper; silver / warm white on graphite
  vec3 a = uDark > 0.5 ? vec3(0.86,0.84,0.80) : vec3(0.20,0.18,0.16);
  vec3 b = uDark > 0.5 ? vec3(0.74,0.62,0.48) : vec3(0.52,0.43,0.34);
  vec3 c = uDark > 0.5 ? vec3(0.38,0.70,0.78) : vec3(0.05,0.42,0.49);
  vC = aR.y < 0.55 ? a : (aR.y < 0.93 ? b : c);
  vA = (uDark > 0.5 ? 0.55 : 0.62) * (0.35 + 0.65*aR.x) * clamp(1.6 - z*0.25, 0.25, 1.0);
}`;

const FS = `#version 300 es
precision mediump float;
in float vA; in vec3 vC; out vec4 o;
void main(){
  vec2 c = gl_PointCoord*2.0-1.0;
  float d = dot(c,c);
  if (d > 1.0) discard;
  float a = vA * smoothstep(1.0, 0.2, d);
  o = vec4(vC*a, a);
}`;

// ------------------------------------------------------------------------------- shapes (all ≈ in [-1.4, 1.4])
const rnd = (seed: number) => () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
const gauss = (r: () => number) => Math.sqrt(-2 * Math.log(r() + 1e-9)) * Math.cos(2 * Math.PI * r());

/** A ribbon of filaments wound on a (2,3) torus knot — the bridge. */
export function knot(n: number, seed = 1): Shape {
  const r = rnd(seed), out = new Float32Array(n * 3);
  const C = (t: number) => { const R = 0.62 + 0.24 * Math.cos(3 * t); return [R * Math.cos(2 * t), 0.24 * Math.sin(3 * t) * 1.2, R * Math.sin(2 * t)]; };
  for (let i = 0; i < n; i++) {
    const t = r() * Math.PI * 2, e = 0.002;
    const a = C(t), b = C(t + e);
    const T = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]; const tl = Math.hypot(...T);
    const Tn = T.map((v) => v / tl);
    const up = [0, 1, 0];
    let N = [Tn[1] * up[2] - Tn[2] * up[1], Tn[2] * up[0] - Tn[0] * up[2], Tn[0] * up[1] - Tn[1] * up[0]];
    const nl = Math.hypot(...N) || 1; N = N.map((v) => v / nl);
    const B = [Tn[1] * N[2] - Tn[2] * N[1], Tn[2] * N[0] - Tn[0] * N[2], Tn[0] * N[1] - Tn[1] * N[0]];
    const fil = Math.floor(r() * 46) / 45 - 0.5;                       // filament across the ribbon
    const twist = t * 3 + fil * 0.6;
    const w = 0.26 * fil, th = 0.012 * gauss(r);
    const loose = r() < 0.12 ? 0.08 * gauss(r) : 0;                     // dust leaving the ribbon
    for (let k = 0; k < 3; k++) out[i * 3 + k] = a[k] + (N[k] * Math.cos(twist) + B[k] * Math.sin(twist)) * w + B[k] * th + loose * (r() - 0.5) * 2;
  }
  return out;
}

/** A horizontal stream across the hero: loose on the left, snapped to a grid on the right (done in the shader). */
export function stream(n: number, seed = 2): Shape {
  const r = rnd(seed), out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const x = (r() * 2 - 1) * 2.4;
    const spread = 0.12 + 0.22 * Math.abs(x) / 2.4;
    out[i * 3] = x; out[i * 3 + 1] = gauss(r) * spread * 0.8; out[i * 3 + 2] = gauss(r) * spread * 0.5;
  }
  return out;
}

/** Noise: a loose cloud — the drawing as received. */
export function cloud(n: number, seed = 3): Shape {
  const r = rnd(seed), out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const u = r() * 2 - 1, ph = r() * Math.PI * 2, rad = 0.95 * Math.cbrt(r()) + 0.05 * gauss(r);
    const s = Math.sqrt(1 - u * u);
    out[i * 3] = rad * s * Math.cos(ph) * 1.05; out[i * 3 + 1] = rad * u * 0.95; out[i * 3 + 2] = rad * s * Math.sin(ph);
  }
  return out;
}

/** Layers: four stacked sheets of linework — each CAD layer finding its feature class. */
export function layers(n: number, seed = 4): Shape {
  const r = rnd(seed), out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const L = Math.floor(r() * 4), y = -0.66 + L * 0.44;
    let x: number, z: number;
    if (r() < 0.55) {                                                   // lines drawn on the sheet
      const k = Math.floor(r() * 5), u = r() * 2 - 1;
      const ang = (L * 0.9 + k * 1.3);
      const off = (k - 2) * 0.22;
      x = u * Math.cos(ang) - off * Math.sin(ang); z = u * Math.sin(ang) + off * Math.cos(ang);
    } else { x = r() * 2 - 1; z = r() * 2 - 1; }
    out[i * 3] = Math.max(-1, Math.min(1, x)) * 0.95; out[i * 3 + 1] = y + 0.008 * gauss(r); out[i * 3 + 2] = Math.max(-1, Math.min(1, z)) * 0.95;
  }
  return out;
}

/** Review: one map sheet — a network with junctions, checked on the plane. */
export function network(n: number, seed = 5): Shape {
  const r = rnd(seed), out = new Float32Array(n * 3);
  const nodes: [number, number][] = [];
  for (let i = 0; i < 26; i++) nodes.push([r() * 2 - 1, r() * 2 - 1]);
  const edges: [number, number][] = [];
  nodes.forEach((a, i) => {
    const near = nodes.map((b, j) => [Math.hypot(a[0] - b[0], a[1] - b[1]), j] as [number, number]).sort((p, q) => p[0] - q[0]).slice(1, 3);
    near.forEach(([, j]) => edges.push([i, j]));
  });
  for (let i = 0; i < n; i++) {
    let x: number, z: number;
    const k = r();
    if (k < 0.7) { const [a, b] = edges[Math.floor(r() * edges.length)]; const u = r(); x = nodes[a][0] + (nodes[b][0] - nodes[a][0]) * u; z = nodes[a][1] + (nodes[b][1] - nodes[a][1]) * u; }
    else if (k < 0.85) { const nd = nodes[Math.floor(r() * nodes.length)]; const ph = r() * 6.283, rr = 0.045 * Math.sqrt(r()); x = nd[0] + rr * Math.cos(ph); z = nd[1] + rr * Math.sin(ph); }
    else { x = r() * 2 - 1; z = r() * 2 - 1; }
    out[i * 3] = x * 1.05; out[i * 3 + 1] = z * 0.95; out[i * 3 + 2] = 0.006 * gauss(r) + (k >= 0.85 ? -0.02 : 0);   // faces the viewer: a map sheet
  }
  return out;
}

/** Deliver: the geodatabase — a cylinder of three tables. */
export function database(n: number, seed = 6): Shape {
  const r = rnd(seed), out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const ph = r() * Math.PI * 2, k = r();
    let x: number, y: number, z: number;
    if (k < 0.62) { y = r() * 1.5 - 0.75; x = Math.cos(ph) * 0.72; z = Math.sin(ph) * 0.72; }
    else { const ring = Math.floor(r() * 4); y = -0.75 + ring * 0.5; const rr = ring === 3 ? 0.72 * Math.sqrt(r()) : 0.72; x = Math.cos(ph) * rr; z = Math.sin(ph) * rr; }
    out[i * 3] = x + 0.006 * gauss(r); out[i * 3 + 1] = y; out[i * 3 + 2] = z + 0.006 * gauss(r);
  }
  return out;
}

// ------------------------------------------------------------------------------- engine
export interface FieldOpts { shapes: Shape[]; streamFrom?: number; flow?: boolean; scale?: number; rot?: number; sway?: boolean }

export class ParticleField {
  private gl: WebGL2RenderingContext;
  private prog: WebGLProgram;
  private n: number;
  private u: Record<string, WebGLUniformLocation | null> = {};
  private raf = 0;
  private t0 = performance.now();
  private mouse = [9, 9]; private mouseT = [9, 9]; private push = 0; private pushT = 0;
  stage = 0; stageT = 0;
  dark = false;
  private visible = true;
  private io?: IntersectionObserver;
  private still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

  static supported(): boolean {
    try { return !!document.createElement("canvas").getContext("webgl2"); } catch { return false; }
  }

  constructor(private canvas: HTMLCanvasElement, private o: FieldOpts) {
    const gl = canvas.getContext("webgl2", { premultipliedAlpha: true, antialias: false, alpha: true });
    if (!gl) throw new Error("webgl2");
    this.gl = gl;
    const sh = (type: number, src: string) => { const s = gl.createShader(type)!; gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) || "shader"); return s; };
    const p = gl.createProgram()!;
    gl.attachShader(p, sh(gl.VERTEX_SHADER, VS)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, FS)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p) || "link");
    this.prog = p; gl.useProgram(p);
    const shapes = o.shapes; this.n = shapes[0].length / 3;
    const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    ["aP0", "aP1", "aP2", "aP3"].forEach((name, k) => this.attr(name, shapes[Math.min(k, shapes.length - 1)], 3));
    const R = new Float32Array(this.n * 4); const r = rnd(99);
    for (let i = 0; i < this.n; i++) { R[i * 4] = r(); R[i * 4 + 1] = r(); R[i * 4 + 2] = r() * r(); R[i * 4 + 3] = o.streamFrom !== undefined && i >= o.streamFrom ? 1 : 0; }
    this.attr("aR", R, 4);
    for (const k of ["uStage", "uTime", "uAspect", "uFlow", "uPush", "uDark", "uScale", "uRot", "uSway", "uMouse"]) this.u[k] = gl.getUniformLocation(p, k);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    canvas.addEventListener("pointermove", this.onMove); canvas.addEventListener("pointerleave", this.onLeave);
    window.addEventListener("resize", this.resize);
    this.io = new IntersectionObserver(([e]) => { this.visible = e.isIntersecting; if (this.visible) this.loop(); });
    this.io.observe(canvas);
    this.resize(); this.loop();
  }

  private attr(name: string, data: Float32Array, size: number) {
    const gl = this.gl, loc = gl.getAttribLocation(this.prog, name);
    if (loc < 0) return;
    const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
  }

  private onMove = (e: PointerEvent) => {
    const b = this.canvas.getBoundingClientRect();
    this.mouseT = [((e.clientX - b.left) / b.width) * 2 - 1, 1 - ((e.clientY - b.top) / b.height) * 2];
    if (this.mouse[0] > 5) this.mouse = [...this.mouseT];
    this.pushT = 1;
  };
  private onLeave = () => { this.pushT = 0; };

  resize = () => {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    this.canvas.width = Math.max(1, Math.round(w * dpr)); this.canvas.height = Math.max(1, Math.round(h * dpr));
    this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    if (this.still) this.frame();
  };

  private loop = () => {
    cancelAnimationFrame(this.raf);
    if (!this.visible) return;
    this.frame();
    if (!this.still) this.raf = requestAnimationFrame(this.loop);
  };

  private frame() {
    const gl = this.gl, u = this.u;
    const k = 0.08;
    this.mouse = [this.mouse[0] + (this.mouseT[0] - this.mouse[0]) * k, this.mouse[1] + (this.mouseT[1] - this.mouse[1]) * k];
    this.push += (this.pushT - this.push) * 0.05;
    this.stage += (this.stageT - this.stage) * (this.still ? 1 : 0.06);
    const aspect = this.canvas.width / this.canvas.height;
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform1f(u.uStage, this.stage);
    gl.uniform1f(u.uTime, this.still ? 0 : (performance.now() - this.t0) / 1000);
    gl.uniform1f(u.uAspect, aspect);
    gl.uniform1f(u.uFlow, this.o.flow ? 1 : 0);
    gl.uniform1f(u.uPush, this.push);
    gl.uniform1f(u.uDark, this.dark ? 1 : 0);
    gl.uniform1f(u.uScale, (this.o.scale ?? 1) * (aspect < 1 ? Math.max(0.55, aspect * 1.1) : 1));
    gl.uniform1f(u.uRot, this.o.rot ?? 0);
    gl.uniform1f(u.uSway, this.o.sway ? 1 : 0);
    gl.uniform2f(u.uMouse, this.mouse[0], this.mouse[1]);
    gl.drawArrays(gl.POINTS, 0, this.n);
  }

  redraw() { if (this.still) this.frame(); }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.io?.disconnect();
    this.canvas.removeEventListener("pointermove", this.onMove); this.canvas.removeEventListener("pointerleave", this.onLeave);
    window.removeEventListener("resize", this.resize);
    this.gl.getExtension("WEBGL_lose_context")?.loseContext();
  }
}

/** Hero: the knot, with the stream running through it. */
export function heroShapes(mobile: boolean): { shapes: Shape[]; streamFrom: number } {
  const nk = mobile ? 9000 : 20000, ns = mobile ? 5000 : 11000;
  const k = knot(nk), s = stream(ns);
  const all = new Float32Array((nk + ns) * 3); all.set(k); all.set(s, nk * 3);
  return { shapes: [all], streamFrom: nk };
}

// ------------------------------------------------------------------------------- building (CAD → BIM)
type V3 = [number, number, number];
type Seg = [V3, V3];
const BW = 0.62, BD = 0.42, FL = 6, H0 = -0.78, FH = 0.26;            // half width, half depth, floors, base, floor height
const GX = [-BW, -BW / 3, BW / 3, BW], GZ = [-BD, 0, BD];               // structural grid

/** Points along segments (by length), a little jitter, a little dust. */
function sample(segs: Seg[], n: number, seed: number, jit = 0.004, dust = 0.04): Shape {
  const r = rnd(seed), out = new Float32Array(n * 3);
  const len = segs.map(([a, b]) => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]));
  const cum: number[] = []; let tot = 0; len.forEach((l) => { tot += l; cum.push(tot); });
  for (let i = 0; i < n; i++) {
    let x: number, y: number, z: number;
    if (r() < dust) { x = (r() * 2 - 1) * 1.1; y = (r() * 2 - 1) * 0.9; z = (r() * 2 - 1) * 0.8; }
    else {
      const t = r() * tot; let lo = 0, hi = cum.length - 1;
      while (lo < hi) { const m = (lo + hi) >> 1; if (cum[m] < t) lo = m + 1; else hi = m; }
      const [a, b] = segs[lo], u = r();
      x = a[0] + (b[0] - a[0]) * u + jit * gauss(r); y = a[1] + (b[1] - a[1]) * u + jit * gauss(r); z = a[2] + (b[2] - a[2]) * u + jit * gauss(r);
    }
    out[i * 3] = x; out[i * 3 + 1] = y; out[i * 3 + 2] = z;
  }
  return out;
}
const rect = (x0: number, z0: number, x1: number, z1: number, y: number): Seg[] =>
  [[[x0, y, z0], [x1, y, z0]], [[x1, y, z0], [x1, y, z1]], [[x1, y, z1], [x0, y, z1]], [[x0, y, z1], [x0, y, z0]]];
const arc = (cx: number, cz: number, rad: number, a0: number, a1: number, y: number, k = 10): Seg[] =>
  Array.from({ length: k }, (_, i) => { const p = a0 + (a1 - a0) * i / k, q = a0 + (a1 - a0) * (i + 1) / k;
    return [[cx + rad * Math.cos(p), y, cz + rad * Math.sin(p)], [cx + rad * Math.cos(q), y, cz + rad * Math.sin(q)]] as Seg; });
/** One floor plan in the xz plane: double walls, partitions, doors, columns. */
function floorPlan(y: number, s = 1, detail = true): Seg[] {
  const w = BW * s, d = BD * s, t = 0.035 * s, out: Seg[] = [];
  out.push(...rect(-w, -d, w, d, y), ...rect(-w + t, -d + t, w - t, d - t, y));
  if (!detail) return out;
  const px = GX.map((g) => g * s), pz = GZ.map((g) => g * s);
  out.push([[px[1], y, -d], [px[1], y, pz[1] - 0.06 * s]], [[px[2], y, pz[1] + 0.06 * s], [px[2], y, d]],
           [[-w, y, pz[1]], [px[1] - 0.08 * s, y, pz[1]]], [[px[2] + 0.08 * s, y, pz[1]], [w, y, pz[1]]],
           [[px[1], y, pz[1] + 0.12 * s], [px[1], y, d]]);
  out.push(...arc(px[1], pz[1] - 0.06 * s, 0.1 * s, 0, Math.PI / 2, y), ...arc(px[2], pz[1] + 0.06 * s, 0.1 * s, Math.PI, 1.5 * Math.PI, y));
  out.push(...rect(-0.09 * s, -0.12 * s, 0.09 * s, 0.12 * s, y));                                   // core
  for (let k = 0; k < 4; k++) out.push([[-0.09 * s, y, -0.12 * s + k * 0.06 * s], [0.09 * s, y, -0.12 * s + k * 0.06 * s]]); // stair
  px.forEach((x) => pz.forEach((z) => out.push(...rect(x - 0.025 * s, z - 0.025 * s, x + 0.025 * s, z + 0.025 * s, y))));
  return out;
}
const toXY = (segs: Seg[]): Seg[] => segs.map(([a, b]) => [[a[0], -a[2], 0], [b[0], -b[2], 0]]); // lay a plan on the sheet

/** 01 Upload — the CAD sheet: plan, grid lines with bubbles, dimensions, title block. */
export function cadSheet(n: number, seed = 11): Shape {
  const S = 1.45, segs: Seg[] = toXY(floorPlan(0, S));
  const ext = 0.2;
  GX.forEach((g) => { const x = g * S; segs.push([[x, BD * S + ext, 0], [x, -BD * S - ext * 0.4, 0]]);
    segs.push(...toXY(arc(x, -(BD * S + ext + 0.05), 0.05, 0, 2 * Math.PI, 0, 12))); });
  GZ.forEach((g) => { const yv = -g * S; segs.push([[-BW * S - ext, yv, 0], [BW * S + ext * 0.4, yv, 0]]);
    segs.push(...toXY(arc(-(BW * S + ext + 0.05), -yv, 0.05, 0, 2 * Math.PI, 0, 12))); });
  const dy = -BD * S - 0.12;                                                                          // dimension line
  segs.push([[-BW * S, dy, 0], [BW * S, dy, 0]]);
  GX.forEach((g) => segs.push([[g * S - 0.02, dy - 0.02, 0], [g * S + 0.02, dy + 0.02, 0]]));
  segs.push(...toXY(rect(BW * S - 0.38, BD * S + 0.12, BW * S + 0.05, BD * S + 0.3, 0)));           // title block
  segs.push([[BW * S - 0.38, -(BD * S + 0.2), 0], [BW * S + 0.05, -(BD * S + 0.2), 0]]);
  return sample(segs, n, seed, 0.0018, 0.02);
}

/** 02 Layers — the drawing exploded into floors, each a CAD layer. */
export function explodedFloors(n: number, seed = 12): Shape {
  const segs: Seg[] = [];
  for (let f = 0; f < 5; f++) segs.push(...floorPlan(-0.9 + f * 0.43, 1.05, true));
  GX.forEach((x) => GZ.forEach((z) => segs.push([[x * 1.05, -0.9, z * 1.05], [x * 1.05, -0.9 + 4 * 0.43, z * 1.05]])));  // thin guides
  return sample(segs, n, seed, 0.0018, 0.02);
}

/** 03 Review — the BIM model's structure: columns, beams, slabs, core. */
export function bimFrame(n: number, seed = 13): Shape {
  const segs: Seg[] = [], top = H0 + FL * FH;
  GX.forEach((x) => GZ.forEach((z) => { segs.push([[x, H0, z], [x, top, z]], [[x, H0, z], [x, top, z]]); }));   // columns (weighted)
  for (let f = 0; f <= FL; f++) {
    const y = H0 + f * FH;
    GZ.forEach((z) => segs.push([[-BW, y, z], [BW, y, z]]));
    GX.forEach((x) => segs.push([[x, y, -BD], [x, y, BD]]));
  }
  segs.push(...rect(-0.09, -0.12, 0.09, 0.12, H0).map(([a]) => [a, [a[0], top + 0.08, a[2]]] as Seg));               // core
  segs.push(...rect(-0.09, -0.12, 0.09, 0.12, top + 0.08));
  return sample(segs, n, seed, 0.0018, 0.02);
}

/** 04 Deliver — the finished building: curtain wall, floors, roof, plinth. */
export function building(n: number, seed = 14): Shape {
  const segs: Seg[] = [], top = H0 + FL * FH;
  for (let f = 0; f <= FL; f++) segs.push(...rect(-BW, -BD, BW, BD, H0 + f * FH));
  const mull = (a: V3, b: V3, k: number) => { for (let i = 0; i <= k; i++) { const u = i / k;
    const x = a[0] + (b[0] - a[0]) * u, z = a[2] + (b[2] - a[2]) * u; segs.push([[x, H0, z], [x, top, z]]); } };
  mull([-BW, 0, -BD], [BW, 0, -BD], 12); mull([-BW, 0, BD], [BW, 0, BD], 12); mull([-BW, 0, -BD], [-BW, 0, BD], 8); mull([BW, 0, -BD], [BW, 0, BD], 8);
  segs.push(...rect(-0.2, -0.16, 0.2, 0.16, top + 0.1), ...rect(-0.2, -0.16, 0.2, 0.16, top).map(([a]) => [a, [a[0], top + 0.1, a[2]]] as Seg)); // roof plant
  segs.push(...rect(-BW - 0.12, -BD - 0.12, BW + 0.12, BD + 0.12, H0 - 0.02));                                       // plinth
  return sample(segs, n, seed, 0.0016, 0.015);
}

export function approachShapes(mobile: boolean): Shape[] {
  const n = mobile ? 12000 : 26000;
  return [cadSheet(n), explodedFloors(n), bimFrame(n), building(n)];
}
