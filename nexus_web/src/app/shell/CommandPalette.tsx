import { t, useLocale, getLocale } from "../../localization";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { ExternalLink, Search, X } from "lucide-react";

import type { NavigationItem } from "./navigation";

export type CommandSection = {
  id: string;
  label: string;
  verb: string;
  items: NavigationItem[];
};

export function CommandPalette({
  open,
  sections,
  onClose,
  onSelect
}: {
  open: boolean;
  sections: CommandSection[];
  onClose: () => void;
  onSelect: (item: NavigationItem) => void;
}) {
  useLocale();
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const filteredSections = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return sections
      .map((section) => ({
        ...section,
        items: section.items.filter((item) => !normalized || `${item.label} ${item.description} ${section.label} ${t(item.label)} ${t(item.description)} ${t(section.label)}`.toLowerCase().includes(normalized))
      }))
      .filter((section) => section.items.length > 0);
  }, [query, sections, getLocale()]);
  const flatItems = filteredSections.flatMap((section) => section.items);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
  }, [open]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    itemRefs.current[activeIndex]?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  if (!open) return null;

  function handleKeyDown(event: ReactKeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => Math.min(index + 1, Math.max(flatItems.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => Math.max(index - 1, 0));
    } else if (event.key === "Home") {
      event.preventDefault();
      setActiveIndex(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActiveIndex(Math.max(flatItems.length - 1, 0));
    } else if (event.key === "Enter" && flatItems[activeIndex]) {
      event.preventDefault();
      onSelect(flatItems[activeIndex]);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "Tab") {
      const focusable = dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]), input, [href], [tabindex]:not([tabindex="-1"])');
      if (!focusable?.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  let itemIndex = -1;
  return (
    <div className="nexilume-command-layer" role="dialog" aria-modal="true" aria-label={t("Quick navigation")} onKeyDown={handleKeyDown}>
      <button className="nexilume-command-layer__backdrop" onClick={onClose} aria-label={t("Close quick navigation")} type="button" />
      <div className="nexilume-command" ref={dialogRef}>
        <label className="nexilume-command__search">
          <Search size={18} />
          <span className="sr-only">{t("Find a Nexilume AI page")}</span>
          <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("Search capability and workflows")} />
          <button onClick={onClose} aria-label={t("Close")} type="button"><X size={17} /></button>
        </label>
        <div className="nexilume-command__results">
          {filteredSections.map((section) => (
            <section key={section.id}>
              <header><span>{t(section.label)}</span><small>{t(section.verb)}</small></header>
              <div>
                {section.items.map((item) => {
                  itemIndex += 1;
                  const index = itemIndex;
                  const Icon = item.icon;
                  return (
                    <button
                      ref={(node) => { itemRefs.current[index] = node; }}
                      key={item.to}
                      className={activeIndex === index ? "is-active" : ""}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => onSelect(item)}
                      type="button"
                    >
                      <span className="nexilume-command__node" aria-hidden="true"><Icon size={16} /></span>
                      <span><strong>{t(item.label)}</strong><small>{t(item.description)}</small></span>
                      {item.opensInWindow ? <ExternalLink size={14} aria-hidden="true" /> : null}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
          {flatItems.length === 0 ? <div className="nexilume-command__empty">{t("No matching Nexilume AI capability.")}</div> : null}
        </div>
      </div>
    </div>
  );
}
