import { useEffect, useRef, useState } from "react";
import { multipartHeaders, type ApiContext } from "./api";
import type { DisplayStreamEvent } from "./types";

export function usePrivateDisplayStream(context: ApiContext, runId: string, token: string, enabled: boolean,
  cursor: React.RefObject<number>, onEvent: (event: DisplayStreamEvent) => void) {
  const [live, setLive] = useState(false);
  const receive = useRef(onEvent); receive.current = onEvent;
  useEffect(() => {
    setLive(false);
    if (!enabled || !runId || !token) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    async function connect() {
      let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
      let denied = false;
      try {
        const response = await fetch(`/api/v1/agent-runs/${runId}/events/stream/?cursor=${cursor.current}`, {
          credentials: "same-origin", signal: controller.signal,
          headers: { ...multipartHeaders(context, "GET"), Accept: "text/event-stream", "X-Nexus-Agent-Display-Token": token },
        });
        if (!response.ok || !response.body || !response.headers.get("Content-Type")?.includes("text/event-stream")) throw new Error("Stream unavailable");
        reader = response.body.getReader();
        const decoder = new TextDecoder(); let buffer = "";
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read();
          if (done) break;
          setLive(true); failures = 0;
          buffer += decoder.decode(value, { stream: true });
          if (buffer.length > 2_000_000) throw new Error("Stream event limit");
          let end: number;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
            if (frame.includes("event: denied")) { denied = true; return; }
            if (frame.includes("event: reconnect")) throw new Error("Reconnect requested");
            const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trim()).join("\n");
            if (!data) continue;
            const event = JSON.parse(data) as DisplayStreamEvent;
            if (event.run_id === runId && Number.isSafeInteger(event.seq) && event.seq > cursor.current) receive.current(event);
          }
        }
      } catch { failures++; }
      finally {
        await reader?.cancel().catch(() => {});
        if (!controller.signal.aborted) {
          setLive(false);
          if (!denied) timer = setTimeout(connect, failures ? Math.min(15_000, 1000 * 2 ** Math.min(failures, 4)) : 250);
        }
      }
    }
    void connect();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [context.token, context.tenantId, context.projectId, runId, token, enabled, cursor]);
  return live;
}
