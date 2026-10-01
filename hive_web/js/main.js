// 主装配：会话、布局、轮询、事件带、路由
import { el, toast, fmtAgo, lsGet, lsSet } from "./util.js";
import { api, errText } from "./api.js";
import { store, onChange, startPolling, select, renderConn, projectAgents,
         refLabel, eventLog } from "./store.js";
import { Graph } from "./graph.js";
import { Detail } from "./detail.js";
import { Inbox } from "./inbox.js";
import { Assets } from "./assets.js";
import { Nebula } from "./nebula.js";

const $ = (s) => document.querySelector(s);

let graph, detail, inbox, assets, firstLayout = true;

// ---------------- 启动 ----------------
boot();
async function boot() {
  const s = await api.session().catch(() => null);
  if (s && s.authenticated) {
    store.me = s.subject;
    enterApp();
  } else {
    showLogin();
  }
}

function showLogin(msg = "") {
  $("#login").classList.remove("hidden");
  $("#app").classList.add("hidden");
  $("#login-note").textContent = "";
  $("#login-err").classList.toggle("hidden", !msg);
  $("#login-err").textContent = msg;
  const btn = $("#login-btn");
  btn.onclick = async () => {
    const token = $("#login-token").value.trim();
    if (!token) return;
    btn.disabled = true;
    try {
      const r = await api.login(token);
      store.me = r.subject;
      $("#login").classList.add("hidden");
      $("#login-token").value = "";
      enterApp();
    } catch (e) {
      showLogin(errText(e));
    } finally {
      btn.disabled = false;
    }
  };
  $("#login-token").onkeydown = (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) btn.click();
  };
}

function enterApp() {
  $("#app").classList.remove("hidden");
  if (!window.__nebula)
    window.__nebula = new Nebula(document.getElementById("nebula"));
  $("#user-chip").textContent = store.me.name + " · " + store.me.type;
  $("#logout-btn").onclick = async () => {
    await api.logout().catch(() => {});
    location.reload();
  };

  const svg = $("#graph");
  detail = new Detail($("#detail-body"), {
    close: closeDetail,
    locate: (nodeId) => { graph.centerOn(nodeId); },
    openTask: (id) => detail.task(id),
    openAsset: (key) => assets.open(key),
    eventLog,
  });
  inbox = new Inbox($("#detail-body"), {
    onClose: closeDetail,
    refreshBadge: updateBadge,
  });
  assets = new Assets($("#detail-body"), { close: closeDetail });
  graph = new Graph(svg, onSelect);
  graph.loadPositions();

  $("#zoom-fit").onclick = () => graph.fit();
  $("#layout-reset").onclick = () => { graph.resetLayout(); toast("已恢复自动布局"); };
  $("#inbox-entry").onclick = () => openInbox();
  $("#eventband-toggle").onclick = () =>
    $("#eventband").classList.toggle("collapsed");
  for (const b of document.querySelectorAll("#edge-mode .seg-btn")) {
    b.classList.toggle("active", b.dataset.mode === store.edgeMode);
    b.onclick = () => {
      store.edgeMode = b.dataset.mode;
      lsSet("hive.edgeMode", b.dataset.mode);
      for (const x of document.querySelectorAll("#edge-mode .seg-btn"))
        x.classList.toggle("active", x === b);
      graph.update();
      if (!(graph.view.k > 0.05)) graph.fit();   // 视图退化时自动适配
    };
  }

  onChange(onStoreChange);
  startPolling();
  setTimeout(() => $("#graph-hint").style.opacity = 0, 9000);
}

// ---------------- 状态变化 ----------------
function onStoreChange(what) {
  if (what === "auth-lost") {
    showLogin("会话已过期，请重新登录");
    return;
  }
  renderConn();
  if (what === "conn") return;
  if (what === "selection") { graph.applySelectionDim(); return; }
  if (what === "overview") {
    renderProjectPicker();
    renderNavNodes();
    if (store.currentProject == null) {
      const saved = lsGet("hive.lastProject", "");
      if (saved && store.projects.some(p => p.key === saved))
        store.currentProject = saved;
      else if (store.projects.length === 1)
        store.currentProject = store.projects[0].key;
      const sel = document.querySelector("#project-picker select");
      if (sel && store.currentProject) sel.value = store.currentProject;
    }
    if (firstLayout && store.agents.size) {
      graph.update(true);
      graph.fit();
      firstLayout = false;
    } else {
      graph.update();
    }
    updateBadge();
    renderEventBand();
    for (const e of store.newEvents) graph.pulseForEvent(e);
  }
}

// ---------------- 顶栏/导航 ----------------
let pickerBuilt = false;
function renderProjectPicker() {
  const host = $("#project-picker");
  const keys = store.projects.map(p => p.key);
  const sig = keys.join("|");
  if (pickerBuilt === sig) return;
  pickerBuilt = sig;
  host.textContent = "";
  const sel = el("select", {},
    el("option", { value: "" }, "全部项目"),
    store.projects.map(p =>
      el("option", { value: p.key, selected: p.key === store.currentProject ? "" : null },
        `${p.key}（${p.agents.length}）`)));
  sel.onchange = () => {
    store.currentProject = sel.value || null;
    lsSet("hive.lastProject", store.currentProject || "");
    firstLayout = true;
    graph.loadPositions();
    graph.update(true);
    requestAnimationFrame(() => graph.fit());
    renderNavNodes();
  };
  host.append(sel);
}

function renderNavNodes() {
  const host = $("#nav-nodes");
  host.textContent = "";
  const agents = projectAgents(store.currentProject);
  const byLine = new Map();
  for (const a of agents) {
    const k = a.line || "other";
    if (!byLine.has(k)) byLine.set(k, []);
    byLine.get(k).push(a);
  }
  const LINE_LABEL = { coordinator: "项目负责人", research: "研究",
    "skill-test": "Skill 测试线", evolution: "反馈进化线", production: "正式生产线" };
  for (const [line, group] of byLine) {
    host.append(el("div", { class: "nav-group-label" },
      `${LINE_LABEL[line] || line}（${group.length}）`));
    for (const a of group) {
      const short = (a.name.split("·").pop() || a.name).trim();
      const obs = a.instance_count > 0
        ? (a.observation === "busy" ? "#43c6d8" : a.observation === "idle" ? "#57b389" : "#e5a33c")
        : "#4d5663";
      const row = el("div", { class: "nav-node", onclick: () => {
          onSelect({ kind: "agent", id: "agent:" + a.agent_id });
          graph.centerOn("agent:" + a.agent_id);
        } },
        el("span", { class: "nn-dot", style: { background: obs } }),
        el("span", {}, short),
        el("span", { class: "nn-role" }, a.domain || ""));
      if (store.selection && store.selection.id === "agent:" + a.agent_id)
        row.classList.add("selected");
      host.append(row);
    }
  }
  // 资产入口
  const proj = store.projects.find(p => p.key === store.currentProject);
  if (proj && proj.has_assets) {
    host.append(el("div", { class: "nav-group-label" }, "资产"));
    host.append(el("div", { class: "nav-node",
      onclick: () => assets.open(proj.key) },
      el("span", {}, "▣"),
      el("span", {}, "共享资产库")));
  }
}

function updateBadge() {
  const b = $("#inbox-badge");
  const n = store.human.pending;
  b.classList.toggle("hidden", !n);
  b.textContent = n > 99 ? "99+" : n;
}

// ---------------- 事件带 ----------------
function renderEventBand() {
  const list = $("#eventband-list");
  const frag = document.createDocumentFragment();
  for (const e of store.newEvents) {
    frag.prepend(eventRow(e));
  }
  list.prepend(frag);
  while (list.children.length > 120) list.lastChild.remove();
  $("#eb-count").textContent = `（${list.children.length}）`;
  const band = $("#eventband");
  if (store.newEvents.some(e => e.kind === "task.terminal" &&
      e.payload && e.payload.state === "fail") && band.classList.contains("collapsed")) {
    band.classList.remove("collapsed");   // 异常时自动展开提示
  }
}

function eventRow(e) {
  const kindCls = e.kind.startsWith("task.") ? "k-task" :
    e.kind.startsWith("agent.") ? "k-agent" :
    e.kind.includes("fail") || e.kind.includes("error") ? "k-err" :
    e.kind.startsWith("human") || e.kind.startsWith("wait") ? "k-human" : "";
  const row = el("div", { class: "eb-row" },
    el("span", { class: "eb-time" }, fmtAgo(e.recorded_at)),
    el("span", { class: "eb-kind " + kindCls }, e.kind),
    el("span", { class: "eb-msg", title: e.entity }, describeEvent(e)));
  row.querySelector(".eb-msg").onclick = () => {
    const [type, id] = e.entity.split(":");
    if (type === "task") detail.task(id);
    else if (type === "agent") onSelect({ kind: "agent", id: "agent:" + id });
  };
  return row;
}

function describeEvent(e) {
  const t = store.flows.get((e.entity.split(":")[1] || ""));
  const p = e.payload || {};
  switch (e.kind) {
    case "task.created": return `新任务「${t ? t.title : p.title || ""}」`;
    case "task.claimed": return `任务被领取${t ? "「" + t.title + "」" : ""}`;
    case "task.terminal": return `任务终态 ${p.state}${t ? "「" + t.title + "」" : ""}`;
    case "task.transferred": return `任务转交${t ? "「" + t.title + "」" : ""}`;
    case "agent.started": return `Agent 启动（模型 ${p.model || "?"}）`;
    case "agent.stopped": return "Agent 停止";
    case "agent.failed": return `Agent 异常 ${p.error || ""}`;
    case "wait.ready": return "等待已就绪";
    case "wait.timed_out": return "等待超时";
    default: return e.kind + " · " + e.entity;
  }
}

// ---------------- 选择路由 ----------------
function openPanel() {
  document.getElementById("detail").classList.add("open");
}

function onSelect(sel) {
  select(sel);
  if (!sel) { closeDetail(); return; }
  openPanel();
  if (sel.kind === "agent") detail.agent(sel.id.split(":")[1]);
  else if (sel.kind === "human") openInbox();
  else if (sel.kind === "asset") assets.open(sel.id.split(":")[1]);
  else if (sel.kind === "port") detail.port(sel.id);
  else if (sel.kind === "edge") detail.edge(sel);
  // 面板展开后星图收缩；选中节点保持在可视中心（保留图上上下文）
  if (["agent", "asset", "human", "port"].includes(sel.kind))
    setTimeout(() => graph.centerOn(sel.id), 270);
}

function openInbox() {
  openPanel();
  inbox.render();
}

function closeDetail() {
  document.getElementById("detail").classList.remove("open");
  $("#detail-body").classList.add("hidden");
  $("#detail-empty").classList.remove("hidden");
  select(null);
  renderNavNodes();
}
