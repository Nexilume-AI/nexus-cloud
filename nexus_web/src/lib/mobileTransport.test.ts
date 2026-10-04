import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, requestBlob } from "./api";

const context = { token: "caller-token", tenantId: "tenant-a", projectId: "project-b" };
const scopedHeaders = { Authorization: "Bearer caller-token", "X-Nexus-Tenant": "tenant-a", "X-Nexus-Project": "project-b" };
afterEach(() => vi.unstubAllGlobals());

describe("reviewed Mobile and pairing transport additions", () => {
  it("returns screen frame metadata without changing scoped image transport", async () => {
    const fetcher = vi.fn(async () => new Response("pixels", { headers: { "X-Nexus-Mobile-Screen-Frame": "frame-17" } }));
    vi.stubGlobal("fetch", fetcher);
    const screen = await api.mobileScreenImage(context, "device-a");
    expect(screen.frameId).toBe("frame-17");
    expect(await screen.blob.text()).toBe("pixels");
    expect(fetcher).toHaveBeenCalledWith("/api/v1/mobile-devices/device-a/screenshot/", expect.objectContaining({
      credentials: "same-origin", headers: expect.objectContaining({ ...scopedHeaders, Accept: "*/*" }),
    }));
    expect(await (await api.mobileScreenshot(context, "device-a")).text()).toBe("pixels");
  });

  it("uses an empty frame ID for legacy screenshots and preserves extra headers", async () => {
    const fetcher = vi.fn(async () => new Response("pixels"));
    vi.stubGlobal("fetch", fetcher);
    expect((await api.mobileScreenImage(context, "legacy")).frameId).toBe("");
    await requestBlob("/preview", context, { Accept: "image/png", "X-Preview": "1" });
    expect(fetcher).toHaveBeenLastCalledWith("/preview", expect.objectContaining({
      headers: expect.objectContaining({ ...scopedHeaders, Accept: "image/png", "X-Preview": "1" }),
    }));
  });

  it("preserves envelope error codes and request IDs for blob responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ok: false, error: { code: "NO_ACCESS", message: "Denied" }, request_id: "request-a" }), { status: 403 })));
    await expect(api.mobileScreenImage(context, "device-a")).rejects.toMatchObject({ name: "ApiError", message: "Denied", code: "NO_ACCESS", requestId: "request-a", status: 403 });
    await expect(api.mobileScreenshot(context, "device-a")).rejects.toBeInstanceOf(ApiError);
  });

  it("rejects non-envelope HTTP failures instead of returning their body as an image", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("gateway error", { status: 502, statusText: "Bad Gateway" })));
    await expect(api.mobileScreenImage(context, "device-a")).rejects.toMatchObject({ message: "Bad Gateway", status: 502 });
    await expect(requestBlob("/preview", context)).rejects.toMatchObject({ status: 502 });
  });

  it("keeps video creation, polling and signaling scoped with fresh message identities", async () => {
    const session = { id: "video-a", status: "starting" };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ ok: true, data: session })));
    vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("document", { cookie: "csrftoken=csrf-a" });
    expect(await api.startMobileVideo(context, "device-a")).toEqual(session);
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/mobile-devices/device-a/video/", expect.objectContaining({ method: "POST", body: "{}", credentials: "same-origin", headers: expect.objectContaining({ ...scopedHeaders, "X-CSRFToken": "csrf-a" }) }));
    await api.mobileVideo(context, "video-a", 7);
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/mobile-video/video-a/?after=7", expect.objectContaining({ headers: expect.objectContaining(scopedHeaders) }));
    const signal = { type: "offer", sdp: "offer-sdp", message_id: "must-not-be-reused" };
    await api.signalMobileVideo(context, "video-a", signal);
    await api.signalMobileVideo(context, "video-a", signal);
    const bodies = fetcher.mock.calls.slice(-2).map(([, init]) => JSON.parse((init as RequestInit).body as string));
    expect(bodies[0]).toMatchObject({ type: "offer", sdp: "offer-sdp" });
    expect(bodies[0].message_id).not.toBe(signal.message_id);
    expect(bodies[0].message_id).not.toBe(bodies[1].message_id);
    expect(signal.message_id).toBe("must-not-be-reused");
    expect(fetcher).toHaveBeenLastCalledWith("/api/v1/mobile-video/video-a/", expect.objectContaining({ method: "POST", headers: expect.objectContaining({ ...scopedHeaders, "X-CSRFToken": "csrf-a" }) }));
  });

  it("passes execution setup cancellation and scope to the shared client", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ok: true, data: { engines: {} } })));
    vi.stubGlobal("fetch", fetcher);
    const controller = new AbortController();
    await api.providerExecutionSetup(context, controller.signal);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/provider-connections/execution-setup/", expect.objectContaining({ signal: controller.signal, headers: expect.objectContaining(scopedHeaders) }));
  });

  it("explicitly requests pairing links and keeps caller ownership and expiry", async () => {
    const pairing = { pairing_code: "code-a", pairing_url: "https://pair.example/code-a", project_id: "project-b", expires_at: "later" };
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ok: true, data: pairing })));
    vi.stubGlobal("fetch", fetcher);
    expect(await api.createEdgePairingCode(context, { expires_in_seconds: 120 })).toEqual(pairing);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/edge/pairing-codes/", expect.objectContaining({ method: "POST", body: JSON.stringify({ expires_in_seconds: 120, include_pairing_link: true }), headers: expect.objectContaining(scopedHeaders) }));
  });
});
