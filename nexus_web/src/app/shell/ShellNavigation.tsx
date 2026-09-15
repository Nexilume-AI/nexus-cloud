import { t, useLocale } from "../../localization";
import { ChevronDown, ExternalLink, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { Link, NavLink } from "react-router-dom";

import { NexilumeBrand, NexilumeMark } from "../../components/NexilumeBrand";
import {
  homeItem,
  itemMatchesPath,
  type NavigationDomain,
  type NavigationDomainId,
  type NavigationItem,
  type NavigationLaneVariant
} from "./navigation";

export type SidebarPreference = "auto" | "expanded" | "compact";

export function ShellNavigation({
  domains,
  currentPath,
  expandedDomain,
  compact,
  mobile = false,
  onToggleDomain,
  onNavigate,
  onClose,
  onToggleCompact
}: {
  domains: NavigationDomain[];
  currentPath: string;
  expandedDomain: NavigationDomainId | null;
  compact: boolean;
  mobile?: boolean;
  onToggleDomain: (domainId: NavigationDomainId) => void;
  onNavigate: () => void;
  onClose?: () => void;
  onToggleCompact: () => void;
}) {
  useLocale();
  const activeDomain = domains.find((domain) => domain.lanes.some((lane) => lane.items.some((item) => itemMatchesPath(item, currentPath))));
  const isCompact = compact && !mobile;

  return (
    <aside className={`nexilume-shell-sidebar ${isCompact ? "is-compact" : ""} ${mobile ? "is-mobile" : ""}`} data-sidebar-mode={isCompact ? "compact" : "expanded"}>
      <div className="nexilume-shell-sidebar__brand">
        <Link to="/" className="nexilume-shell-sidebar__brand-link" onClick={onNavigate} aria-label={t("Nexilume AI")}>
          {isCompact ? <NexilumeMark className="h-9 w-9" /> : <NexilumeBrand dark subtitle={t("Capability fabric")} />}
        </Link>
        {mobile && onClose ? (
          <button className="nexilume-shell-icon-button is-on-ink" onClick={onClose} data-close-navigation aria-label={t("Close navigation")} type="button">
            <X size={18} />
          </button>
        ) : null}
      </div>

      <nav className="nexilume-shell-nav" aria-label={t("Primary navigation")}>
        <ShellNavigationLink item={homeItem} currentPath={currentPath} compact={isCompact} onNavigate={onNavigate} />
        <div className="nexilume-shell-nav__separator" aria-hidden="true"><i /></div>

        <div className="nexilume-shell-domains">
          {domains.map((domain) => {
            const expanded = expandedDomain === domain.id;
            const active = activeDomain?.id === domain.id;
            return (
              <section className={`nexilume-shell-domain ${active ? "is-active" : ""} ${expanded ? "is-expanded" : ""}`} key={domain.id}>
                <button
                  className="nexilume-shell-domain__trigger"
                  onClick={() => onToggleDomain(domain.id)}
                  aria-expanded={expanded}
                  aria-label={isCompact ? t("{{0}} navigation", { 0: domain.label }) : undefined}
                  title={isCompact ? `${t(domain.label)} · ${t(domain.verb)}` : undefined}
                  type="button"
                >
                  <DomainMark domain={domain.id} />
                  <span className="nexilume-shell-domain__copy">
                    <strong>{t(domain.label)}</strong>
                    <small>{t(domain.verb)}</small>
                  </span>
                  <ChevronDown className="nexilume-shell-domain__chevron" size={14} aria-hidden="true" />
                </button>

                {expanded ? (
                  <div className="nexilume-shell-domain__panel">
                    {isCompact ? (
                      <header className="nexilume-shell-domain__panel-header">
                        <span>
                          <small>{t(domain.verb)}</small>
                          <strong>{t(domain.label)}</strong>
                        </span>
                        <p>{t(domain.description)}</p>
                      </header>
                    ) : null}
                    {domain.lanes.map((lane) => (
                      <div className={`nexilume-shell-lane nexilume-shell-lane--${lane.variant}`} key={lane.id}>
                        {lane.label ? <div className="nexilume-shell-lane__label">{t(lane.label)}</div> : null}
                        <div className="nexilume-shell-lane__items">
                          {lane.items.map((item) => (
                            <ShellNavigationLink
                              key={item.to}
                              item={item}
                              currentPath={currentPath}
                              variant={lane.variant}
                              compact={false}
                              onNavigate={onNavigate}
                            />
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      </nav>

      {!mobile ? (
        <div className="nexilume-shell-sidebar__footer">
          <button
            className="nexilume-shell-sidebar__mode"
            onClick={onToggleCompact}
            aria-label={isCompact ? t("Expand navigation") : t("Use compact navigation")}
            title={isCompact ? t("Expand navigation") : t("Use compact navigation")}
            type="button"
          >
            {isCompact ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
            {!isCompact ? <span>{t("Compact navigation")}</span> : null}
          </button>
        </div>
      ) : null}
    </aside>
  );
}

function DomainMark({ domain }: { domain: NavigationDomainId }) {
  useLocale();
  return (
    <span className={`nexilume-domain-mark nexilume-domain-mark--${domain}`} aria-hidden="true">
      <i />
      <b />
      <em />
    </span>
  );
}

function ShellNavigationLink({
  item,
  currentPath,
  variant = "standard",
  compact,
  onNavigate
}: {
  item: NavigationItem;
  currentPath: string;
  variant?: NavigationLaneVariant;
  compact: boolean;
  onNavigate: () => void;
}) {
  useLocale();
  const Icon = item.icon;
  const active = itemMatchesPath(item, currentPath);
  const className = `nexilume-shell-link nexilume-shell-link--${variant} ${active ? "is-active" : ""} ${compact ? "is-compact" : ""}`;
  const content = (
    <>
      <span className="nexilume-shell-link__endpoint" aria-hidden="true" />
      {item.decoration ?? (variant === "capability" ? (
        <span className="nexilume-shell-link__stage" aria-hidden="true">{item.step}</span>
      ) : (
        <Icon className="nexilume-shell-link__icon" size={17} aria-hidden="true" />
      ))}
      <span className="nexilume-shell-link__label">{t(item.label)}</span>
      {item.verb ? <span className="nexilume-shell-link__verb">{t(item.verb)}</span> : null}
      {item.opensInWindow ? <ExternalLink className="nexilume-shell-link__external" size={13} aria-hidden="true" /> : null}
    </>
  );

  if (item.opensInWindow) {
    return (
      <a
        href={item.to}
        target="_blank"
        rel="opener"
        onClick={(event) => {
          const popup = openDetachedWindow(item.to, item.windowName);
          if (popup) event.preventDefault();
          onNavigate();
        }}
        className={className}
        aria-label={t("{{0}}, open in a new window", { 0: item.label })}
      >
        {content}
      </a>
    );
  }

  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      onClick={onNavigate}
      className={className}
      aria-label={compact ? item.label : undefined}
      title={compact ? item.label : undefined}
    >
      {content}
    </NavLink>
  );
}

export function openDetachedWindow(path: string, windowName = "nexus-workspace") {
  const popup = window.open(
    path,
    windowName,
    "popup=yes,width=1440,height=960,resizable=yes,scrollbars=yes"
  );
  popup?.focus();
  return popup;
}
