/** The fetch wrapper.
 *
 * Two properties matter here and neither is obvious from reading the calls:
 * every mutating request carries the CSRF header (the server now rejects any
 * that does not), and FastAPI's `detail` is what the user ends up seeing
 * instead of "Internal Server Error".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchJSON, patchJSON, postForm, postJSON } from "../lib/api.js";

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    statusText: "Status Text",
    json: async () => body,
  };
}

beforeEach(() => {
  document.cookie = "offboard_csrf=tok-123";
  global.fetch = vi.fn(async () => jsonResponse({ ok: true }));
});

afterEach(() => {
  vi.restoreAllMocks();
  document.cookie = "offboard_csrf=; max-age=0";
});

describe("CSRF", () => {
  it("postJSON sends the double-submit token", async () => {
    await postJSON("/api/employees", { name: "x" });
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.method).toBe("POST");
    expect(opts.headers["X-CSRF-Token"]).toBe("tok-123");
  });

  it("patchJSON sends it too", async () => {
    await patchJSON("/api/admin/users/1", { role: "it" });
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.method).toBe("PATCH");
    expect(opts.headers["X-CSRF-Token"]).toBe("tok-123");
  });

  it("postForm sends it without overriding the multipart Content-Type", async () => {
    await postForm("/api/agents/x/upload", new FormData());
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers["X-CSRF-Token"]).toBe("tok-123");
    // The browser has to set this itself -- it carries the boundary.
    expect(opts.headers["Content-Type"]).toBeUndefined();
  });

  it("sends an empty token rather than crashing when the cookie is gone", async () => {
    document.cookie = "offboard_csrf=; max-age=0";
    await postJSON("/api/employees", {});
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers["X-CSRF-Token"]).toBe("");
  });
});

describe("error surfacing", () => {
  it("throws FastAPI's detail, not the status text", async () => {
    global.fetch = vi.fn(async () =>
      jsonResponse({ detail: "This exit case is outside your scope." },
                   { ok: false, status: 403 }));
    await expect(fetchJSON("/api/cases/case-001"))
      .rejects.toThrow("This exit case is outside your scope.");
  });

  it("attaches the status so callers can branch without parsing prose", async () => {
    global.fetch = vi.fn(async () =>
      jsonResponse({ detail: "nope" }, { ok: false, status: 403 }));
    await expect(fetchJSON("/x")).rejects.toMatchObject({ status: 403 });
  });

  it("falls back to the status text on a non-JSON error body", async () => {
    global.fetch = vi.fn(async () => ({
      ok: false,
      status: 502,
      statusText: "Bad Gateway",
      json: async () => { throw new Error("not json"); },
    }));
    await expect(fetchJSON("/x")).rejects.toThrow("Bad Gateway");
  });
});
