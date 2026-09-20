/** Stylesheet invariants that a rendering test would not catch cheaply.
 *
 * jsdom does not do layout, so there is no way to assert "the chevron does not
 * sit on top of the text" by rendering. But the *cause* is checkable: the base
 * `select` rule paints its chevron as a background image and reserves right
 * padding for it, and any rule that later uses the `padding` shorthand silently
 * resets that reservation to whatever the shorthand says.
 *
 * That is exactly how the role dropdown on People & access ended up with the
 * arrow drawn over the word "employee".
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Resolved from the vitest root rather than `import.meta.url`: under the jsdom
// environment that is an http:// URL, not a file:// one, and fileURLToPath
// rejects it.
const CSS = readFileSync(resolve(process.cwd(), "src/styles/index.css"), "utf8");

/** Strip comments so a code sample inside one is not parsed as a rule. */
const withoutComments = CSS.replace(/\/\*[\s\S]*?\*\//g, "");

/** [{selector, body}] for every rule in the sheet. */
function rules(css) {
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(css)) !== null) {
    const selector = m[1].trim().replace(/\s+/g, " ");
    if (!selector || selector.startsWith("@")) continue;
    out.push({ selector, body: m[2] });
  }
  return out;
}

function declaration(body, prop) {
  const m = body.match(new RegExp(`(?:^|;)\\s*${prop}\\s*:\\s*([^;]+)`, "i"));
  return m ? m[1].trim() : null;
}

/** Right padding a `padding` shorthand resolves to, in px. */
function rightFromShorthand(value) {
  const parts = value.split(/\s+/);
  const px = (v) => (v.endsWith("px") ? parseFloat(v) : Number.NaN);
  if (parts.length === 1) return px(parts[0]);
  return px(parts[1]); // 2, 3 and 4-value forms all put "right" second
}

// Matches a bare `select` and the compact-select classes. Kept narrow on
// purpose: a false positive here is a broken build for no reason.
const TARGETS_SELECT = /(^|[\s,>])select([\s,:.[]|$)|\.select-sm|\.role-select/;

/** Is every subject of this selector list a select?
 *
 * A shared reset like `input[type="text"], textarea, select { padding: ... }`
 * is not an offender: the dedicated `select` rule right after it restores the
 * gutter, and that relationship is asserted separately below. Only a rule
 * aimed at selects alone can be the last word on their padding, so only those
 * are checked -- which keeps this a cascade-free lint rather than a
 * half-finished CSS engine.
 */
function targetsOnlySelects(selector) {
  return selector
    .split(",")
    .map((s) => s.trim())
    .every((s) => TARGETS_SELECT.test(s));
}

/** Enough room for the 10-14px chevron plus its inset. */
const MIN_RIGHT_PADDING = 20;

describe("select chevron gutter", () => {
  const selectRules = rules(withoutComments).filter((r) =>
    TARGETS_SELECT.test(r.selector),
  );

  it("finds the select rules at all", () => {
    // Guards the guard: a regex that matched nothing would make the real
    // assertion below vacuously pass.
    expect(selectRules.length).toBeGreaterThan(1);
    expect(selectRules.some((r) => r.selector.includes(".role-select"))).toBe(true);
  });

  it("never lets a padding shorthand eat the space the arrow is drawn in", () => {
    const offenders = [];
    for (const rule of selectRules) {
      if (!targetsOnlySelects(rule.selector)) continue;
      const shorthand = declaration(rule.body, "padding");
      if (!shorthand) continue;

      // An explicit padding-right after the shorthand puts it back.
      const explicit = declaration(rule.body, "padding-right");
      const right = explicit
        ? parseFloat(explicit)
        : rightFromShorthand(shorthand);

      if (!Number.isNaN(right) && right < MIN_RIGHT_PADDING) {
        offenders.push(
          `${rule.selector} -> padding-right resolves to ${right}px ` +
          `(needs >= ${MIN_RIGHT_PADDING}px, or the chevron overlaps the text)`,
        );
      }
    }
    expect(offenders).toEqual([]);
  });

  it("keeps the base rule's reservation and chevron in sync", () => {
    const all = rules(withoutComments);
    const sharedIdx = all.findIndex(
      (r) => TARGETS_SELECT.test(r.selector) && !targetsOnlySelects(r.selector)
             && declaration(r.body, "padding"),
    );
    const baseIdx = all.findIndex((r) => r.selector === "select");

    const base = all[baseIdx];
    expect(base, "the base select rule went missing").toBeTruthy();
    expect(declaration(base.body, "padding-right")).toBeTruthy();
    expect(declaration(base.body, "background-image")).toContain("svg");
    expect(declaration(base.body, "background-position")).toBeTruthy();

    // The lint above skips the shared reset on the grounds that this rule
    // comes after it and puts the gutter back. If they are ever reordered,
    // that reasoning silently stops holding.
    if (sharedIdx !== -1) {
      expect(baseIdx).toBeGreaterThan(sharedIdx);
    }
  });
});
