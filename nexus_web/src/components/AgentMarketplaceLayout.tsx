import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowUpRight, Plug } from "lucide-react";
import { t, useLocale } from "../localization";
import "./AgentMarketplaceLayout.css";

type Section = { id: "overview" | "tools" | "details"; label: string; content: ReactNode };

/** Presentation only: the host supplies catalog data, login and connection actions. */
export function AgentMarketplaceLayout({ sections, price, requirements, devices, onConnect, unavailable = false }: {
  sections: Section[]; price: string; requirements: string[]; devices?: ReactNode;
  onConnect: () => void; unavailable?: boolean;
}) {
  useLocale();
  const [active, setActive] = useState<Section["id"]>("overview");
  const id = useId();
  const selected = sections.find(section => section.id === active) ?? sections[0];
  function navigate(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + sections.length) % sections.length;
    setActive(sections[next].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
  }
  return <div className="agent-marketplace-layout">
    <aside className="agent-marketplace-connect" aria-label={t("Agent connection")}>
      <div className="agent-marketplace-connect-heading"><span className="agent-marketplace-connect-icon"><Plug size={20} /></span>
        <div><h3>{t("Connect Agent")}</h3><p>{t("Use in Codex, Claude Code and other MCP clients.")}</p></div></div>
      <div className="agent-marketplace-connect-price"><strong>{price}</strong><span>MCP · Streamable HTTP</span></div>
      <button type="button" className="btn btn-primary min-h-11 w-full justify-center" onClick={onConnect} disabled={unavailable}>
        {t(unavailable ? "Connection unavailable" : "Connect Agent")}<ArrowUpRight size={16} /></button>
      <p className="agent-marketplace-connect-hint">{t("Choose your client and copy its configuration.")}</p>
      {requirements.length > 0 && <div className="agent-marketplace-requirements"><h4>{t("Before you start")}</h4>
        <ul>{requirements.map(requirement => <li key={requirement}>{requirement}</li>)}</ul></div>}
      {devices && <div className="agent-marketplace-devices">{devices}</div>}
    </aside>
    <div className="agent-marketplace-content">
      <div role="tablist" aria-label={t("Product details")} className="agent-marketplace-tabs">
        {sections.map((section, index) => <button type="button" key={section.id} role="tab"
          id={`${id}-tab-${section.id}`} aria-controls={`${id}-panel-${section.id}`} aria-selected={selected.id === section.id}
          tabIndex={selected.id === section.id ? 0 : -1} onClick={() => setActive(section.id)} onKeyDown={event => navigate(event, index)}>
          {t(section.label)}</button>)}
      </div>
      <div role="tabpanel" id={`${id}-panel-${selected.id}`} aria-labelledby={`${id}-tab-${selected.id}`} className="agent-marketplace-panel">
        {selected.content}
      </div>
    </div>
  </div>;
}
