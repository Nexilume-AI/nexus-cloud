import { afterEach, describe, expect, it, vi } from "vitest";
import { inspectImageForOptimization, prepareImageUpload, IMAGE_UPLOAD_LIMIT } from "./imageUpload";

function png(width = 2000, height = 1000, animated = false) {
  const bytes = new Uint8Array(45);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
  const view = new DataView(bytes.buffer);
  view.setUint32(8, 13); bytes.set([73, 72, 68, 82], 12);
  view.setUint32(16, width); view.setUint32(20, height);
  if (animated) bytes.set([97, 99, 84, 76], 37);
  return bytes;
}
function input(bytes = png()) {
  return new File([bytes, new Uint8Array(IMAGE_UPLOAD_LIMIT)], "photo.png", { type: "image/png" });
}
afterEach(() => vi.unstubAllGlobals());
describe("bounded image optimization", () => {
  it("preserves small originals byte for byte without decoding", async () => {
    const file = new File([png()], "small.png", { type: "image/png" });
    expect((await prepareImageUpload(file)).file).toBe(file);
  });
  it("validates dimensions and animation before decoding", () => {
    expect(inspectImageForOptimization(png(), "image/png")).toEqual({ width: 2000, height: 1000 });
    expect(() => inspectImageForOptimization(png(30000, 30000), "image/png")).toThrow(/dimensions/);
    expect(() => inspectImageForOptimization(png(2000, 1000, true), "image/png")).toThrow(/Animated/);
    expect(() => inspectImageForOptimization(png(), "image/jpeg")).toThrow(/valid/);
    expect(() => inspectImageForOptimization(new Uint8Array(2), "image/png")).toThrow(/valid/);
  });
  it("rejects oversized source bytes without decoding", async () => {
    await expect(prepareImageUpload(new File([new Uint8Array(20 * 1024 * 1024 + 1)], "huge.png", { type: "image/png" }))).rejects.toThrow(/20 MiB/);
  });
  it("reads JPEG and all three WebP dimension headers", () => {
    const jpeg = new Uint8Array([255, 216, 255, 192, 0, 8, 8, 0, 20, 0, 30, 0]);
    expect(inspectImageForOptimization(jpeg, "image/jpeg")).toEqual({ width: 30, height: 20 });
    const webp = new Uint8Array(30); const view = new DataView(webp.buffer);
    const set = (value: string, offset: number) => webp.set([...value].map(char => char.charCodeAt(0)), offset);
    set("RIFF", 0); set("WEBP", 8); set("VP8X", 12); webp[24] = 29; webp[27] = 19;
    expect(inspectImageForOptimization(webp, "image/webp")).toEqual({ width: 30, height: 20 });
    webp[20] = 2; expect(() => inspectImageForOptimization(webp, "image/webp")).toThrow(/Animated/);
    webp.fill(0, 20); set("VP8 ", 12); webp.set([157, 1, 42], 23); view.setUint16(26, 30, true); view.setUint16(28, 20, true);
    expect(inspectImageForOptimization(webp, "image/webp")).toEqual({ width: 30, height: 20 });
    webp.fill(0, 20); set("VP8L", 12); webp[20] = 47; view.setUint32(21, 29 | (19 << 14), true);
    expect(inspectImageForOptimization(webp, "image/webp")).toEqual({ width: 30, height: 20 });
  });
  it("does not decode when already cancelled", async () => {
    const controller = new AbortController(); controller.abort();
    await expect(prepareImageUpload(input(), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
  it("releases decoded pixels and returns an optimized copy, not the original", async () => {
    const close = vi.fn();
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 2000, height: 1000, close }));
    const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: vi.fn() }), toBlob: (done: (blob: Blob) => void) => done(new Blob(["optimized"], { type: "image/webp" })) };
    vi.stubGlobal("document", { createElement: () => canvas });
    const original = input();
    const result = await prepareImageUpload(original);
    expect(result.file.name).toBe("photo.webp"); expect(result.file.type).toBe("image/webp");
    expect(result.originalSize).toBe(original.size); expect(original.name).toBe("photo.png");
    expect(close).toHaveBeenCalledOnce(); expect(canvas.width).toBe(0);
  });
  it("bounds attempts and never returns an over-limit or unsupported encoding", async () => {
    const close = vi.fn();
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width: 2000, height: 1000, close }));
    const encode = vi.fn((done: (blob: Blob) => void) => done(new Blob([new Uint8Array(IMAGE_UPLOAD_LIMIT + 1)], { type: "image/webp" })));
    vi.stubGlobal("document", { createElement: () => ({ width: 0, height: 0, getContext: () => ({ drawImage: vi.fn() }), toBlob: encode }) });
    await expect(prepareImageUpload(input())).rejects.toThrow(/smaller copy/);
    expect(encode.mock.calls.length).toBeLessThanOrEqual(6); expect(close).toHaveBeenCalledOnce();
  });
  it("discards results cancelled while decoding and frees the bitmap", async () => {
    const controller = new AbortController(); const close = vi.fn();
    vi.stubGlobal("createImageBitmap", vi.fn(async () => { controller.abort(); return { width: 2000, height: 1000, close }; }));
    await expect(prepareImageUpload(input(), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(close).toHaveBeenCalledOnce();
  });
});
