import { Loader2, Send } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import "./agent-message-composer.css";
import "./agent-display-workspace.css";

export type AgentDisplayMobilePanel = {
  id: string;
  label: string;
  icon: LucideIcon;
  attention?: boolean;
};

export type AgentDisplayMode = {
  id: string;
  label: string;
  icon?: LucideIcon;
  attention?: boolean;
  attentionLabel?: string;
};

export function AgentDisplaySurface({
  children,
  standalone = false,
}: {
  children: ReactNode;
  standalone?: boolean;
}) {
  return (
    <div
      className={`${standalone ? "h-[100dvh]" : "min-h-[calc(100dvh-8rem)] rounded-md"} flex flex-col overflow-hidden bg-[#f8f8f7] text-[#34322d]`}
    >
      {children}
    </div>
  );
}

export function AgentDisplayHeaderFrame({ children }: { children: ReactNode }) {
  return (
    <header className="sticky top-0 z-20 shrink-0 border-b border-black/10 bg-[#f8f8f7]/95 backdrop-blur">
      {children}
    </header>
  );
}

export function AgentDisplayMobileTabs({
  panels,
  active,
  onChange,
}: {
  panels: AgentDisplayMobilePanel[];
  active: string;
  onChange: (panel: string) => void;
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % panels.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + panels.length) % panels.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = panels.length - 1;
    else return;
    event.preventDefault();
    onChange(panels[next].id);
    document.getElementById(`agent-display-mobile-${panels[next].id}`)?.focus();
  }

  return (
    <div
      className={`grid h-12 shrink-0 border-b border-black/10 bg-white px-2 lg:hidden`}
      style={{ gridTemplateColumns: `repeat(${panels.length}, minmax(0, 1fr))` }}
      role="tablist"
      aria-label="Agent run panels"
    >
      {panels.map(({ id, label, icon: Icon, attention }, index) => (
        <button
          id={`agent-display-mobile-${id}`}
          key={id}
          className={`relative inline-flex min-h-11 items-center justify-center gap-2 text-sm font-semibold transition ${
            active === id ? "text-[#1a1a19]" : "text-[#858481] hover:text-[#535350]"
          }`}
          onClick={() => onChange(id)}
          onKeyDown={(event) => handleKeyDown(event, index)}
          role="tab"
          aria-selected={active === id}
          tabIndex={active === id ? 0 : -1}
          type="button"
        >
          <Icon size={15} />
          {label}
          {attention ? (
            <span className="h-2 w-2 rounded-full bg-[#bdfc73] ring-1 ring-[#386a22]" aria-label="Input required" />
          ) : null}
          {active === id ? <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-[#1a1a19]" /> : null}
        </button>
      ))}
    </div>
  );
}

export function AgentDisplayWorkspace({
  activeMobilePanel,
  plan,
  live,
  context,
}: {
  activeMobilePanel: string;
  plan: ReactNode;
  live: ReactNode;
  context: ReactNode;
}) {
  const container = useRef<HTMLElement>(null);
  const [contextWidth, setContextWidth] = useState(() => {
    try { const value = Number(localStorage.getItem("nexus.run-context.width")); return value >= 280 && value <= 900 ? value : 360; } catch { return 360; }
  });
  const [historyWidth, setHistoryWidth] = useState(() => {
    try { const value = Number(localStorage.getItem("nexus.run-history.width")); return value >= 240 && value <= 360 ? value : 280; } catch { return 280; }
  });
  function resize(value: number) {
    const width = Math.round(Math.max(280, Math.min(900, window.innerWidth - 660, value)));
    setContextWidth(width);
    try { localStorage.setItem("nexus.run-context.width", String(width)); } catch { /* Layout preference is optional. */ }
  }
  function resizeHistory(value: number) {
    const width = Math.round(Math.max(240, Math.min(360, window.innerWidth - contextWidth - 420, value)));
    setHistoryWidth(width);
    try { localStorage.setItem("nexus.run-history.width", String(width)); } catch { /* Layout preference is optional. */ }
  }
  return (
    <main ref={container} style={{ "--run-context-width": `${contextWidth}px`, "--run-history-width": `${historyWidth}px` } as CSSProperties} className="agent-display-workspace grid min-h-0 min-w-0 flex-1 grid-cols-[minmax(0,1fr)] gap-3 overflow-hidden px-2 pb-1.5 pt-1.5 lg:px-3 lg:pb-2.5 lg:pt-2.5 xl:px-5">
      <div className={`${activeMobilePanel === "plan" ? "block" : "hidden"} relative min-h-0 min-w-0 lg:block`}>
        {plan}
        <div role="separator" aria-label="Resize Run history" aria-orientation="vertical" aria-valuemin={240} aria-valuemax={360} aria-valuenow={historyWidth} tabIndex={0}
          title="Drag to resize. Arrow keys adjust width; Home resets it."
          className="run-history-resizer absolute -right-3 top-0 z-10 hidden h-full w-3 cursor-col-resize touch-none lg:block"
          onDoubleClick={() => resizeHistory(280)} onKeyDown={event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault(); resizeHistory(event.key === "Home" ? 280 : event.key === "End" ? 360 : historyWidth + (event.key === "ArrowLeft" ? -24 : 24));
          }} onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId) && container.current) resizeHistory(event.clientX - container.current.getBoundingClientRect().left - 20); }}
          onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
      </div>
      <div className={`${activeMobilePanel === "live" ? "block" : "hidden"} min-h-0 min-w-0 lg:block`}>{live}</div>
      <div className={`${activeMobilePanel === "files" ? "block" : "hidden"} relative min-h-0 min-w-0 lg:block`}>
        <div role="separator" aria-label="Resize Run context" aria-orientation="vertical" aria-valuemin={280} aria-valuemax={900} aria-valuenow={contextWidth} tabIndex={0}
          title="Drag to resize. Arrow keys adjust width; Home resets it."
          className="run-context-resizer absolute -left-3 top-0 z-10 hidden h-full w-3 cursor-col-resize touch-none lg:block"
          onDoubleClick={() => resize(360)} onKeyDown={event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault(); resize(event.key === "Home" ? 360 : event.key === "End" ? 900 : contextWidth + (event.key === "ArrowLeft" ? 24 : -24));
          }} onPointerDown={event => { if (event.button !== 0) return; event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerMove={event => { if (event.currentTarget.hasPointerCapture(event.pointerId) && container.current) resize(container.current.getBoundingClientRect().right - event.clientX - 20); }}
          onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
        {context}
      </div>
    </main>
  );
}

export function AgentDisplayModeTabs({
  modes,
  active,
  onChange,
  label,
  touchTargets = false,
}: {
  modes: AgentDisplayMode[];
  active: string;
  onChange: (mode: string) => void;
  label: string;
  touchTargets?: boolean;
}) {
  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % modes.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + modes.length) % modes.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = modes.length - 1;
    else return;
    event.preventDefault();
    onChange(modes[next].id);
    document.getElementById(`agent-display-mode-${modes[next].id}`)?.focus();
  }

  return (
    <div className="flex items-center gap-2 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" role="tablist" aria-label={label}>
      {modes.map(({ id, label: modeLabel, icon: Icon, attention, attentionLabel }, index) => (
        <button
          id={`agent-display-mode-${id}`}
          key={id}
          type="button"
          role="tab"
          aria-selected={active === id}
          aria-label={attentionLabel ? modeLabel : undefined}
          title={attention && attentionLabel ? attentionLabel : undefined}
          tabIndex={active === id ? 0 : -1}
          onClick={() => onChange(id)}
          onKeyDown={(event) => handleKeyDown(event, index)}
          className={`inline-flex ${touchTargets ? "h-11" : "h-9"} shrink-0 items-center justify-center gap-2 rounded-md px-3 text-sm font-medium transition ${
            active === id
              ? "bg-[#1a1a19] text-white"
              : "border border-black/10 bg-white text-[#535350] hover:bg-[#f8f8f7]"
          }`}
        >
          {Icon ? <Icon size={14} /> : null}
          {modeLabel}
          {attention ? <span className="h-2 w-2 rounded-full bg-[#bdfc73] ring-1 ring-[#386a22]" aria-label={attentionLabel || "Input required"} /> : null}
        </button>
      ))}
    </div>
  );
}

export function AgentDisplayLiveHeader({
  icon: Icon,
  title,
  label,
  message,
  trailing,
}: {
  icon: LucideIcon;
  title: string;
  label: string;
  message: string;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-black/10 px-4 py-3">
      <div className="flex min-w-0 items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-[#37352f0a]">
          <Icon className="text-[#535350]" size={22} />
        </div>
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold text-[#34322d]" title={title}>{title}</h2>
          <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-[#858481]">
            <span className="shrink-0">Viewing</span>
            <span className="min-w-0 truncate rounded-full border border-black/10 bg-[#37352f0a] px-2 py-0.5 font-mono text-[#535350]" title={label}>{label}</span>
            <span className="truncate" title={message}>{message}</span>
          </div>
        </div>
      </div>
      {trailing ? <div className="hidden shrink-0 items-center gap-2 sm:flex">{trailing}</div> : null}
    </div>
  );
}

export function AgentDisplayComputerFrame({
  title,
  status = "active",
  timestamp,
  tone = "dark",
  action,
  children,
}: {
  title: string;
  status?: string;
  timestamp?: string;
  tone?: "dark" | "light";
  action?: ReactNode;
  children: ReactNode;
}) {
  const dark = tone === "dark";
  return (
    <div className={`flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border shadow-sm ${dark ? "border-black/20 bg-[#111111]" : "border-black/10 bg-white"}`}>
      <div className={`flex min-h-11 shrink-0 items-center justify-between gap-3 border-b px-3 ${dark ? "border-white/10" : "border-black/10 bg-[#f8f8f7]"}`}>
        <div className={`flex min-w-0 items-center gap-2 text-xs font-semibold uppercase ${dark ? "text-white/60" : "text-[#535350]"}`}>
          <span className={`h-2 w-2 rounded-full ${["active", "running", "completed"].includes(status) ? "bg-emerald-400" : status === "failed" ? "bg-red-400" : "bg-amber-400"}`} />
          <span className="truncate">{title}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {timestamp ? <span className={`text-xs ${dark ? "text-white/35" : "text-[#858481]"}`}>{timestamp}</span> : null}
          {action}
        </div>
      </div>
      {children}
    </div>
  );
}

export function AgentDisplayComposer({
  prompt,
  choices = [],
  value,
  onChange,
  onSubmit,
  disabled = false,
  busy = false,
  editable,
  maxCharacters,
  placeholder,
  error,
  label = "Reply to this Agent",
  allowEmpty = false,
  attachments,
  attachmentActionsDisabled = false,
  deviceActions,
  deliveryActions,
}: {
  prompt?: string;
  choices?: Array<{ value: string; label: string }>;
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  disabled?: boolean;
  busy?: boolean;
  // Private Display can keep a draft editable while delivery is blocked.
  // Other consumers retain their existing disabled/busy input behaviour.
  editable?: boolean;
  maxCharacters?: number;
  placeholder: string;
  error?: string;
  label?: string;
  /** Used only when a verified attachment is itself the message content. */
  allowEmpty?: boolean;
  /** Keep upload state mounted above the text; its actions target the footer. */
  attachments?: (toolbar: HTMLDivElement | null) => ReactNode;
  attachmentActionsDisabled?: boolean;
  /** Device recovery stays available even when attachment edits are locked. */
  deviceActions?: ReactNode;
  /** Queue/steer belongs to the message toolbar, not a separate status row. */
  deliveryActions?: ReactNode;
}) {
  const [toolbar, setToolbar] = useState<HTMLDivElement | null>(null);
  const characters = Array.from(value.trim()).length;
  const overLimit = maxCharacters !== undefined && characters > maxCharacters;
  const lengthError = overLimit ? `Keep this message within ${maxCharacters!.toLocaleString("en-US")} characters. Your full draft is kept.` : "";
  function submit(event?: FormEvent) {
    event?.preventDefault();
    const next = value.trim();
    if (!disabled && !busy && !overLimit && (next || allowEmpty)) onSubmit(next);
  }

  const input = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const element = input.current;
    if (!element) return;
    function resize() {
      if (!element) return;
      // Placeholder wrapping must not resize an empty composer when its mode
      // changes (continue / queue / answer), especially on narrow screens.
      if (!element.value) {
        element.style.height = "44px";
        element.style.overflowY = "hidden";
        return;
      }
      element.style.height = "auto";
      const height = Math.min(element.scrollHeight, Math.max(88, Math.min(192, window.innerHeight * 0.28)));
      element.style.height = `${Math.max(44, height)}px`;
      element.style.overflowY = element.scrollHeight > height ? "auto" : "hidden";
    }
    resize();
    let width = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth !== width) { width = element.clientWidth; resize(); }
    });
    observer.observe(element);
    window.addEventListener("resize", resize);
    return () => { observer.disconnect(); window.removeEventListener("resize", resize); };
  }, [value]);

  return (
    <form aria-label="Message composer" className="agent-message-composer min-w-0 rounded-xl border border-black/15 bg-white p-2 focus-within:border-[#52783b]" onSubmit={submit}>
      {attachments ? <div className="max-h-40 min-w-0 overflow-y-auto overscroll-contain">{attachments(toolbar)}</div> : null}
      {prompt ? <p className="px-2 pb-2 text-sm font-medium leading-6 text-[#34322d]">{prompt}</p> : null}
      {choices.length ? (
        <div className="mb-2 flex flex-wrap gap-2 border-b border-black/10 px-2 pb-2">
          {choices.map((choice) => (
            <button key={choice.value} type="button" disabled={disabled || busy} onClick={() => onSubmit(choice.value)} className="min-h-11 rounded-md border border-black/20 px-3 text-sm font-medium text-[#34322d] hover:bg-[#f8f8f7] disabled:opacity-50">
              {choice.label}
            </button>
          ))}
        </div>
      ) : null}
      <div className={attachments ? "flex flex-col gap-2" : "flex items-end gap-2"}>
        <textarea
          ref={input}
          aria-label={label}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
          rows={1}
          disabled={(editable === undefined ? disabled || busy : !editable) || choices.length > 0}
          aria-invalid={overLimit || undefined}
          placeholder={placeholder}
          className={`min-h-11 min-w-0 ${attachments ? "w-full flex-none" : "flex-1"} resize-none bg-transparent px-2 py-2 text-sm text-[#34322d] outline-none placeholder:text-[#858481]`}
        />
        <div className="flex min-w-0 items-end gap-1.5" aria-label="Message actions">
        {attachments ? <fieldset disabled={attachmentActionsDisabled} className="min-w-0 flex-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"><div ref={setToolbar} /></fieldset> : null}
        {deliveryActions}
        {deviceActions}
        <button type="submit" disabled={disabled || busy || overLimit || choices.length > 0 || (!value.trim() && !allowEmpty)} className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg bg-[#1a1a19] text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40" aria-label="Send message">
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
        </button>
        </div>
      </div>
      {maxCharacters !== undefined && characters >= maxCharacters * 0.9 ? <p className="px-2 pt-1 text-right text-xs text-[#535350]" aria-label="Message length">{characters.toLocaleString("en-US")} / {maxCharacters.toLocaleString("en-US")}</p> : null}
      {lengthError || error ? <p role="alert" className="px-2 pt-2 text-xs text-[#9b2c2c]">{lengthError || error}</p> : null}
    </form>
  );
}

export function AgentDisplayPanel({
  children,
  className = "",
  as: Component = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "aside";
}) {
  return (
    <Component className={`flex h-full min-h-0 min-w-0 flex-col overflow-hidden rounded-md border border-black/10 bg-white shadow-sm ${className}`}>
      {children}
    </Component>
  );
}
