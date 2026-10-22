import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { t, useLocale } from "../localization";
import "./PublisherBadgePanel.css";

type Badge = { name: string; image_url: string; target_url: string };
type Badges = { agent: Badge; publisher: Badge };

export function PublisherBadgePanel({ agentId }: { agentId: string }) {
  useLocale();
  const [kind, setKind] = useState<keyof Badges>("agent");
  const [style, setStyle] = useState("compact");
  const [format, setFormat] = useState("Markdown");
  const [imageFailed, setImageFailed] = useState(false);
  const query = useQuery({
    queryKey: ["public", "marketplace-badges", agentId],
    queryFn: async (): Promise<Badges> => {
      const response = await fetch(`/api/v1/marketplace/badges/agents/${encodeURIComponent(agentId)}/`, { credentials: "omit" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error("Badge unavailable");
      return payload.data;
    },
    staleTime: 60_000,
    retry: false,
  });
  const selected = query.data?.[kind];
  const image = selected ? `${selected.image_url}?style=${style}` : "";
  const alt = kind === "agent" ? "Available on Nexus" : "Nexus Publisher";
  const attr = (text: string) => text.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
  const code = selected ? format === "Markdown"
    ? `[![${alt}](${image})](${selected.target_url})`
    : `<a href="${attr(selected.target_url)}"><img src="${attr(image)}" alt="${alt}" /></a>` : "";
  async function copy() {
    try { await navigator.clipboard.writeText(code); toast.success(t("Badge code copied")); }
    catch { toast.error(t("Could not copy. Select the embed code below.")); }
  }
  return <section className="publisher-badge-panel" aria-label={t("Share your badge")}>
    <header><h3>{t("Share your badge")}</h3><p>{t("Automatically generated for every published Agent. Embed it on your website or README.")}</p></header>
    {query.isPending ? <p role="status">{t("Loading badges…")}</p> : query.isError ? <div role="alert"><p>{t("Badges are available for publicly listed Agents only.")}</p><button className="btn btn-secondary" onClick={() => void query.refetch()}>{t("Retry")}</button></div> : <>
      <div className="publisher-badge-options">
        <label>{t("Badge")}<select className="input" aria-label={t("Badge")} value={kind} onChange={event => { setKind(event.target.value as keyof Badges); setImageFailed(false); }}><option value="agent">{t("Product badge")}</option><option value="publisher">{t("Publisher badge")}</option></select></label>
        <label>{t("Style")}<select className="input" aria-label={t("Style")} value={style} onChange={event => { setStyle(event.target.value); setImageFailed(false); }}><option value="compact">{t("Compact")}</option><option value="card">{t("Honor card")}</option></select></label>
        <label>{t("Embed format")}<select className="input" aria-label={t("Embed format")} value={format} onChange={event => setFormat(event.target.value)}><option>Markdown</option><option>HTML</option></select></label>
      </div>
      <div className="publisher-badge-preview">{imageFailed ? <p role="alert">{t("Badge preview unavailable. Refresh to check publication status.")}</p> : <a href={selected?.target_url}><img key={image} src={image} alt={alt} width={style === "card" ? 600 : 420} height={style === "card" ? 220 : 64} onError={() => setImageFailed(true)} /></a>}</div>
      <div className="publisher-badge-actions"><button className="btn btn-primary" onClick={() => void copy()}>{t("Copy embed code")}</button><a className="btn btn-secondary" href={image} download="nexus-badge.svg">{t("Download SVG")}</a><a className="text-brand" href={query.data?.publisher.target_url}>{t("View publisher page")}</a></div>
      <textarea className="input publisher-badge-code" aria-label={t("Embed code")} readOnly value={code} rows={3} onFocus={event => event.target.select()} />
      <p className="publisher-badge-note">{t("Recognizes published contributions, not certification. Linked images update with publication status; third-party caches may take longer.")}</p>
    </>}
  </section>;
}
