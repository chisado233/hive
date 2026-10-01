// 资产库浏览：项目 assets/ 目录树 + 预览；方法 candidates/releases 与当前采用版本
import { el, fmtSize, toast } from "./util.js";
import { api, errText } from "./api.js";
import { store } from "./store.js";
import { renderMarkdownCollapsible } from "./markdown.js";

const KIND_ICON = { video: "🎬", image: "🖼", audio: "🎵", text: "📄", binary: "📦" };

export class Assets {
  constructor(body, ctx) {
    this.body = body;
    this.ctx = ctx;   // {close()}
  }

  async open(projectKey, path = "") {
    this.key = projectKey;
    this.path = path;
    document.getElementById("detail").classList.add("open");
    this.body.classList.remove("hidden");
    document.getElementById("detail-empty").classList.add("hidden");
    this.body.textContent = "";
    this.body.append(el("div", { class: "inbox-empty" }, "加载中…"));
    let d;
    try {
      d = (await api.projectAssets(projectKey, path)).data;
    } catch (e) {
      this.body.textContent = "";
      this.body.append(el("div", { class: "inbox-empty" }, errText(e)));
      return;
    }
    this.body.textContent = "";

    // 头部：面包屑
    const crumbs = el("div", { class: "asset-crumbs" },
      el("a", { onclick: () => this.open(this.key, "") }, "assets"));
    let acc = "";
    for (const seg of path.split("/").filter(Boolean)) {
      acc = acc ? acc + "/" + seg : seg;
      const p = acc;
      crumbs.append(el("span", {}, "/"),
        el("a", { onclick: () => this.open(this.key, p) }, seg));
    }
    this.body.append(el("div", { class: "dt-head" },
      el("div", { class: "dt-title-row" },
        el("div", { class: "dt-title" }, `▣ ${projectKey} 资产库`),
        el("button", { class: "dt-close", onclick: () => this.ctx.close() }, "✕")),
      crumbs));

    const c = el("div", { class: "dt-content" });

    // active.json 中的当前采用版本（methods 区显示采用状态）
    const proj = store.projects.find(p => p.key === this.key);
    const active = proj && proj.active || {};
    const m = path.match(/^methods\/([^/]+)\/releases\/([^/]+)/);
    if (m && active.methods) {
      const cur = active.methods[m[1]];
      c.append(el("div", { class: "card" },
        el("div", { class: "card-title" }, "采用状态"),
        el("div", { class: "field-help" },
          cur == null
            ? `「${m[1]}」尚无正式采用版本（active.methods 为 null）——候选内容不得当作正式方法使用`
            : `「${m[1]}」当前采用版本：${cur}` +
              (cur === m[2] ? "（你正在查看当前采用版本）" : `；此处为 ${m[2]}`))));
    }

    // 目录列表
    const card = el("div", { class: "card" },
      el("div", { class: "card-title" }, `目录（${d.entries.length} 项）`));
    if (!d.entries.length)
      card.append(el("div", { class: "field-help" }, "空目录"));
    for (const e of d.entries) {
      const sub = path ? path + "/" + e.name : e.name;
      const row = el("div", {
        class: "asset-row",
        onclick: () => e.dir ? this.open(this.key, sub) : this._preview(sub, e),
      },
        el("span", {}, e.dir ? "📁" : (KIND_ICON[e.kind] || "📦")),
        el("span", { class: "a-name" }, e.name),
        el("span", { class: "a-meta" },
          e.dir ? "" : fmtSize(e.size)));
      card.append(row);
    }
    c.append(card);

    // 说明文档（当前目录 README）
    const readme = d.entries.find(e => !e.dir && /^readme\.md$/i.test(e.name));
    if (readme) {
      const sub = path ? path + "/" + readme.name : readme.name;
      const rc = el("div", { class: "card" },
        el("div", { class: "card-title" }, readme.name),
        el("div", { class: "field-help" }, "加载中…"));
      c.append(rc);
      fetch(api.projectFileUrl(this.key, "assets/" + sub)).then(r => r.text())
        .then(txt => {
          rc.querySelector(".field-help").remove();
          rc.append(renderMarkdownCollapsible(txt, 200));
        }).catch(() => {});
    }
    this.body.append(c);
  }

  async _preview(path, entry) {
    // 文本类直接内联预览；媒体走受权 URL；其它给下载
    const url = api.projectFileUrl(this.key, "assets/" + path);
    if (entry.kind === "text" && (entry.size || 0) < 512 * 1024) {
      try {
        const txt = await (await fetch(url)).text();
        const modalRoot = document.getElementById("modal-root");
        const modal = el("div", { class: "modal", style: { width: "min(720px,94vw)" } },
          el("h3", {}, entry.name),
          /\.md$/i.test(entry.name) ? renderMarkdownCollapsible(txt, 480)
            : el("pre", { class: "mono", style: { whiteSpace: "pre-wrap",
              maxHeight: "60vh", overflow: "auto" } }, txt),
          el("div", { class: "modal-actions" },
            el("a", { class: "btn btn-ghost", href: url + "&download=1" }, "下载"),
            el("button", { class: "btn btn-primary",
              onclick: () => mask.remove() }, "关闭")));
        const mask = el("div", { class: "modal-mask",
          onclick: (e) => { if (e.target === mask) mask.remove(); } }, modal);
        modalRoot.append(mask);
      } catch { toast("读取失败", "err"); }
      return;
    }
    if (entry.kind === "image" || entry.kind === "video" || entry.kind === "audio") {
      const modalRoot = document.getElementById("modal-root");
      const media = entry.kind === "image"
        ? el("img", { src: url, style: { maxWidth: "100%", borderRadius: "9px" } })
        : entry.kind === "video"
          ? el("video", { src: url, controls: true, preload: "metadata",
            style: { width: "100%", borderRadius: "9px", background: "#000" } })
          : el("audio", { src: url, controls: true, style: { width: "100%" } });
      const modal = el("div", { class: "modal", style: { width: "min(720px,94vw)" } },
        el("h3", {}, entry.name),
        media,
        el("div", { class: "field-help" },
          `${entry.kind} · ${fmtSize(entry.size)} · 受权预览，不自动播放声音`),
        el("div", { class: "modal-actions" },
          el("a", { class: "btn btn-ghost", href: url + "&download=1" }, "下载"),
          el("button", { class: "btn btn-primary",
            onclick: () => mask.remove() }, "关闭")));
      const mask = el("div", { class: "modal-mask",
        onclick: (e) => { if (e.target === mask) mask.remove(); } }, modal);
      modalRoot.append(mask);
      return;
    }
    window.open(url + "&download=1", "_blank");
  }
}
