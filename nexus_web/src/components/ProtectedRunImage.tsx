import { t, useLocale } from "../localization";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { useAuth } from "../app/AuthContext";
import { api } from "../lib/api";

/** Only caller-bound Run assets can produce image pixels in Chat. */
export function ProtectedRunImage({ path, title, alt }: { path: string; title?: string; alt?: string }) {
  useLocale();
  const { apiContext } = useAuth();
  const match = /^\/api\/v1\/agent-runs\/([0-9a-f-]{36})\/display-assets\/[0-9a-f-]{36}\/$/i.exec(path);
  const runId = match?.[1] || "";
  const token = useQuery({ queryKey: ["agent-run-display-token", apiContext, runId], queryFn: () => api.issueAgentRunDisplayToken(apiContext, runId), enabled: Boolean(runId), staleTime: 45 * 60 * 1000 });
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    let objectUrl = "";
    setUrl(""); setFailed(false);
    if (runId && token.data?.display_token) {
      api.privateAgentRunDisplayAsset(apiContext, path, token.data.display_token).then((blob) => {
        if (!active) return;
        if (!["image/png", "image/jpeg", "image/webp"].includes(blob.type)) throw new Error("Unsupported image");
        objectUrl = URL.createObjectURL(blob); setUrl(objectUrl);
      }).catch(() => { if (active) setFailed(true); });
    }
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [apiContext, path, runId, token.data?.display_token, attempt]);
  if (!runId) return <p className="text-sm opacity-70">{t("Untrusted image reference was blocked.")}</p>;
  return <figure className="min-w-0 overflow-hidden rounded-lg border border-black/10 bg-white text-[#34322d]">
    {url ? <img src={url} alt={alt || title || t("Agent image")} className="max-h-[32rem] w-full object-contain" loading="lazy" />
      : failed || token.isError ? <div role="alert" className="p-4 text-sm">{t("Image unavailable.")}{" "}<button type="button" className="min-h-11 underline" onClick={() => { void token.refetch(); setAttempt((value) => value + 1); }}>{t("Retry")}</button></div>
      : <div role="status" className="flex items-center gap-2 p-4 text-sm"><Loader2 size={16} className="animate-spin" />{t("Loading image")}</div>}
    <figcaption className="flex items-center justify-between gap-3 border-t border-black/10 px-3 text-xs">
      <span className="truncate">{title || t("Image")}</span>
      {url ? <a href={url} download="agent-image" className="inline-flex min-h-11 items-center gap-2" aria-label={t("Download {{0}}", { 0: title || "image" })}><Download size={14} />{t("Download")}</a> : null}
    </figcaption>
  </figure>;
}
