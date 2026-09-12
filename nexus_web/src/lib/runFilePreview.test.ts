import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());
describe("caller-bound bounded file preview", () => {
  const path = "/api/v1/agent-runs/run-1/files/file-1/download/";
  const preview = (url = path, limit = 32) => api.previewRunFile({ tenantId: "org" }, "run-1", url, "private-display-token", limit, new AbortController().signal);
  it("rejects external, cross-Run and traversal references before any request", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    for (const url of ["https://evil.test/file", "/api/v1/agent-runs/other/files/file-1/download/", "/api/v1/agent-runs/run-1/files/../download/", `${path}?token=secret`]) {
      await expect(preview(url)).rejects.toThrow("Untrusted file reference");
    }
    expect(fetch).not.toHaveBeenCalled();
  });
  it("preserves Organization and Display Token headers, forbids redirects and does not put credentials in URLs", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response("private text")); vi.stubGlobal("fetch", fetch);
    expect(await (await preview()).text()).toBe("private text");
    expect(fetch).toHaveBeenCalledWith(path, expect.objectContaining({ redirect: "error", credentials: "same-origin", signal: expect.any(AbortSignal),
      headers: expect.objectContaining({ "X-Nexus-Tenant": "org", "X-Nexus-Agent-Display-Token": "private-display-token", Range: "bytes=0-32" }) }));
  });
  it("rejects oversized declared bodies before buffering and bounds streams without Content-Length", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response("tiny", { headers: { "Content-Range": "bytes 0-32/9999999" } }))
      .mockResolvedValueOnce(new Response("a".repeat(40)));
    vi.stubGlobal("fetch", fetch);
    await expect(preview()).rejects.toThrow("too large");
    await expect(preview()).rejects.toThrow("too large");
  });
  it("does not render unauthorized response bodies", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { message: "Denied" } }), { status: 404, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    await expect(preview()).rejects.toThrow();
  });
});
