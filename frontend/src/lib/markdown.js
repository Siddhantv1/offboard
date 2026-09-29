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

  /* A per-render nonce, because the marker has to be one the source cannot
     contain.

     It was a bare `@@CODE0@@`. `escapeHtml` leaves `@` alone by design -- and
     so does a person typing it. "Please see @@CODE0@@ for details" rendered
     as `<pre><code>undefined</code></pre>` in a document with no fences, and
     as some *other* block's contents in one that had them. A nonce makes a
     forged marker impossible rather than merely unlikely. */
  const nonce = Math.random().toString(36).slice(2, 10);
  const marker = new RegExp(`@@CODE(\\d+)_${nonce}@@`, "g");

  let text = String(src).replace(/```(?:[\w-]+)?\n?([\s\S]*?)```/g, (_, code) => {
    codeBlocks.push(code.replace(/\n$/, ""));
    return `@@CODE${codeBlocks.length - 1}_${nonce}@@`;
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
    if (new RegExp(`^@@CODE\\d+_${nonce}@@$`).test(line.trim())) {
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
      // A replacer *function*, not a replacement string. As a string, `$&`,
      // "$`" and `$'` in the user's text expand as patterns: a wrapped bullet
      // reading "Cost: $& more" produced `<li>bullet one Cost: </li>amp;
      // more</li>` -- the item closed early and the text escaped the tag it
      // was supposed to be inside. Not an injection (the inserted text is
      // already escaped) but user text reaching the tag structure, which is
      // the thing the escaping exists to prevent.
      const tail = ` ${inline(line.trim())}</li>`;
      html.push(html.pop().replace(/<\/li>$/, () => tail));
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
    .replace(marker, (_, i) => `<pre><code>${escapeHtml(codeBlocks[+i] ?? "")}</code></pre>`);
}

/** Convenience for `<div {...markdownHtml(src)} />`. */
export const markdownHtml = (src) => ({ dangerouslySetInnerHTML: { __html: renderMarkdown(src) } });
