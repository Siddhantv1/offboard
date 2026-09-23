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
    // `@@` rather than the surrounding spaces this used before. The old token
    // was restored with a regex that required a space on both sides, and the
    // line was `trimEnd()`ed before the block loop saw it -- so a fenced block
    // sitting on its own lines, which is every fenced block, never matched and
    // came out as the literal text "CODE0". `escapeHtml` leaves @ alone, so
    // the marker survives escaping intact.
    return `@@CODE${codeBlocks.length - 1}@@`;
  });
  text = escapeHtml(text);

  const lines = text.split("\n");
  const html = [];
  let list = null; // 'ul' | 'ol'
  /* Soft-wrapped lines waiting to become one block.
     Every non-blank line used to become its own <p>. That reads fine for the
     model output this started with, where a paragraph is one long line, and
     badly for a document a human hard-wrapped at 78 columns -- a handover
     document came out as a gappy list of sentence fragments, with a list
     item's continuation line ejected from its own bullet. A blank line ends a
     paragraph; a line break inside one does not. */
  let para = [];
  let quote = [];

  const closeList = () => {
    if (list) {
      html.push(`</${list}>`);
      list = null;
    }
  };
  const flushPara = () => {
    if (para.length) {
      html.push(`<p>${inline(para.join(" "))}</p>`);
      para = [];
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      html.push(`<blockquote><p>${inline(quote.join(" "))}</p></blockquote>`);
      quote = [];
    }
  };
  const closeAll = () => {
    flushPara();
    flushQuote();
    closeList();
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
      closeAll();
      continue;
    }
    if (/^@@CODE\d+@@$/.test(line.trim())) {
      // A fence on its own lines is a block, not a paragraph. Letting it fall
      // through would wrap a <pre> in a <p>, which the parser then splits.
      closeAll();
      html.push(line.trim());
    } else if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      closeAll();
      const lvl = m[1].length + 2; // h3..h6
      html.push(`<h${lvl}>${inline(m[2])}</h${lvl}>`);
    } else if ((m = line.match(/^\s*&gt;\s?(.*)$/))) {
      // `>` has already been escaped by this point, so the marker to match is
      // the entity, not the character. KT documents use blockquotes for the
      // line that matters most -- "reviewer to flag this as a critical gap" --
      // and it was rendering as a literal &gt; at the start of a fragment.
      flushPara();
      closeList();
      quote.push(m[1]);
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      flushPara();
      flushQuote();
      if (list !== "ul") {
        closeList();
        html.push("<ul>");
        list = "ul";
      }
      html.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      flushPara();
      flushQuote();
      if (list !== "ol") {
        closeList();
        html.push("<ol>");
        list = "ol";
      }
      html.push(`<li>${inline(m[1])}</li>`);
    } else if (list && html[html.length - 1]?.endsWith("</li>")) {
      // A wrapped bullet. It belongs to the item above, not to a paragraph
      // sitting between two bullets where no paragraph exists.
      html.push(html.pop().replace(/<\/li>$/, ` ${inline(line.trim())}</li>`));
    } else if (quote.length) {
      quote.push(line); // lazy continuation: a wrapped line inside the quote
    } else {
      closeList();
      para.push(line);
    }
  }
  closeAll();

  return html
    .join("\n")
    .replace(/@@CODE(\d+)@@/g, (_, i) => `<pre><code>${escapeHtml(codeBlocks[+i])}</code></pre>`);
}

/** Convenience for `<div {...markdownHtml(src)} />`. */
export const markdownHtml = (src) => ({ dangerouslySetInnerHTML: { __html: renderMarkdown(src) } });
