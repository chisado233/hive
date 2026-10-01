// 蜂群星图：SVG 渲染，节点=Agent(圆)/资产库(方)/人(小人)/端口(菱形)
// 边=订阅结构 / 任务流(实际方向) / 回调 / 声明的资产读写
import { store, select, projectAgents, refLabel } from "./store.js";
import { el, lsGet, lsSet, lsDel, refKind, refId, reduceMotion } from "./util.js";

const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

const LINE_ORDER = ["coordinator", "research", "skill-test", "evolution", "production"];
const LINE_LABEL = {
  coordinator: "项目负责人", research: "研究节点", "skill-test": "Skill 测试入库线",
  evolution: "反馈进化线", production: "正式生产线", other: "其他节点",
};
const ROLE_ORDER = { coordinator: 0, researcher: 0, maker: 1, reviewer: 2 };

export class Graph {
  constructor(svg, onSelect) {
    this.svg = svg;
    this.onSelect = onSelect;
    this.view = { x: 0, y: 0, k: 1 };
    this.nodes = new Map();     // id -> {id,kind,x,y,data,el}
    this.edges = [];            // 计算后的边
    this.positions = {};        // 持久化位置
    this.dragNode = null;
    this.panning = null;
    this.regionLabels = [];
    this._buildLayers();
    this._bindEvents();
  }

  _buildLayers() {
    this.svg.textContent = "";
    this.vp = s("g", { class: "viewport" });
    this.gRegions = s("g"); this.gEdges = s("g");
    this.gNodes = s("g"); this.gFx = s("g");
    this.vp.append(this.gRegions, this.gEdges, this.gNodes, this.gFx);
    this.svg.append(this.vp);
    const defs = s("defs");
    defs.innerHTML = `
      <marker id="arr-cyan" viewBox="0 0 10 10" refX="9" refY="5"
        markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,9 z" fill="#7addf0"></path></marker>
      <marker id="arr-dim" viewBox="0 0 10 10" refX="9" refY="5"
        markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,9 z" fill="#28406b"></path></marker>
      <marker id="arr-amber" viewBox="0 0 10 10" refX="9" refY="5"
        markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,9 z" fill="#e9bf72"></path></marker>
      <radialGradient id="star-core" cx="42%" cy="40%" r="72%">
        <stop offset="0%" stop-color="#1d2c3d"></stop>
        <stop offset="62%" stop-color="#121f2e"></stop>
        <stop offset="100%" stop-color="#0d1622"></stop>
      </radialGradient>
      <radialGradient id="star-halo">
        <stop offset="0%" stop-color="rgba(122,221,240,0.20)"></stop>
        <stop offset="55%" stop-color="rgba(122,221,240,0.05)"></stop>
        <stop offset="100%" stop-color="transparent"></stop>
      </radialGradient>`;
    this.svg.append(defs);
  }

  layoutKey() {
    const subj = store.me ? store.me.subject_id : "anon";
    return `hive.layout.${subj}.${store.currentProject || "all"}`;
  }

  // ---------------- 布局 ----------------
  loadPositions() { this.positions = lsGet(this.layoutKey(), {}); }
  savePositions() { lsSet(this.layoutKey(), this.positions); }
  resetLayout() {
    lsDel(this.layoutKey());
    this.positions = {};
    this.update(true);
    this.fit();
  }

  defaultPositions() {
    // 确定性默认布局：按 line 分区成行，行内按 role/domain 排序
    const pos = {};
    const agents = projectAgents(store.currentProject);
    const lines = new Map();
    for (const a of agents) {
      const line = a.line || "other";
      if (!lines.has(line)) lines.set(line, []);
      lines.get(line).push(a);
    }
    const orderedLines = [...lines.keys()].sort((x, y) => {
      const ix = LINE_ORDER.indexOf(x), iy = LINE_ORDER.indexOf(y);
      return (ix < 0 ? 99 : ix) - (iy < 0 ? 99 : iy) || x.localeCompare(y);
    });
    const CX = 720, X_GAP = 168, Y_GAP = 158, Y0 = 120;
    let y = Y0;
    for (const line of orderedLines) {
      const group = lines.get(line).sort((a, b) =>
        (ROLE_ORDER[a.role] ?? 1) - (ROLE_ORDER[b.role] ?? 1) ||
        String(a.domain).localeCompare(String(b.domain)) ||
        String(a.agent_key).localeCompare(String(b.agent_key)));
      // 制作/审核按领域配对的线路：maker,reviewer 交替相邻
      const n = group.length;
      const w = (n - 1) * X_GAP;
      group.forEach((a, i) => {
        pos["agent:" + a.agent_id] = { x: CX - w / 2 + i * X_GAP, y };
      });
      this.regionLabels.push({ text: LINE_LABEL[line] || line, x: CX, y: y - 74 });
      y += Y_GAP;
    }
    // 资产库：右侧；人类：底部中央
    const proj = store.projects.find(p => p.key === store.currentProject);
    if (proj && proj.has_assets)
      pos["asset:" + proj.key] = { x: CX + Math.max(560, (Math.max(...[...lines.values()].map(g => g.length), 2) - 1) * X_GAP / 2 + 190), y: Y0 + Y_GAP * 1.6 };
    pos["human"] = { x: CX, y: y + 8 };
    return pos;
  }

  // ---------------- 数据 → 图模型 ----------------
  buildModel() {
    const agents = projectAgents(store.currentProject);
    const nodes = new Map();
    for (const a of agents) nodes.set("agent:" + a.agent_id, {
      id: "agent:" + a.agent_id, kind: "agent", data: a,
    });
    const proj = store.projects.find(p => p.key === store.currentProject);
    if (proj && proj.has_assets)
      nodes.set("asset:" + proj.key, { id: "asset:" + proj.key, kind: "asset", data: proj });
    nodes.set("human", { id: "human", kind: "human", data: store.human });

    // 任务流边（当前任务视图）
    const edges = [];
    if (store.edgeMode === "flows") {
      const agg = new Map(); // from|to|cls -> {count,tasks,states}
      const addFlow = (from, to, cls, t) => {
        if (!from || !to) return;
        const key = from + "|" + to + "|" + cls;
        if (!agg.has(key)) agg.set(key, { from, to, cls, tasks: [], states: new Set() });
        const e = agg.get(key);
        e.tasks.push(t); e.states.add(t.state);
      };
      const nodeForRef = (ref) => {
        if (!ref) return null;
        const kind = refKind(ref), id = refId(ref);
        if (kind === "agent") return nodes.has("agent:" + id) ? "agent:" + id : null;
        if (kind === "port") {
          const p = store.ports.get(id);
          if (!p) return null;
          if (p.handler === "human") return "human";
          return "port:" + id;   // 端口菱形（仅队列停留时显示）
        }
        if (kind === "owner" || kind === "human") return "human";
        return null;
      };
      for (const t of store.flows.values()) {
        const srcRef = (t.source && t.source.subject) || null;
        const from = nodeForRef(srcRef);
        let to = null;
        if (t.state === "claimed" && t.owner_subject)
          to = nodes.has("agent:" + t.owner_subject) ? "agent:" + t.owner_subject : null;
        if (!to) to = nodeForRef(t.target);
        // 终端任务画回调方向（责任回到回调目标）；在途任务画投递/领取方向
        if (["done", "fail", "trash"].includes(t.state)) {
          const cb = t.callback && t.callback.target;
          const cbNode = nodeForRef(cb);
          if (cb && cb !== "none" && cbNode && from)
            addFlow(to || from, cbNode, "callback", t);
          else if (from && to) addFlow(from, to, "flow", t);
        } else if (from && to) {
          addFlow(from, to, "flow", t);
        }
        // 父→子 血缘在任务详情中呈现，不在默认星图画线，避免毛线团
      }
      // 去掉自环（如 owner 发往人工端口：人→人无意义，待办已在节点徽标）
      for (const [k, e] of [...agg.entries()])
        if (e.from === e.to) agg.delete(k);
      // 队列端口节点仅在真有任务压在上面时出现
      for (const e of agg.values()) {
        for (const end of [e.from, e.to]) {
          if (end && end.startsWith("port:") && !nodes.has(end)) {
            const p = store.ports.get(refId(end));
            if (p) nodes.set(end, { id: end, kind: "port", data: p });
          }
        }
        edges.push(e);
      }
    } else {
      // 结构关系：订阅 + 声明的资产读取
      for (const sub of store.subscriptions) {
        if (!sub.enabled) continue;
        const aid = "agent:" + sub.agent_id;
        if (!nodes.has(aid)) continue;
        const p = store.ports.get(sub.port_id);
        if (!p) continue;
        if (p.handler === "human") continue;   // 人工端口订阅不画（语义不同）
        const pid = "port:" + sub.port_id;
        if (!nodes.has(pid)) nodes.set(pid, { id: pid, kind: "port", data: p });
        edges.push({ from: aid, to: pid, cls: "sub", tasks: [], states: new Set() });
      }
      if (proj && proj.has_assets) {
        for (const a of agents) {
          if (a.declared_asset_read)
            edges.push({ from: "agent:" + a.agent_id, to: "asset:" + proj.key,
                         cls: "asset-read", tasks: [], states: new Set() });
        }
      }
    }

    // 位置：已保存优先，新节点用默认；端口菱形贴在订阅者右上方
    this.regionLabels = [];
    const def = this.defaultPositions();
    for (const [id, node] of nodes) {
      let p = this.positions[id] || def[id];
      if (!p && node.kind === "port") {
        const subEdge = edges.find(e => e.to === id && nodes.has(e.from));
        const anchor = subEdge ? nodes.get(subEdge.from) : null;
        const ap = anchor && (this.positions[anchor.id] || def[anchor.id]);
        p = ap ? { x: ap.x + 58, y: ap.y - 58 } :
          { x: 200 + Math.random() * 600, y: 160 + Math.random() * 400 };
      }
      if (!p) p = { x: 200 + Math.random() * 600, y: 160 + Math.random() * 400 };
      node.x = p.x; node.y = p.y;
      if (!this.positions[id]) this.positions[id] = { x: p.x, y: p.y };
    }
    return { nodes, edges };
  }

  // ---------------- 渲染 ----------------
  update(fresh = false) {
    if (!store.me) return;
    if (fresh) this.loadPositions();
    const { nodes, edges } = this.buildModel();
    // 保留节点 DOM 身份：按 id 更新/新增/删除，位置不跳动
    for (const [id, node] of nodes) {
      if (!this.nodes.has(id)) {
        node.el = this._renderNode(node);
        this.gNodes.append(node.el);
      } else {
        node.el = this.nodes.get(id).el;
        this._updateNode(node);
      }
      node.el.setAttribute("transform", `translate(${node.x},${node.y})`);
      this.nodes.set(id, node);
    }
    for (const [id, node] of [...this.nodes]) {
      if (!nodes.has(id)) { node.el.remove(); this.nodes.delete(id); }
    }
    this._renderEdges(edges);
    this._renderRegions();
    this.applySelectionDim();
  }

  _renderRegions() {
    this.gRegions.textContent = "";
    for (const r of this.regionLabels) {
      const t = s("text", { class: "region-label", x: r.x, y: r.y, "text-anchor": "middle" });
      t.textContent = r.text;
      this.gRegions.append(t);
    }
  }

  _nodeColors(node) {
    if (node.kind === "human") return { ring: "#d8bb86" };
    if (node.kind === "asset") return { ring: "#b3a9cf" };
    if (node.kind === "port") return { ring: "#3a4a63" };
    const a = node.data;
    if (a.lifecycle === "paused") return { ring: "#7c8798" };
    if (a.lifecycle === "retiring") return { ring: "#d8a086" };
    if (a.instance_count > 0) {
      if (a.observation === "busy") return { ring: "#9fced0" };
      if (a.observation === "idle") return { ring: "#93c9a8" };
      return { ring: "#d8bb86" };   // live 但观测未知
    }
    return { ring: "#57637a" };     // 无实例：低亮
  }

  _shortName(a) {
    const n = a.name || "";
    const i = n.indexOf("·");
    return (i >= 0 ? n.slice(i + 1) : n).trim() || n;
  }

  _renderNode(node) {
    const g = s("g", { class: "node", "data-id": node.id });
    g.addEventListener("pointerdown", (e) => this._nodeDown(e, node));
    g.addEventListener("click", (e) => {
      if (this._dragged) return;
      e.stopPropagation();
      this.onSelect({ kind: node.kind, id: node.id });
    });
    const c = this._nodeColors(node);
    if (node.kind === "agent") {
      // 恒星：光晕底层 + 核心径向渐变 + 外环
      g.append(s("circle", { class: "n-halo", r: 34,
        fill: "url(#star-halo)", stroke: "none" }));
      const ring = s("circle", { class: "n-ring", r: 24, stroke: c.ring });
      if (node.data.instance_count === 0)
        ring.setAttribute("stroke-dasharray", "3 6");
      g.append(ring);
      g.append(s("circle", { class: "n-core", r: 19, fill: "url(#star-core)" }));
      const glyph = s("text", { class: "n-glyph", y: 1 });
      glyph.textContent = this._shortName(node.data).slice(0, 2);
      g.append(glyph);
      const label = s("text", { class: "n-label", y: 42 });
      label.textContent = this._shortName(node.data);
      g.append(label);
      const sub = s("text", { class: "n-sub", y: 56 });
      sub.textContent = [node.data.domain, node.data.role].filter(Boolean).join(" · ");
      g.append(sub);
    } else if (node.kind === "asset") {
      g.append(s("rect", { class: "n-ring", x: -26, y: -26, width: 52, height: 52,
        rx: 11, stroke: c.ring }));
      g.append(s("rect", { class: "n-core", x: -20, y: -20, width: 40, height: 40,
        rx: 8, fill: "url(#star-core)" }));
      const glyph = s("text", { class: "n-glyph", y: 1 });
      glyph.textContent = "资";
      g.append(glyph);
      const label = s("text", { class: "n-label", y: 44 });
      label.textContent = "共享资产库";
      g.append(label);
    } else if (node.kind === "human") {
      g.append(s("circle", { class: "n-halo", r: 34, fill: "url(#star-halo)" }));
      g.append(s("circle", { class: "n-ring", r: 24, stroke: c.ring }));
      g.append(s("circle", { class: "n-core", r: 19, fill: "url(#star-core)" }));
      const glyph = s("text", { class: "n-glyph", y: 1, style: "font-size:15px" });
      glyph.textContent = "👤";
      g.append(glyph);
      const label = s("text", { class: "n-label", y: 42 });
      label.textContent = "人类决策台";
      g.append(label);
    } else if (node.kind === "port") {
      const d = s("path", { class: "n-core",
        d: "M0,-10 L10,0 L0,10 L-10,0 Z", fill: "#0c1529", stroke: c.ring });
      g.append(d);
      const label = s("text", { class: "n-sub", y: 23 });
      label.textContent = node.data.route || node.data.name || "";
      g.append(label);
    }
    this._updateNode(node, g);
    return g;
  }

  _updateNode(node, g = node.el) {
    if (!g) return;
    // 状态徽标：进行中任务数 / 人类待处理数
    g.querySelectorAll(".n-badge,.n-badge-bg").forEach(n => n.remove());
    let count = 0, red = false;
    if (node.kind === "agent")
      count = (node.data.responsibility?.claimed_count || 0);
    if (node.kind === "human") { count = store.human.pending; red = count > 0; }
    if (count > 0) {
      const w = count > 9 ? 13 : 9;
      g.append(s("circle", { class: "n-badge-bg" + (red ? " red" : ""),
        cx: 20, cy: -20, r: w }));
      const t = s("text", { class: "n-badge", x: 20, y: -19 });
      t.textContent = count > 99 ? "99" : count;
      g.append(t);
    }
    if (node.kind === "agent") {
      const c = this._nodeColors(node);
      const ring = g.querySelector(".n-ring");
      if (ring) {
        ring.setAttribute("stroke", c.ring);
        if (node.data.instance_count === 0) ring.setAttribute("stroke-dasharray", "3 6");
        else ring.removeAttribute("stroke-dasharray");
      }
      // 呼吸光晕：仅真实 working（observation==='busy'）；unknown 不伪装 idle
      const halo = g.querySelector(".n-halo");
      if (halo)
        halo.classList.toggle("breathing",
          node.data.observation === "busy" && node.data.instance_count > 0);
    }
  }

  _edgePath(from, to, idx) {
    const dx = to.x - from.x, dy = to.y - from.y;
    const mx = (from.x + to.x) / 2, my = (from.y + to.y) / 2;
    const len = Math.hypot(dx, dy) || 1;
    const off = idx * 26;                       // 多边分离
    const nx = -dy / len * off, ny = dx / len * off;
    // 端点缩到节点半径外
    const r1 = from.kind === "agent" || from.kind === "human" ? 28 :
      from.kind === "asset" ? 30 : 12;
    const r2 = to.kind === "agent" || to.kind === "human" ? 28 :
      to.kind === "asset" ? 30 : 12;
    const sx = from.x + dx / len * r1, sy = from.y + dy / len * r1;
    const ex = to.x - dx / len * r2, ey = to.y - dy / len * r2;
    return { d: `M${sx},${sy} Q${mx + nx},${my + ny} ${ex},${ey}`,
             lx: mx + nx * 1.4, ly: my + ny * 1.4 };
  }

  _edgeStyle(cls, states) {
    if (cls === "sub") return { stroke: "#3a4350", w: 1.1, dash: null, marker: "arr-dim" };
    if (cls === "asset-read") return { stroke: "#8f83c9", w: 1, dash: "3 6", marker: "arr-dim" };
    if (cls === "callback") {
      const failed = states.has("fail");
      return { stroke: failed ? "#e9a09b" : "#93c9a8", w: 1.4, dash: "6 4",
               marker: failed ? "arr-dim" : "arr-dim" };
    }
    // flow
    if (states.has("claimed")) return { stroke: "#d8bb86", w: 1.8, dash: null, marker: "arr-amber" };
    return { stroke: "#9fced0", w: 1.6, dash: null, marker: "arr-cyan" };
  }

  _renderEdges(edges) {
    this.edges = edges;
    this.gEdges.textContent = "";
    const pairCount = new Map();
    for (const e of edges) {
      const from = this.nodes.get(e.from), to = this.nodes.get(e.to);
      if (!from || !to) continue;
      const pk = [e.from, e.to].sort().join("|");
      const idx = pairCount.get(pk) || 0;
      pairCount.set(pk, idx + 1);
      const { d, lx, ly } = this._edgePath(from, to, idx);
      const st = this._edgeStyle(e.cls, e.states);
      const g = s("g", { class: "edge", "data-from": e.from, "data-to": e.to });
      g.append(s("path", { class: "hit", d }));
      const vis = s("path", {
        class: "vis", d, stroke: st.stroke, "stroke-width": st.w,
        "stroke-dasharray": st.dash, "marker-end": `url(#${st.marker})`,
      });
      g.append(vis);
      if (e.tasks.length) {
        const label = s("text", { class: "edge-label edge-count", x: lx, y: ly });
        label.textContent = e.cls === "callback"
          ? `回执 ×${e.tasks.length}` : `${e.tasks.length} 件任务`;
        g.append(label);
        g.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this.onSelect({ kind: "edge", tasks: e.tasks.map(t => t.task_id),
                          cls: e.cls, from: e.from, to: e.to });
        });
      } else {
        const label = s("text", { class: "edge-label", x: lx, y: ly });
        label.textContent = e.cls === "sub" ? "订阅" :
          e.cls === "asset-read" ? "声明读取" : "";
        g.append(label);
        g.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this.onSelect({ kind: "port", id: e.to.startsWith("port:") ? e.to : e.from });
        });
      }
      this.gEdges.append(g);
      e.el = g;
    }
  }

  // ---------------- 选中高亮 ----------------
  applySelectionDim() {
    const sel = store.selection;
    const connected = new Set();
    if (sel && (sel.kind === "agent" || sel.kind === "human" || sel.kind === "asset")) {
      connected.add(sel.id);
      for (const e of this.edges) {
        if (e.from === sel.id) connected.add(e.to);
        if (e.to === sel.id) connected.add(e.from);
      }
    }
    for (const [id, node] of this.nodes) {
      node.el.classList.toggle("selected", !!sel && sel.id === id);
      node.el.classList.toggle("dimmed",
        !!sel && connected.size > 0 && !connected.has(id));
    }
    for (const e of this.edges) {
      if (!e.el) continue;
      const related = !sel || connected.size === 0 ||
        (connected.has(e.from) && connected.has(e.to));
      e.el.classList.toggle("dimmed", !related);
    }
  }

  // ---------------- 交互 ----------------
  _bindEvents() {
    this.svg.addEventListener("pointerdown", (e) => {
      if (e.target === this.svg || e.target.closest(".region-label")) {
        this.panning = { x: e.clientX, y: e.clientY,
                         vx: this.view.x, vy: this.view.y };
        this.svg.classList.add("dragging");
      }
    });
    window.addEventListener("pointermove", (e) => {
      if (this.dragNode) {
        const pt = this._toWorld(e.clientX, e.clientY);
        this.dragNode.x = pt.x - this.dragOff.dx;
        this.dragNode.y = pt.y - this.dragOff.dy;
        this.dragNode.el.setAttribute("transform",
          `translate(${this.dragNode.x},${this.dragNode.y})`);
        this._dragged = true;
        this._renderEdges(this.edges);
        this.applySelectionDim();
      } else if (this.panning) {
        this.view.x = this.panning.vx + (e.clientX - this.panning.x);
        this.view.y = this.panning.vy + (e.clientY - this.panning.y);
        this._applyView();
      }
    });
    window.addEventListener("pointerup", () => {
      if (this.dragNode) {
        this.positions[this.dragNode.id] = { x: this.dragNode.x, y: this.dragNode.y };
        this.savePositions();
        setTimeout(() => this._dragged = false, 0);
      }
      this.dragNode = null;
      this.panning = null;
      this.svg.classList.remove("dragging");
    });
    this.svg.addEventListener("wheel", (e) => {
      e.preventDefault();
      const k0 = this.view.k;
      const k = Math.min(2.6, Math.max(0.32, k0 * (e.deltaY < 0 ? 1.12 : 0.89)));
      const rect = this.svg.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      this.view.x = mx - (mx - this.view.x) * (k / k0);
      this.view.y = my - (my - this.view.y) * (k / k0);
      this.view.k = k;
      this._applyView();
    }, { passive: false });
    this.svg.addEventListener("click", (e) => {
      if (e.target === this.svg) this.onSelect(null);
    });
  }

  _nodeDown(e, node) {
    e.stopPropagation();
    const pt = this._toWorld(e.clientX, e.clientY);
    this.dragOff = { dx: pt.x - node.x, dy: pt.y - node.y };
    this.dragNode = node;
  }

  _toWorld(cx, cy) {
    const rect = this.svg.getBoundingClientRect();
    return { x: (cx - rect.left - this.view.x) / this.view.k,
             y: (cy - rect.top - this.view.y) / this.view.k };
  }

  _applyView() {
    this.vp.setAttribute("transform",
      `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`);
  }

  fit() {
    if (!this.nodes.size) return;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const n of this.nodes.values()) {
      x0 = Math.min(x0, n.x - 90); y0 = Math.min(y0, n.y - 90);
      x1 = Math.max(x1, n.x + 90); y1 = Math.max(y1, n.y + 90);
    }
    const rect = this.svg.getBoundingClientRect();
    if (rect.width < 10 || rect.height < 10 || x1 <= x0 || y1 <= y0) return;
    const k = Math.min(1.15, Math.max(0.32,
      Math.min(rect.width / (x1 - x0), rect.height / (y1 - y0))));
    this.view.k = k;
    this.view.x = (rect.width - (x1 - x0) * k) / 2 - x0 * k;
    this.view.y = (rect.height - (y1 - y0) * k) / 2 - y0 * k;
    this._applyView();
  }

  centerOn(nodeId) {
    const n = this.nodes.get(nodeId);
    if (!n) return;
    const rect = this.svg.getBoundingClientRect();
    this.view.x = rect.width / 2 - n.x * this.view.k;
    this.view.y = rect.height / 2 - n.y * this.view.k;
    this._applyView();
  }

  // ---------------- 事件动效 ----------------
  pulseForEvent(e) {
    if (reduceMotion) return;
    const findEdge = (pred) => this.edges.find(pred);
    let edge = null, color = "";
    const [etype, eid] = String(e.entity || "").split(":");
    if (e.kind === "task.created") {
      const t = store.flows.get(eid);
      if (t) {
        const srcRef = (t.source && t.source.subject) || "";
        const from = srcRef.startsWith("agent:") ? "agent:" + refId(srcRef) :
          ["owner", "human"].includes(refKind(srcRef)) ? "human" : null;
        edge = findEdge(x => from && x.from === from);
        color = "";
      }
    } else if (e.kind === "task.terminal") {
      edge = findEdge(x => x.cls === "callback" || x.cls === "flow");
      color = e.payload && e.payload.state === "fail" ? "red" : "amber";
    } else if (e.kind && e.kind.startsWith("agent.")) {
      const node = this.nodes.get("agent:" + eid);
      if (node) {
        const ring = node.el.querySelector(".n-ring");
        if (ring) { ring.classList.remove("flash"); void ring.getBoundingClientRect();
                    ring.classList.add("flash"); }
      }
      return;
    }
    if (!edge || !edge.el) return;
    const path = edge.el.querySelector("path.vis");
    if (!path) return;
    const dot = s("circle", { class: "pulse-dot " + color, r: 4 });
    const anim = s("animateMotion", {
      dur: "0.9s", fill: "freeze", rotate: "auto",
      path: path.getAttribute("d"),
    });
    dot.append(anim);
    this.gFx.append(dot);
    anim.addEventListener("endEvent", () => setTimeout(() => dot.remove(), 60));
    setTimeout(() => dot.remove(), 1600);   // 兜底清理
  }
}
