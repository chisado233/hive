// 富内容协议渲染：markdown/image/video/audio/file 块 + 选项/评分/文本字段
// 以及 video-lab 双重评价结构（TASK_CONTRACT §3）
import { el, fmtDuration, fmtSize, toast, reduceMotion } from "./util.js";
import { renderMarkdownCollapsible, renderMarkdown } from "./markdown.js";
import { api } from "./api.js";

// ---------------- 附件引用解析 ----------------
// uri 形态：hive-file:<id>（web上传）、绝对路径（项目内文件）、其它（不可预览）
export function refPreviewUrl(taskId, uri) {
  if (!uri) return null;
  if (uri.startsWith("hive-file:"))
    return api.fileUrl(uri.slice("hive-file:".length));
  if (/^[A-Za-z]:[\\/]/.test(uri) || uri.startsWith("\\\\"))
    return api.taskFileUrl(taskId, uri);
  return null;
}
export function refDownloadUrl(taskId, uri) {
  if (!uri) return null;
  if (uri.startsWith("hive-file:"))
    return api.fileUrl(uri.slice("hive-file:".length), true);
  if (/^[A-Za-z]:[\\/]/.test(uri) || uri.startsWith("\\\\"))
    return api.taskFileUrl(taskId, uri, true);
  return null;
}
function fileKind(name = "", mime = "") {
  if (/\.(mp4|webm|mov|mkv|m4v)$/i.test(name) || mime.startsWith("video/")) return "video";
  if (/\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name) || mime.startsWith("image/")) return "image";
  if (/\.(mp3|wav|ogg|m4a|flac)$/i.test(name) || mime.startsWith("audio/")) return "audio";
  if (/\.(md|txt|json|srt|vtt|csv|log|py|js|ya?ml)$/i.test(name) || mime.startsWith("text/")) return "text";
  return "binary";
}
const FILE_ICON = { video: "🎬", image: "🖼", audio: "🎵", text: "📄", binary: "📦" };

// ---------------- 图片灯箱 ----------------
export function openLightbox(items, idx) {
  let cur = idx;
  const img = el("img");
  const cap = el("div", { class: "lb-cap" });
  const box = el("div", { class: "lightbox" },
    el("button", { class: "lb-close", onclick: () => box.remove() }, "✕"),
    items.length > 1 ? el("button", { class: "lb-nav lb-prev", onclick: (e) => { e.stopPropagation(); go(-1); } }, "‹") : null,
    img,
    items.length > 1 ? el("button", { class: "lb-nav lb-next", onclick: (e) => { e.stopPropagation(); go(1); } }, "›") : null,
    cap);
  function go(d) { cur = (cur + d + items.length) % items.length; show(); }
  function show() {
    img.src = items[cur].url;
    cap.textContent = items[cur].caption || "";
  }
  box.addEventListener("click", (e) => { if (e.target === box) box.remove(); });
  const keyh = (e) => {
    if (e.key === "Escape") { box.remove(); document.removeEventListener("keydown", keyh); }
    if (items.length > 1 && e.key === "ArrowLeft") go(-1);
    if (items.length > 1 && e.key === "ArrowRight") go(1);
  };
  document.addEventListener("keydown", keyh);
  show();
  document.body.append(box);
}

// ---------------- 内容块 ----------------
export function renderBlock(block, ctx) {
  const { taskId, draft } = ctx;
  const kind = block.kind;
  const wrap = el("div", { class: "block block-" + kind });
  if (kind === "markdown") {
    wrap.append(renderMarkdownCollapsible(block.text || ""));
    return wrap;
  }
  if (kind === "image") {
    const url = block.url || refPreviewUrl(taskId, block.ref);
    if (url) {
      const img = el("img", { class: "block-img", src: url, loading: "lazy" });
      img.onclick = () => ctx.openImageGallery
        ? ctx.openImageGallery(block) : openLightbox([{ url, caption: block.title || "" }], 0);
      wrap.append(img);
    } else {
      wrap.append(fileChip(taskId, block.ref, block.title, block));
    }
    wrap.append(captionRow(block));
    // 对具体图片添加意见
    if (draft) {
      const note = el("input", {
        class: "text-input", placeholder: "对这张图片的意见（可选，随答复提交）",
        value: (draft.image_notes || {})[block.ref] || "",
      });
      note.addEventListener("input", () => {
        draft.image_notes = draft.image_notes || {};
        if (note.value.trim()) draft.image_notes[block.ref] = note.value.trim();
        else delete draft.image_notes[block.ref];
        ctx.onDraft();
      });
      wrap.append(note);
    }
    return wrap;
  }
  if (kind === "video") {
    const url = block.url || refPreviewUrl(taskId, block.ref);
    if (!url) { wrap.append(fileChip(taskId, block.ref, block.title, block)); return wrap; }
    const video = el("video", {
      class: "block-video", src: url, controls: true,
      preload: "metadata", // 不自动播放、不出声
    });
    wrap.append(video);
    const meta = [];
    if (block.duration) meta.push("时长 " + fmtDuration(block.duration));
    if (block.version) meta.push("版本 " + block.version);
    if (block.script_ref) meta.push("关联脚本 " + block.script_ref);
    if (meta.length) wrap.append(el("div", { class: "block-cap" }, meta.join(" · ")));
    wrap.append(captionRow(block));
    // 时间码批注
    if (draft) {
      const notesKey = block.ref || url;
      draft.video_notes = draft.video_notes || [];
      const list = el("div", { style: { display: "flex", gap: "6px", flexWrap: "wrap" } });
      const redraw = () => {
        list.textContent = "";
        for (const n of draft.video_notes.filter(n => n.ref === notesKey)) {
          const chip = el("span", { class: "annot-chip" },
            el("span", { class: "t" }, fmtDuration(n.t)),
            n.text,
            el("span", { class: "x", title: "删除" }, "✕"));
          chip.querySelector(".t").onclick = () => {
            video.currentTime = n.t; video.pause();
          };
          chip.querySelector(".x").onclick = (e) => {
            e.stopPropagation();
            draft.video_notes = draft.video_notes.filter(x => x !== n);
            ctx.onDraft(); redraw();
          };
          list.append(chip);
        }
      };
      const btn = el("button", { class: "btn btn-cyan btn-sm" }, "⏱ 在当前时间添加批注");
      // 内联批注条（不用原生 prompt，任何环境都可靠）
      const inputRow = el("div", { style: { display: "none", gap: "6px", alignItems: "center" } });
      const tLabel = el("span", { class: "annot-chip" }, el("span", { class: "t" }, "0:00"));
      const input = el("input", { class: "text-input", style: { flex: "1" },
        placeholder: "批注内容（回车保存，Esc取消）" });
      btn.onclick = () => {
        video.pause();
        const t = Math.round(video.currentTime * 10) / 10;
        inputRow.dataset.t = t;
        tLabel.querySelector(".t").textContent = fmtDuration(t);
        inputRow.style.display = "flex";
        input.value = "";
        input.focus();
      };
      const commit = () => {
        const text = input.value.trim();
        if (text) {
          draft.video_notes.push({
            ref: notesKey, artifact: block.artifact_ref || block.ref,
            version: block.version || null,
            t: parseFloat(inputRow.dataset.t || "0"),
            text,
          });
          ctx.onDraft(); redraw();
        }
        inputRow.style.display = "none";
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") inputRow.style.display = "none";
      });
      inputRow.append(tLabel, input,
        el("button", { class: "btn btn-sm btn-cyan", onclick: commit }, "存"));
      redraw();
      wrap.append(el("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" } }, btn), inputRow, list);
    }
    return wrap;
  }
  if (kind === "audio") {
    const url = block.url || refPreviewUrl(taskId, block.ref);
    if (url) wrap.append(el("audio", { src: url, controls: true, preload: "metadata", style: { width: "100%" } }));
    else wrap.append(fileChip(taskId, block.ref, block.title, block));
    wrap.append(captionRow(block));
    return wrap;
  }
  if (kind === "file" || kind === "attachment") {
    wrap.append(fileChip(taskId, block.ref, block.name || block.title, block));
    return wrap;
  }
  // 未知块：诚实降级为 JSON 提示
  wrap.append(el("div", { class: "block-cap" }, `不支持的内容块类型: ${kind || "?"}`));
  return wrap;
}

function captionRow(block) {
  const bits = [];
  if (block.title) bits.push(block.title);
  if (block.artifact_ref) bits.push("产物 " + block.artifact_ref);
  if (block.version) bits.push("v" + block.version);
  return el("div", { class: "block-cap" }, bits.join(" · "));
}

export function fileChip(taskId, uri, name, meta = {}) {
  const kind = fileKind(name || uri, meta.mime || "");
  const dl = refDownloadUrl(taskId, uri);
  const chip = el("div", { class: "file-chip" },
    el("span", { class: "fc-icon" }, FILE_ICON[kind]),
    el("span", { class: "fc-name", title: uri || "" }, name || uri || "附件"));
  if (meta.size) chip.append(el("span", { class: "fc-size" }, fmtSize(meta.size)));
  if (meta.mime) chip.append(el("span", { class: "fc-size" }, meta.mime));
  chip.append(el("span", { class: "spacer" }));
  if (dl) chip.append(el("a", { class: "btn btn-ghost btn-sm", href: dl, target: "_blank" }, "下载"));
  else chip.append(el("span", { class: "fc-err" }, "无法预览：引用不可解析或不在受管目录"));
  return chip;
}

// ---------------- 字段控件 ----------------
// collect() → {fid: value}（未触碰的 score 不出现；0 是有效值）
// validate() → [错误文案]
export function renderField(f, value, onChange) {
  const box = el("div", { class: "field", dataset: { fid: f.id } });
  const head = el("div", { class: "field-head" },
    el("span", { class: "field-label" }, f.label || f.id),
    f.required ? el("span", { class: "field-req" }, "必填") : null);
  box.append(head);
  if (f.help) box.append(el("div", { class: "field-help" }, f.help));
  const err = el("div", { class: "field-err hidden" });
  box.append(err);

  let touched = value != null;
  let get = () => value;

  if (f.type === "single_select" || f.type === "multi_select") {
    const multi = f.type === "multi_select";
    const selected = new Set(multi ? (Array.isArray(value) ? value : []) :
      (value != null ? [value] : []));
    const list = el("div");
    const redraw = () => {
      list.textContent = "";
      for (const o of f.options || []) {
        const input = el("input", { type: multi ? "checkbox" : "radio",
          name: "f_" + f.id });
        input.checked = selected.has(o.id);
        const row = el("label", { class: "opt" + (selected.has(o.id) ? " checked" : "") },
          input,
          el("span", {},
            el("div", { class: "opt-label" }, o.label || o.id),
            o.desc ? el("div", { class: "opt-desc" }, o.desc) : null));
        row.onclick = () => {
          if (multi) {
            selected.has(o.id) ? selected.delete(o.id) : selected.add(o.id);
          } else {
            selected.clear(); selected.add(o.id);
          }
          touched = true; redraw(); onChange();
        };
        list.append(row);
      }
    };
    redraw();
    get = () => {
      if (!selected.size) return multi ? [] : null;
      return multi ? [...selected] : [...selected][0];
    };
    box.append(list);
    if (f.allow_text) {
      const extra = el("input", { class: "text-input",
        placeholder: "补充说明（可选）", value: (value && value._text) || "" });
      extra.addEventListener("input", onChange);
      box.append(extra);
      const inner = get;
      get = () => {
        const v = inner();
        return extra.value.trim() ? { choice: v, text: extra.value.trim() } : v;
      };
    }
  } else if (f.type === "score") {
    const lo = f.min ?? 0, hi = f.max ?? 100, step = f.step ?? 1;
    const slider = el("input", { class: "score-slider", type: "range",
      min: lo, max: hi, step });
    const num = el("input", { class: "score-num", type: "number",
      min: lo, max: hi, step });
    const val = el("span", { class: "score-val score-unset" }, "未评分");
    const setVal = (v, from) => {
      if (v == null || v === "" || isNaN(+v)) {
        touched = false; slider.value = lo; num.value = "";
        val.textContent = "未评分"; val.classList.add("score-unset");
      } else {
        let n = Math.round((+v) / step) * step;
        n = Math.min(hi, Math.max(lo, n));
        touched = true; slider.value = n; num.value = n;
        val.textContent = n; val.classList.remove("score-unset");
      }
      if (from !== "init") onChange();
    };
    slider.value = lo;
    slider.addEventListener("input", () => setVal(slider.value));
    num.addEventListener("input", () => setVal(num.value));
    if (value != null) setVal(value, "init");
    get = () => touched ? +slider.value : null;   // 未触碰 → null（不会变默认50）
    box.append(el("div", { class: "score-row" }, slider, num, val));
  } else if (f.type === "long_text") {
    const ta = el("textarea", { class: "text-input", rows: 3,
      placeholder: f.placeholder || "" });
    ta.value = value || "";
    ta.addEventListener("input", onChange);
    get = () => ta.value;
    box.append(ta);
  } else { // text
    const inp = el("input", { class: "text-input",
      placeholder: f.placeholder || "" });
    inp.value = value || "";
    inp.addEventListener("input", onChange);
    get = () => inp.value;
    box.append(inp);
  }

  function validate() {
    const v = get();
    const empty = v == null || v === "" || (Array.isArray(v) && !v.length);
    if (f.required && empty) {
      err.textContent = "此项必填";
      err.classList.remove("hidden");
      return false;
    }
    err.classList.add("hidden");
    return true;
  }
  return { el: box, get, validate, id: f.id };
}

// ---------------- video-lab 双重评价 ----------------
// 检测 TASK_CONTRACT §3 的评价模板键
export function isDualEvalData(data) {
  if (!data || typeof data !== "object") return false;
  return "artifact_score" in data && "review_score" in data;
}

// 构建双重评价字段组（与 TASK_CONTRACT 字段名严格一致）
export function dualEvalFields(data) {
  const domain = data.domain || "";
  return [
    { id: "artifact_decision", type: "single_select", required: true,
      label: "产物判决", help: "通过 / 返工 / 放弃——拒绝发布是正常回答，不是任务失败",
      options: [
        { id: "pass", label: "通过", desc: "本轮产物达到采用标准" },
        { id: "rework", label: "返工", desc: "方向可取，需按修改意见重做" },
        { id: "abandon", label: "放弃", desc: "此路线不继续" },
      ] },
    { id: "artifact_score", type: "score", min: 0, max: 100, step: 1,
      required: true, label: `产物评分（0–100）${domain ? " · " + domain : ""}`,
      help: "审核Agent预测分仅供参考，这里记录你的真实评分" },
    { id: "review_decision", type: "single_select", required: true,
      label: "审核评价", help: "对审核意见本身的认可程度",
      options: [
        { id: "agree", label: "认可", desc: "审核判断准确、证据充分" },
        { id: "partial", label: "部分认可", desc: "部分成立，有误判或遗漏" },
        { id: "disagree", label: "不认可", desc: "审核结论不可靠" },
      ] },
    { id: "review_score", type: "score", min: 0, max: 100, step: 1,
      required: true, label: "审核评分（0–100）" },
    { id: "keep", type: "long_text", label: "应保留的内容",
      placeholder: "每行一条；可留空", multiline_list: true },
    { id: "change", type: "long_text", label: "需要修改的内容",
      placeholder: "每行一条；可留空", multiline_list: true },
    { id: "review_agreements", type: "long_text", label: "审核中判断对的要点",
      placeholder: "每行一条；可留空", multiline_list: true },
    { id: "review_disagreements", type: "long_text", label: "审核中判断错的地方",
      placeholder: "每行一条；可留空", multiline_list: true },
    { id: "review_omissions", type: "long_text", label: "审核漏掉的地方",
      placeholder: "每行一条；可留空", multiline_list: true },
  ];
}

// 收集时把多行文本拆为数组（contract: keep/change/... 是数组）
export function normalizeDualAnswers(raw) {
  const out = {};
  for (const [k, v] of Object.entries(raw)) {
    if (["keep", "change", "review_agreements", "review_disagreements",
         "review_omissions"].includes(k)) {
      out[k] = String(v || "").split("\n").map(s => s.trim()).filter(Boolean);
    } else if (v && typeof v === "object" && "choice" in v) {
      out[k] = v.choice;
      if (v.text) out[k + "_note"] = v.text;
    } else out[k] = v;
  }
  return out;
}
