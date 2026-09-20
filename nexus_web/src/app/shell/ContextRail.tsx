import { LanguageSelect } from "../../localization/LanguageSelect";
import { t, useLocale } from "../../localization";
import { useRef, useState } from "react";
import {
  Command,
  HelpCircle,
  LogIn,
  LogOut,
  Menu,
  RotateCcw,
  Search,
  UserRound
} from "lucide-react";
import { Link } from "react-router-dom";

import { useAuth } from "../AuthContext";
import { useApplicationDistribution } from "../distribution";
import { useDismissiblePopover } from "./useDismissiblePopover";
import { NotificationCenter } from "../../components/NotificationCenter";
import type { NavigationDomain, NavigationItem } from "./navigation";
import type { SidebarPreference } from "./ShellNavigation";

export function ContextRail({
  currentDomain,
  currentItem,
  sidebarPreference,
  onOpenMobileNavigation,
  onOpenCommand,
  onResetSidebarPreference
}: {
  currentDomain?: NavigationDomain;
  currentItem: NavigationItem;
  sidebarPreference: SidebarPreference;
  onOpenMobileNavigation: () => void;
  onOpenCommand: (invoker: HTMLElement) => void;
  onResetSidebarPreference: () => void;
}) {
  useLocale();
  const auth = useAuth();
  const ScopeSwitcher = useApplicationDistribution().contextSwitcher;

  return (
    <header className="nexilume-context-rail">
      <div className="nexilume-context-rail__identity">
        <button className="nexilume-shell-icon-button nexilume-context-rail__menu" onClick={onOpenMobileNavigation} aria-label={t("Open navigation")} type="button">
          <Menu size={18} />
        </button>
        <span className="nexilume-context-rail__path-node" aria-hidden="true" />
        <div className="nexilume-context-rail__path">
          <span>{t(currentDomain?.label ?? "Network")}</span>
          <i>/</i>
          <strong>{t(currentItem.label)}</strong>
        </div>
      </div>

      <div className="nexilume-context-rail__tools">
        <LanguageSelect />
        {ScopeSwitcher ? <ScopeSwitcher /> : null}
        <button
          className="nexilume-command-trigger"
          onClick={(event) => onOpenCommand(event.currentTarget)}
          aria-label={t("Find anything")}
          type="button"
        >
          <Search size={16} />
          <span>{t("Find anything")}</span>
          <kbd><Command size={11} />K</kbd>
        </button>
        {auth.isAuthenticated ? <NotificationCenter /> : null}
        <AccountControl
          sidebarPreference={sidebarPreference}
          onResetSidebarPreference={onResetSidebarPreference}
        />
      </div>
    </header>
  );
}

function AccountControl({
  sidebarPreference,
  onResetSidebarPreference
}: {
  sidebarPreference: SidebarPreference;
  onResetSidebarPreference: () => void;
}) {
  useLocale();
  const auth = useAuth();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useDismissiblePopover(open, setOpen, rootRef, triggerRef);

  const identity = auth.user?.display_name || auth.user?.email || "User";
  const initial = identity.slice(0, 1).toUpperCase();

  if (!auth.isAuthenticated) {
    return (
      <button className="nexilume-sign-in" onClick={() => auth.requestLogin(t("Sign in to select a workspace and access private Nexilume AI capability."))} type="button">
        <LogIn size={16} />
        <span>{t("Sign in")}</span>
      </button>
    );
  }

  return (
    <div className="nexilume-account" ref={rootRef}>
      <button ref={triggerRef} className="nexilume-account__trigger" onClick={() => setOpen((value) => !value)} aria-label={t("Open account menu")} aria-haspopup="menu" aria-expanded={open} type="button">
        <span>{initial}</span>
      </button>
      {open ? (
        <div className="nexilume-account__menu" role="menu">
          <header>
            <span className="nexilume-account__avatar"><UserRound size={17} /></span>
            <div>
              <strong>{identity}</strong>
              <small>{auth.user?.email}</small>
            </div>
          </header>
          <Link to="/settings" onClick={() => setOpen(false)} role="menuitem">
            <HelpCircle size={16} />{t("Profile & organization settings")}</Link>
          <button onClick={() => { onResetSidebarPreference(); setOpen(false); }} disabled={sidebarPreference === "auto"} role="menuitem" type="button">
            <RotateCcw size={16} />{t("Use automatic sidebar")}</button>
          <button onClick={() => void auth.logout()} role="menuitem" type="button">
            <LogOut size={16} />{t("Sign out")}</button>
        </div>
      ) : null}
    </div>
  );
}
