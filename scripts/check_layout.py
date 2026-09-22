"""Walk every page as every role and fail on a stray scrollbar.

    python -m scripts.check_layout                 # against a running server
    python -m scripts.check_layout --base http://127.0.0.1:8000

Needs `pip install playwright && python -m playwright install chromium`, and a
server already running with the frontend built.

Why this exists: the app is a fixed-height shell with exactly ONE scroll
container (`section.content`). Anything else that scrolls is a bug, and it is
the kind of bug that is invisible in a diff and obvious to whoever opens the
page. Two shipped in a row -- a `padding` shorthand that let a chevron sit on
text, then a visually-hidden file input that rendered full width and pushed the
document out sideways and down. Both were CSS specificity and containing-block
problems that no amount of reading the stylesheet caught. Measuring catches
them in seconds.
"""
from __future__ import annotations

import argparse
import sys

PASSWORD = "offboard-demo-2026"

#: (role, sign-in address, paths to walk). Paths that role cannot reach are
#: skipped rather than failed -- a 403 page is a valid render.
ROLES = [
    ("hr", "hr@perficient.com",
     # A case detail page is included deliberately: it is the densest page in
     # the product and was swept only as a list. Its own layout -- two document
     # panels, the review queue, the timeline -- went unchecked for months.
     ["/", "/cases", "/cases/case-001", "/orchestrator", "/deprovisioning",
      "/employees/new", "/settings"]),
    # /admin/users is IT's now, not HR's -- account administration is access
    # management. Swept under the role that can actually reach it, or the sweep
    # would only ever be measuring the 403 page.
    ("it", "sanya.kapoor@perficient.com",
     # The clearance workspace, not a case page: IT no longer has /cases or the
     # all-plans Deprovisioning page, and this is where its queue now lands.
     ["/", "/queue/all", "/it/clearance/case-001", "/admin/users", "/settings"]),
    ("manager", "vikram.sharma@perficient.com",
     ["/", "/queue/all", "/cases", "/cases/case-001", "/settings"]),
    ("employee", "aarav.mehta@perficient.com",
     ["/", "/faq", "/interview", "/settings"]),
]

VIEWPORTS = [("desktop", 1440, 900), ("laptop", 1180, 760), ("phone", 390, 844)]

PROBE = """
() => {
  const se = document.scrollingElement;
  const de = document.documentElement;
  window.scrollTo(0, 4000); const movedY = window.scrollY;
  window.scrollTo(4000, 0); const movedX = window.scrollX;
  window.scrollTo(0, 0);

  const label = (el) => {
    const cls = (el.className && typeof el.className === "string")
      ? "." + el.className.trim().split(/\\s+/).slice(0, 3).join(".") : "";
    return el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + cls;
  };
  const wide = [];
  for (const el of document.querySelectorAll("*")) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > de.clientWidth + 1) {
      wide.push({ el: label(el), over: Math.round(r.right - de.clientWidth) });
    }
  }
  wide.sort((a, b) => b.over - a.over);
  return { movedY, movedX, wide: wide.slice(0, 5) };
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed:\n"
              "  pip install playwright && python -m playwright install chromium",
              file=sys.stderr)
        return 2

    failures: list[str] = []
    checked = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for role, email, paths in ROLES:
            for vp_name, w, h in VIEWPORTS:
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(args.base, wait_until="networkidle")
                page.fill('input[autocomplete="username"]', email)
                page.fill('input[autocomplete="current-password"]', PASSWORD)
                page.click('button[type="submit"]')
                try:
                    page.wait_for_selector(".app", timeout=15000)
                except Exception:
                    failures.append(f"{role}/{vp_name}: could not sign in")
                    page.close()
                    continue

                for path in paths:
                    page.goto(args.base + path, wait_until="networkidle")
                    page.wait_for_timeout(500)
                    r = page.evaluate(PROBE)
                    checked += 1
                    where = f"{role:8} {vp_name:7} {path}"
                    if r["movedY"] or r["movedX"]:
                        failures.append(
                            f"{where}: page itself scrolls "
                            f"(y={r['movedY']} x={r['movedX']}) -- there should be "
                            f"exactly one scroller, section.content")
                    if r["wide"]:
                        worst = r["wide"][0]
                        failures.append(
                            f"{where}: {worst['el']} sticks out "
                            f"{worst['over']}px past the right edge")
                page.close()
        browser.close()

    print(f"checked {checked} page renders")
    if failures:
        print(f"\n{len(failures)} problem(s):")
        for f in failures:
            print("  " + f)
        return 1
    print("no stray scrollbars, no horizontal overflow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
