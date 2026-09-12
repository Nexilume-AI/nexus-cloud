import type { ReactNode } from "react";

export function Card({
  title,
  description,
  action,
  children,
  className = ""
}: {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel w-full min-w-0 max-w-full overflow-hidden ${className}`}>
      {(title || description || action) && (
        <div className="flex items-start justify-between gap-4 border-b border-line/80 px-5 py-4">
          <div className="min-w-0">
            {title && <h2 className="text-base font-semibold tracking-[-0.01em] text-ink">{title}</h2>}
            {description && <p className="mt-1 max-w-3xl text-sm leading-5 text-muted">{description}</p>}
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      <div className="min-w-0 max-w-full p-5">{children}</div>
    </section>
  );
}

export function MetricCard({
  label,
  value,
  detail,
  tone = "neutral"
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "neutral" | "success" | "warn" | "danger";
}) {
  const dots = {
    neutral: "bg-accent",
    success: "bg-success",
    warn: "bg-warn",
    danger: "bg-danger"
  };

  return (
    <div className="panel relative overflow-hidden p-5">
      <div className="label flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${dots[tone]}`} aria-hidden="true" />
        {label}
      </div>
      <div className="mt-2 text-[1.7rem] font-semibold leading-8 tracking-[-0.03em] text-ink [font-variant-numeric:tabular-nums]">{value}</div>
      {detail && <div className="mt-2 text-sm leading-5 text-muted">{detail}</div>}
    </div>
  );
}
