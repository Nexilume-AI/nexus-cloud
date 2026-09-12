import { ChevronDown, Monitor, Smartphone } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { ComposerOverlay } from "./PrivateDisplayComposerTools";

type DeviceSummary = {
  requirement: string; attached: boolean; ready: boolean; device_name?: string;
  message?: string; platform?: string; browser_name?: string;
  browser_available?: boolean | null; device_status?: string;
};

export function ComposerDeviceMenu({ computer, mobile, onComputer, onMobile }: {
  computer?: DeviceSummary; mobile?: DeviceSummary;
  onComputer: () => void; onMobile: () => void;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const devices = [
    { name: "Computer", data: computer, icon: Monitor, action: onComputer },
    { name: "Mobile", data: mobile, icon: Smartphone, action: onMobile },
  ].filter(item => item.data && item.data.requirement !== "disabled");
  const attention = devices.some(item => !item.data!.ready);
  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus());
    function outside(event: PointerEvent) {
      if (!menu.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") { event.preventDefault(); setOpen(false); button.current?.focus(); }
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  if (!devices.length) return null;
  return <div ref={anchor} className="shrink-0">
    <button ref={button} type="button" aria-label="Devices" aria-haspopup="menu" aria-controls={open ? id : undefined} aria-expanded={open}
      title={attention ? "Devices · Needs attention" : "Devices · Ready"}
      onClick={() => setOpen(value => !value)} onKeyDown={event => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); setOpen(true); } }}
      className={`relative inline-flex h-11 min-w-11 items-center justify-center gap-1 rounded-lg px-2 text-xs hover:bg-black/5 ${attention ? "text-amber-800" : "text-[#535350]"}`}>
      <Monitor size={16} /><span className="hidden xl:inline">Devices</span><ChevronDown size={12} />
      {attention ? <><span aria-hidden="true" className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-amber-600" /><span className="sr-only">Needs attention</span></> : null}
    </button>
    {open ? <ComposerOverlay anchor={anchor}><div ref={menu} id={id} role="menu" aria-label="Attached devices" className="max-h-[inherit] overflow-auto rounded-xl border border-black/15 bg-white p-2 shadow-lg"
      onBlur={event => { if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== button.current) setOpen(false); }}
      onKeyDown={event => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')];
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
      }}>
      {devices.map(({ name, data, icon: Icon, action }) => <button key={name} type="button" role="menuitem" aria-label={`${data!.attached ? "Change" : "Attach"} ${name}`}
        onClick={() => { setOpen(false); button.current?.focus(); action(); }} className="flex min-h-11 w-full items-start gap-3 rounded-lg p-2 text-left hover:bg-black/5">
        <Icon size={17} className="mt-1 shrink-0" />
        <span className="grid min-w-0 flex-1 gap-1">
          <span className="flex items-center justify-between gap-2 text-sm font-medium"><span>{name}</span><span className="text-xs">{data!.attached ? "Change" : "Attach"}</span></span>
          <span className="truncate text-xs text-[#535350]" title={data!.device_name}>{data!.device_name || "Not attached"}{data!.platform ? ` · ${data!.platform}` : ""}</span>
          <span className={`text-xs ${data!.ready ? "text-[#535350]" : "text-amber-800"}`}>{data!.attached && data!.ready ? "Connected" : data!.message || (data!.attached ? "Unavailable · Review connection or permissions" : data!.requirement === "optional" ? "Optional · Not attached" : "Attach and approve the requested permissions")}{data!.device_status ? ` · ${data!.device_status}` : ""}{data!.browser_name ? ` · ${data!.browser_name}` : data!.browser_available === false ? " · Browser unavailable" : ""}</span>
        </span>
      </button>)}
    </div></ComposerOverlay> : null}
  </div>;
}
