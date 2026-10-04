import { t, useLocale, getLocale } from "../localization";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "./AuthContext";
import { CommandPalette, type CommandSection } from "./shell/CommandPalette";
import { ContextRail } from "./shell/ContextRail";
import {
  domainForPath,
  homeItem,
  itemMatchesPath,
  itemsForDomain,
  visibleNavigationDomains,
  type NavigationDomainId,
  type NavigationDomain,
  type NavigationItem
} from "./shell/navigation";
import { openDetachedWindow, ShellNavigation, type SidebarPreference } from "./shell/ShellNavigation";

const SIDEBAR_PREFERENCE_KEY = "nexilume.shell.sidebar-mode";

function loadSidebarPreference(): SidebarPreference {
  const stored = localStorage.getItem(SIDEBAR_PREFERENCE_KEY);
  return stored === "expanded" || stored === "compact" ? stored : "auto";
}

function useWideDesktop() {
  const [wide, setWide] = useState(() => window.matchMedia("(min-width: 1440px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1440px)");
    const update = () => setWide(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return wide;
}

export function AppShell({ children, navigationDomains }: { children: ReactNode; navigationDomains: NavigationDomain[] }) {
  useLocale();
  const auth = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const wideDesktop = useWideDesktop();
  const [sidebarPreference, setSidebarPreference] = useState<SidebarPreference>(loadSidebarPreference);
  const sidebarCompact = sidebarPreference === "compact" || (sidebarPreference === "auto" && !wideDesktop);
  const [mobileOpen, setMobileOpen] = useState(false);
  const mobileInvokerRef = useRef<HTMLElement | null>(null);
  const [expandedDomain, setExpandedDomain] = useState<NavigationDomainId | null>(() => domainForPath(location.pathname, navigationDomains)?.id ?? "build");
  const [commandOpen, setCommandOpen] = useState(false);
  const commandInvokerRef = useRef<HTMLElement | null>(null);

  const visibleDomains = useMemo(
    () => visibleNavigationDomains(auth.isAuthenticated, Boolean(auth.user?.is_superuser), navigationDomains),
    [auth.isAuthenticated, auth.user?.is_superuser, navigationDomains]
  );
  const allItems = useMemo(() => [homeItem, ...navigationDomains.flatMap(itemsForDomain)], [navigationDomains]);
  const currentItem = allItems.find((item) => itemMatchesPath(item, location.pathname)) ?? homeItem;
  const currentDomain = domainForPath(location.pathname, navigationDomains);
  const commandSections = useMemo<CommandSection[]>(
    () => [
      { id: "overview", label: t("Network"), verb: t("Overview"), items: [homeItem] },
      ...visibleDomains.map((domain) => ({
        id: domain.id,
        label: domain.label,
        verb: domain.verb,
        items: itemsForDomain(domain)
      }))
    ],
    [visibleDomains, getLocale()]
  );

  useEffect(() => {
    const activeDomain = domainForPath(location.pathname, visibleDomains);
    setExpandedDomain(sidebarCompact ? null : activeDomain?.id ?? null);
    setMobileOpen(false);
  }, [location.pathname, sidebarCompact, visibleDomains]);

  useEffect(() => {
    localStorage.setItem(SIDEBAR_PREFERENCE_KEY, sidebarPreference);
  }, [sidebarPreference]);

  useEffect(() => {
    function handleKeyboard(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (commandOpen) closeCommand();
        else {
          commandInvokerRef.current = document.activeElement as HTMLElement | null;
          setCommandOpen(true);
        }
      }
      if (event.key === "Escape" && mobileOpen) {
        closeMobileNavigation();
      }
    }
    window.addEventListener("keydown", handleKeyboard);
    return () => window.removeEventListener("keydown", handleKeyboard);
  }, [commandOpen, mobileOpen]);

  useEffect(() => {
    if (!mobileOpen) return;
    const dialog = document.querySelector<HTMLElement>('.nexilume-mobile-navigation[role="dialog"]');
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), [href], [tabindex]:not([tabindex="-1"])') ?? [])
      .filter(element => element.getClientRects().length > 0);
    window.requestAnimationFrame(() => dialog?.querySelector<HTMLElement>('[data-close-navigation]')?.focus());
    function trapFocus(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const elements = focusable();
      if (!elements?.length) return;
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    dialog?.addEventListener("keydown", trapFocus);
    return () => dialog?.removeEventListener("keydown", trapFocus);
  }, [mobileOpen]);

  function setPreference(preference: SidebarPreference) {
    setSidebarPreference(preference);
  }

  function toggleCompact() {
    setPreference(sidebarCompact ? "expanded" : "compact");
  }

  function toggleDomain(domainId: NavigationDomainId) {
    setExpandedDomain((current) => current === domainId ? null : domainId);
  }

  function handleNavigate() {
    setMobileOpen(false);
    if (sidebarCompact) setExpandedDomain(null);
  }

  function openMobileNavigation() {
    mobileInvokerRef.current = document.activeElement as HTMLElement | null;
    setExpandedDomain(currentDomain?.id ?? visibleDomains[0]?.id ?? null);
    setMobileOpen(true);
  }

  function closeMobileNavigation() {
    setMobileOpen(false);
    window.requestAnimationFrame(() => mobileInvokerRef.current?.focus());
  }

  function openCommand(invoker: HTMLElement) {
    commandInvokerRef.current = invoker;
    setCommandOpen(true);
  }

  function closeCommand() {
    setCommandOpen(false);
    window.requestAnimationFrame(() => commandInvokerRef.current?.focus());
  }

  function selectCommandItem(item: NavigationItem) {
    setCommandOpen(false);
    if (item.opensInWindow && openDetachedWindow(item.to, item.windowName)) return;
    navigate(item.to);
  }

  return (
    <div className={`nexilume-cloud-shell ${sidebarCompact ? "is-sidebar-compact" : ""}`}>
      <div className="nexilume-cloud-shell__desktop-nav">
        <ShellNavigation
          domains={visibleDomains}
          currentPath={location.pathname}
          expandedDomain={expandedDomain}
          compact={sidebarCompact}
          onToggleDomain={toggleDomain}
          onNavigate={handleNavigate}
          onToggleCompact={toggleCompact}
        />
      </div>

      {mobileOpen ? (
        <div className="nexilume-mobile-navigation" role="dialog" aria-modal="true" aria-label={t("Navigation")}>
          <button className="nexilume-mobile-navigation__backdrop" onClick={closeMobileNavigation} aria-label={t("Dismiss navigation")} type="button" />
          <ShellNavigation
            domains={visibleDomains}
            currentPath={location.pathname}
            expandedDomain={expandedDomain}
            compact={false}
            mobile
            onToggleDomain={toggleDomain}
            onNavigate={handleNavigate}
            onClose={closeMobileNavigation}
            onToggleCompact={toggleCompact}
          />
        </div>
      ) : null}

      <div className="nexilume-cloud-shell__surface">
        <ContextRail
          currentDomain={currentDomain}
          currentItem={currentItem}
          sidebarPreference={sidebarPreference}
          onOpenMobileNavigation={openMobileNavigation}
          onOpenCommand={openCommand}
          onResetSidebarPreference={() => setPreference("auto")}
        />
        <main className="nexilume-cloud-shell__main">{children}</main>
      </div>

      <CommandPalette
        open={commandOpen}
        sections={commandSections}
        onClose={closeCommand}
        onSelect={selectCommandItem}
      />
    </div>
  );
}
