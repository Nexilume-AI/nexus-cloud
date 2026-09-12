type MarkTone = "lume" | "ink";

export function NexilumeMark({ className = "h-9 w-9", tone = "lume" }: { className?: string; tone?: MarkTone }) {
  return (
    <img
      className={className}
      src={tone === "ink" ? "/brand/nexilume-mark-ink.svg" : "/brand/nexilume-mark-primary.svg"}
      alt=""
      aria-hidden="true"
    />
  );
}

export function NexilumeBrand({
  className = "",
  dark = false,
  subtitle = "AI operations workspace"
}: {
  className?: string;
  dark?: boolean;
  subtitle?: string;
}) {
  return (
    <span className={`flex min-w-0 items-center gap-3 ${className}`} aria-label="Nexilume AI">
      <NexilumeMark tone={dark ? "lume" : "ink"} />
      <span className="min-w-0">
        <span className={`block truncate text-sm font-bold tracking-[0.08em] ${dark ? "text-white" : "text-ink"}`}>NEXILUME AI</span>
        {subtitle ? <span className={`block truncate text-xs ${dark ? "text-[#9eb0a8]" : "text-muted"}`}>{subtitle}</span> : null}
      </span>
    </span>
  );
}
