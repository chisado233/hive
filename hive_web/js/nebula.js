// 深空背景：分层星点 + 极慢星云漂移（纯装饰，不是业务节点，不拦截交互）
// - Canvas 绘制，与业务 SVG 分层；DPR 限 1.5，粒子数随面积自适应
// - 30–90s 一个变化周期；隐藏标签页/prefers-reduced-motion 时完全暂停
import { reduceMotion } from "./util.js";

const STAR_LAYERS = [
  { density: 0.00008, rMin: 0.4, rMax: 1.0, alpha: 0.5, twinkle: 0 },      // 远层
  { density: 0.000045, rMin: 0.8, rMax: 1.6, alpha: 0.75, twinkle: 0.5 }, // 中层
  { density: 0.000014, rMin: 1.2, rMax: 2.2, alpha: 1.0, twinkle: 1 },    // 近层（少量闪烁）
];
const NEBULAE = [
  { hue: "#14323c", r: 0.44, speed: 90000, phase: 0.0, ax: 0.28, ay: 0.30 },
  { hue: "#16233c", r: 0.36, speed: 65000, phase: 2.1, ax: 0.72, ay: 0.62 },
  { hue: "#3a3050", r: 0.26, speed: 78000, phase: 4.2, ax: 0.58, ay: 0.86 },
];

export class Nebula {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.stars = [];
    this.raf = null;
    this.t0 = performance.now();
    this.running = false;
    this._onResize = () => this._resize();
    this._onVis = () => {
      if (document.hidden) this.stop();
      else this.start();
    };
    window.addEventListener("resize", this._onResize);
    document.addEventListener("visibilitychange", this._onVis);
    this._resize();
    if (!reduceMotion) this.start();
    else this._draw(0);   // 静态一帧
  }

  _resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    if (!w || !h) return;
    this.canvas.width = w * dpr;
    this.canvas.height = h * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = w; this.h = h;
    this._seed();
    if (!this.running) this._draw(performance.now());
  }

  _seed() {
    this.stars = [];
    const area = this.w * this.h;
    let id = 0;
    for (const L of STAR_LAYERS) {
      const n = Math.min(260, Math.round(area * L.density));
      for (let i = 0; i < n; i++) {
        this.stars.push({
          id: id++,
          x: Math.random() * this.w, y: Math.random() * this.h,
          r: L.rMin + Math.random() * (L.rMax - L.rMin),
          alpha: L.alpha * (0.55 + Math.random() * 0.45),
          tw: L.twinkle ? (20 + Math.random() * 40) : 0,   // 闪烁周期 s
          ph: Math.random() * Math.PI * 2,
        });
      }
    }
  }

  start() {
    if (this.running || reduceMotion) return;
    this.running = true;
    const loop = (t) => {
      if (!this.running) return;
      this._draw(t);
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }

  stop() {
    this.running = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = null;
  }

  destroy() {
    this.stop();
    window.removeEventListener("resize", this._onResize);
    document.removeEventListener("visibilitychange", this._onVis);
  }

  _draw(t) {
    const { ctx, w, h } = this;
    if (!w) return;
    const sec = (t - this.t0) / 1000;
    ctx.clearRect(0, 0, w, h);
    // 宇宙底
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, "#0a1119");
    g.addColorStop(0.55, "#080e18");
    g.addColorStop(1, "#060b12");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    // 星云（径向渐变光斑，极慢漂移）
    for (const nb of NEBULAE) {
      const ang = (sec * 1000 / nb.speed) * Math.PI * 2 + nb.phase;
      const cx = (nb.ax + Math.cos(ang) * 0.05) * w;
      const cy = (nb.ay + Math.sin(ang * 0.8) * 0.05) * h;
      const r = nb.r * Math.max(w, h);
      const rg = ctx.createRadialGradient(cx, cy, 0, cx, cy, r);
      rg.addColorStop(0, nb.hue + "55");
      rg.addColorStop(0.5, nb.hue + "22");
      rg.addColorStop(1, "transparent");
      ctx.fillStyle = rg;
      ctx.fillRect(0, 0, w, h);
    }
    // 地平辉光
    const hg = ctx.createLinearGradient(0, h * 0.7, 0, h);
    hg.addColorStop(0, "transparent");
    hg.addColorStop(1, "rgba(24,42,80,0.35)");
    ctx.fillStyle = hg;
    ctx.fillRect(0, h * 0.7, w, h * 0.3);
    // 星点
    for (const st of this.stars) {
      let a = st.alpha;
      if (st.tw) a *= 0.72 + 0.28 * Math.sin((sec / st.tw) * Math.PI * 2 + st.ph);
      ctx.globalAlpha = a;
      ctx.fillStyle = "#cfe2ff";
      ctx.beginPath();
      ctx.arc(st.x, st.y, st.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}
