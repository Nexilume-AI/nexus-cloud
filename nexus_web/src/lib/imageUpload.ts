/** Local-only upload copies. Original files and decoded pixels never enter draft storage. */
export const IMAGE_UPLOAD_LIMIT = 2 * 1024 * 1024;
export const IMAGE_SOURCE_LIMIT = 20 * 1024 * 1024;
const TYPES = ["image/png", "image/jpeg", "image/webp"];
export class ImagePreparationError extends Error {}
function invalid(): never { throw new ImagePreparationError("This is not a valid PNG, JPEG or WebP image. Export a new copy and try again."); }
function animated(): never { throw new ImagePreparationError("Animated images cannot be optimized without losing frames. Attach a still image instead."); }

// Read dimensions before asking the browser to allocate decoded pixels. This is
// a bounded preflight, not a substitute for Cloud's content/security validation.
export function inspectImageForOptimization(bytes: Uint8Array, type: string) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const text = (offset: number, length: number) => String.fromCharCode(...bytes.subarray(offset, offset + length));
  let width = 0, height = 0;
  if (type === "image/png" && bytes.length >= 33 && text(1, 7) === "PNG\r\n\x1a\n" && bytes[0] === 137 && text(12, 4) === "IHDR") {
    width = view.getUint32(16); height = view.getUint32(20);
    for (let p = 8; p + 12 <= bytes.length;) {
      const size = view.getUint32(p);
      if (text(p + 4, 4) === "acTL") animated();
      if (size > bytes.length - p - 12) invalid();
      if (text(p + 4, 4) === "IEND") break;
      p += size + 12;
    }
  } else if (type === "image/jpeg" && bytes.length >= 4 && bytes[0] === 255 && bytes[1] === 216) {
    for (let p = 2; p + 4 <= bytes.length;) {
      if (bytes[p++] !== 255) invalid();
      while (p < bytes.length && bytes[p] === 255) p++;
      const marker = bytes[p++];
      if (marker === 217 || marker === 218) break;
      if (marker === 1 || (marker >= 208 && marker <= 215)) continue;
      if (p + 2 > bytes.length) invalid();
      const size = view.getUint16(p);
      if (size < 2 || p + size > bytes.length) invalid();
      if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker)) {
        if (size < 8) invalid();
        height = view.getUint16(p + 3); width = view.getUint16(p + 5); break;
      }
      p += size;
    }
  } else if (type === "image/webp" && bytes.length >= 30 && text(0, 4) === "RIFF" && text(8, 4) === "WEBP") {
    const kind = text(12, 4);
    if (kind === "VP8X") {
      if (bytes[20] & 2) animated();
      width = 1 + bytes[24] + bytes[25] * 256 + bytes[26] * 65536;
      height = 1 + bytes[27] + bytes[28] * 256 + bytes[29] * 65536;
    } else if (kind === "VP8 " && bytes.length >= 30 && text(23, 3) === "\x9d\x01\x2a") {
      width = view.getUint16(26, true) & 16383; height = view.getUint16(28, true) & 16383;
    } else if (kind === "VP8L" && bytes[20] === 47) {
      const bits = view.getUint32(21, true);
      width = (bits & 16383) + 1; height = ((bits >>> 14) & 16383) + 1;
    }
  }
  if (!width || !height) invalid();
  if (width > 16384 || height > 16384 || width * height > 40_000_000)
    throw new ImagePreparationError("Image dimensions are too large to optimize safely. Export a smaller copy (at most 40 megapixels and 16,384 pixels per side).");
  return { width, height };
}

export function validateImageSource(file: File) {
  if (!TYPES.includes(file.type) || !file.size)
    throw new ImagePreparationError("Choose a non-empty PNG, JPEG or WebP image.");
  if (file.size > IMAGE_SOURCE_LIMIT)
    throw new ImagePreparationError("Choose an image up to 20 MiB, or export a smaller copy.");
}
function check(signal?: AbortSignal) { if (signal?.aborted) throw new DOMException("Image preparation cancelled", "AbortError"); }
export async function prepareImageUpload(file: File, signal?: AbortSignal): Promise<{ file: File; originalSize?: number }> {
  check(signal); validateImageSource(file);
  if (file.size <= IMAGE_UPLOAD_LIMIT) return { file };
  const bytes = new Uint8Array(await file.arrayBuffer());
  check(signal); const dimensions = inspectImageForOptimization(bytes, file.type);
  if (typeof createImageBitmap !== "function")
    throw new ImagePreparationError("This browser cannot optimize images. Export a copy under 2 MiB and attach it again.");
  let bitmap: ImageBitmap;
  try { bitmap = await createImageBitmap(file); } catch { check(signal); return invalid(); }
  let canvas: HTMLCanvasElement | undefined;
  try {
    check(signal);
    if (!bitmap.width || !bitmap.height || bitmap.width * bitmap.height !== dimensions.width * dimensions.height)
      invalid();
    canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) throw new ImagePreparationError("Image preparation is unavailable. Export a smaller copy and try again.");
    // Preserve orientation/transparency; bound both memory and encoding work.
    for (const edge of [4096, 3072, 2048]) {
      const scale = Math.min(1, edge / Math.max(bitmap.width, bitmap.height));
      canvas.width = Math.max(1, Math.round(bitmap.width * scale));
      canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.92, 0.84]) {
        check(signal);
        const blob = await new Promise<Blob | null>(resolve => canvas!.toBlob(resolve, "image/webp", quality));
        check(signal);
        if (!blob || blob.type !== "image/webp") throw new ImagePreparationError("Image optimization is unavailable. Export a copy under 2 MiB and try again.");
        if (blob.size > 0 && blob.size <= IMAGE_UPLOAD_LIMIT) return {
          file: new File([blob], file.name.replace(/\.[^.]*$/, "") + ".webp", { type: "image/webp", lastModified: file.lastModified }),
          originalSize: file.size,
        };
      }
    }
    throw new ImagePreparationError("This image cannot fit within 2 MiB at a readable quality. Crop it or export a smaller copy.");
  } finally { bitmap.close(); if (canvas) { canvas.width = 0; canvas.height = 0; } }
}
