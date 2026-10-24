import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { t, useLocale } from "../localization";
import "./AgentMarketplaceLayout.css";

type Section = { id: "overview" | "tools" | "details"; label: string; content: ReactNode };

/** Presentation only: the host supplies catalog data, login and connection actions. */
export function AgentMarketplaceLayout({ sections }: { sections: Section[] }) {
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
    <div className="agent-marketplace-content">
      <div role="tablist" aria-label={t("Product details")} className="agent-marketplace-tabs">
        {sections.map((section, index) => <button type="button" key={section.id} role="tab"
          id={`${id}-tab-${section.id}`} aria-controls={`${id}-panel-${section.id}`} aria-selected={selected.id === section.id}
          tabIndex={selected.id === section.id ? 0 : -1} onClick={() => setActive(section.id)} onKeyDown={event => navigate(event, index)}>
          {t(section.label)}</button>)}
      </div>
      <div tabIndex={0} role="tabpanel" id={`${id}-panel-${selected.id}`} aria-labelledby={`${id}-tab-${selected.id}`} className="agent-marketplace-panel">
        {selected.content}
      </div>
    </div>
  </div>;
}

/** Setup information belongs beside the client configuration, not above the overview. */
export function AgentMarketplaceConnectionContext({ requirements, devices }: { requirements: string[]; devices?: ReactNode }) {
  useLocale();
  if (!requirements.length && !devices) return null;
  return <section className="agent-marketplace-setup" aria-label={t("Before you start")}>
    {requirements.length > 0 && <ul>{requirements.map(requirement => <li key={requirement}>{requirement}</li>)}</ul>}
    {devices && <div className="agent-marketplace-devices">{devices}</div>}
  </section>;
}
