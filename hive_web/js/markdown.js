// 安全 Markdown 渲染：全部经 DOM 构建，不注入 HTML，不执行任何脚本/事件
import { el } from "./util.js";

const SVG_FILE = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>';
const SVG_COPY = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const SVG_CHECK = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
const SVG_WRAP = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M3 12h15a3 3 0 1 1 0 6h-4"/><path d="m14 16-2 2 2 2"/></svg>';

const LANG_LABEL = {
  py: "python", js: "javascript", ts: "typescript", json: "json",
  sh: "shell", bash: "shell", md: "markdown", yaml: "yaml", yml: "yaml",
  html: "html", css: "css", sql: "sql", txt: "text", "": "text",
};

export function codeBlock(codeText, lang) {
  const label = LANG_LABEL[lang] || lang || "text";
  const wrap = el("div", { class: "zc-code" });
  const head = el("div", { class: "zc-code-head" });
  const title = el("span", { class: "zc-code-lang" });
  title.innerHTML = SVG_FILE;
  title.append(el("span", { class: "mono" }, label));
  const actions = el("span", { class: "zc-code-actions" });
  const pre = el("pre", { class: "zc-code-body" }, el("code", {}, codeText));
  const wrapBtn = el("button", { class: "zc-icon-btn", title: "自动换行" });
  wrapBtn.innerHTML = SVG_WRAP;
  wrapBtn.onclick = (e) => { e.stopPropagation(); pre.classList.toggle("wrap"); };
  const copyBtn = el("button", { class: "zc-icon-btn", title: "复制" });
  copyBtn.innerHTML = SVG_COPY;
  copyBtn.onclick = (e) => {
    e.stopPropagation();
    navigator.clipboard.writeText(codeText);
    copyBtn.innerHTML = SVG_CHECK;
    copyBtn.classList.add("done");
    setTimeout(() => { copyBtn.innerHTML = SVG_COPY; copyBtn.classList.remove("done"); }, 2000);
  };
  actions.append(wrapBtn, copyBtn);
  head.append(title, actions);
  wrap.append(head, pre);
  return wrap;
}

const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\n]+\)|__[^_]+__|~~[^~]+~~)/g;

function inline(text, parent) {
  const parts = String(text).split(INLINE_RE);
  for (const p of parts) {
    if (!p) continue;
    if (p.startsWith("**") && p.endsWith("**") && p.length > 4)
      parent.append(el("strong", {}, p.slice(2, -2)));
    else if (p.startsWith("__") && p.endsWith("__") && p.length > 4)
      parent.append(el("strong", {}, p.slice(2, -2)));
    else if (p.startsWith("~~") && p.endsWith("~~") && p.length > 4)
      parent.append(el("del", {}, p.slice(2, -2)));
    else if (p.startsWith("*") && p.endsWith("*") && p.length > 2)
      parent.append(el("em", {}, p.slice(1, -1)));
    else if (p.startsWith("`") && p.endsWith("`") && p.length > 2)
      parent.append(el("code", {}, p.slice(1, -1)));
    else if (p.startsWith("[") && p.includes("](") && p.endsWith(")")) {
      const m = p.match(/^\[([^\]]*)\]\(([^)]*)\)$/);
      const href = m ? m[2].trim() : "";
      if (/^https?:\/\//i.test(href)) {
        parent.append(el("a", { href, target: "_blank", rel: "noopener noreferrer" },
          m[1] || href));
      } else parent.append(document.createTextNode(p)); // 非http链接按原文显示
    } else parent.append(document.createTextNode(p));
  }
}

function table(lines) {
  const rows = lines.map(l => l.replace(/^\||\|$/g, "").split("|").map(c => c.trim()));
  const t = el("table");
  const head = rows[0];
  const thead = el("thead", {}, el("tr", {}, head.map(c => {
    const th = el("th"); inline(c, th); return th;
  })));
  const tbody = el("tbody");
  for (const r of rows.slice(2)) {
    tbody.append(el("tr", {}, r.map(c => { const td = el("td"); inline(c, td); return td; })));
  }
  t.append(thead, tbody);
  return t;
}

export function renderMarkdown(src) {
  const root = el("div", { class: "md" });
  const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // 代码块（ZCode web 形态：圆角卡片 + 头部语言标签 + 换行/复制钮）
    const fence = line.match(/^```(\w*)\s*$/);
    if (fence) {
      const lang = (fence[1] || "").toLowerCase();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      root.append(codeBlock(buf.join("\n"), lang));
      continue;
    }
    // 表格
    if (/^\|.*\|$/.test(line.trim()) && i + 1 < lines.length &&
        /^\|[\s:|-]+\|$/.test(lines[i + 1].trim())) {
      const buf = [];
      while (i < lines.length && /^\|.*\|$/.test(lines[i].trim())) buf.push(lines[i++]);
      root.append(table(buf));
      continue;
    }
    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const node = el("h" + h[1].length);
      inline(h[2], node);
      root.append(node); i++; continue;
    }
    // 分隔线
    if (/^(-{3,}|\*{3,})\s*$/.test(line.trim())) { root.append(el("hr")); i++; continue; }
    // 引用
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, "")); i++;
      }
      const bq = el("blockquote");
      renderInlineBlocks(buf.join("\n"), bq);
      root.append(bq);
      continue;
    }
    // 列表
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const list = el(ordered ? "ol" : "ul");
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        const item = lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, "");
        const li = el("li");
        inline(item, li);
        list.append(li);
        i++;
      }
      root.append(list);
      continue;
    }
    // 空行
    if (!line.trim()) { i++; continue; }
    // 段落（合并连续行）
    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() &&
           !/^(#{1,4}\s|```|\||>|(-{3,}|\*{3,})\s*$|\s*([-*+]|\d+\.)\s)/.test(lines[i])) {
      buf.push(lines[i++]);
    }
    const p = el("p");
    inline(buf.join(" "), p);
    root.append(p);
  }
  return root;
}

function renderInlineBlocks(text, parent) {
  for (const part of text.split(/\n{2,}/)) {
    const p = el("p"); inline(part.replace(/\n/g, " "), p); parent.append(p);
  }
}

// 长文折叠
export function renderMarkdownCollapsible(src, maxH = 260) {
  const wrap = el("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } });
  const md = renderMarkdown(src);
  wrap.append(md);
  requestAnimationFrame(() => {
    if (md.scrollHeight > maxH + 60) {
      md.classList.add("md-collapsed");
      const btn = el("button", { class: "btn btn-ghost btn-sm md-expand" }, "展开全文");
      btn.onclick = () => {
        const open = md.classList.toggle("md-collapsed");
        btn.textContent = open ? "收起" : "展开全文";
      };
      wrap.append(btn);
    }
  });
  return wrap;
}
