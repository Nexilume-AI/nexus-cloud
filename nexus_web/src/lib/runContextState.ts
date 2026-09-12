import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";

export type ContextPanel = "plan" | "files" | "workspace";
type MobilePanel = "history" | "live" | "context";
type Point = { x: number; y: number };
type View = { panel: ContextPanel; mobile: MobilePanel; scroll: Record<string, Point>; seen: Record<string, string> };
const storage = "nexus.run-context.v1";
const panels = ["plan", "files", "workspace"];
const defaults = (): View => ({ panel: "plan", mobile: "live", scroll: {}, seen: {} });
export const runContextKey = (caller: unknown, organization: unknown, project: unknown, run: string) => JSON.stringify([caller ?? "", organization ?? "", project ?? "", run]);
export function contextRevision(value: unknown): string {
  const text = JSON.stringify(value) || "";
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  return (hash >>> 0).toString(16);
}
function entries(): Record<string, { at: number; value: View }> {
  try { const value = JSON.parse(sessionStorage.getItem(storage) || "{}"); return value && typeof value === "object" && !Array.isArray(value) ? value : {}; } catch { return {}; }
}
export function readContextView(key: string): View {
  const row = entries()[key]; const empty = defaults();
  if (!row || !Number.isFinite(row.at) || row.at > Date.now() || Date.now() - row.at > 86400_000 || !row.value) return empty;
  const value = row.value;
  const coordinate = (n: unknown) => typeof n === "number" && Number.isFinite(n) && n >= 0 && n <= 10_000_000;
  const storedPanel = String(value.panel || "");
  const panel = storedPanel === "computer" ? "workspace" : panels.includes(storedPanel) ? storedPanel as ContextPanel : "plan";
  const scroll = Object.fromEntries(Object.entries(value.scroll || {}).map(([slot, point]): [string, Point] => [slot.replace(/^computer:/, "workspace:"), point])
    .filter(([slot, point]) => slot.length <= 240 && point && coordinate((point as Point).x) && coordinate((point as Point).y)).slice(-40));
  const seen = Object.fromEntries(Object.entries(value.seen || {}).map(([name, stamp]): [string, string] => [name === "computer" ? "workspace" : name, stamp])
    .filter(([name, stamp]) => panels.includes(name) && typeof stamp === "string" && /^[a-f0-9]{1,8}$/.test(stamp)));
  return { panel, mobile: ["history", "live", "context"].includes(value.mobile) ? value.mobile : "live", scroll, seen };
}
export function patchContextView(key: string, patch: Partial<View>) {
  const value = { ...readContextView(key), ...patch };
  try {
    const rows = entries(); rows[key] = { at: Date.now(), value };
    sessionStorage.setItem(storage, JSON.stringify(Object.fromEntries(Object.entries(rows)
      .filter(([, row]) => row && Number.isFinite(row.at) && Date.now() - row.at < 86400_000)
      .sort((a, b) => b[1].at - a[1].at).slice(0, 50))));
  } catch { /* Optional browsing preferences: never store content or credentials. */ }
  return value;
}
export function useContextView(key: string) {
  const [entry, setEntry] = useState(() => ({ key, value: readContextView(key) }));
  const value = entry.key === key ? entry.value : readContextView(key);
  if (entry.key !== key) setEntry({ key, value });
  function update(patch: Partial<View>) { setEntry({ key, value: patchContextView(key, patch) }); }
  return { panel: value.panel, mobile: value.mobile,
    setPanel: (panel: ContextPanel) => update({ panel }), setMobile: (mobile: MobilePanel) => update({ mobile }) };
}

// A new snapshot is a hint, never a command to change the reader's panel/anchor.
export function useContextUpdates(key: string, revisions: Partial<Record<ContextPanel, string>>) {
  const signature = JSON.stringify(revisions);
  const current = useMemo(() => JSON.parse(signature) as typeof revisions, [signature]);
  const [seen, setSeen] = useState(() => readContextView(key).seen);
  useEffect(() => {
    const next = { ...seen }; let changed = false;
    for (const [panel, stamp] of Object.entries(current)) if (!(panel in next)) { next[panel] = stamp; changed = true; }
    if (changed) { setSeen(next); patchContextView(key, { seen: next }); }
  }, [key, current, seen]);
  function acknowledge(panel: ContextPanel) {
    if (!current[panel]) return;
    const next = { ...seen, [panel]: current[panel] }; setSeen(next); patchContextView(key, { seen: next });
  }
  return { acknowledge, unread: (panel: ContextPanel) => Boolean(seen[panel] && current[panel] && seen[panel] !== current[panel]) };
}

// Restore only when content exists. Loading/hidden containers must not overwrite
// saved positions with zero; a user scroll always takes precedence over restore.
export function useContextScroll(key: string, panel: string) {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const ref = useCallback((element: HTMLDivElement | null) => setNode(element), []);
  useLayoutEffect(() => {
    if (!node) return;
    const positions = readContextView(key).scroll;
    const restored = new WeakMap<HTMLElement, string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let frame = 0;
    const dirty: Record<string, Point> = {};
    const slot = (element: HTMLElement) => `${panel}:${element.dataset.contextScroll || "panel"}`;
    const owned = (element: HTMLElement) => element.closest("[data-context-scroll-root]") === node;
    function flush() {
      clearTimeout(timer);
      if (!Object.keys(dirty).length) return;
      const merged = { ...readContextView(key).scroll, ...dirty };
      patchContextView(key, { scroll: Object.fromEntries(Object.entries(merged).slice(-40)) });
      for (const name of Object.keys(dirty)) delete dirty[name];
    }
    function restore() {
      for (const element of [node!, ...node!.querySelectorAll<HTMLElement>("[data-context-scroll]")]) {
        if (!owned(element) || !element.clientHeight || restored.get(element) === slot(element)) continue;
        const point = positions[slot(element)] || { x: 0, y: 0 };
        if (element.scrollHeight - element.clientHeight + 1 < point.y || element.scrollWidth - element.clientWidth + 1 < point.x) continue;
        restored.set(element, slot(element)); element.scrollTop = point.y; element.scrollLeft = point.x;
      }
    }
    function schedule() { cancelAnimationFrame(frame); frame = requestAnimationFrame(restore); }
    function onScroll(event: Event) {
      const element = event.target;
      if (!(element instanceof HTMLElement) || !owned(element) || !element.clientHeight || restored.get(element) !== slot(element)) return;
      dirty[slot(element)] = { x: element.scrollLeft, y: element.scrollTop };
      positions[slot(element)] = dirty[slot(element)];
      clearTimeout(timer); timer = setTimeout(flush, 150);
    }
    function userIntent(event: Event) {
      let element = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-context-scroll]") : null;
      if (!element && event.target instanceof Node && node!.contains(event.target)) element = node;
      if (element && owned(element)) restored.set(element, slot(element));
    }
    node.addEventListener("scroll", onScroll, true);
    for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) node.addEventListener(type, userIntent, { passive: true });
    window.addEventListener("pagehide", flush);
    const mutation = new MutationObserver(schedule); mutation.observe(node, { subtree: true, childList: true, attributes: true });
    const resize = new ResizeObserver(schedule); resize.observe(node);
    restore();
    return () => {
      flush(); cancelAnimationFrame(frame); mutation.disconnect(); resize.disconnect();
      node.removeEventListener("scroll", onScroll, true); window.removeEventListener("pagehide", flush);
      for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) node.removeEventListener(type, userIntent);
    };
  }, [node, key, panel]);
  return ref;
}
