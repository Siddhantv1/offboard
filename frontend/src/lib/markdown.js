import { escapeHtml } from "./format.js";

/** Minimal, safe Markdown -> HTML.
 *
 * The input is escaped before any transformation, and only a fixed set of tags
 * is produced afterwards, so model output can be rendered with
 * dangerouslySetInnerHTML without becoming an injection surface. Fenced code is
 * lifted out first so its contents are never treated as markup.
 */
export function renderMarkdown(src) {
  if (src == null) return "";
  const codeBlocks = [];
  let text = String(src).replace(/```(?:[\w-]+)?\n?([\s\S]*?)```/g, (_, code) => {
    codeBlocks.push(code.replace(/\n$/, ""));
    return ` CODE${codeBlocks.length - 1} `;
  });
  text = escapeHtml(text);

  const lines = text.split("\n");
  const html = [];
  let list = null; // 'ul' | 'ol'
  const closeList = () => {
    if (list) {
      html.push(`</${list}>`);
      list = null;
    }
  };

  const inline = (s) =>
    s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if (/^\s*$/.test(line)) {
      closeList();
      continue;
    }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      closeList();
      const lvl = m[1].length + 2; // h3..h6
      html.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      if (list !== "ul") {
        closeList();
        html.push("<ul>");
        list = "ul";
      }
      html.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") {
        closeList();
        html.push("<ol>");
        list = "ol";
      }
      html.push(`<li>${inline(m[1])}</li>`);
    } else {
      closeList();
      html.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();

  return html
    .join("\n")
    .replace(/ CODE(\d+) /g, (_, i) => `<pre><code>${escapeHtml(codeBlocks[+i])}</code></pre>`);
}

/** Convenience for `<div {...markdownHtml(src)} />`. */
export const markdownHtml = (src) => ({ dangerouslySetInnerHTML: { __html: renderMarkdown(src) } });
