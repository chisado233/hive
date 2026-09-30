// 会话舱：真实 ACP/fixture 会话的流式视图 + 输入队列 + 取消/中断
// 跨 Agent 切换保留草稿与阅读位置；fixture 模式有明确"演示"标识
import { el, fmtAgo, toast, uuid, lsGet, lsSet, shortId, reduceMotion } from "./util.js";
import { api, errText } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { fileChip } from "./content.js";

const paneCache = new Map();   // agent_id → {draft, scrollTop, cursor, follow}

export class ConversationPane {
  // host: 容器；agent: agent 摘要；ctx: {openTask(id)}
  constructor(host, agent, ctx) {
    this.host = host;
    this.agent = agent;
    this.ctx = ctx;
    this.agentId = agent.agent_id;
    this.cache = paneCache.get(this.agentId) || {
      draft: "", scrollTop: null, cursor: 0, follow: true,
      requestId: uuid(),
    };
    paneCache.set(this.agentId, this.cache);
    this.es = null;
    this.rowsById = new Map();   // row_id → DOM 引用（就地更新，不整树重建）
    this.destroyed = false;
  }

  async mount() {
    this.host.textContent = "";
    this.host.classList.add("conv-pane");
    let d;
    try {
      d = (await api.command("control.agent.show",
        { agent: this.agentId, runtime: true })).data;
    } catch (e) {
      this.host.append(el("div", { class: "inbox-empty" }, errText(e)));
      return;
    }
    let convInfo = null;
    try {
      convInfo = (await fetch(
        `/ui/api/agent/${this.agentId}/conversation`,
        { headers: { "X-Requested-With": "hive-ui" } }).then(r => r.json())).data;
    } catch (e) { /* 无会话权限时详情在下方显示 */ }

    // 头部状态条
    const inst = convInfo?.instance;
    const binding = convInfo?.binding;
    const head = el("div", { class: "conv-head" },
      el("span", { class: "conv-dot " + (inst ? "live" : "off") }),
      el("span", { class: "conv-head-name" }, this.agent.name),
      inst ? el("span", { class: "tag cyan" },
        `实例 ${shortId(inst.instance_id)} · gen${inst.generation}`) :
        el("span", { class: "tag" }, "无运行实例"),
      convInfo?.bridge === "fixture"
        ? el("span", { class: "tag amber", title: "上游 ACP 未就绪，当前为演示数据" },
            "演示模式")
        : convInfo?.bridge === "acp"
          ? el("span", { class: "tag green" }, "ACP")
          : null,
      binding ? el("span", { class: "conv-sid" }, shortId(binding.session_id)) : null);
    this.host.append(head);

    // 消息流
    this.flow = el("div", { class: "conv-flow" });
    this.host.append(this.flow);
    this._renderHistory(convInfo?.tail);

    // 底部：排队指示 + 输入区
    this.queueBar = el("div", { class: "conv-queue hidden" });
    this.host.append(this.queueBar);
    this.host.append(this._composer(convInfo));

    // 恢复阅读位置
    if (this.cache.scrollTop != null)
      this.flow.scrollTop = this.cache.scrollTop;
    this._watchScroll();
    this._connectStream();
    this._refreshQueue();
  }

  destroy() {
    this.destroyed = true;
    this.cache.scrollTop = this.flow ? this.flow.scrollTop : null;
    if (this.es) { this.es.close(); this.es = null; }
  }

  // ---------------- 历史投影 ----------------
  _renderHistory(tail) {
    this.flow.textContent = "";
    this.rowsById.clear();
    if (!tail || !tail.messages || !tail.messages.length) {
      this.flow.append(el("div", { class: "conv-empty" },
        "暂无会话内容。发送一条消息开始对话；",
        el("br"), "正式任务请用「发任务」，聊天不会自动生成 work。"));
      return;
    }
    for (const m of tail.messages) this._appendMessage(m, false);
    this._scrollBottom(true);
  }

  _appendMessage(m, animate = true) {
    // 就地更新已有 row（流式 delta）
    if (m.id && this.rowsById.get(m.id)) {
      const exist = this.rowsById.get(m.id);
      const body = exist.querySelector(".conv-msg-body");
      if (body) { body.textContent = ""; body.append(renderMarkdown(m.text || "")); }
      exist.dataset.state = m.state || "streaming";
      this._maybeScroll();
      return;
    }
    const row = el("div", { class: `conv-msg conv-${m.kind}`,
      dataset: { state: m.state || "" } });
    const who = { user: "你", assistant: this.agent.name.split("·").pop().trim(),
      thought: "思考", tool: "工具", system: "系统", permission: "权限" }[m.kind] || m.kind;
    if (m.kind === "tool") {
      row.append(this._toolCard(m));
    } else if (m.kind === "permission") {
      row.append(this._permissionCard(m));
    } else {
      row.append(el("div", { class: "conv-msg-who" },
        el("span", {}, who),
        el("span", { class: "conv-msg-at" }, fmtAgo(m.at)),
        m.state === "streaming" ? el("span", { class: "conv-streaming" }, "▍") : null,
        m.replay ? el("span", { class: "conv-replay" }, "重放") : null));
      const body = el("div", { class: "conv-msg-body" });
      if (m.kind === "assistant" || m.kind === "thought")
        body.append(renderMarkdown(m.text || ""));
      else body.textContent = m.text || "";
      row.append(body);
    }
    if (m.id) this.rowsById.set(m.id, row);
    this.flow.append(row);
    this._maybeScroll();
  }

  _toolCard(m) {
    const stateIcon = { pending: "⏳", running: "⚙️", completed: "✓",
      failed: "✗", cancelled: "⊘" }[m.state] || "⚙️";
    const card = el("div", { class: `tool-card tool-${m.state}` },
      el("div", { class: "tool-head", onclick: () => card.classList.toggle("open") },
        el("span", { class: "tool-icon" }, stateIcon),
        el("span", { class: "tool-name" }, m.tool || "tool"),
        m.title ? el("span", { class: "tool-title" }, m.title) : null,
        el("span", { class: "tool-state" }, m.state)),
      el("div", { class: "tool-detail" },
        m.args ? el("pre", { class: "mono" }, typeof m.args === "string" ? m.args : JSON.stringify(m.args, null, 1)) : null,
        m.result != null ? el("pre", { class: "mono" }, typeof m.result === "string" ? m.result : JSON.stringify(m.result, null, 1)) : null,
        m.error ? el("div", { class: "field-err" }, m.error) : null));
    return card;
  }

  _permissionCard(m) {
    const card = el("div", { class: "tool-card tool-permission" },
      el("div", { class: "tool-head" },
        el("span", { class: "tool-icon" }, "🔐"),
        el("span", { class: "tool-name" }, "工具权限请求"),
        el("span", { class: "tool-state" }, m.state)));
    if (m.state === "pending") {
      const opts = el("div", { style: { display: "flex", gap: "6px", padding: "8px" } });
      for (const o of m.options || []) {
        opts.append(el("button", { class: "btn btn-sm btn-cyan",
          onclick: async () => {
            try {
              await api.command("control.conversation.permission", {
                agent: this.agentId, request_id: m.request_id,
                answer: { optionId: o.id || o } });
              toast("已答复权限请求", "ok");
            } catch (e) { toast(errText(e), "err"); }
          } }, o.label || o.id || String(o)));
      }
      card.append(opts);
    }
    return card;
  }

  // ---------------- 输入区 ----------------
  _composer(convInfo) {
    const wrap = el("div", { class: "conv-composer" });
    const ta = el("textarea", { class: "text-input conv-input", rows: 2,
      placeholder: "发消息给 " + this.agent.name.split("·").pop().trim() +
        "…（Enter 发送 / Shift+Enter 换行 / Ctrl+Enter 备选）" });
    ta.value = this.cache.draft;
    let composing = false;
    ta.addEventListener("compositionstart", () => composing = true);
    ta.addEventListener("compositionend", () => composing = false);
    ta.addEventListener("input", () => { this.cache.draft = ta.value; });
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !composing) {
        e.preventDefault(); this._send(ta, "queue");
      } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !composing) {
        e.preventDefault(); this._send(ta, "queue");
      }
    });

    const sendQ = el("button", { class: "btn btn-primary btn-sm", title: "排队发送（当前轮结束后发）" }, "发送");
    sendQ.onclick = () => this._send(ta, "queue");
    const sendI = el("button", { class: "btn btn-ghost btn-sm", title: "先取消当前轮，再发送" }, "中断并发送");
    sendI.onclick = () => this._send(ta, "interrupt");
    const cancel = el("button", { class: "btn btn-ghost btn-sm", title: "停止本轮（保留会话）" }, "停止本轮");
    cancel.onclick = () => this._cancel(cancel);
    const taskBtn = el("button", { class: "btn btn-cyan btn-sm", title: "打开正式任务表单（emit.new）" }, "发任务");
    taskBtn.onclick = () => this.ctx.openTaskComposer && this.ctx.openTaskComposer(this.agent);

    const noInstance = !convInfo?.instance;
    if (noInstance) {
      ta.disabled = true; sendQ.disabled = true; sendI.disabled = true;
      ta.placeholder = "无运行实例——可查看历史；发送需先启动 Agent";
    }
    wrap.append(ta,
      el("div", { class: "conv-actions" }, sendQ, sendI, cancel, taskBtn,
        el("span", { class: "draft-note" },
          convInfo?.bridge === "fixture" ? "演示模式：fixture 模拟回答" :
          "聊天不入任务账；正式交付请发任务")));
    return wrap;
  }

  async _send(ta, mode) {
    const text = ta.value.trim();
    if (!text) return;
    const rid = this.cache.requestId;
    try {
      const r = await fetch(`/ui/api/agent/${this.agentId}/conversation/input`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "hive-ui" },
        body: JSON.stringify({ text, mode, request_id: rid,
          instance_id: undefined, generation: undefined }),
      }).then(r => r.json());
      if (!r.ok) throw { code: r.error?.code, message: r.error?.message };
      ta.value = ""; this.cache.draft = "";
      this.cache.requestId = uuid();   // 新消息 = 新 request-id
      this._pendingEcho = this._pendingEcho || new Set();
      this._pendingEcho.add(rid);
      this._appendMessage({ kind: "user", id: "local-" + rid, text,
        state: "queued", at: new Date().toISOString() });
      this._refreshQueue();
      if (r.data?.replayed) toast("该消息已受理过（幂等重放）", "info");
    } catch (e) {
      toast(errText(e.code ? e : { code: "NETWORK", message: e.message }), "err", 5000);
    }
  }

  async _cancel(btn) {
    btn.disabled = true;
    try {
      const r = await fetch(`/ui/api/agent/${this.agentId}/conversation/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "hive-ui" },
        body: JSON.stringify({ request_id: uuid() }),
      }).then(r => r.json());
      if (r.ok && r.data?.cancelled) toast("已请求取消本轮", "ok");
      else toast(r.data?.note || r.data?.upstream_error || "当前没有活动轮次", "info");
    } catch (e) { toast("取消失败", "err"); }
    finally { btn.disabled = false; this._refreshQueue(); }
  }

  async _refreshQueue() {
    try {
      const d = await fetch(`/ui/api/agent/${this.agentId}/conversation/inputs`,
        { headers: { "X-Requested-With": "hive-ui" } }).then(r => r.json());
      const items = (d.data?.items || []).filter(i =>
        ["queued", "dispatched", "running"].includes(i.state));
      this.queueBar.classList.toggle("hidden", !items.length);
      if (items.length) {
        this.queueBar.textContent = "";
        this.queueBar.append(el("span", {}, "队列: "));
        for (const i of items)
          this.queueBar.append(el("span", { class: "tag cyan" },
            `${i.state} ${shortId(i.input_id)}`));
      }
    } catch { /* 忽略 */ }
  }

  // ---------------- 流式连接 ----------------
  _connectStream() {
    if (this.es) this.es.close();
    const url = `/ui/api/agent/${this.agentId}/conversation/stream?after=${this.cache.cursor || 0}`;
    this.es = new EventSource(url);
    const kinds = ["user.message", "agent.delta", "agent.message.final",
      "tool.call", "turn.started", "turn.completed", "turn.failed",
      "turn.cancelled", "permission.request", "system.note"];
    for (const k of kinds) {
      this.es.addEventListener(k, (e) => {
        if (this.destroyed) return;
        const ev = JSON.parse(e.data);
        if (ev.cursor) this.cache.cursor = ev.cursor;
        this._projectEvent(ev);
      });
    }
    this.es.addEventListener("heartbeat", () => {});
    this.es.onerror = () => { /* EventSource 自动重连；恢复时 after=cursor */ };
  }

  _projectEvent(ev) {
    const p = ev.payload || {};
    const t = ev.type;
    if (t === "user.message") {
      // 乐观回显去重：同文本的本地气泡换成正式事件（含真实 request_id 关联）
      const localDup = [...this.flow.querySelectorAll(".conv-user")]
        .filter(n => n.dataset.state === "queued" &&
          n.querySelector(".conv-msg-body")?.textContent === p.text)
        .pop();
      if (localDup) { localDup.remove(); }
      this._appendMessage({ kind: "user", id: ev.event_id, text: p.text,
        state: "completed", at: ev.recorded_at });
    } else if (t === "agent.delta" || t === "agent.message.final") {
      this._appendMessage({ kind: "assistant", id: ev.row_id || ev.event_id,
        text: p.text, state: p.state, at: ev.recorded_at, replay: ev.replay });
    } else if (t === "tool.call") {
      this._appendMessage({ kind: "tool", id: ev.tool_call_id || ev.event_id,
        tool: p.tool, title: p.title, state: p.state, args: p.args,
        result: p.result, error: p.error, at: ev.recorded_at });
    } else if (t === "permission.request") {
      this._appendMessage({ kind: "permission", id: ev.event_id,
        tool: p.tool, options: p.options, request_id: p.request_id,
        state: p.state, at: ev.recorded_at });
    } else if (t.startsWith("turn.")) {
      this._refreshQueue();
    }
  }

  // ---------------- 滚动跟随 ----------------
  _watchScroll() {
    this.flow.addEventListener("scroll", () => {
      const nearBottom = this.flow.scrollHeight - this.flow.scrollTop -
        this.flow.clientHeight < 60;
      this.cache.follow = nearBottom;
      const btn = this.host.querySelector(".conv-newout");
      if (btn) btn.classList.toggle("hidden", nearBottom);
    });
  }

  _maybeScroll() {
    if (this.cache.follow) this._scrollBottom();
    else this._showNewOutBtn();
  }

  _scrollBottom(force = false) {
    if (force || this.cache.follow)
      this.flow.scrollTop = this.flow.scrollHeight;
  }

  _showNewOutBtn() {
    if (this.host.querySelector(".conv-newout")) return;
    const btn = el("button", { class: "conv-newout",
      onclick: () => { this.cache.follow = true; this._scrollBottom(true);
        btn.classList.add("hidden"); } }, "↓ 有新输出");
    this.host.append(btn);
  }
}
