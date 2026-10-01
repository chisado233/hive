// 回复与裁决编排器：评论(不结算) 与 提交答复(结算) 严格分离
// 幂等：request_id 随草稿固定，重试沿用；提交中禁用重复点击
import { el, toast, uuid, lsGet, lsSet } from "./util.js";
import { api, errText, ApiError } from "./api.js";
import { renderField, isDualEvalData, dualEvalFields, normalizeDualAnswers } from "./content.js";

export class Composer {
  // task: 完整 task card；ctx: {onSubmitted(), onRefresh()}
  constructor(task, ctx) {
    this.task = task;
    this.ctx = ctx;
    this.mode = "comment";
    this.submitting = false;
    this.draftKey = `hive.draft.${task.task_id}`;
    this.draft = lsGet(this.draftKey, null) || {
      request_id: uuid(), spec_version: task.spec_version,
      text: "", answers: {}, uploads: [], video_notes: [], image_notes: {},
    };
    if (this.draft.spec_version !== task.spec_version) {
      // 版本已变：保留草稿但标记冲突，不静默覆盖
      this.versionDrift = { from: this.draft.spec_version, to: task.spec_version };
    }
    this.fields = [];
  }

  saveDraft() {
    this.draft.text = this.textEl ? this.textEl.value : this.draft.text;
    for (const f of this.fields) this.draft.answers[f.id] = f.get();
    lsSet(this.draftKey, this.draft);
  }

  _rerender() {
    // 模式切换只重排 composer，不重新拉取任务（保留输入中的草稿）
    if (this.textEl) this.saveDraft();
    const fresh = this.render();
    if (this.mountedEl && this.mountedEl.parentNode)
      this.mountedEl.replaceWith(fresh);
    this.mountedEl = fresh;
  }

  mount(parent) {
    const node = this.render();
    this.mountedEl = node;
    parent.append(node);
    return node;
  }

  render() {
    const t = this.task;
    const terminal = ["done", "fail", "trash"].includes(t.state);
    const root = el("div", { class: "composer" });
    this.rootEl = root;

    if (this.versionDrift) {
      root.append(el("div", { class: "conflict-banner" },
        `问题版本已从 v${this.versionDrift.from} 变为 v${this.versionDrift.to}。草稿已保留，请核对新内容后再提交。`,
        el("button", { class: "btn btn-sm", onclick: () => {
          this.versionDrift = null;
          this.draft.spec_version = t.spec_version;
          this.draft.request_id = uuid();      // 新版本 = 新回答，新 request-id
          this.saveDraft();
          this.ctx.onRefresh();
        } }, "已核对，继续编辑")));
    }

    if (terminal) {
      root.append(el("div", { class: "field-help" },
        "任务已终态，结果不可改写。可追加补充/纠正评论（保留修订关联，不覆盖原答案）。"));
      const area = this._commentArea();
      root.append(area);
      return root;
    }

    // 模式切换
    const tabs = el("div", { class: "composer-tabs" },
      el("button", { class: "composer-mode comment" + (this.mode === "comment" ? " active" : "") },
        "💬 发送补充 / 评论", el("small", {}, "保存交流内容并提醒相关方，不结束任务")),
      el("button", { class: "composer-mode answer" + (this.mode === "answer" ? " active" : "") },
        "⚖️ 提交答复 / 作出裁决", el("small", {}, "校验必填后入账并恢复等待中的工作")));
    tabs.children[0].onclick = () => { this.mode = "comment"; this._rerender(); };
    tabs.children[1].onclick = () => { this.mode = "answer"; this._rerender(); };
    root.append(tabs);

    if (this.mode === "comment") {
      root.append(this._commentArea());
      return root;
    }

    // ---- answer 模式：字段区 ----
    const data = t.data || {};
    const ui = data.ui || {};
    const fieldsHost = el("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } });
    this.fields = [];
    if (isDualEvalData(data)) {
      fieldsHost.append(el("div", { class: "dual-col-title" }, "双重评价——产物与审核分别评分"));
      const grid = el("div", { class: "dual" });
      // SDEF-02：优先服务端 schema（与后端校验同源），本地副本仅离线兜底
      const schema = Array.isArray(this.task.eval_fields) &&
        this.task.eval_fields.length ? this.task.eval_fields
        : dualEvalFields(data);
      for (const f of schema) {
        const w = renderField(f, this.draft.answers[f.id], () => this.saveDraft());
        this.fields.push(w);
        (["artifact_decision", "artifact_score", "review_decision", "review_score"]
          .includes(f.id) ? grid : fieldsHost).append(w.el);
      }
      fieldsHost.prepend(grid);
      // 审核Agent预测分：仅参考标识，不预填
      const predicted = data.predicted_artifact_score;
      if (predicted != null) {
        fieldsHost.append(el("div", { class: "predict-note" },
          `审核Agent预测分（仅供参考，不构成人评）：${predicted}`));
      }
      fieldsHost.append(this._grantBox(data));
    }
    for (const f of ui.fields || []) {
      const w = renderField(f, this.draft.answers[f.id], () => this.saveDraft());
      this.fields.push(w);
      fieldsHost.append(w.el);
    }
    root.append(fieldsHost);

    // 说明文字 + 附件
    this.textEl = el("textarea", { class: "text-input", rows: 3,
      placeholder: "说明文字（写入 result.text；可留空）" });
    this.textEl.value = this.draft.text || "";
    this.textEl.addEventListener("input", () => this.saveDraft());
    root.append(this.textEl);
    root.append(this._uploadRow());

    const submit = el("button", { class: "btn btn-primary" }, "提交答复 / 裁决");
    submit.onclick = () => this._submitAnswer(submit);
    const failBtn = el("button", { class: "btn btn-ghost btn-sm" }, "无法回答（标记 fail）");
    failBtn.onclick = () => this._submitFail(failBtn);
    root.append(el("div", { style: { display: "flex", gap: "10px", alignItems: "center" } },
      submit, failBtn,
      el("span", { class: "draft-note" }, `草稿自动保存 · spec v${t.spec_version}`)));
    return root;
  }

  _grantBox(data) {
    // 自主裁决授权：独立、明确、默认关闭的操作；高分不产生授权
    const box = el("div", { class: "grant-box" },
      el("div", { class: "gb-title" }, "🔐 自主裁决授权（独立操作，默认不授权）"),
      el("div", { class: "field-help" },
        "授权审核Agent在明确范围内自行裁决。分数达标不会自动授权；不填即不授权。"));
    const grant = this.draft.answers.explicit_autonomy_grant || null;
    const enable = el("input", { type: "checkbox" });
    enable.checked = !!(grant && grant.grant);
    const scope = el("input", { class: "text-input",
      placeholder: "适用范围：领域/同位体/方法/模型条件",
      value: grant && grant.scope || "" });
    const basis = el("input", { class: "text-input",
      placeholder: "授权依据（为何此刻授权）", value: grant && grant.basis || "" });
    const row = el("label", { class: "opt" }, enable,
      el("span", { class: "opt-label" }, "我在此明确授权自主裁决"));
    const sync = () => {
      if (enable.checked && scope.value.trim()) {
        this.draft.answers.explicit_autonomy_grant = {
          grant: true, scope: scope.value.trim(), basis: basis.value.trim(),
          granted_at: new Date().toISOString(),
          source_task: this.task.task_id,
        };
      } else {
        this.draft.answers.explicit_autonomy_grant = null;
      }
      this.saveDraft();
    };
    enable.onchange = sync; scope.oninput = sync; basis.oninput = sync;
    box.append(row, scope, basis);
    return box;
  }

  _commentArea() {
    const wrap = el("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } });
    this.textEl = el("textarea", { class: "text-input", rows: 3,
      placeholder: "评论/补充内容（支持 Markdown）；不会结束任务" });
    this.textEl.value = this.draft.text || "";
    this.textEl.addEventListener("input", () => this.saveDraft());
    wrap.append(this.textEl, this._uploadRow());
    const send = el("button", { class: "btn btn-cyan" }, "发送评论");
    send.onclick = () => this._submitComment(send);
    wrap.append(el("div", {}, send));
    return wrap;
  }

  _uploadRow() {
    const wrap = el("div", { class: "upload-row" });
    const input = el("input", { type: "file", multiple: true, style: { display: "none" } });
    const btn = el("button", { class: "btn btn-ghost btn-sm" }, "📎 上传附件");
    btn.onclick = () => input.click();
    const list = el("div", { class: "upload-row" });
    const redraw = () => {
      list.textContent = "";
      for (const u of this.draft.uploads) {
        const chip = el("span", { class: "annot-chip" }, `${u.name}`,
          el("span", { class: "x" }, "✕"));
        chip.querySelector(".x").onclick = () => {
          this.draft.uploads = this.draft.uploads.filter(x => x !== u);
          this.saveDraft(); redraw();
        };
        list.append(chip);
      }
    };
    input.onchange = async () => {
      for (const f of input.files) {
        const chip = el("span", { class: "annot-chip" }, `${f.name}（上传中…）`);
        list.append(chip);
        try {
          const meta = await api.upload(f);
          this.draft.uploads.push(meta);
          this.saveDraft();
          toast(`已上传 ${meta.name}`, "ok");
        } catch (e) {
          toast(`上传失败：${errText(e)}`, "err");
        }
        redraw();
      }
      input.value = "";
    };
    redraw();
    wrap.append(btn, input, list);
    return wrap;
  }

  _lock(btn) {
    if (this.submitting) return true;
    this.submitting = true;
    btn.disabled = true;
    return false;
  }
  _unlock(btn) { this.submitting = false; if (btn) btn.disabled = false; }

  async _submitComment(btn) {
    if (this._lock(btn)) return;
    const text = this.textEl.value.trim();
    const atts = this.draft.uploads.map(u => u.path || u.ref);
    if (!text && !atts.length) {
      toast("评论内容为空", "err"); this._unlock(btn); return;
    }
    try {
      await api.comment(this.task.task_id, text, { attachments: atts },
        this.draft.request_id);
      toast("评论已发送（任务未结算）", "ok");
      this._clearDraft();
      this.ctx.onSubmitted();
    } catch (e) {
      this._unlock(btn);
      this._handleSubmitError(e, "评论");
    }
  }

  async _submitAnswer(btn) {
    if (this._lock(btn)) return;
    // 前端校验与后端一致：必填/范围/步长
    const bad = this.fields.filter(f => !f.validate());
    if (bad.length) {
      toast(`还有 ${bad.length} 个必填项未通过校验`, "err");
      this._unlock(btn);
      return;
    }
    const answers = {};
    for (const f of this.fields) answers[f.id] = f.get();
    const data = isDualEvalData(this.task.data) ? normalizeDualAnswers(answers)
      : { answers };
    // 契约键显式落账：grant 为 null 即未授权，preference_scope 缺省 null
    if (isDualEvalData(this.task.data)) {
      const src = this.task.data || {};
      data.artifact_ref = src.artifact_ref ?? null;
      data.review_ref = src.review_ref ?? null;
      data.domain = src.domain ?? null;
      data.preference_scope = answers.preference_scope ?? null;
      data.explicit_autonomy_grant =
        this.draft.answers.explicit_autonomy_grant ?? null;
    }
    if (this.draft.video_notes?.length) data.video_notes = this.draft.video_notes;
    if (Object.keys(this.draft.image_notes || {}).length)
      data.image_notes = this.draft.image_notes;
    if (this.draft.uploads.length) {
      data.ui_attachments = this.draft.uploads.map(u => ({
        ref: u.ref, name: u.name, mime: u.mime, size: u.size }));
    }
    // deny 也是 done：拒绝发布 = done + artifact_decision=abandon/rework，不是 fail
    const attachPaths = this.draft.uploads.map(u => u.path || u.ref);
    try {
      const r = await api.answer(this.task.task_id, this.task.spec_version, {
        text: this.textEl.value.trim() || undefined,
        data,
        attachments: attachPaths.length ? attachPaths : undefined,
      }, this.draft.request_id);
      const st = r.data?.task?.state;
      if (st !== "done") {
        toast(`请求已受理但状态异常：${st}`, "err");
      } else {
        toast("答复已入账，等待中的工作已恢复", "ok");
      }
      this._clearDraft();
      this.ctx.onSubmitted();
    } catch (e) {
      this._unlock(btn);
      this._handleSubmitError(e, "答复");
    }
  }

  async _submitFail(btn) {
    const reason = this.textEl.value.trim() ||
      prompt("标记「无法回答」的原因（必填）：", "");
    if (!reason || !reason.trim()) return;
    if (this._lock(btn)) return;
    try {
      await api.fail(this.task.task_id, this.task.spec_version,
        reason.trim(), this.draft.request_id);
      toast("已标记 fail 并说明原因", "ok");
      this._clearDraft();
      this.ctx.onSubmitted();
    } catch (e) {
      this._unlock(btn);
      this._handleSubmitError(e, "fail");
    }
  }

  _handleSubmitError(e, what) {
    if (e.code === "TASK_SPEC_CHANGED") {
      toast(errText(e), "err", 6000);
      this.ctx.onRefresh();   // 重新拉取；草稿保留
      return;
    }
    if (e.retryable) {
      toast(`${what}提交结果未知：${errText(e)}。可安全重试（同一 request-id 不会产生第二份答案）。`, "err", 8000);
      return;
    }
    toast(`${what}提交失败：${errText(e)}`, "err", 6000);
  }

  _clearDraft() {
    lsSet(this.draftKey, null);
    localStorage.removeItem(this.draftKey);
  }
}
