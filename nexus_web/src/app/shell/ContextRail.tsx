import { t, useLocale } from "../../localization";
import { useRef, useState } from "react";
import {
  Command,
  LogIn,
  LogOut,
  Menu,
  RotateCcw,
  Search,
  Settings,
  UserRound
} from "lucide-react";
import { Link } from "react-router-dom";

import { useAuth } from "../AuthContext";
import { useApplicationDistribution } from "../distribution";
import { useDismissiblePopover } from "./useDismissiblePopover";
import { AccountLanguageControl } from "./AccountLanguageControl";
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
        {ScopeSwitcher ? <div className="nexilume-context-rail__scope"><ScopeSwitcher /></div> : null}
        <button
          className="nexilume-command-trigger"
          onClick={(event) => onOpenCommand(event.currentTarget)}
          aria-label={t("Find anything")}
          title={t("Find anything")}
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
  const ScopeSwitcher = useApplicationDistribution().contextSwitcher;
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useDismissiblePopover(open, setOpen, rootRef, triggerRef);

  const identity = auth.user?.display_name || auth.user?.email || "User";
  const initial = identity.slice(0, 1).toUpperCase();

  return (
    <>
      {!auth.isAuthenticated ? (
        <button className="nexilume-sign-in" onClick={() => auth.requestLogin("Sign in to select a workspace and access private Nexilume AI capability.")} type="button">
          <LogIn size={16} />
          <span>{t("Sign in")}</span>
        </button>
      ) : null}
      <div className="nexilume-account" ref={rootRef}>
        <button ref={triggerRef} className="nexilume-account__trigger" onClick={() => setOpen((value) => !value)} aria-label={t("Open account menu")} aria-haspopup="dialog" aria-expanded={open} type="button">
          {auth.isAuthenticated ? <span>{initial}</span> : <UserRound size={17} aria-hidden="true" />}
        </button>
        {open ? (
          <div className="nexilume-account__menu" role="dialog" aria-label={t("Account menu")}>
            <header>
              <span className="nexilume-account__avatar" aria-hidden="true">{auth.isAuthenticated ? initial : <UserRound size={18} />}</span>
              <div>
                <strong title={auth.isAuthenticated ? identity : undefined}>{auth.isAuthenticated ? identity : t("Account")}</strong>
                {auth.isAuthenticated ? <small title={auth.user?.email}>{auth.user?.email}</small> : null}
              </div>
            </header>
            <div className="nexilume-account__preferences">
              <AccountLanguageControl />
              {auth.isAuthenticated && ScopeSwitcher ? (
                <div className="nexilume-account__context"><ScopeSwitcher presentation="account" /></div>
              ) : null}
            </div>
            {auth.isAuthenticated ? (
              <div className="nexilume-account__actions">
                <Link to="/settings" onClick={() => setOpen(false)}>
                  <Settings size={16} aria-hidden="true" />{t("Profile & organization settings")}</Link>
                {sidebarPreference !== "auto" ? (
                  <button onClick={() => { onResetSidebarPreference(); setOpen(false); }} type="button">
                    <RotateCcw size={16} aria-hidden="true" />{t("Use automatic sidebar")}</button>
                ) : null}
                <button onClick={() => { setOpen(false); void auth.logout(); }} type="button">
                  <LogOut size={16} aria-hidden="true" />{t("Sign out")}</button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  );
}
