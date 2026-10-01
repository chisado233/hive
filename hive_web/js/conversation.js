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
    this._lastEventAt = Date.now();
    this._lastKnownActive = null;   // 上次权威校对的活动输入状态
    this._lastTexts = new Map();    // 增量归并：消息 id → 已渲染文本
    this._tailGen = 0;              // 异步代次守卫（旧响应不覆盖新状态）
    this._watchdog = null;
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
    const ss = convInfo?.session_state;
    const active = convInfo?.active_input;
    const busy = !!active && ["dispatched", "running", "cancelling"].includes(active.state);
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
      ss?.status || active ? el("span", { class: "tag " + (busy ? "cyan" : "") },
        busy ? "生成中" : `会话 ${ss?.status || "未知"}`) : null,
      ss?.model ? el("span", { class: "conv-sid", title: "确认模型" },
        `${ss.model.providerId}/${ss.model.modelId}`) : null,
      (binding?.session_id || inst?.handle || "").startsWith("sess")
        ? el("span", { class: "conv-sid" },
            shortId(binding?.session_id || inst.handle.split(":")[1])) : null);
    this.host.append(head);

    // 消息流
    this.flow = el("div", { class: "conv-flow" });
    this.host.append(this.flow);
    this._renderHistory(convInfo?.tail, convInfo);

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
    this._closeStream();
    if (this._watchdog) { clearInterval(this._watchdog); this._watchdog = null; }
  }

  // ---------------- 历史投影 ----------------
  _renderHistory(tail, convInfo) {
    this.flow.textContent = "";
    this.rowsById.clear();
    const upstream = convInfo?.upstream_history || [];
    const live = (tail && tail.messages) || [];
    if (!upstream.length && !live.length) {
      this.flow.append(el("div", { class: "conv-empty" },
        convInfo?.upstream_error
          ? `会话历史读取失败：${convInfo.upstream_error}`
          : "暂无会话内容。发送一条消息开始对话；",
        el("br"), "正式任务请用「发任务」，聊天不会自动生成 work。"));
      return;
    }
    if (upstream.length) {
      this.flow.append(el("div", { class: "conv-divider" },
        `ZCode 会话历史（${upstream.length} 条）` +
        (convInfo?.upstream_note ? ` · ${convInfo.upstream_note}` : "")));
      for (const m of upstream)
        this._appendMessage({ ...m, id: "hist-" + Math.random().toString(36).slice(2) },
          false);
      if (live.length)
        this.flow.append(el("div", { class: "conv-divider" }, "—— 本次会话接入后 ——"));
    }
    for (const m of live) this._appendMessage(m, false);
    this._scrollBottom(true);
  }

  _appendMessage(m, animate = true) {
    // 就地更新已有 row（流式 delta）
    if (m.id && this.rowsById.get(m.id)) {
      const exist = this.rowsById.get(m.id);
      const body = exist.querySelector(".conv-msg-body");
      if (body) { body.textContent = ""; body.append(renderMarkdown(m.text || "")); }
      const thinkText = exist.querySelector(".zc-think-text");
      if (thinkText) { thinkText.textContent = m.text || ""; this._thinkScroll(exist); }
      exist.dataset.state = m.state || "streaming";
      const lbl = exist.querySelector(".zc-think-label");
      if (lbl && m.state === "completed") { lbl.textContent = "思考"; lbl.classList.remove("shimmer"); }
      this._maybeScroll();
      return;
    }
    const row = el("div", { class: `conv-msg conv-${m.kind}`,
      dataset: { state: m.state || "", source: m.source || "" } });
    const who = { user: "你", assistant: this.agent.name.split("·").pop().trim(),
      thought: "思考", tool: "工具", system: "系统", permission: "权限" }[m.kind] || m.kind;
    if (m.kind === "tool") {
      row.append(this._toolCard(m));
    } else if (m.kind === "permission") {
      row.append(this._permissionCard(m));
    } else if (m.kind === "thought") {
      row.append(this._thinkCard(m));
    } else {
      row.append(el("div", { class: "conv-msg-who" },
        el("span", {}, who),
        el("span", { class: "conv-msg-at" }, fmtAgo(m.at)),
        m.state === "streaming" ? el("span", { class: "conv-streaming" }, "▍") : null,
        m.replay ? el("span", { class: "conv-replay" }, "重放") : null));
      const body = el("div", { class: "conv-msg-body" });
      if (m.kind === "assistant")
        body.append(renderMarkdown(m.text || ""));
      else body.textContent = m.text || "";
      row.append(body);
    }
    if (m.id) this.rowsById.set(m.id, row);
    this.flow.append(row);
    this._maybeScroll();
  }

  _thinkScroll(rowEl) {
    const area = rowEl.querySelector(".zc-think-body.open");
    if (area) area.scrollTop = area.scrollHeight;
  }

  _toolCard(m) {
    // ZCode web 形态：无卡片边框的单行摘要（图标+类别+内容摘要+状态+悬停出现的箭头），
    // 点击展开参数/结果详情；运行中类别标签带渐变微光
    const icons = {
      execute: "⌘", bash: "⌘", terminal: "⌘", read: "📄", edit: "✎",
      write: "✎", glob: "⌕", grep: "⌕", web: "⌁", task: "⧉", fallback: "⚙",
    };
    const name = m.tool || "tool";
    const low = name.toLowerCase();
    const icon = icons[Object.keys(icons).find(k => low.includes(k)) || "fallback"];
    const running = m.state === "running";
    const failed = m.state === "failed";
    // 摘要：JSON 参数提炼为可读形态（command/description/file_path），截断 100
    let summary = m.title || "";
    if (summary.includes(":")) summary = summary.split(":").slice(1).join(":").trim();
    if (summary.startsWith("{")) {
      try {
        const j = JSON.parse(summary.replace(/\n/g, " "));
        summary = j.command || j.description || j.file_path || j.path ||
                  JSON.stringify(j);
      } catch { /* 保留原文 */ }
    }
    if (summary.length > 100) summary = summary.slice(0, 100) + "…";
    const row = el("button", { class: "zc-tool-row" },
      el("span", { class: "zc-tool-icon" }, icon),
      el("span", { class: "zc-tool-kind" + (running ? " shimmer" : "") }, name),
      summary ? el("span", { class: "zc-tool-title" }, summary) : null,
      failed ? el("span", { class: "zc-tool-fail", title: m.error || "执行失败" }, "failed") : null,
      el("span", { class: "zc-tool-chev" }, "›"));
    const detail = el("div", { class: "zc-tool-detail" },
      m.args != null ? el("div", { class: "zc-sec" },
        el("div", { class: "zc-sec-h" }, "参数"),
        el("pre", { class: "mono zc-sec-body" },
          typeof m.args === "string" ? m.args : JSON.stringify(m.args, null, 1))) : null,
      m.result != null ? el("div", { class: "zc-sec" },
        el("div", { class: "zc-sec-h" }, "结果"),
        el("pre", { class: "mono zc-sec-body" },
          typeof m.result === "string" ? m.result : JSON.stringify(m.result, null, 1))) : null,
      m.error ? el("div", { class: "zc-sec" },
        el("div", { class: "zc-sec-h" }, "错误"),
        el("pre", { class: "mono zc-sec-body zc-err" }, m.error)) : null);
    row.onclick = () => {
      const open = detail.classList.toggle("open");
      row.classList.toggle("open", open);
    };
    return el("div", { class: "zc-tool", dataset: { state: m.state } }, row, detail);
  }

  _thinkCard(m) {
    // ZCode web 形态：默认折叠的思考区（Brain 图标 + “思考”，展开后左侧轨线滚动区）
    const secs = m.duration != null && m.duration >= 1
      ? el("span", { class: "zc-think-dur" }, `· ${Math.round(m.duration)}s`) : null;
    const row = el("button", { class: "zc-think-row" },
      el("span", { class: "zc-tool-icon" }, "🧠"),
      el("span", { class: m.state === "streaming" ? "shimmer zc-think-label"
        : "zc-think-label" },
        m.state === "streaming" ? "思考中…" : "思考"),
      secs,
      el("span", { class: "zc-tool-chev" }, "›"));
    const body = el("div", { class: "zc-think-body" },
      el("div", { class: "zc-think-text" }, m.text || ""));
    row.onclick = () => {
      const open = body.classList.toggle("open");
      row.classList.toggle("open", open);
    };
    return el("div", { class: "zc-think" }, row, body);
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
      // RDEF-10：携带实例/代际/预期轮守卫，防竞态误杀下一轮
      let guard = {};
      try {
        const d = await fetch(`/ui/api/agent/${this.agentId}/conversation`,
          { headers: { "X-Requested-With": "hive-ui" } }).then(r => r.json());
        const inst = d.data?.instance, act = d.data?.active_input;
        if (inst) guard = { instance_id: inst.instance_id,
                            generation: inst.generation };
        if (act?.turn_id) guard.expected_turn_id = act.turn_id;
      } catch { /* 守卫尽力携带 */ }
      const r = await fetch(`/ui/api/agent/${this.agentId}/conversation/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "hive-ui" },
        body: JSON.stringify({ request_id: uuid(), ...guard }),
      }).then(r => r.json());
      if (r.ok && (r.data?.cancelling || r.data?.cancelled))
        toast("已请求取消本轮，等待原生确认…", "ok");
      else toast(r.data?.note || r.data?.upstream_error ||
                 r.error?.message || "当前没有活动轮次", "info", 4500);
    } catch (e) { toast("取消失败", "err"); }
    finally { btn.disabled = false; this._refreshQueue(); }
  }

  async _refreshQueue() {
    try {
      const d = await fetch(`/ui/api/agent/${this.agentId}/conversation/inputs`,
        { headers: { "X-Requested-With": "hive-ui" } }).then(r => r.json());
      const items = (d.data?.items || []).filter(i =>
        ["queued", "dispatched", "running", "cancelling"].includes(i.state));
      const unknown = (d.data?.items || []).filter(i =>
        i.state === "outcome_unknown");
      this.queueBar.classList.toggle("hidden", !items.length && !unknown.length);
      if (items.length || unknown.length) {
        this.queueBar.textContent = "";
        if (items.length) {
          this.queueBar.append(el("span", {}, "队列: "));
          for (const i of items)
            this.queueBar.append(el("span", { class: "tag cyan" },
              `${i.state} ${shortId(i.input_id)}`));
        }
        // FDEF-01：unknown 输入的原位处置入口（只读重对账；人工收口走 confirm）
        for (const u of unknown.slice(0, 3)) {
          const chip = el("span", { class: "tag amber",
            title: u.error || "结果未知（未自动判定）" },
            `未知 ${shortId(u.input_id)}`);
          const btn = el("button", { class: "btn btn-ghost btn-sm" }, "对账");
          btn.onclick = () => this._reconcileAction(u.input_id);
          this.queueBar.append(chip, btn);
        }
      }
    } catch { /* 忽略 */ }
  }

  // ---------------- 流式连接 ----------------
  _connectStream() {
    this._closeStream();
    const url = `/ui/api/agent/${this.agentId}/conversation/stream?after=${this.cache.cursor || 0}`;
    this.es = new EventSource(url);
    this._streamState("connecting");
    this._lastBeat = Date.now();
    this._startWatchdog();
    this._beatTimer = setInterval(() => {
      // 25s 无心跳即视为断开（服务端 15s 心跳）
      if (Date.now() - this._lastBeat > 25000) this._streamState("stalled");
    }, 5000);
    this.es.onopen = () => this._streamState("live");
    const kinds = ["user.message", "agent.delta", "agent.message.final",
      "agent.thought", "tool.call", "turn.started", "turn.completed",
      "turn.failed", "turn.cancelled", "permission.request", "system.note"];
    for (const k of kinds) {
      this.es.addEventListener(k, (e) => {
        if (this.destroyed) return;
        this._lastBeat = Date.now();
        this._lastEventAt = Date.now();
        this._streamState("live");
        const ev = JSON.parse(e.data);
        if (ev.cursor) this.cache.cursor = ev.cursor;
        this._projectEvent(ev);
      });
    }
    this.es.addEventListener("heartbeat", () => {
      this._lastBeat = Date.now();
      this._streamState("live");
    });
    this.es.onerror = () => {
      // EventSource 会自动重连（带 Last-Event-ID）；显示降级状态。
      // 若服务端 401（会话过期/撤销），轮询层会触发 auth-lost 统一进登录页。
      if (this.es.readyState === EventSource.CLOSED) this._streamState("closed");
      else this._streamState("reconnecting");
    };
  }

  _closeStream() {
    if (this.es) { this.es.close(); this.es = null; }
    if (this._beatTimer) { clearInterval(this._beatTimer); this._beatTimer = null; }
  }

  // RDEF-08：停流看门狗——连接未断但业务事件停流时（合法长工具也可能
  // 安静，故仅在有活动输入且长时间无事件时）低频校对权威状态；不一致
  // 即从游标重建订阅并补齐，不双写、不重发输入。
  _startWatchdog() {
    if (this._watchdog) clearInterval(this._watchdog);
    this._watchdog = setInterval(async () => {
      if (this.destroyed || !this.es) return;
      const silentMs = Date.now() - this._lastEventAt;
      // FDEF-02 §5：上游桥状态独立轮询（SSE 心跳活着不代表桥活着）
      try {
        const u = await fetch(`/ui/api/agent/${this.agentId}/conversation`,
          { headers: { "X-Requested-With": "hive-ui" } }).then(r => r.json());
        if (u.ok) this._upstreamState(u.data?.upstream,
          u.data?.active_input, silentMs);
      } catch { /* 传输层由 onerror 处理 */ }
      if (this.destroyed || !this.es) return;
      if (silentMs < 18000) return;
      try {
        const d = await fetch(
          `/ui/api/agent/${this.agentId}/conversation`,
          { headers: { "X-Requested-With": "hive-ui" } }).then(r => r.json());
        if (!d.ok) return;
        const act = d.data?.active_input || null;
        const actKey = act ? `${act.input_id}:${act.state}` : "none";
        if (actKey !== this._lastKnownActive || silentMs > 60000) {
          this._lastKnownActive = actKey;
          // 权威状态与本地不一致（或长时间静默后强制校对）：重建订阅+补尾
          this._streamState("resync");
          this._connectStream();
          await this._refreshTail();
          toast("实时通道已自动校对恢复", "info", 2500);
        }
        this._lastEventAt = Date.now();
      } catch { /* 网络层失败由 onerror 分支处理 */ }
    }, 8000);
  }

  // FDEF-02 §5：连接与运行状态分开呈现（不互相覆盖）
  _upstreamState(up, active, silentMs) {
    if (!this._upBar) {
      this._upBar = el("div", { class: "conv-upstream hidden" });
      this.host.insertBefore(this._upBar, this.host.firstChild);
    }
    let text = null;
    if (up && up.bridge_alive === false)
      text = "上游连接中断，正在恢复——已显示内容与草稿保留";
    else if (up && up.unknown_inputs > 0 && !active)
      text = "有待对账的历史输入（结果未知未自动判定）";
    else if (silentMs > 18000 && active)
      text = null;   // 下方 resync 分支负责
    this._upBar.classList.toggle("hidden", !text);
    if (text) {
      this._upBar.textContent = "";
      this._upBar.append(el("span", {}, text));
      if (up && up.unknown_inputs > 0) {
        const btn = el("button", { class: "btn btn-ghost btn-sm" }, "重新对账");
        btn.onclick = () => this._reconcileAction();
        this._upBar.append(btn);
      }
    }
  }

  async _reconcileAction(inputId) {
    try {
      const r = await fetch(
        `/ui/api/agent/${this.agentId}/conversation/reconcile`, {
        method: "POST",
        headers: { "Content-Type": "application/json",
                   "X-Requested-With": "hive-ui" },
        body: JSON.stringify({ action: "recheck", request_id: uuid(),
                               input_id: inputId }),
      }).then(r => r.json());
      if (r.ok) toast("已触发只读重新对账，稍候自动收口", "info");
      else toast(r.error?.message || "对账失败", "err", 4500);
    } catch { toast("对账请求失败", "err"); }
    setTimeout(() => this._refreshTail(), 4000);
  }

  async _refreshTail() {
    // FDEF-02 §6：增量更新——不清空整 Pane（保草稿/阅读位置/展开态）
    const gen = ++this._tailGen;
    try {
      const d = await fetch(`/ui/api/agent/${this.agentId}/conversation`,
        { headers: { "X-Requested-With": "hive-ui" } }).then(r => r.json());
      if (!d.ok || gen !== this._tailGen || this.destroyed) return;
      this._mergeTail(d.data?.tail, d.data);
      this._refreshQueue();
    } catch { /* 忽略 */ }
  }

  _mergeTail(tail, info) {
    if (!tail) return;
    const live = tail.messages || [];
    if (!this.flow || !this.flow.children.length) {
      this._renderHistory(tail, info);   // 首次或空态才整渲染
      return;
    }
    // 以稳定 id 归并：已有的就地更新（文本/状态），新的追加，缺失的移除
    const keep = new Set(live.map(m => m.id));
    for (const dom of [...this.rowsById.entries()]) {
      if (!keep.has(dom[0]) && !String(dom[0]).startsWith("hist-")) {
        dom[1].remove();
        this.rowsById.delete(dom[0]);
      }
    }
    let last = null;
    for (const m of live) {
      const exist = this.rowsById.get(m.id);
      if (exist) {
        // 就地更新正文/状态（保留 DOM：滚动位置/展开态/工具卡开合）
        const body = exist.querySelector(".conv-msg-body");
        if (body && (m.text || "") !== this._lastTexts.get(m.id)) {
          body.textContent = "";
          if (m.kind === "assistant" || m.kind === "thought")
            body.append(renderMarkdown(m.text || ""));
          else body.textContent = m.text || "";
          this._lastTexts.set(m.id, m.text || "");
        }
        const tt = exist.querySelector(".zc-think-text");
        if (tt && (m.text || "") !== this._lastTexts.get(m.id)) {
          tt.textContent = m.text || "";
          this._lastTexts.set(m.id, m.text || "");
          this._thinkScroll(exist);   // 展开态下跟随（折叠态不受影响）
        }
        exist.dataset.state = m.state || "";
      } else {
        this._appendMessage(m);
      }
      last = this.rowsById.get(m.id) || last;
    }
    this._maybeScroll();
  }

  _streamState(state) {
    let bar = this.host.querySelector(".conv-stream-state");
    if (!bar) {
      bar = el("div", { class: "conv-stream-state" });
      this.host.append(bar);
    }
    const map = {
      connecting: null, live: null,
      reconnecting: "⟳ 实时连接中断，正在重连…",
      stalled: "⟳ 实时通道无心跳（>25s），正在重连…",
      closed: "✕ 实时通道已关闭——刷新面板恢复",
      resync: "⟳ 检测到数据停流，正在校对补齐…",
    };
    const text = map[state];
    bar.classList.toggle("hidden", !text);
    if (text) bar.textContent = text;
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
    } else if (t === "agent.delta" || t === "agent.thought" ||
               t === "agent.message.final") {
      // ACP 链路 chunk 为字符级 append（无 rowId 上游帧由桥聚合成行）；
      // fixture 链路 payload.text 为累积全文。两种语义统一在此归并。
      this._rowAcc = this._rowAcc || new Map();
      const rid = ev.row_id || ev.event_id;
      let acc = this._rowAcc.get(rid);
      if (p.append) acc = (acc || "") + p.append;
      else acc = p.text != null ? p.text : (acc || "");
      this._rowAcc.set(rid, acc);
      const finalState = t === "agent.message.final" ? "completed" : null;
      this._appendMessage({
        kind: t === "agent.thought" ? "thought" : "assistant",
        id: rid, text: acc,
        state: finalState || p.state || "streaming",
        at: ev.recorded_at, replay: ev.replay });
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
