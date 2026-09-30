// 安全 Markdown 渲染：全部经 DOM 构建，不注入 HTML，不执行任何脚本/事件
import { el } from "./util.js";

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
    // 代码块
    if (/^```/.test(line)) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      const pre = el("pre");
      const code = el("code", {}, buf.join("\n"));
      const btn = el("button", {
        class: "btn btn-ghost btn-sm copy-btn", title: "复制代码",
        onclick: (e) => {
          e.stopPropagation();
          navigator.clipboard.writeText(buf.join("\n"));
          btn.textContent = "已复制"; setTimeout(() => btn.textContent = "复制", 1200);
        },
      }, "复制");
      pre.append(code, btn);
      root.append(pre);
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
