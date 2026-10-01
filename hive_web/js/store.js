// 应用状态 + 有界轮询引擎 + 事件去重
import { api } from "./api.js";
import { el, fmtAgo, lsGet, lsSet } from "./util.js";

export const store = {
  me: null,                    // 当前 subject
  projects: [],                // [{key, root, agents, active, authorizations,...}]
  agents: new Map(),           // id -> agent 摘要
  ports: new Map(),
  subscriptions: [],
  flows: new Map(),            // task_id -> task card（活跃+近期终态）
  human: { pending: 0, processing: 0, unread: 0 },
  eventCursor: null,
  seenEvents: new Set(),       // 事件去重（跨重连）
  newEvents: [],               // 本批新事件（供动画/事件带）
  online: null,                // null=未连接 true/false
  lastUpdate: null,
  currentProject: null,        // key 或 null（全部）
  selection: null,             // {kind:'agent'|'asset'|'human'|'task'|'port', id}
  edgeMode: lsGet("hive.edgeMode", "flows"),
  canObserve: false,
  errors: {},
  listeners: new Set(),
};

export function onChange(fn) { store.listeners.add(fn); return () => store.listeners.delete(fn); }
function emit(what) {
  for (const fn of store.listeners) {
    try { fn(what); } catch (e) {
      console.error("[hive] listener error on", what, e);
      window.__hiveLastListenerError = String(e && e.stack || e);
    }
  }
}

// 调试探针：控制台可读当前状态
window.__hiveStore = store;

// ---------------- 轮询 ----------------
let pollTimer = null, backoff = 0, polling = false;
const POLL_ACTIVE = 4000, POLL_HIDDEN = 20000;

export function startPolling() {
  stopPolling();
  schedule(0);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) schedule(0);   // 回前台立即补状态
  });
}
export function stopPolling() { if (pollTimer) clearTimeout(pollTimer); pollTimer = null; }

function schedule(ms) {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = setTimeout(tick, ms);
}

async function tick() {
  if (polling) return;
  polling = true;
  try {
    const r = await api.overview(store.eventCursor);
    applyOverview(r.data);
    const wasOffline = store.online !== true;
    store.online = true;
    store.lastUpdate = new Date();
    backoff = 0;
    emit("overview");
    if (wasOffline) emit("reconnect");
    schedule(document.hidden ? POLL_HIDDEN : POLL_ACTIVE);
  } catch (e) {
    store.online = false;
    emit("conn");
    if (e.code === "AUTH_REQUIRED") { emit("auth-lost"); polling = false; return; }
    backoff = Math.min(backoff ? backoff * 2 : 2000, 30000);
    schedule(backoff);
  } finally {
    polling = false;
  }
}

function applyOverview(d) {
  store.serverTime = d.server_time;
  store.canObserve = !!d.can_observe;
  store.errors = d.errors || {};
  if (d.projects) store.projects = d.projects;
  if (d.agents) {
    const keep = new Set();
    for (const a of d.agents) { store.agents.set(a.agent_id, a); keep.add(a.agent_id); }
    for (const k of [...store.agents.keys()]) if (!keep.has(k)) store.agents.delete(k);
  }
  if (d.ports) {
    store.ports = new Map(d.ports.map(p => [p.port_id, p]));
  }
  if (d.subscriptions) store.subscriptions = d.subscriptions;
  if (d.flows) {
    const keep = new Set();
    for (const t of d.flows) { store.flows.set(t.task_id, t); keep.add(t.task_id); }
    // 终态任务超窗后在服务端已剔除，这里同步移除
    for (const k of [...store.flows.keys()]) if (!keep.has(k)) store.flows.delete(k);
  }
  if (d.human) store.human = d.human;
  store.newEvents = [];
  if (d.events) {
    for (const e of d.events) {
      if (store.seenEvents.has(e.event_id)) continue;
      store.seenEvents.add(e.event_id);
      store.newEvents.push(e);
      eventLog.push(e);
    }
    if (store.seenEvents.size > 4000) {
      store.seenEvents = new Set([...store.seenEvents].slice(-2000));
    }
    if (eventLog.length > 500) eventLog.splice(0, eventLog.length - 500);
  }
  if (d.event_cursor) store.eventCursor = d.event_cursor;
}

// ---------------- 事件带 ----------------
export const eventLog = [];

// ---------------- 选择 ----------------
export function select(sel) {
  store.selection = sel;
  emit("selection");
}

// ---------------- 工具 ----------------
export function agentOf(id) { return store.agents.get(id); }
export function portOf(id) { return store.ports.get(id); }

export function refLabel(ref) {
  if (!ref) return "—";
  const [kind, id] = ref.split(":", 2);
  if (kind === "agent") return store.agents.get(id)?.name || id;
  if (kind === "port") {
    const p = store.ports.get(id);
    return p ? (p.name || p.route) : id;
  }
  if (kind === "owner") return store.me && store.me.subject_id === id ? "我（管理者）" : "管理者";
  if (kind === "human") return store.me && store.me.subject_id === id ? "我" : "人类用户";
  if (kind === "producer") return "外部生产者";
  return ref;
}

// 项目内 agent 列表
export function projectAgents(key) {
  return [...store.agents.values()].filter(a =>
    key ? a.project === key : !a.project);
}

// 连接状态渲染
export function renderConn() {
  const conn = document.getElementById("conn");
  if (!conn) return;
  const txt = conn.querySelector(".conn-text");
  conn.classList.toggle("online", store.online === true);
  conn.classList.toggle("offline", store.online === false);
  if (txt) txt.textContent = store.online === true ? "已连接" :
    store.online === false ? "连接中断，重试中" : "连接中";
  conn.title = store.lastUpdate
    ? "更新于 " + fmtAgo(store.lastUpdate.toISOString()) : "";
}
