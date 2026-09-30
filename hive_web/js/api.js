// API 客户端：会话、幂等、错误映射、上传
import { uuid } from "./util.js";

export class ApiError extends Error {
  constructor(status, code, message, details, retryable, requestId) {
    super(message || code || "请求失败");
    this.status = status; this.code = code; this.details = details || {};
    this.retryable = !!retryable; this.requestId = requestId;
  }
}

async function parseErr(res) {
  let body = null;
  try { body = await res.json(); } catch { /* 非JSON */ }
  const e = body && body.error || {};
  return new ApiError(res.status, e.code || `HTTP_${res.status}`,
    e.message || res.statusText, e.details, e.retryable,
    body && body.request_id);
}

async function req(method, path, { json, raw, timeout = 20000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(path, {
      method,
      credentials: "same-origin",
      signal: ctrl.signal,
      headers: {
        "X-Requested-With": "hive-ui",
        ...(json ? { "Content-Type": "application/json" } : {}),
        ...(raw ? { "Content-Type": raw.mime || "application/octet-stream" } : {}),
      },
      body: json ? JSON.stringify(json) : raw ? raw.body : undefined,
    });
    if (!res.ok) throw await parseErr(res);
    const ct = res.headers.get("Content-Type") || "";
    return ct.includes("json") ? res.json() : res;
  } catch (e) {
    if (e.name === "AbortError")
      throw new ApiError(0, "TIMEOUT", "请求超时", {}, true);
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, "NETWORK", "网络错误或服务不可达", {}, true);
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  // --- session ---
  login: (token) => req("POST", "/ui/api/session", { json: { token } }),
  logout: () => req("POST", "/ui/api/logout"),
  session: () => req("GET", "/ui/api/session"),

  // --- reads ---
  overview: (since) =>
    req("GET", "/ui/api/overview" + (since ? `?since=${encodeURIComponent(since)}` : "")),
  agent: (id) => req("GET", `/ui/api/agent/${encodeURIComponent(id)}`),
  task: (id, markSeen = false) =>
    req("GET", `/ui/api/task/${encodeURIComponent(id)}${markSeen ? "?mark_seen=1" : ""}`),
  humanInbox: (state) => req("GET", `/ui/api/human/inbox?state=${state}`),
  projects: () => req("GET", "/ui/api/projects"),
  projectInfo: (key) => req("GET", `/ui/api/project/${encodeURIComponent(key)}/info`),
  projectAssets: (key, path) =>
    req("GET", `/ui/api/project/${encodeURIComponent(key)}/assets?path=${encodeURIComponent(path || "")}`),
  fileUrl: (id, download = false) => `/ui/api/file/${encodeURIComponent(id)}${download ? "?download=1" : ""}`,
  taskFileUrl: (taskId, uri, download = false) =>
    `/ui/api/task-file?task=${encodeURIComponent(taskId)}&uri=${encodeURIComponent(uri)}${download ? "&download=1" : ""}`,
  projectFileUrl: (key, path, download = false) =>
    `/ui/api/project/${encodeURIComponent(key)}/file?path=${encodeURIComponent(path)}${download ? "&download=1" : ""}`,

  // --- mutations（幂等：request_id 由调用方生成一次，重试复用）---
  async command(command, args = {}, requestId = uuid(), timeout) {
    return req("POST", "/ui/api/command",
      { json: { command, arguments: args, request_id: requestId }, timeout });
  },
  async answer(taskId, specVersion, { text, data, attachments } = {}, requestId = uuid()) {
    return req("POST", "/ui/api/human/answer", {
      json: { task: taskId, spec_version: specVersion, text, data,
              attachments, request_id: requestId }, timeout: 30000 });
  },
  async fail(taskId, specVersion, reason, requestId = uuid()) {
    return req("POST", "/ui/api/human/fail", {
      json: { task: taskId, spec_version: specVersion, reason,
              request_id: requestId } });
  },
  async comment(taskId, text, { mentions, revisionOf, attachments } = {}, requestId = uuid()) {
    return req("POST", "/ui/api/comment", {
      json: { task: taskId, text, mentions, revision_of: revisionOf,
              attachments, request_id: requestId } });
  },
  async upload(file, onProgress) {
    const qs = `?name=${encodeURIComponent(file.name)}&mime=${encodeURIComponent(file.type || "")}`;
    // 原生字节上传；fetch 无进度事件，大文件用 XHR
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/ui/api/upload" + qs);
      xhr.setRequestHeader("X-Requested-With", "hive-ui");
      xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        try {
          const body = JSON.parse(xhr.responseText);
          if (xhr.status >= 200 && xhr.status < 300 && body.ok) resolve(body.data);
          else reject(new ApiError(xhr.status, body.error?.code,
            body.error?.message || "上传失败", body.error?.details));
        } catch { reject(new ApiError(xhr.status, "BAD_RESPONSE", "上传响应异常")); }
      };
      xhr.onerror = () => reject(new ApiError(0, "NETWORK", "上传网络错误", {}, true));
      xhr.ontimeout = () => reject(new ApiError(0, "TIMEOUT", "上传超时", {}, true));
      xhr.timeout = 10 * 60 * 1000;
      xhr.send(file);
    });
  },

  // 操作轮询：受理 → 完成 分态反馈（operation.show 服务端长轮询）
  async waitOperation(opId, onTick, maxWaitMs = 45000) {
    const t0 = Date.now();
    for (;;) {
      const r = await this.command("control.operation.show",
        { op: opId, wait_seconds: 5 }, uuid(), 12000);
      const op = r.data.operation;
      if (onTick) onTick(op);
      if (["succeeded", "failed", "cancelled"].includes(op.state)) return op;
      if (Date.now() - t0 > maxWaitMs) return op;
    }
  },
};

// 业务错误文案：200 不等于业务完成，错误码逐类呈现
export function errText(e) {
  const map = {
    TASK_SPEC_CHANGED: "问题版本已变化，请刷新后重新作答（你的草稿已保留）",
    TASK_TERMINAL: "任务已终态，结果不可改写",
    FORBIDDEN: "没有执行该操作的权限",
    NOT_VISIBLE: "任务对你不可见",
    TOKEN_INVALID: "凭据无效",
    TOKEN_REVOKED: "凭据已被撤销",
    CLAIM_FENCED: "领取凭据已失效",
    STORAGE_UNAVAILABLE: "存储冲突：可用相同 request-id 重试",
    IDEMPOTENCY_CONFLICT: "相同 request-id 但参数不同（不应发生）",
    NETWORK: "网络错误或服务不可达",
    TIMEOUT: "请求超时——若为重试将沿用同一 request-id，不会重复入账",
  };
  return map[e.code] ? `${map[e.code]}（${e.code}）` : `${e.message}（${e.code}）`;
}
