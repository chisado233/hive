// 通用工具
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === "class") n.className = v;
    else if (k === "dataset") Object.assign(n.dataset, v);
    else if (k.startsWith("on") && typeof v === "function")
      n.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
    else n.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    n.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return n;
}

export function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const pad = (x) => String(x).padStart(2, "0");
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (sameDay) return hm;
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${hm}`;
}

export function fmtFull(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const pad = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function fmtAgo(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 5) return "刚刚";
  if (s < 60) return `${Math.floor(s)}秒前`;
  if (s < 3600) return `${Math.floor(s / 60)}分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)}小时前`;
  return `${Math.floor(s / 86400)}天前`;
}

export function fmtSize(n) {
  if (n == null) return "";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  if (n < 1024 * 1024 * 1024) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(2) + " GB";
}

export function fmtDuration(sec) {
  if (sec == null || isNaN(sec)) return "";
  sec = Math.round(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  const h = Math.floor(m / 60);
  const pad = (x) => String(x).padStart(2, "0");
  return h ? `${h}:${pad(m % 60)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function uuid() {
  return "req_" + crypto.randomUUID().replaceAll("-", "");
}

export function toast(msg, kind = "info", ms = 3400) {
  const root = $("#toast-root");
  const t = el("div", { class: `toast ${kind}` }, msg);
  root.append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, ms);
  setTimeout(() => t.remove(), ms + 350);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function shortId(id) {
  if (!id) return "";
  const s = String(id);
  return s.length > 14 ? s.slice(0, 12) + "…" : s;
}

// 引用格式化：agent:agt_x → 名称由调用方解析
export function refKind(ref) {
  if (!ref) return "none";
  const i = ref.indexOf(":");
  return i < 0 ? ref : ref.slice(0, i);
}
export function refId(ref) {
  if (!ref) return "";
  const i = ref.indexOf(":");
  return i < 0 ? "" : ref.slice(i + 1);
}

export function escapeRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

// 布局偏好按 用户+项目 存 localStorage（非凭据，允许）
export function lsGet(key, fallback = null) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : JSON.parse(v);
  } catch { return fallback; }
}
export function lsSet(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* 满则忽略 */ }
}
export function lsDel(key) { try { localStorage.removeItem(key); } catch { } }

export const reduceMotion =
  matchMedia("(prefers-reduced-motion: reduce)").matches;
