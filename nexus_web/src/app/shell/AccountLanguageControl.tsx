import { Check, ChevronRight, Languages } from "lucide-react";
import { useRef, useState } from "react";
import { setLocale, t, useLocale, type Locale } from "../../localization";
import { useDismissiblePopover } from "./useDismissiblePopover";

const choices: { locale: Locale; name: string }[] = [
  { locale: "en-US", name: "English" },
  { locale: "zh-CN", name: "简体中文" },
];

export function AccountLanguageControl() {
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  useDismissiblePopover(open, setOpen, rootRef, triggerRef);

  function choose(next: Locale) {
    setLocale(next);
    setOpen(false);
    triggerRef.current?.focus();
  }

  return (
    <div className="nexilume-account__language" ref={rootRef}>
      <button className="nexilume-account__row" ref={triggerRef} type="button" aria-label="语言 / Language"
        aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <Languages size={16} aria-hidden="true" />
        <span className="nexilume-account__row-copy">
          <strong>{t("Language")}</strong>
          <small>{choices.find(choice => choice.locale === locale)?.name}</small>
        </span>
        <ChevronRight className="nexilume-account__row-chevron" size={14} aria-hidden="true" />
      </button>
      {open ? (
        <div className="nexilume-account__language-options" role="menu" aria-label={t("Choose language")}
          onKeyDown={event => {
            const options = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>("button"));
            const index = options.indexOf(document.activeElement as HTMLButtonElement);
            let next: number;
            if (event.key === "ArrowDown" || event.key === "ArrowRight") next = (index + 1) % options.length;
            else if (event.key === "ArrowUp" || event.key === "ArrowLeft") next = (index + options.length - 1) % options.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = options.length - 1;
            else return;
            event.preventDefault();
            options[next]?.focus();
          }}>
          {choices.map(choice => (
            <button key={choice.locale} type="button" role="menuitemradio" aria-checked={locale === choice.locale}
              autoFocus={locale === choice.locale} onClick={() => choose(choice.locale)}>
              <span>{choice.name}</span>
              {locale === choice.locale ? <Check size={14} aria-hidden="true" /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
