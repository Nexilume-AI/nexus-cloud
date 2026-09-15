import { t, useLocale } from "../localization";
import {
  useEffect,
  useId,
  useRef,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { X } from "lucide-react";

export type NexilumeTabOption<T extends string> = {
  value: T;
  label: string;
  eyebrow?: string;
  count?: number;
};

export function NexilumeTabs<T extends string>({
  label,
  options,
  value,
  onChange,
  variant = "view",
  idBase,
}: {
  label: string;
  options: NexilumeTabOption<T>[];
  value: T;
  onChange: (value: T) => void;
  variant?: "stage" | "view" | "compact";
  idBase?: string;
}) {
  useLocale();
  const generatedId = useId();
  const id = idBase || generatedId;
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const activeIndex = Math.max(
    0,
    options.findIndex((option) => option.value === value),
  );

  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (index + 1) % options.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (index - 1 + options.length) % options.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = options.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    const option = options[nextIndex];
    onChange(option.value);
    refs.current[nextIndex]?.focus();
  }

  return (
    <div
      className={`nexilume-tabs nexilume-tabs--${variant}`}
      role="tablist"
      aria-label={label}
    >
      {options.map((option, index) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`${id}-tab-${option.value}`}
            aria-selected={selected}
            aria-controls={`${id}-panel-${option.value}`}
            tabIndex={selected ? 0 : -1}
            className={selected ? "is-active" : ""}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => moveFocus(event, index)}
          >
            {option.eyebrow && <span className="nexilume-tab__eyebrow">{t(option.eyebrow)}</span>}
            <span>{t(option.label)}</span>
            {typeof option.count === "number" && (
              <span className="nexilume-tab__count">{option.count}</span>
            )}
          </button>
        );
      })}
      <span
        className="nexilume-tabs__rail"
        aria-hidden="true"
        style={{
          width: `${100 / Math.max(options.length, 1)}%`,
          transform: `translateX(${activeIndex * 100}%)`,
        }}
      />
    </div>
  );
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function NexilumeDialog({
  open,
  title,
  eyebrow,
  description,
  children,
  footer,
  busy = false,
  initialFocusRef,
  onClose,
  size = "medium",
  variant = "dialog",
}: {
  open: boolean;
  title: string;
  eyebrow?: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  busy?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
  onClose: () => void;
  size?: "medium" | "large";
  variant?: "dialog" | "drawer";
}) {
  useLocale();
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  closeRef.current = onClose;
  busyRef.current = busy;

  useEffect(() => {
    if (!open) return;
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = window.requestAnimationFrame(() => {
      const target =
        initialFocusRef?.current ||
        (dialogRef.current?.querySelector(focusableSelector) as HTMLElement | null);
      target?.focus();
    });

    function onKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(focusableSelector),
      ).filter((element) => !element.hasAttribute("disabled") && element.getClientRects().length > 0);
      if (focusable.length === 0) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
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

    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      restoreFocusRef.current?.focus();
    };
  }, [initialFocusRef, open]);

  if (!open) return null;

  return (
    <div className={`nexilume-dialog-layer nexilume-dialog-layer--${variant}`} role="presentation">
      <button
        type="button"
        className="nexilume-dialog-backdrop"
        aria-label={t("Close dialog")}
        disabled={busy}
        onClick={onClose}
      />
      <div
        ref={dialogRef}
        className={`nexilume-dialog nexilume-dialog--${size} nexilume-dialog--${variant}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
      >
        <header className="nexilume-dialog__header">
          <div>
            {eyebrow && <p className="nexilume-dialog__eyebrow">{eyebrow}</p>}
            <h2 id={titleId}>{title}</h2>
            {description && <p id={descriptionId}>{description}</p>}
          </div>
          <button
            className="nexilume-dialog__close"
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label={t("Close dialog")}
          >
            <X size={18} />
          </button>
        </header>
        <div className="nexilume-dialog__body">{children}</div>
        {footer && <footer className="nexilume-dialog__footer">{footer}</footer>}
      </div>
    </div>
  );
}
