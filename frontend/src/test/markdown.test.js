/** The Markdown renderer, which now has two kinds of caller.
 *
 * It was written for model output, where a paragraph arrives as one long line,
 * and it turned every non-blank line into its own <p>. The manager's handover
 * page feeds it a document a human hard-wrapped at 78 columns, and the result
 * was a gappy column of sentence fragments with the continuation of each
 * bullet ejected from its own bullet — on the one page whose entire purpose is
 * reading that document.
 *
 * The escaping guarantee is what makes `dangerouslySetInnerHTML` defensible at
 * the call sites, so it is pinned here too.
 */
import { describe, expect, it } from "vitest";

import { renderMarkdown } from "../lib/markdown.js";

describe("hard-wrapped prose", () => {
  it("joins soft-wrapped lines into one paragraph", () => {
    const html = renderMarkdown("The AWS environment is a landing zone\nmanaged with Terraform.");
    expect(html).toBe("<p>The AWS environment is a landing zone managed with Terraform.</p>");
  });

  it("still breaks a paragraph on a blank line", () => {
    const html = renderMarkdown("First.\n\nSecond.");
    expect(html).toBe("<p>First.</p>\n<p>Second.</p>");
  });

  it("keeps a wrapped bullet inside its own bullet", () => {
    const html = renderMarkdown("- run it against the prod workspace with the\n  prod-deployer role.");
    expect(html).toContain("<li>run it against the prod workspace with the prod-deployer role.</li>");
    // The regression: the continuation used to land after </ul> as a stray <p>.
    expect(html).not.toContain("<p>");
  });

  it("lets emphasis span a line break, now that the lines are joined", () => {
    expect(renderMarkdown("this is **very\nimportant** text"))
      .toContain("<strong>very important</strong>");
  });
});

describe("blockquotes", () => {
  it("renders one rather than printing the marker", () => {
    const html = renderMarkdown("> NOTE: credentials are not documented here.");
    expect(html).toBe(
      "<blockquote><p>NOTE: credentials are not documented here.</p></blockquote>");
    expect(html).not.toContain("&gt;");
  });

  it("joins a wrapped quote into one block", () => {
    const html = renderMarkdown("> Reviewer to flag this\n> as a critical gap.");
    expect(html).toBe(
      "<blockquote><p>Reviewer to flag this as a critical gap.</p></blockquote>");
  });

  it("ends the quote at a blank line", () => {
    const html = renderMarkdown("> quoted\n\nnot quoted");
    expect(html).toBe("<blockquote><p>quoted</p></blockquote>\n<p>not quoted</p>");
  });
});

describe("headings and lists still work", () => {
  it("renders a heading, then a list, then prose", () => {
    const html = renderMarkdown("## Overview\n\n- one\n- two\n\nAfter.");
    expect(html).toBe(
      "<h4>Overview</h4>\n<ul>\n<li>one</li>\n<li>two</li>\n</ul>\n<p>After.</p>");
  });

  it("closes an open list before a paragraph that follows a blank line", () => {
    expect(renderMarkdown("- one\n\nplain")).toBe("<ul>\n<li>one</li>\n</ul>\n<p>plain</p>");
  });
});

describe("escaping", () => {
  it("neutralises markup before any transformation", () => {
    // The whole reason the callers may use dangerouslySetInnerHTML.
    const html = renderMarkdown("<img src=x onerror=alert(1)>");
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("does not let a fenced block smuggle tags through", () => {
    const html = renderMarkdown("```\n<script>alert(1)</script>\n```");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});

describe("user text reaching the tag structure", () => {
  it("does not let $-sequences in a wrapped bullet expand as replacement patterns", () => {
    /* `String.replace` with a *string* replacement expands `$&`, "$`" and
       `$'`. The continuation line is user text, so "Cost: $& more" produced
       `<li>bullet one Cost: </li>amp; more</li>` -- the item closed early and
       the text escaped the tag it was meant to be inside. */
    expect(renderMarkdown("- bullet one\nCost: $& more"))
      .toBe("<ul>\n<li>bullet one Cost: $&amp; more</li>\n</ul>");
    expect(renderMarkdown("- a\ncontinuation $` end"))
      .toBe("<ul>\n<li>a continuation $` end</li>\n</ul>");
    expect(renderMarkdown("- a\nprice $$100"))
      .toBe("<ul>\n<li>a price $$100</li>\n</ul>");
  });

  it("leaves a code-block marker typed by a human alone", () => {
    // The placeholder was `@@CODE0@@`, which escaping does not touch and a
    // person can type. With no fences it rendered "undefined" in a code
    // block; with fences it rendered a different block's contents.
    expect(renderMarkdown("Please see @@CODE0@@ for details."))
      .toBe("<p>Please see @@CODE0@@ for details.</p>");
  });

  it("does not swap a typed marker for a real block's contents", () => {
    const html = renderMarkdown("Intro @@CODE0@@ text\n\n```\nreal code\n```\n");
    expect(html).toBe(
      "<p>Intro @@CODE0@@ text</p>\n<pre><code>real code</code></pre>");
  });

  it("uses a different marker on every render", () => {
    // Two renders must not share a guessable token, or the forgery is back
    // for anyone who reads one output.
    const a = renderMarkdown("```\nx\n```");
    const b = renderMarkdown("```\nx\n```");
    expect(a).toBe(b); // the *output* is stable
    expect(a).toBe("<pre><code>x</code></pre>");
  });
});

describe("fenced code", () => {
  it("renders at all", () => {
    /* It did not. The placeholder was delimited by spaces and restored by a
       regex that demanded one on each side, but the line was trimmed before
       the block loop -- so every fenced block on its own lines came out as the
       literal text "CODE0". Chat has rendered them that way from the start. */
    expect(renderMarkdown("```\nnpm run dev\n```"))
      .toBe("<pre><code>npm run dev</code></pre>");
  });

  it("is a block, not something wrapped in a paragraph", () => {
    const html = renderMarkdown("Run it:\n\n```\nnpm test\n```");
    expect(html).toBe("<p>Run it:</p>\n<pre><code>npm test</code></pre>");
  });

  it("leaves a language tag off the output", () => {
    expect(renderMarkdown("```js\nconst a = 1;\n```"))
      .toBe("<pre><code>const a = 1;</code></pre>");
  });
});
