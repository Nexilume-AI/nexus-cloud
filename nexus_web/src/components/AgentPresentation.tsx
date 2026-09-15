import { t, useLocale } from "../localization";
import { useEffect, useId, useRef } from "react";

import { Copy, X } from "lucide-react";
import { toast } from "sonner";

export function AgentModal({
  title,
  description,
  children,
  onClose,
  maxWidth = "max-w-3xl",
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  onClose: () => void;
  maxWidth?: string;
}) {
  useLocale();
  const titleId = useId();
  const modalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !modalRef.current) return;
      const focusable = Array.from(
        modalRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
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
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/45 p-3 sm:p-4">
      <div
        ref={modalRef}
        className={`max-h-[90vh] w-full ${maxWidth} overflow-hidden rounded-lg border border-line bg-white shadow-2xl`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <div id={titleId} className="text-lg font-semibold text-ink">
              {title}
            </div>
            {description && (
              <div className="mt-1 break-words text-sm text-muted">
                {description}
              </div>
            )}
          </div>
          <button
            className="btn h-9 w-9 shrink-0 p-0"
            onClick={onClose}
            type="button"
            aria-label={t("Close dialog")}
          >
            <X size={16} />
          </button>
        </div>
        <div className="max-h-[calc(90vh-73px)] overflow-auto p-5">
          {children}
        </div>
      </div>
    </div>
  );
}

export function TrustPill({ icon, label }: { icon: React.ReactNode; label: string }) {
  useLocale();
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-line bg-slate-50 px-2 py-1 text-xs font-medium text-slate-700">
      {icon}
      {label}
    </span>
  );
}

export function KeyResultCard({
  title,
  value,
  onDismiss,
}: {
  title: string;
  value: string;
  onDismiss: () => void;
}) {
  useLocale();
  return (
    <div className="rounded-md border border-line p-4">
      <div className="mb-3 font-medium text-ink">{title}</div>
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
        <code className="min-w-0 flex-1 break-all rounded-md border border-line bg-slate-950 px-3 py-2 font-mono text-sm text-white">
          {value}
        </code>
        <button className="btn w-fit" onClick={() => void copy(value)}>
          <Copy size={16} />{t("Copy")}</button>
        <button className="btn w-fit" onClick={onDismiss}>{t("Dismiss")}</button>
      </div>
    </div>
  );
}

export function humanizeToken(value: string) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase();
  const known: Record<string, string> = {
    needs_setup: "Setup required",
    not_deployed: "Not deployed",
    per_call: "Per call",
    per_request: "Per request",
    tenant: "Organization",
    workspace_wide: "All projects",
  };
  if (known[normalized]) return known[normalized];
  return (
    normalized
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase()) || "Unknown"
  );
}

export function workspaceScopeLabel(scope: string) {
  const labels: Record<string, string> = {
    "connection.list": "List Computer connections",
    "connection.create": "Create SSH connections",
    "connection.update": "Update SSH connections",
    "connection.delete": "Delete SSH connections",
    "connection.test": "Test SSH connections",
    "connection.bind": "Bind a Computer",
    "files.list": "List Workspace files",
    "files.read": "Read Workspace files",
    "files.write": "Write Workspace files",
    "command.execute": "Execute terminal commands",
    "browser.control": "Control an isolated browser",
  };
  return labels[scope] ?? humanizeToken(scope);
}

export async function copy(value: string) {
  if (!value) return;
  await navigator.clipboard.writeText(value);
  toast.success(t("Copied"));
}

export function setOptionalSearchParam(
  params: URLSearchParams,
  key: string,
  value: string,
) {
  if (value) params.set(key, value);
  else params.delete(key);
}
import { formatNumber } from "../lib/format";
export function StatusStripItem({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "success" | "warn";
}) {
  useLocale();
  const valueTone =
    tone === "success"
      ? "text-success"
      : tone === "warn"
        ? "text-warn"
        : "text-ink";
  return (
    <div className="agent-inventory-status__item">
      <span>{label}</span>
      <strong className={valueTone}>{formatNumber(value)}</strong>
    </div>
  );
}

export function AgentInventoryFact({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  useLocale();
  return (
    <div>
      <dt>{label}</dt>
      <dd title={value}>{value}</dd>
    </div>
  );
}
