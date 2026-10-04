import { useEffect, useRef, useState, type PointerEvent, type ReactNode } from "react";
import { api, type ApiContext } from "../lib/api";
import { containedScreenPoint, screenGesture, type ScreenPoint } from "../lib/mobileScreenControl";
import type { MobileDevice, MobileVideoSession } from "../lib/types";
import { t, useLocale } from "../localization";

type Command = { action: "tap_coordinates" | "swipe" | "long_press"; arguments: Record<string, unknown> };
type Connection = { id: string; peer: RTCPeerConnection; stopped: boolean; cursor: number; context: ApiContext; session: MobileVideoSession; disconnectedAt?: number; answerAt?: number };
const terminal = new Set(["stopped", "failed", "expired"]);

/** Media is peer-to-peer; all gestures still use the authenticated approval queue. */
export function MobileLiveVideo({ device, context, busy, onCommand, capturePreview, onCapture, capturePending, canCapture }: {
  device: MobileDevice; context: ApiContext; busy: boolean; onCommand: (command: Command) => void;
  capturePreview?: ReactNode; onCapture?: () => void; capturePending?: boolean; canCapture?: boolean;
}) {
  useLocale();
  const video = useRef<HTMLVideoElement>(null);
  const current = useRef<Connection | null>(null);
  const mounted = useRef(true);
  const generation = useRef(0);
  const starting = useRef(false);
  const [session, setSession] = useState<MobileVideoSession | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [frames, setFrames] = useState(0);
  const [fresh, setFresh] = useState(false);
  const [playbackBlocked, setPlaybackBlocked] = useState(false);
  const [mode, setMode] = useState<"live" | "capture">("live");
  const [relayStats, setRelayStats] = useState({ local: "", remote: "", protocol: "", bytes: 0 });
  const lastFrame = useRef(0);
  const gesture = useRef<{ id: number; point: ScreenPoint; x: number; y: number; time: number; version: number } | null>(null);
  const stop = (notify = true, reason = "viewer") => {
    generation.current += 1;
    const connection = current.current;
    if (connection && import.meta.env.DEV) console.debug("Mobile video closed:", reason);
    current.current = null;
    if (connection) {
      connection.stopped = true;
      connection.peer.close();
      if (notify) void api.signalMobileVideo(connection.context, connection.id, { type: "stop" }).catch(() => undefined);
    }
    if (video.current) video.current.srcObject = null;
    if (mounted.current) { setSession(null); setEnabled(false); setFresh(false); setPending(false); setPlaybackBlocked(false); }
    gesture.current = null;
  };
  useEffect(() => {
    mounted.current = true;
    generation.current += 1;
    return () => { generation.current += 1; mounted.current = false; stop(true, "workspace left"); };
  }, [device.id]);
  useEffect(() => {
    if (device.lifecycle_status !== "online") {
      if (current.current) setError(t("The phone is no longer ready. Restore Nexus Mobile connection and control permissions, then start video again."));
      stop(true, `device ${device.lifecycle_status}`);
    }
  }, [device.lifecycle_status]);
  useEffect(() => { gesture.current = null; }, [enabled, busy, session?.geometry_version]);

  async function start() {
    if (starting.current || current.current) return;
    starting.current = true; setPending(true); setError(""); setFrames(0); lastFrame.current = 0;
    const startedGeneration = generation.current;
    let created: MobileVideoSession | null = null;
    try {
      if (!window.RTCPeerConnection || !window.isSecureContext) throw new Error(t("Live video requires HTTPS or localhost and a WebRTC-capable browser."));
      created = await api.startMobileVideo(context, device.id);
      if (!mounted.current || generation.current !== startedGeneration) { await api.signalMobileVideo(context, created.id, { type: "stop" }); return; }
      const peer = new RTCPeerConnection({ iceServers: created.ice_servers ?? [], iceTransportPolicy: created.ice_transport_policy ?? "all" });
      const connection: Connection = { id: created.id, peer, stopped: false, cursor: 0, context, session: created };
      current.current = connection;
      setSession(created);
      const remoteIce: RTCIceCandidateInit[] = [];
      let localSignal = Promise.resolve();
      peer.onicecandidate = event => {
        if (event.candidate && !connection.stopped) {
          const candidate = event.candidate.toJSON();
          localSignal = localSignal.then(async () => {
            if (!connection.stopped) await api.signalMobileVideo(context, connection.id, { type: "ice", candidate });
          }).catch(() => { if (!connection.stopped) { setError(t("Live video disconnected. Start again.")); stop(); } });
        }
      };
      peer.ontrack = event => {
        const element = video.current;
        if (element && !connection.stopped) {
          element.srcObject = new MediaStream([event.track]);
          void element.play().catch(cause => {
            // Closing the peer aborts play(); an old callback must not replace
            // the actual connection failure with an unrelated autoplay error.
            if (mounted.current && current.current === connection && !connection.stopped && cause?.name !== "AbortError") {
              setPlaybackBlocked(true);
            }
          });
        }
      };
      peer.onconnectionstatechange = () => {
        if (!connection.stopped && peer.connectionState === "disconnected") {
          connection.disconnectedAt ??= Date.now(); setFresh(false);
        } else if (peer.connectionState === "connected") {
          connection.disconnectedAt = undefined;
        } else if (!connection.stopped && peer.connectionState === "failed") {
          setError(t("Live video disconnected. Start again.")); stop();
        }
      };
      peer.addTransceiver("video", { direction: "recvonly" });
      const offer = await peer.createOffer();
      if (connection.stopped) return;
      await peer.setLocalDescription(offer);
      if (connection.stopped) return;
      await api.signalMobileVideo(context, connection.id, { type: "offer", sdp: offer.sdp });
      if (connection.stopped) return;
      setPending(false);
      void (async () => {
        let decoded = 0;
        try {
          while (!connection.stopped) {
            const update = await api.mobileVideo(context, connection.id, connection.cursor);
            if (connection.stopped) return;
            connection.session = update; setSession(update);
            if (terminal.has(update.state)) {
              if (update.state !== "stopped") setError(videoError(update.error_code));
              stop(false); return;
            }
            for (const signal of update.signals) {
              if (signal.type === "answer" && signal.sdp && !peer.remoteDescription) {
                await peer.setRemoteDescription({ type: "answer", sdp: signal.sdp });
                connection.answerAt = Date.now();
                for (const c of remoteIce.splice(0)) await peer.addIceCandidate(c);
              } else if (signal.type === "ice" && signal.candidate) {
                if (peer.remoteDescription) await peer.addIceCandidate(signal.candidate); else remoteIce.push(signal.candidate);
              }
            }
            connection.cursor = update.sequence;
            const stats = await peer.getStats();
            stats.forEach(stat => {
              if (stat.type === "transport" && stat.selectedCandidatePairId) {
                const pair = stats.get(stat.selectedCandidatePairId);
                if (pair) setRelayStats({ local: stats.get(pair.localCandidateId)?.candidateType ?? "",
                  remote: stats.get(pair.remoteCandidateId)?.candidateType ?? "",
                  protocol: stats.get(pair.localCandidateId)?.relayProtocol ?? stats.get(pair.localCandidateId)?.protocol ?? "", bytes: pair.bytesReceived ?? 0 });
              }
              if (stat.type === "inbound-rtp" && stat.kind === "video" && stat.framesDecoded > decoded) {
                decoded = stat.framesDecoded; lastFrame.current = Date.now(); setFrames(decoded);
              }
            });
            setFresh(Date.now() - lastFrame.current < 3000);
            if ((connection.disconnectedAt && Date.now() - connection.disconnectedAt >= 5000) ||
                (connection.answerAt && !decoded && Date.now() - connection.answerAt >= 30000)) {
              throw new Error(t("Video could not reach the relay. Check TURN address and firewall, then start again."));
            }
            await new Promise(resolve => window.setTimeout(resolve, 500));
          }
        } catch (cause) {
          if (!connection.stopped) { setError(cause instanceof Error ? cause.message : t("Live video disconnected. Start again.")); stop(); }
        }
      })();
    } catch (cause) {
      if (!mounted.current || generation.current !== startedGeneration) {
        if (created) void api.signalMobileVideo(context, created.id, { type: "stop" }).catch(() => undefined);
        return;
      }
      setError(cause instanceof Error ? cause.message : t("Live video disconnected. Start again."));
      if (created && !current.current) void api.signalMobileVideo(context, created.id, { type: "stop" }).catch(() => undefined);
      stop();
    } finally { starting.current = false; if (mounted.current && generation.current === startedGeneration) setPending(false); }
  }
  const canControl = enabled && !busy && fresh && session?.state === "live" && session.geometry_version > 0;
  function point(event: PointerEvent<HTMLElement>) {
    const element = video.current;
    return element ? containedScreenPoint(element.getBoundingClientRect(), element.videoWidth, element.videoHeight, event.clientX, event.clientY) : null;
  }
  function send(command: Command) {
    const active = current.current;
    if (!canControl || Date.now() - lastFrame.current >= 3000 || !active || !device.available_actions?.includes(command.action)) return;
    onCommand({ ...command, arguments: { ...command.arguments, coordinate_space: "normalized",
      video_session_id: active.id, video_geometry_version: active.session.geometry_version } });
  }
  return <section className="min-w-0 overflow-hidden rounded-xl border border-line bg-white p-4 sm:p-5" aria-label={t("Phone screen")}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h3 className="font-semibold">{t("Phone screen")}</h3>
      <select className="select min-h-11 w-auto" aria-label={t("Screen mode")} value={mode} onChange={event => {
        const next = event.target.value as "live" | "capture";
        if (next === "capture") stop(true, "capture selected");
        setMode(next); setError("");
      }}><option value="live">{t("Live video")}</option><option value="capture">{t("Screenshot")}</option></select>
    </div>
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {mode === "live" && <>
      <button type="button" className="btn min-h-11" disabled={pending || device.lifecycle_status !== "online"}
        onClick={() => session ? stop() : void start()}>{pending ? t("Starting…") : session ? t("Stop video") : t("Start live video")}</button>
      {(session || pending) && <button className="btn min-h-11" type="button" aria-pressed={enabled} disabled={(!fresh || session?.state !== "live") && !enabled}
        onClick={() => setEnabled(value => !value)}>{enabled ? t("Stop screen control") : t("Control live screen")}</button>}
      </>}
      {mode === "capture" && onCapture && <button type="button" className="btn min-h-11" disabled={!canCapture || capturePending} onClick={() => {
        stop(true, "capture requested"); setMode("capture"); setError(""); onCapture();
      }}>{capturePending ? t("Capturing") : t("Capture screen")}</button>}
    </div>
    <div data-testid="mobile-phone-frame" className="mx-auto mt-4 w-full max-w-[224px] overflow-hidden rounded-[1.5rem] border-[6px] border-ink bg-ink sm:max-w-[288px]">
      <div className="flex h-7 items-center justify-between gap-2 px-3 text-xs text-white">
        <span className="truncate">{device.name}</span><span className="shrink-0">{mode === "live" && session ? t("Live video") : t("Screenshot")}</span>
      </div>
      {mode === "live" && (session || pending) ? <>
      <div role="button" aria-label={t("Live phone screen control")} aria-disabled={!canControl} tabIndex={canControl ? 0 : -1}
        style={{ touchAction: enabled ? "none" : "auto", cursor: canControl ? "crosshair" : "default" }}
        onPointerDown={event => {
          if (!canControl || !event.isPrimary || event.button !== 0 || gesture.current) return;
          const p = point(event); if (!p) return;
          event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId);
          gesture.current = { id: event.pointerId, point: p, x: event.clientX, y: event.clientY, time: performance.now(), version: session?.geometry_version ?? 0 };
        }}
        onPointerUp={event => {
          const initial = gesture.current; gesture.current = null;
          if (!initial || initial.id !== event.pointerId || initial.version !== session?.geometry_version) return;
          const p = point(event); if (p) send(screenGesture(initial.point, p, Math.hypot(event.clientX - initial.x, event.clientY - initial.y), performance.now() - initial.time));
        }} onPointerCancel={() => { gesture.current = null; }} onLostPointerCapture={() => { gesture.current = null; }}
        onKeyDown={event => { if (["Enter", " "].includes(event.key) && !event.repeat) { event.preventDefault(); send({ action: "tap_coordinates", arguments: { x: 0.5, y: 0.5 } }); } }}>
        <video ref={video} muted autoPlay playsInline data-frames-decoded={frames}
          data-local-candidate={relayStats.local} data-remote-candidate={relayStats.remote} data-relay-protocol={relayStats.protocol} data-received-bytes={relayStats.bytes}
          className="block aspect-[9/20] max-h-[min(65vh,560px)] min-h-72 w-full bg-ink object-contain" />
      </div>
      </> : <div className="bg-white">{capturePreview ?? <div className="flex aspect-[9/20] items-center justify-center p-6 text-sm text-muted">{t("Start video or capture a screen to view your phone.")}</div>}</div>}
    </div>
    {mode === "live" && (session || pending) && <div className="mt-3 grid gap-2">
        {playbackBlocked && <button type="button" className="btn min-h-11" onClick={() => {
          const active = current.current;
          void video.current?.play().then(() => { if (current.current === active) setPlaybackBlocked(false); })
            .catch(() => { if (active && !active.stopped) setError(t("Press play to view the phone screen.")); });
        }}>{t("Play video")}</button>}
        <p role="status" className="text-sm text-muted">{session?.state === "pending" || (session?.state === "connecting" && !frames)
          ? t("Open Nexus Mobile on your phone and tap Share screen, then approve Android screen sharing.")
          : !fresh ? t("Waiting for live frames. Screen control is paused.") : t("Live · No audio or recording")}</p>
        {enabled && <p className="text-xs text-muted">{t("Click to tap. Drag to swipe. Hold to long press. Approval settings still apply.")}</p>}
    </div>}
    {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
  </section>;
}

function videoError(code: string) {
  if (code === "MOBILE_VIDEO_CONSENT_DENIED") return t("Screen sharing was declined on the phone. Start again when ready.");
  if (code === "MOBILE_VIDEO_SCREEN_BLOCKED") return t("Sharing stopped because the phone displayed sensitive or protected content.");
  return t("Live video disconnected. Start again.");
}
