// 节点详情面板：Agent / 资产库 / 端口 / 任务线 / 任务
// 所有操作走真实命令；"已受理"与"已完成"分态反馈
import { el, fmtAgo, fmtFull, shortId, toast, uuid } from "./util.js";
import { api, errText } from "./api.js";
import { store, refLabel } from "./store.js";
import { renderMarkdownCollapsible } from "./markdown.js";

export class Detail {
  constructor(body, ctx) {
    this.body = body;      // #detail-body
    this.ctx = ctx;        // {close(), locate(nodeId), openTask(id), openAsset(key)}
  }

  show() {
    this.body.classList.remove("hidden");
    document.getElementById("detail-empty").classList.add("hidden");
  }

  _head(title, sub = [], tags = []) {
    return el("div", { class: "dt-head" },
      el("div", { class: "dt-title-row" },
        el("div", { class: "dt-title" }, title),
        el("button", { class: "dt-close", onclick: () => this.ctx.close() }, "✕")),
      sub.length ? el("div", { class: "dt-sub" }, sub.map(s => el("span", {}, s))) : null,
      tags.length ? el("div", { class: "dt-tags" }, tags) : null);
  }

  _tabs(tabs, active, onPick) {
    return el("div", { class: "dt-tabs" },
      tabs.map(t => el("button", {
        class: "dt-tab" + (t === active ? " active" : ""),
        onclick: () => onPick(t) }, t)));
  }

  // ================= Agent =================
  async agent(agentId, tab = "概览") {
    this.show();
    this.body.textContent = "";
    this.body.append(el("div", { class: "inbox-empty" }, "加载中…"));
    let d;
    try {
      d = (await api.agent(agentId)).data;
    } catch (e) {
      this.body.textContent = "";
      this.body.append(el("div", { class: "inbox-empty" }, errText(e)));
      return;
    }
    const a = d.agent, cfg = d.config || {}, rt = d.runtime || {};
    const meta = cfg.metadata || {};
    const runtime = cfg.runtime || {};
    this.body.textContent = "";
    this.body.append(this._head(a.name, [
      shortId(a.agent_id),
      meta.agent_key || "",
      meta.project ? "项目 " + meta.project : "未归属项目",
    ], [
      el("span", { class: "tag " + (a.lifecycle === "enabled" ? "green" : "red") }, a.lifecycle),
      rt.active_instance ? el("span", { class: "tag cyan" },
        `实例 ${shortId(rt.active_instance.instance_id)} · ${rt.active_instance.phase}` +
        (rt.active_instance.observation ? ` · ${rt.active_instance.observation}` : ""))
        : el("span", { class: "tag" }, "无运行实例"),
      meta.role ? el("span", { class: "tag amber" }, meta.role) : null,
    ].filter(Boolean)));
    this.body.append(this._tabs(["概览", "任务", "操作", "事件"], tab,
      t => this.agent(agentId, t)));
    const c = el("div", { class: "dt-content" });
    this.body.append(c);

    if (tab === "概览") this._agentOverview(c, a, cfg, rt, d);
    else if (tab === "任务") this._agentTasks(c, d);
    else if (tab === "操作") this._agentOps(c, a, cfg, rt);
    else this._agentEvents(c, a.agent_id);
  }

  _agentOverview(c, a, cfg, rt, d) {
    const meta = cfg.metadata || {};
    const runtime = cfg.runtime || {};
    const ws = cfg.workspace || {};
    const kv = el("dl", { class: "kv-grid" });
    const row = (k, v, unknown = false) => kv.append(el("dt", {}, k),
      el("dd", { class: v == null || v === "" || unknown ? "unknown" : "" },
        v == null || v === "" ? "未知" : String(v)));
    row("agent_id", a.agent_id);
    row("项目标识", meta.agent_key);
    row("线路 / 领域", [meta.line, meta.domain].filter(Boolean).join(" / "));
    row("职责", meta.role);
    row("运行根目录", runtime.options && runtime.options.execution_root);
    row("独立工位", a.workspace);
    row("生命周期", a.lifecycle);
    row("实例数", String((rt.instances || []).filter(i =>
      ["starting", "live", "stopping"].includes(i.phase)).length));
    row("当前实例", rt.active_instance && rt.active_instance.instance_id);
    row("Harness", runtime.harness);
    row("配置模型路由", meta.model_route || runtime.model);
    row("实际模型落点", d.model_actual, !d.model_actual);
    if (!d.model_actual)
      kv.append(el("dt"), el("dd", { class: "unknown" },
        "（无原生观测渠道时显示未知，不冒充空闲）"));
    row("配置版本", `${rt.applied_revision ?? "?"}/${rt.desired_revision ?? "?"}`);
    row("观测限制", "实例存活=句柄在册；忙碌/空闲依 harness observe 回报");
    c.append(el("div", { class: "card" }, el("div", { class: "card-title" }, "身份与运行"), kv));

    const resp = rt.responsibility || {};
    const rc = el("div", { class: "card" },
      el("div", { class: "card-title" }, "当前责任"));
    rc.append(el("div", { class: "kv-grid" },
      el("dt", {}, "进行中任务"), el("dd", {}, String(resp.claimed_count || 0)),
      el("dt", {}, "个人待办"), el("dd", {}, String(resp.personal_queued || 0)),
      el("dt", {}, "等待登记"), el("dd", {}, String(resp.pending_waits || 0))));
    for (const t of resp.claimed_tasks || []) {
      rc.append(el("div", { class: "task-row",
        onclick: () => this.ctx.openTask(t.task_id) },
        el("span", { class: "t-title" }, t.title || t.task_id),
        el("span", { class: "st st-claimed" }, "claimed")));
    }
    c.append(rc);

    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, "数据"),
      el("div", { class: "field-help" },
        "更新于 " + fmtAgo(store.serverTime) + " · 轮询快照，非持续推送"),
      el("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } },
        el("button", { class: "btn btn-ghost btn-sm",
          onclick: () => this.ctx.locate("agent:" + a.agent_id) }, "在图中定位"),
        el("button", { class: "btn btn-ghost btn-sm",
          onclick: () => this.agent(a.agent_id, "概览") }, "刷新"))));
  }

  _agentTasks(c, d) {
    const tasks = (d.tasks || []).slice().sort((x, y) =>
      String(y.created_at).localeCompare(String(x.created_at)));
    const card = el("div", { class: "card" },
      el("div", { class: "card-title" }, `相关任务（${tasks.length}）`));
    if (!tasks.length) card.append(el("div", { class: "field-help" }, "暂无"));
    for (const t of tasks.slice(0, 60)) {
      card.append(el("div", { class: "task-row",
        onclick: () => this.ctx.openTask(t.task_id) },
        el("span", { class: "t-title" }, t.title),
        el("span", { class: "t-state" }, fmtAgo(t.created_at)),
        el("span", { class: `st st-${t.state}` }, t.state)));
    }
    c.append(card);
    if (d.waits && d.waits.length) {
      const wc = el("div", { class: "card" },
        el("div", { class: "card-title" }, `等待登记（${d.waits.length}）`));
      for (const w of d.waits) {
        wc.append(el("div", { class: "task-row",
          onclick: () => this.ctx.openTask(w.owner_task_id) },
          el("span", { class: "t-title" }, `等待 ${w.mode} · ${w.state}`),
          el("span", { class: "t-state" }, w.deadline ? fmtAgo(w.deadline) : "")));
      }
      c.append(wc);
    }
  }

  _agentEvents(c, agentId) {
    const card = el("div", { class: "card" },
      el("div", { class: "card-title" }, "近期事件（来自事件带缓存）"));
    const rows = (this.ctx.eventLog || []).filter(e =>
      e.entity === "agent:" + agentId).slice(-30).reverse();
    if (!rows.length)
      card.append(el("div", { class: "field-help" },
        store.canObserve ? "暂无该Agent事件" : "当前身份无 system.observe 权限，事件不可见"));
    for (const e of rows)
      card.append(el("div", { class: "task-row" },
        el("span", { class: "t-title" }, e.kind),
        el("span", { class: "t-state" }, fmtAgo(e.recorded_at))));
    c.append(card);
  }

  // ---- 操作 ----
  _agentOps(c, a, cfg, rt) {
    const log = el("div", { class: "op-log" });
    const run = async (label, command, args, btn) => {
      btn.disabled = true;
      const row = el("div", { class: "op-row" },
        el("div", { class: "op-head" },
          el("span", {}, label),
          el("span", { class: "op-state op-accepted" }, "已受理，执行中…")));
      log.prepend(row);
      try {
        const r = await api.command(command, args, uuid(), 40000);
        const d = r.data || {};
        if (d.operation_id) {
          const op = await api.waitOperation(d.operation_id, (op) => {
            row.querySelector(".op-state").textContent =
              `已受理 · ${op.state}`;
          });
          const st = row.querySelector(".op-state");
          st.className = "op-state op-" + op.state;
          st.textContent = { succeeded: "✓ 已完成", failed: "✗ 失败",
            cancelled: "已取消" }[op.state] || op.state;
          if (op.error) row.append(el("div", { class: "op-err" },
            typeof op.error === "string" ? op.error : JSON.stringify(op.error)));
          if (op.result && op.state === "succeeded")
            row.append(el("div", { class: "field-help" },
              Object.entries(op.result).filter(([k, v]) => v != null &&
                ["instance_id", "model", "delivery_mode", "degraded", "generation"]
                  .includes(k))
                .map(([k, v]) => `${k}=${v}`).join(" · ")));
        } else {
          const st = row.querySelector(".op-state");
          st.className = "op-state op-succeeded";
          st.textContent = "✓ 已完成";
          if (d.coalesced) row.append(el("div", { class: "field-help" }, "唤醒请求已合并"));
          if (d.reason) row.append(el("div", { class: "field-help" }, d.reason));
          if (d.delivery_mode)
            row.append(el("div", { class: "field-help" },
              `投递模式 ${d.delivery_mode}${d.degraded ? "（降级）" : ""}`));
          if (d.desired_model)
            row.append(el("div", { class: "field-help" },
              `模型期望 → ${d.desired_model}（${d.apply || "auto"}）`));
        }
        toast(`${label} 完成`, "ok");
      } catch (e) {
        const st = row.querySelector(".op-state");
        st.className = "op-state op-failed";
        st.textContent = "✗ 被拒绝/失败";
        row.append(el("div", { class: "op-err" }, errText(e)));
        toast(`${label} 失败：${errText(e)}`, "err", 5000);
      } finally {
        btn.disabled = false;
      }
    };

    const mk = (label, hint, fn, cls = "") => {
      const btn = el("button", { class: "btn " + cls }, label);
      btn.title = hint;
      btn.onclick = () => fn(btn);
      return btn;
    };
    const id = a.agent_id;
    const askReason = (title, fields, onOk) => this._reasonDialog(title, fields, onOk);

    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, "通信"),
      el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
        mk("发消息", "向实例队列发送文本", (btn) => askReason("发消息给 " + a.name, [
          { key: "text", label: "内容", type: "textarea", required: true },
          { key: "mode", label: "模式", type: "select",
            options: [["queue", "queue（排队）"], ["interrupt", "interrupt（打断）"]],
            value: "queue" },
          { key: "reason", label: "原因（审计）", value: "指挥台操作" },
        ], (v) => run("发消息", "control.agent.send",
          { agent: id, text: v.text, mode: v.mode, reason: v.reason }, btn))),
        mk("发任务", "向该Agent投递个人work任务", () => this._newTaskToAgent(a)),
        mk("唤醒", "提醒查看inbox（非业务任务）", (btn) =>
          run("唤醒", "control.agent.wake", { agent: id }, btn), "btn-cyan"))));

    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, "生命周期"),
      el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
        mk("启动", "启动实例", (btn) => run("启动", "control.agent.start",
          { agent: id }, btn)),
        mk("停止", "保存提示宽限后停止", (btn) => askReason("停止 " + a.name, [
          { key: "reason", label: "原因（审计）", required: true, value: "" },
        ], (v) => run("停止", "control.agent.stop",
          { agent: id, reason: v.reason }, btn)), "btn-danger"),
        mk("重启", "身份与责任不变", (btn) => askReason("重启 " + a.name, [
          { key: "reason", label: "原因（审计）", required: true, value: "" },
        ], (v) => run("重启", "control.agent.restart",
          { agent: id, reason: v.reason }, btn))))));

    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, "模型"),
      el("div", { style: { display: "flex", gap: "8px" } },
        mk("切换模型", "auto=优先热切换", (btn) => askReason("切换模型", [
          { key: "model", label: "目标模型（harness可识别的名字）",
            required: true, value: (cfg.runtime || {}).model || "" },
          { key: "apply", label: "生效方式", type: "select",
            options: [["auto", "auto"], ["next-start", "next-start"],
                      ["restart", "restart"]], value: "auto" },
          { key: "reason", label: "原因（审计）", required: true,
            value: "指挥台切换模型" },
        ], (v) => run("切换模型", "control.agent.model",
          { agent: id, model: v.model, apply: v.apply, reason: v.reason }, btn))))));

    // 退役：先 dry-run 展示未结责任，逐项决定去向，不静默丢任务
    const retireBtn = el("button", { class: "btn btn-danger" }, "退役…");
    retireBtn.onclick = () => this._retireFlow(a);
    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, "危险操作"),
      el("div", { class: "field-help" },
        "退役将停止实例并转移/结束未结责任；执行前会列出全部未结事项并要求逐项处置。"),
      retireBtn));

    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, "操作记录（本次会话）"), log));
  }

  async _retireFlow(a) {
    try {
      const dry = await api.command("control.agent.retire", {
        agent: a.agent_id, plan: { reason: "退役评估" }, dry_run: true,
        reason: "退役评估",
      });
      const blockers = dry.data.blockers || [];
      const modalRoot = document.getElementById("modal-root");
      const list = el("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } });
      if (!blockers.length)
        list.append(el("div", { class: "field-help" }, "无未结责任，可安全退役。"));
      const picks = [];
      for (const b of blockers) {
        const sel = el("select", { class: "text-input", style: { width: "auto" } },
          el("option", { value: "keep" }, "保留（仍阻塞退役）"),
          el("option", { value: "trash" }, "作废"),
          el("option", { value: "forward" }, "转交原端口"));
        picks.push({ b, sel });
        list.append(el("div", { class: "card", style: { padding: "8px 10px" } },
          el("div", { style: { fontSize: "12.5px" } }, `${b.title || b.task_id}`),
          el("div", { class: "field-help" }, `${b.why} · ${b.state}`),
          sel));
      }
      const reason = el("input", { class: "text-input", placeholder: "退役原因（审计，必填）" });
      const modal = el("div", { class: "modal" },
        el("h3", {}, `退役 ${a.name}`),
        el("div", { class: "field-help" },
          `共 ${blockers.length} 项未结责任。保留项将阻止完全退役（保持 retiring）。`),
        list, reason,
        el("div", { class: "modal-actions" },
          el("button", { class: "btn btn-ghost", onclick: () => mask.remove() }, "取消"),
          el("button", { class: "btn btn-danger", onclick: async (e) => {
            if (!reason.value.trim()) { toast("必须填写退役原因", "err"); return; }
            e.target.disabled = true;
            const work = picks.filter(p => p.sel.value !== "keep").map(p => ({
              task_id: p.b.task_id, action: p.sel.value,
              reason: reason.value.trim(),
            }));
            try {
              const r = await api.command("control.agent.retire", {
                agent: a.agent_id,
                plan: { reason: reason.value.trim(), stop: true, work },
                reason: reason.value.trim(),
              });
              const d = r.data || {};
              toast(`退役${d.state === "retired" ? "完成" : "进行中（仍有未结责任）"}` +
                (d.dispositions ? `，处置 ${d.dispositions.length} 项` : ""), "ok");
              mask.remove();
            } catch (err) {
              e.target.disabled = false;
              toast(errText(err), "err", 6000);
            }
          } }, "执行退役")));
      const mask = el("div", { class: "modal-mask",
        onclick: (e) => { if (e.target === mask) mask.remove(); } }, modal);
      modalRoot.append(mask);
    } catch (e) {
      toast(errText(e), "err", 5000);
    }
  }

  _newTaskToAgent(a) {
    this._reasonDialog("发任务给 " + a.name, [
      { key: "title", label: "标题", required: true },
      { key: "body", label: "正文（Markdown）", type: "textarea", required: true },
      { key: "urgency", label: "紧急度", type: "select",
        options: [["normal", "普通"], ["urgent", "紧急"], ["low", "低"]],
        value: "normal" },
      { key: "callback", label: "回执目标", type: "select",
        options: [["none", "不需要回执"], ["self", "回执给我（owner）"]],
        value: "self" },
    ], async (v) => {
      try {
        // owner 不是 Agent，不能登记 self 回执；无callback任务结果由有权者主动查看
        const r = await api.command("emit.new", {
          root: true, to: "agent:" + a.agent_id, type: "work",
          title: v.title, body: v.body, urgency: v.urgency, callback: "none",
        });
        toast("任务已投递（" + shortId(r.data.task?.task_id || "") + "）", "ok");
      } catch (e) { toast(errText(e), "err", 5000); }
    });
  }

  _reasonDialog(title, fields, onOk) {
    const modalRoot = document.getElementById("modal-root");
    const inputs = {};
    const modal = el("div", { class: "modal" }, el("h3", {}, title));
    for (const f of fields) {
      let input;
      if (f.type === "textarea")
        input = el("textarea", { class: "text-input", rows: 4 });
      else if (f.type === "select")
        input = el("select", { class: "text-input" },
          (f.options || []).map(([v, l]) =>
            el("option", { value: v, selected: v === f.value ? "" : null }, l)));
      else input = el("input", { class: "text-input", value: f.value || "" });
      inputs[f.key] = input;
      modal.append(el("label", { class: "f-label" },
        f.label + (f.required ? " *" : ""), input));
    }
    modal.append(el("div", { class: "modal-actions" },
      el("button", { class: "btn btn-ghost", onclick: () => mask.remove() }, "取消"),
      el("button", { class: "btn btn-primary", onclick: () => {
        const v = {};
        for (const f of fields) {
          const val = inputs[f.key].value;
          if (f.required && !String(val).trim()) {
            toast(`请填写：${f.label}`, "err"); return;
          }
          v[f.key] = val;
        }
        mask.remove(); onOk(v);
      } }, "确定")));
    const mask = el("div", { class: "modal-mask",
      onclick: (e) => { if (e.target === mask) mask.remove(); } }, modal);
    modalRoot.append(mask);
  }

  // ================= 任务线 / 端口 / 任务 =================
  edge(sel) {
    this.show();
    this.body.textContent = "";
    const clsLabel = { flow: "任务流", callback: "回执流", sub: "订阅",
      "asset-read": "声明读取" }[sel.cls] || sel.cls;
    this.body.append(this._head(`${clsLabel}（${sel.tasks.length} 件）`, [
      refLabel(sel.from) + " → " + refLabel(sel.to)]));
    const c = el("div", { class: "dt-content" });
    this.body.append(c);
    const card = el("div", { class: "card" });
    for (const tid of sel.tasks) {
      const t = store.flows.get(tid);
      card.append(el("div", { class: "task-row",
        onclick: () => this.task(tid) },
        el("span", { class: "t-title" }, t ? t.title : tid),
        t ? el("span", { class: `st st-${t.state}` }, t.state) : null));
    }
    c.append(card);
  }

  port(portRef) {
    const p = store.ports.get(portRef.split(":")[1]);
    if (!p) return;
    this.show();
    this.body.textContent = "";
    this.body.append(this._head(p.name || p.route, [
      p.port_id, `handler: ${p.handler}`, `visibility: ${p.visibility}`],
      [el("span", { class: "tag" }, p.state)]));
    const c = el("div", { class: "dt-content" });
    const subs = store.subscriptions.filter(s => s.port_id === p.port_id && s.enabled);
    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, `订阅者（${subs.length}）`),
      ...subs.map(s => {
        const ag = store.agents.get(s.agent_id);
        return el("div", { class: "task-row",
          onclick: () => ag && this.agent(s.agent_id) },
          el("span", { class: "t-title" }, ag ? ag.name : s.agent_id));
      })));
    const queued = [...store.flows.values()].filter(t =>
      t.target === "port:" + p.port_id);
    c.append(el("div", { class: "card" },
      el("div", { class: "card-title" }, `在途任务（${queued.length}）`),
      ...queued.map(t => el("div", { class: "task-row",
        onclick: () => this.task(t.task_id) },
        el("span", { class: "t-title" }, t.title),
        el("span", { class: `st st-${t.state}` }, t.state)))));
    this.body.append(c);
  }

  async task(taskId) {
    this.show();
    this.body.textContent = "";
    this.body.append(el("div", { class: "inbox-empty" }, "加载中…"));
    let d;
    try {
      d = (await api.task(taskId)).data;
    } catch (e) {
      this.body.textContent = "";
      this.body.append(el("div", { class: "inbox-empty" }, errText(e)));
      return;
    }
    const t = d.task;
    this.body.textContent = "";
    this.body.append(this._head(t.title, [
      t.task_id,
      `${refLabel(t.source?.subject)} → ${refLabel(t.target)}`,
      `创建 ${fmtFull(t.created_at)}`,
    ], [
      el("span", { class: `st st-${t.state}` }, t.state),
      el("span", { class: "tag" }, t.type),
      t.owner_subject ? el("span", { class: "tag cyan" },
        "领取者 " + refLabel("agent:" + t.owner_subject)) : null,
    ].filter(Boolean)));
    const c = el("div", { class: "dt-content" });
    const kv = el("dl", { class: "kv-grid" });
    const row = (k, v) => { if (v != null && v !== "") kv.append(el("dt", {}, k), el("dd", {}, String(v))); };
    row("Task ID", t.task_id);
    row("父任务", t.parent_id);
    row("根任务", t.root_id);
    row("回执目标", t.callback && t.callback.target);
    row("回应", t.reply_to);
    row("spec_version", "v" + t.spec_version);
    row("领取截止", t.claim_deadline);
    row("有效期至", t.expires_at);
    row("结束时间", t.ended_at);
    c.append(el("div", { class: "card" }, el("div", { class: "card-title" }, "任务"), kv));
    if (t.body)
      c.append(el("div", { class: "card" },
        el("div", { class: "card-title" }, "正文"),
        renderMarkdownCollapsible(t.body)));
    if (t.result) {
      const r = t.result;
      c.append(el("div", { class: "card" },
        el("div", { class: "card-title" }, "结果"),
        r.text ? renderMarkdownCollapsible(r.text) : null,
        el("pre", { class: "mono", style: { whiteSpace: "pre-wrap", fontSize: "11px" } },
          JSON.stringify({ data: r.data, artifacts: r.artifacts,
            reason: r.reason, ended_by: r.ended_by }, null, 1))));
    }
    if (d.comments && d.comments.length) {
      const cc = el("div", { class: "card" },
        el("div", { class: "card-title" }, `评论（${d.comments.length}）`));
      for (const cm of d.comments)
        cc.append(el("div", { class: "field-help" },
          `${refLabel(cm.author)} · ${fmtAgo(cm.created_at)}：${(cm.body || "").slice(0, 140)}`));
      c.append(cc);
    }
    if (d.history && d.history.length) {
      const hc = el("div", { class: "card" },
        el("div", { class: "card-title" }, "流转记录"));
      for (const hrec of d.history.slice(-20))
        hc.append(el("div", { class: "field-help" },
          `${fmtAgo(hrec.at || hrec.created_at)} · ${hrec.kind} ` +
          (hrec.from ? `${refLabel(hrec.from)} → ${refLabel(hrec.to)}` : "")));
      c.append(hc);
    }
    this.body.append(c);
  }
}
