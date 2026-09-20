import { t, tToken, useLocale } from "../localization";
/** Shared presentation and existing action checks; no commercial requests. */
import { useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { NexilumeDialog } from "./NexilumeControls";
import type { Agent } from "../lib/types";

export function ResponsiveAgentInspector({
  eyebrow,
  title,
  description,
  children,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  useLocale();
  const [open, setOpen] = useState(false);
  const content = (mode: "desktop" | "drawer") => (
    <aside
      className={`agent-inspector agent-inspector--${mode}`}
      aria-label={t("{{0}} context", { 0: eyebrow })}
    >
      <p className="agent-inspector__eyebrow">{t("Inspector ·")}{" "}{eyebrow}</p>
      <h2>{title}</h2>
      <p>{description}</p>
      <dl>{children}</dl>
      {action}
    </aside>
  );
  return (
    <>
      <button
        className="agent-responsive-inspector-trigger"
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <span>
          <small>{t("Inspector ·")}{" "}{eyebrow}</small>
          <strong>{title}</strong>
        </span>
        <span>{t("View context")}{" "}<ChevronRight size={15} />
        </span>
      </button>
      {content("desktop")}
      <NexilumeDialog
        open={open}
        variant="drawer"
        eyebrow={t("Inspector · {{0}}", { 0: eyebrow })}
        title={title}
        description={description}
        onClose={() => setOpen(false)}
      >
        {content("drawer")}
      </NexilumeDialog>
    </>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  useLocale();
  return (
    <header className="agent-section-heading">
      <p className="agent-control-eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      <p>{description}</p>
    </header>
  );
}

export function InspectorValue({ label, value }: { label: string; value: string }) {
  useLocale();
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function canAgent(agent: Agent, action: string) {
  return (
    !agent.allowed_actions?.length || agent.allowed_actions.includes(action)
  );
}

export function agentOwnershipLabel(agent: Agent) {
  if (agent.ownership?.label) return agent.ownership.label;
  return agent.project_id ? t("Project resource") : t("Organization shared");
}

export function humanize(value: string | null | undefined) {
  const normalized = String(value ?? "").trim();
  if (!normalized) return t("Unknown");
  return tToken(normalized);
}

export function mutationError(fallback: string) {
  return (error: unknown) =>
    toast.error(error instanceof Error ? error.message : fallback);
}
import { Link } from "react-router-dom";
export function OverviewRow({
  icon,
  title,
  value,
  detail,
  to,
}: {
  icon: ReactNode;
  title: string;
  value: string;
  detail: string;
  to: string;
}) {
  useLocale();
  return (
    <Link className="agent-overview-row" to={to}>
      <span className="agent-overview-row__icon">{icon}</span>
      <span>
        <small>{title}</small>
        <strong>{value}</strong>
        <em>{detail}</em>
      </span>
      <ChevronRight size={16} />
    </Link>
  );
}
