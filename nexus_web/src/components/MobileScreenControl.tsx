import { useEffect, useRef, useState, type PointerEvent } from "react";
import { t, useLocale } from "../localization";
import type { MobileDevice } from "../lib/types";
import { containedScreenPoint, screenGesture, type ScreenPoint } from "../lib/mobileScreenControl";

type ScreenCommand = { action: "tap_coordinates" | "swipe" | "long_press"; arguments: Record<string, unknown> };

export function MobileScreenControl({ device, url, displayedFrameId, busy, onCommand }: {
  device: MobileDevice; url: string; displayedFrameId: string; busy: boolean; onCommand: (command: ScreenCommand) => void;
}) {
  useLocale();
  const [enabled, setEnabled] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const [loaded, setLoaded] = useState(false);
  const [gestureActive, setGestureActive] = useState(false);
  const image = useRef<HTMLImageElement>(null);
  const gesture = useRef<{ id: number; point: ScreenPoint; clientX: number; clientY: number; time: number; frame: string; url: string } | null>(null);
  const frame = device.screen_frame;
  const current = Boolean(frame?.controllable && frame.id === displayedFrameId && Date.parse(frame.expires_at) > clock);
  const canControl = current && loaded && !busy && device.lifecycle_status === "online";
  const available = device.available_actions ?? [];
  const canTap = available.includes("tap_coordinates");
  const canSwipe = available.includes("swipe");
  const canLongPress = available.includes("long_press");
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => { setLoaded(Boolean(image.current?.complete && image.current.naturalWidth)); gesture.current = null; setGestureActive(false); }, [url]);
  useEffect(() => { gesture.current = null; setGestureActive(false); }, [frame?.id, enabled, busy]);
  useEffect(() => { if (!current) { gesture.current = null; setGestureActive(false); } }, [current]);

  function point(event: PointerEvent<HTMLDivElement>) {
    const img = image.current;
    return img ? containedScreenPoint(img.getBoundingClientRect(), img.naturalWidth, img.naturalHeight, event.clientX, event.clientY) : null;
  }
  function dispatch(command: ScreenCommand) {
    if (!canControl || !enabled || !frame || Date.parse(frame.expires_at) <= Date.now() || !available.includes(command.action)) return;
    onCommand({ action: command.action, arguments: { ...command.arguments, coordinate_space: "normalized", screen_frame_id: frame.id } });
  }
  function start(event: PointerEvent<HTMLDivElement>) {
    if (!enabled || !canControl || !event.isPrimary || event.button !== 0 || gesture.current || !frame) return;
    const p = point(event);
    if (!p) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { id: event.pointerId, point: p, clientX: event.clientX, clientY: event.clientY, time: performance.now(), frame: frame.id, url };
    setGestureActive(true);
  }
  function finish(event: PointerEvent<HTMLDivElement>) {
    const started = gesture.current;
    gesture.current = null;
    setGestureActive(false);
    if (!started || started.id !== event.pointerId || started.frame !== frame?.id || started.url !== url) return;
    const end = point(event);
    if (!end) return;
    dispatch(screenGesture(started.point, end, Math.hypot(event.clientX - started.clientX, event.clientY - started.clientY), performance.now() - started.time));
  }
  return <div>
    <div className="relative" role="button" aria-label={t("Phone screen control")} aria-disabled={!enabled || !canControl || !canTap}
      tabIndex={enabled && canControl && canTap ? 0 : -1}
      style={{ touchAction: enabled ? "none" : "auto", cursor: enabled && canControl ? "crosshair" : "default" }}
      onPointerDown={start} onPointerUp={finish}
      onPointerCancel={() => { gesture.current = null; setGestureActive(false); }}
      onLostPointerCapture={() => { gesture.current = null; setGestureActive(false); }}
      onKeyDown={event => { if ((event.key === "Enter" || event.key === " ") && !event.repeat) { event.preventDefault(); dispatch({ action: "tap_coordinates", arguments: { x: 0.5, y: 0.5 } }); } }}>
      <img ref={image} src={url} alt={t("Latest Android screen capture")} draggable={false}
        onLoad={() => setLoaded(true)} onError={() => setLoaded(false)}
        className="block h-auto min-h-72 w-full select-none bg-slate-100 object-contain" />
      {gestureActive && <span className="pointer-events-none absolute right-2 top-2 rounded bg-slate-900 px-2 py-1 text-xs text-white">{t("Release to send gesture")}</span>}
    </div>
    <div className="grid gap-2 border-t border-line bg-white p-3">
      <button type="button" className="btn min-h-11 w-full" aria-pressed={enabled}
        disabled={(!canControl || !canTap) && !enabled} onClick={() => setEnabled(value => !value)}>
        {enabled ? t("Stop screen control") : t("Control screen")}
      </button>
      <p className="text-xs leading-5 text-muted">
        {busy ? t("Waiting for this action to finish. Approval may be required.") : !frame ? t("Update Nexus Mobile, then capture a fresh screen to enable control.")
          : !current ? t("Screen control expired. Capture a fresh screen.")
          : enabled ? t("Click to tap. Enter taps the center. Drag to swipe when supported. Hold to long press when supported.")
          : t("Viewing only. Enable control to send phone actions.")}
      </p>
      {enabled && (!canSwipe || !canLongPress) && <p className="text-xs text-muted">{t("Some gestures require a newer Nexus Mobile version.")}</p>}
    </div>
  </div>;
}
