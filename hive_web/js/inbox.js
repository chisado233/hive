// 人类收件箱：列表（待处理/处理中/已回复/已结束）+ 消息详情 + 决策台
import { el, fmtAgo, fmtFull, fmtSize, toast, shortId } from "./util.js";
import { api, errText } from "./api.js";
import { renderMarkdownCollapsible, renderMarkdown } from "./markdown.js";
import { renderBlock, fileChip, refPreviewUrl, openLightbox, isDualEvalData } from "./content.js";
import { Composer } from "./composer.js";
import { store, refLabel } from "./store.js";

const TABS = [
  ["queued", "待处理"], ["claimed", "处理中"],
  ["done", "已回复"], ["ended", "已结束"],
];

export class Inbox {
  constructor(root, ctx) {
    this.root = root;          // detail-body 容器
    this.ctx = ctx;            // {onClose, openTask}
    this.tab = "queued";
    this.openTaskId = null;
    this.listCache = {};
  }

  async render() {
    document.getElementById("detail").classList.add("open");
    this.root.classList.remove("hidden");
    document.getElementById("detail-empty").classList.add("hidden");
    this.root.textContent = "";
    const head = el("div", { class: "dt-head" },
      el("div", { class: "dt-title-row" },
        el("div", { class: "dt-title" }, "✉ 人类收件箱"),
        el("button", { class: "btn btn-ghost btn-sm", title: "新建人工任务",
          onclick: () => this._newTaskDialog() }, "＋ 新建"),
        el("button", { class: "dt-close", title: "关闭",
          onclick: () => this.ctx.onClose() }, "✕")),
      el("div", { class: "dt-sub" },
        el("span", {}, "Agent 发来的问题、产物与裁决请求"),
        el("span", { class: "draft-note" }, "声音通知默认关闭")));
    const tabs = el("div", { class: "dt-tabs" },
      TABS.map(([k, label]) =>
        el("button", { class: "dt-tab" + (this.tab === k ? " active" : ""),
          onclick: () => { this.tab = k; this.openTaskId = null; this.render(); } },
          label + this._tabCount(k))));
    this.root.append(head, tabs);
    this.listEl = el("div", { class: "dt-content", style: { padding: "0" } });
    this.root.append(this.listEl);
    await this._loadList();
  }

  _tabCount(k) {
    if (k === "queued") return store.human.pending ? ` (${store.human.pending})` : "";
    if (k === "claimed") return store.human.processing ? ` (${store.human.processing})` : "";
    return "";
  }

  async _loadList() {
    this.listEl.textContent = "";
    const states = this.tab === "ended" ? ["fail", "trash"] : [this.tab];
    try {
      const all = [];
      for (const st of states) {
        const r = await api.humanInbox(st);
        all.push(...r.data.items);
      }
      all.sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
      this.listCache[this.tab] = all;
      this._renderList(all);
    } catch (e) {
      this.listEl.append(el("div", { class: "inbox-empty" }, errText(e)));
    }
  }

  _renderList(items) {
    this.listEl.textContent = "";
    if (!items.length) {
      this.listEl.append(el("div", { class: "inbox-empty" },
        this.tab === "queued" ? "没有待处理的消息 🎉" : "此分类为空"));
      return;
    }
    const list = el("div", { class: "inbox-list" });
    for (const t of items) {
      const unread = t.state === "queued" && !t.seen_at;
      const item = el("div", {
        class: "inbox-item" + (unread ? " unread" : "") +
          (this.openTaskId === t.task_id ? " active" : ""),
        onclick: () => this.open(t.task_id),
      },
        el("div", { class: "ii-top" },
          el("span", { class: `prio prio-${t.urgency}` },
            { urgent: "紧急", normal: "普通", low: "低" }[t.urgency] || t.urgency),
          el("span", { class: "ii-title" }, t.title),
          el("span", { class: "ii-time", title: fmtFull(t.created_at) },
            fmtAgo(t.created_at))),
        el("div", { class: "ii-meta" },
          el("span", {}, "来自 " + refLabel(t.source?.subject)),
          el("span", {}, shortId(t.task_id)),
          t.data && t.data.intent ? el("span", {}, "阶段 " + t.data.intent) : null,
          el("span", { class: `st st-${t.state}` }, t.state)),
        el("div", { class: "ii-preview" },
          (t.summary || t.body || "").slice(0, 80)));
      list.append(item);
    }
    this.listEl.append(list);
  }

  // ---------------- 消息详情（决策台）----------------
  async open(taskId) {
    this.openTaskId = taskId;
    this.root.textContent = "";
    this.root.append(el("div", { class: "inbox-empty" }, "加载中…"));
    let d;
    try {
      d = (await api.task(taskId, true)).data;   // mark_seen → 未读徽标即时清
    } catch (e) {
      this.root.textContent = "";
      this.root.append(el("div", { class: "inbox-empty" }, errText(e)));
      return;
    }
    const t = d.task;
    this.root.textContent = "";
    // 先建 composer（内容块的批注直接写入其草稿），最后再挂载
    this._composer = new Composer(t, {
      onSubmitted: () => { this.openTaskId = null; this.ctx.refreshBadge(); this.render(); },
      onRefresh: () => this.open(taskId),
    });

    // 头部
    const head = el("div", { class: "dt-head" },
      el("div", { class: "dt-title-row" },
        el("button", { class: "dt-close", title: "返回列表",
          onclick: () => { this.openTaskId = null; this.render(); } }, "‹"),
        el("div", { class: "dt-title" }, t.title)),
      el("div", { class: "dt-sub" },
        el("span", {}, shortId(t.task_id)),
        el("span", {}, "来自 " + refLabel(t.source?.subject)),
        el("span", {}, fmtFull(t.created_at)),
        el("span", {}, `spec v${t.spec_version}`)),
      el("div", { class: "dt-tags" },
        el("span", { class: `st st-${t.state}` }, t.state),
        el("span", { class: "tag" }, { work: "工作", notice: "通知", message: "消息", receipt: "回执" }[t.type] || t.type),
        t.urgency !== "normal" ? el("span", { class: "tag red" }, t.urgency) : null,
        t.data && t.data.intent ? el("span", { class: "tag cyan" }, t.data.intent) : null,
        t.data && t.data.domain ? el("span", { class: "tag" }, t.data.domain) : null));
    this.root.append(head);

    const body = el("div", { class: "dt-content" });

    // 上下文：父任务/回复对象
    const related = d.related || {};
    if (t.parent_id || t.reply_to) {
      const card = el("div", { class: "card" },
        el("div", { class: "card-title" }, "上下文"));
      for (const [label, rid] of [["父任务", t.parent_id], ["回应", t.reply_to]]) {
        if (!rid) continue;
        const rel = related[rid];
        const row = el("div", { class: "task-row", onclick: () => this.open(rid) },
          el("span", { class: "t-state" }, label),
          el("span", { class: "t-title" }, rel ? rel.title : rid),
          rel ? el("span", { class: `st st-${rel.state}` }, rel.state) : null);
        card.append(row);
      }
      body.append(card);
    }

    // 正文
    if (t.body) {
      body.append(el("div", { class: "card" },
        el("div", { class: "card-title" }, "正文"),
        renderMarkdownCollapsible(t.body)));
    }

    // 富内容块
    const ui = (t.data && t.data.ui) || {};
    const gallery = [];
    if (Array.isArray(ui.blocks) && ui.blocks.length) {
      const card = el("div", { class: "card" },
        el("div", { class: "card-title" }, "内容"));
      const draft = {};   // 批注写入 composer 草稿，在 composer.render 才真正使用
      for (const b of ui.blocks) {
        if (b.kind === "image") {
          const url = b.url || refPreviewUrl(t.task_id, b.ref);
          if (url) gallery.push({ url, caption: b.title || b.ref || "" });
        }
      }
      // 内容块（批注草稿透传 composer）
      card.append(el("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
        ...ui.blocks.map(b => renderBlock(b, {
          taskId: t.task_id,
          draft: this._composer.draft,
          onDraft: () => this._composer.saveDraft(),
          openImageGallery: (blk) => {
            const idx = gallery.findIndex(g => g.caption === (blk.title || blk.ref));
            openLightbox(gallery, Math.max(0, idx));
          },
        }))));
      body.append(card);
    }

    // 附件（含无 ui 描述的旧任务）
    const atts = t.attachments || [];
    if (atts.length) {
      const card = el("div", { class: "card" },
        el("div", { class: "card-title" }, "附件"));
      for (const a of atts) {
        card.append(fileChip(t.task_id, typeof a === "string" ? a : a.uri,
          typeof a === "string" ? null : (a.name || a.uri)));
      }
      body.append(card);
    }

    // 产物与审核意见并列（双重评价上下文）
    if (isDualEvalData(t.data)) {
      body.append(this._dualContext(t));
    }

    // 原始 data（仅当有 UI/已知元数据之外的键时才展开，避免噪音）
    const KNOWN = new Set(["ui", "project", "intent", "line", "domain", "run_id",
      "episode_id", "revision", "input_refs", "output_root", "locked_versions",
      "candidate_ref", "baseline_ref", "hypothesis", "constraints",
      "authority_ref", "feedback_visibility", "domain", "artifact_ref",
      "review_ref", "predicted_artifact_score"]);
    const extraKeys = Object.keys(t.data || {}).filter(k => !KNOWN.has(k));
    if (extraKeys.length && !isDualEvalData(t.data)) {
      const det = el("details", { class: "card" },
        el("summary", { class: "card-title", style: { cursor: "pointer" } }, "结构化数据 (data)"),
        el("pre", { class: "mono", style: { whiteSpace: "pre-wrap", fontSize: "11px" } },
          JSON.stringify(t.data, null, 1)));
      body.append(det);
    }

    // 评论线程
    if (d.comments && d.comments.length) {
      const card = el("div", { class: "card" },
        el("div", { class: "card-title" }, `讨论（${d.comments.length}）`));
      for (const c of d.comments) {
        card.append(el("div", { style: { padding: "6px 0", borderBottom: "1px solid var(--line)" } },
          el("div", { style: { fontSize: "11px", color: "var(--text-2)", marginBottom: "3px" } },
            `${refLabel(c.author)} · ${fmtAgo(c.created_at)}` +
            (c.revision_of ? " · 修订评论" : "")),
          renderMarkdown(c.body || "")));
      }
      body.append(card);
    }

    // 终态结果
    if (["done", "fail", "trash"].includes(t.state) && t.result) {
      body.append(this._resultCard(t));
    }

    this.root.append(body);

    // 决策台（composer 已在内容块之前创建，直接挂载）
    this._composer.mount(this.root);
  }

  _dualContext(t) {
    const d = t.data;
    const card = el("div", { class: "card" },
      el("div", { class: "card-title" }, "本轮评价上下文"));
    const rows = [
      ["领域", d.domain], ["产物引用", d.artifact_ref], ["审核引用", d.review_ref],
    ];
    const kv = el("dl", { class: "kv-grid" });
    for (const [k, v] of rows) {
      if (!v) continue;
      kv.append(el("dt", {}, k), el("dd", {}, String(v)));
    }
    card.append(kv);
    if (d.predicted_artifact_score != null)
      card.append(el("div", { class: "predict-note" },
        `审核Agent预测分：${d.predicted_artifact_score}（独立标识，不代表人评）`));
    return card;
  }

  _resultCard(t) {
    const r = t.result || {};
    const card = el("div", { class: "card" },
      el("div", { class: "card-title" }, "已提交结果",
        el("span", { class: "spacer" }),
        el("span", { class: `st st-${t.state}` }, t.state)));
    if (r.ended_by) card.append(el("div", { class: "field-help" },
      `回答者 ${refLabel(r.ended_by)} · ${fmtFull(r.ended_at)}`));
    if (r.text) card.append(renderMarkdown(r.text));
    if (r.data && Object.keys(r.data).length) {
      // 双重评分结果：结构化呈现
      const dd = r.data;
      if ("artifact_score" in dd || "review_score" in dd) {
        const kv = el("dl", { class: "kv-grid" });
        const add = (k, v) => kv.append(el("dt", {}, k),
          el("dd", { class: v == null ? "unknown" : "" },
            v == null ? "未评分" : Array.isArray(v) ? v.join("；") : String(v)));
        add("产物判决", dd.artifact_decision);
        add("产物评分", dd.artifact_score);
        add("审核评价", dd.review_decision);
        add("审核评分", dd.review_score);
        if (dd.keep?.length) add("应保留", dd.keep);
        if (dd.change?.length) add("需修改", dd.change);
        if (dd.review_disagreements?.length) add("审核误判", dd.review_disagreements);
        if (dd.review_omissions?.length) add("审核遗漏", dd.review_omissions);
        if (dd.video_notes?.length)
          add("视频批注", dd.video_notes.map(n => `[${Math.round(n.t)}s] ${n.text}`));
        if (dd.explicit_autonomy_grant)
          add("自主授权", dd.explicit_autonomy_grant.grant
            ? `已授权：${dd.explicit_autonomy_grant.scope}` : "未授权");
        card.append(kv);
      } else {
        card.append(el("pre", { class: "mono", style: { whiteSpace: "pre-wrap", fontSize: "11px" } },
          JSON.stringify(dd, null, 1)));
      }
    }
    if (r.artifacts && r.artifacts.length) {
      for (const a of r.artifacts) card.append(fileChip(t.task_id, a.uri));
    }
    if (r.reason) card.append(el("div", { class: "field-err" }, "原因：" + r.reason));
    return card;
  }

  // ---------------- 新建人工任务（演示/自测入口，明确标识）----------------
  async _newTaskDialog() {
    const modalRoot = document.getElementById("modal-root");
    const title = el("input", { class: "text-input", value: "" });
    const bodyIn = el("textarea", { class: "text-input", rows: 4 });
    const rich = el("input", { type: "checkbox" });
    const humanPorts = [...store.ports.values()].filter(p => p.handler === "human");
    const portSel = el("select", { class: "text-input" },
      humanPorts.map(p => el("option", { value: "port:" + p.port_id },
        `${p.name || p.route}（${p.route}）`)));
    const modal = el("div", { class: "modal" },
      el("h3", {}, "新建人工任务"),
      el("label", { class: "f-label" }, "目标人工端口", portSel),
      el("label", { class: "f-label" }, "标题", title),
      el("label", { class: "f-label" }, "正文（Markdown）", bodyIn),
      el("label", { class: "opt" }, rich,
        el("span", { class: "opt-label" }, "附加演示富内容（选项+双评分滑块+图片+视频占位块，标题带【演示】标识）")),
      el("div", { class: "modal-actions" },
        el("button", { class: "btn btn-ghost", onclick: () => mask.remove() }, "取消"),
        el("button", { class: "btn btn-primary", onclick: async (e) => {
          if (!title.value.trim()) { toast("请填标题", "err"); return; }
          const btn = e.target; btn.disabled = true;
          try {
            const data = {};
            let ttl = title.value.trim();
            if (rich.checked) {
              ttl = "【演示】" + ttl;
              data.ui = {
                schema: "hive-ui/1",
                blocks: [
                  { kind: "markdown", text: "这是一段 **Markdown** 内容块：\n\n- 列表项\n- `code`\n\n| 列 | 值 |\n|---|---|\n| A | 1 |" },
                  { kind: "image", ref: "demo://image", title: "演示图片占位（未绑定真实文件）" },
                  { kind: "video", ref: "demo://video", title: "演示视频占位（未绑定真实文件）" },
                ],
                fields: [
                  { id: "demo_choice", type: "single_select", required: true,
                    label: "选择一个方向", options: [
                      { id: "a", label: "方案 A", desc: "保守" },
                      { id: "b", label: "方案 B", desc: "激进" }] },
                  { id: "demo_score_a", type: "score", min: 0, max: 100,
                    step: 1, required: true, label: "评分一" },
                  { id: "demo_score_b", type: "score", min: 0, max: 100,
                    step: 1, required: false, label: "评分二（可选）" },
                ],
              };
            }
            await api.command("emit.new", {
              root: true, to: portSel.value, type: "work",
              title: ttl, body: bodyIn.value, data,
            });
            toast("人工任务已投递", "ok");
            mask.remove();
            this.render();
          } catch (err) {
            btn.disabled = false;
            toast(errText(err), "err", 5000);
          }
        } }, "投递")));
    const mask = el("div", { class: "modal-mask", onclick: (e) => {
      if (e.target === mask) mask.remove(); } }, modal);
    modalRoot.append(mask);
  }
}
