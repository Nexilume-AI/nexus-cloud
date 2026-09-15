import { t, useLocale } from "../localization";
import { useState } from "react";
import { Check, Copy } from "lucide-react";

export function ChatCopyButton({ text, label, caption = "Copy" }: { text: string | (() => string); label: string; caption?: string }) {
  useLocale();
  const [copied, setCopied] = useState(false);
  const [manual, setManual] = useState<string | null>(null);
  async function copy() {
    const value = typeof text === "function" ? text() : text;
    setCopied(false);
    try { await navigator.clipboard.writeText(value); setCopied(true); setManual(null); }
    catch { setManual(value); }
  }
  return <div className="min-w-0"><button type="button" aria-label={label} onClick={copy} className="inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 text-xs text-[#535350] hover:bg-black/5 focus-visible:outline focus-visible:outline-2">
    {copied ? <Check size={13} /> : <Copy size={13} />}{copied ? t("Copied") : caption}
  </button>{manual !== null ? <div role="status" className="my-1 text-xs text-[#535350]"><p>{t("Clipboard unavailable. Select and copy this text:")}</p><textarea readOnly aria-label={t("Manual {{0}}", { 0: label.toLowerCase() })} value={manual} onFocus={event => event.target.select()} className="mt-1 min-h-20 w-full resize-y rounded-md border border-black/15 bg-white p-2 font-mono" /></div> : null}</div>;
}
