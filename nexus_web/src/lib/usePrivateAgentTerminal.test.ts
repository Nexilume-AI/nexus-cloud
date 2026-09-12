import { describe, expect, it } from "vitest";

import { privateAgentTerminalWebSocketUrl } from "./usePrivateAgentTerminal";

describe("privateAgentTerminalWebSocketUrl", () => {
  it("uses a secure WebSocket for an HTTPS Cloud", () => {
    expect(privateAgentTerminalWebSocketUrl("/ws/agent-runs/run-1/terminal/?ticket=secret", "https://cloud.example/agents"))
      .toBe("wss://cloud.example/ws/agent-runs/run-1/terminal/?ticket=secret");
  });

  it("keeps development traffic on ws", () => {
    expect(privateAgentTerminalWebSocketUrl("/ws/terminal", "http://127.0.0.1:5173/run"))
      .toBe("ws://127.0.0.1:5173/ws/terminal");
  });
});
