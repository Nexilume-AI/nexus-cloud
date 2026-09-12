import { useEffect, useRef, useState } from "react";

import { api, type ApiContext } from "./api";
import type { AgentRunTerminal } from "./types";

export type PrivateAgentTerminalConnection = "idle" | "connecting" | "live" | "reconnecting" | "polling";

export function privateAgentTerminalWebSocketUrl(path: string, locationHref: string) {
  const url = new URL(path, locationHref);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function usePrivateAgentTerminal({
  context,
  runId,
  displayToken,
  enabled,
  onEvent,
  onStatus,
}: {
  context: ApiContext;
  runId: string;
  displayToken: string;
  enabled: boolean;
  onEvent: (event: AgentRunTerminal["events"][number]) => void;
  onStatus?: (status: string) => void;
}) {
  const eventRef = useRef(onEvent);
  const statusRef = useRef(onStatus);
  const [connection, setConnection] = useState<PrivateAgentTerminalConnection>("idle");
  eventRef.current = onEvent;
  statusRef.current = onStatus;

  useEffect(() => {
    if (!enabled || !runId || !displayToken) {
      setConnection("idle");
      return;
    }
    let stopped = false;
    let socket: WebSocket | null = null;
    let timer: number | null = null;
    let attempt = 0;

    const retry = () => {
      if (stopped) return;
      attempt += 1;
      setConnection(attempt === 1 ? "polling" : "reconnecting");
      timer = window.setTimeout(connect, Math.min(8_000, 750 * (2 ** Math.min(attempt, 4))));
    };
    const connect = async () => {
      if (stopped) return;
      setConnection(attempt ? "reconnecting" : "connecting");
      try {
        const ticket = await api.privateAgentRunTerminalTicket(context, runId, displayToken);
        if (stopped) return;
        socket = new WebSocket(privateAgentTerminalWebSocketUrl(ticket.websocket_url, window.location.href));
        socket.onopen = () => {
          attempt = 0;
          setConnection("live");
        };
        socket.onmessage = (message) => {
          try {
            const value = JSON.parse(String(message.data)) as Record<string, unknown>;
            if (value.type === "status") {
              statusRef.current?.(String(value.status || ""));
              return;
            }
            if (value.type !== "terminal") return;
            eventRef.current({
              seq: Number(value.seq || 0),
              kind: String(value.kind || "system"),
              command_id: String(value.command_id || ""),
              data: String(value.data || ""),
              exit_code: value.exit_code === null || value.exit_code === undefined ? null : Number(value.exit_code),
              created_at: String(value.created_at || ""),
            });
          } catch {
            // A malformed frame is ignored; the persisted HTTP transcript remains authoritative.
          }
        };
        socket.onerror = () => socket?.close();
        socket.onclose = () => retry();
      } catch {
        retry();
      }
    };

    void connect();
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
      if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
    };
  }, [context, displayToken, enabled, runId]);

  return connection;
}
