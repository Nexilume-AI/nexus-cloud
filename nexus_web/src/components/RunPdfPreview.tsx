import { t, useLocale } from "../localization";
import { useEffect, useRef, useState } from "react";
import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy, type RenderTask } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerUrl;

/** Canvas only: no PDF scripts, forms, annotation actions or embedded HTML. */
export default function RunPdfPreview({ data, name, pageNumber = 1, onPageChange = () => {}, scrollKey }: { data: Uint8Array<ArrayBuffer>; name: string; pageNumber?: number; onPageChange?: (page: number) => void; scrollKey?: string }) {
  useLocale();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [rendering, setRendering] = useState(true);
  useEffect(() => {
    let active = true; setDocument(null); setError("");
    const task = getDocument({ data: data.slice(), enableXfa: false, useWorkerFetch: false, useWasm: false,
      cMapUrl: `${import.meta.env.BASE_URL}pdfjs/cmaps/`, cMapPacked: true,
      standardFontDataUrl: `${import.meta.env.BASE_URL}pdfjs/standard_fonts/`,
      useSystemFonts: true, maxImageSize: 16_000_000, canvasMaxAreaInBytes: 16_000_000 });
    task.promise.then(value => { if (active) setDocument(value); }).catch(() => {
      if (active) setError(t("This PDF could not be opened. It may be encrypted or damaged. Download it to inspect locally."));
    });
    return () => { active = false; void task.destroy(); };
  }, [data]);
  useEffect(() => {
    if (!document) return;
    let active = true; let task: RenderTask | undefined;
    setRendering(true); setText("");
    async function render() {
      try {
        const page = await document!.getPage(Math.min(pageNumber, document!.numPages));
        if (!active || !canvas.current) return;
        const base = page.getViewport({ scale: 1 });
        const scale = Math.min(1.5, 1600 / Math.max(base.width, base.height), Math.sqrt(4_000_000 / (base.width * base.height)));
        const viewport = page.getViewport({ scale });
        const element = canvas.current;
        element.width = Math.ceil(viewport.width); element.height = Math.ceil(viewport.height);
        task = page.render({ canvas: element, viewport });
        await task.promise;
        const content = await page.getTextContent();
        if (active) { setText(content.items.map(item => "str" in item ? item.str : "").join(" ").slice(0, 100_000)); setRendering(false); }
      } catch { if (active) { setRendering(false); setError(t("This PDF page could not be rendered. Download it to inspect locally.")); } }
    }
    void render();
    return () => { active = false; task?.cancel(); };
  }, [document, pageNumber]);
  if (error) return <p role="alert" className="p-3 text-sm">{error}</p>;
  return <div>
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-black/10 p-2 text-xs">
      <button type="button" className="min-h-11 rounded-md border px-3 disabled:opacity-40" disabled={!document || rendering || pageNumber <= 1} onClick={() => onPageChange(pageNumber - 1)}>{t("Previous page")}</button>
      <span aria-live="polite">{t("Page")}{" "}{pageNumber}{" "}{t("of")}{" "}{document?.numPages || "…"}</span>
      <button type="button" className="min-h-11 rounded-md border px-3 disabled:opacity-40" disabled={!document || rendering || pageNumber >= document.numPages} onClick={() => onPageChange(pageNumber + 1)}>{t("Next page")}</button>
    </div>
    {rendering ? <p role="status" className="px-3 text-xs">{t("Rendering PDF…")}</p> : null}
    <div data-context-scroll={scrollKey} className="max-h-[60vh] overflow-auto bg-[#f2f2ef] p-2" style={{ overflowAnchor: "none" }}><canvas ref={canvas} aria-label={t("PDF preview: {{0}}, page {{1}}", { 0: name, 1: pageNumber })} className="mx-auto h-auto max-w-full bg-white" /></div>
    <details className="p-3 text-xs"><summary className="min-h-11 cursor-pointer py-3">{t("Page text")}</summary><p className="max-h-48 overflow-auto whitespace-pre-wrap break-words">{text || t("No selectable text on this page.")}</p></details>
  </div>;
}
