import { describe, expect, it } from "vitest";

import { buildAgentDisplayShellCommands, buildAgentDisplayShellTranscript, deriveCurrentTurnDisplayMessages, type AgentDisplayShellLine } from "./agentDisplayEvents";
import type { DisplayStreamEvent } from "./types";

describe("current-turn stream supplementation", () => {
  const event = (seq: number, type: string, extra = {}): DisplayStreamEvent =>
    ({ id: `${seq}`, agent_id: "agent", run_id: "run", seq, type, created_at: "", ...extra });

  it("does not replay an interrupted old-turn message after a follow-up starts", () => {
    const events = [
      event(1, "RUN_STARTED"),
      event(2, "TEXT_MESSAGE_START", { messageId: "old", role: "assistant" }),
      event(3, "TEXT_MESSAGE_CONTENT", { messageId: "old", delta: "Old unfinished step" }),
      event(4, "RUN_ERROR"),
      event(5, "RUN_STARTED"),
      event(6, "TEXT_MESSAGE_START", { messageId: "new", role: "assistant" }),
      event(7, "TEXT_MESSAGE_CONTENT", { messageId: "new", delta: "New guided step" }),
    ];
    expect(deriveCurrentTurnDisplayMessages(events).map(message => message.content)).toEqual(["New guided step"]);
  });

  it("keeps same-turn messages around Steer receipt and accepts cursor-only event pages", () => {
    const events = [
      event(5, "TEXT_MESSAGE_START", { messageId: "before", role: "assistant" }),
      event(6, "TEXT_MESSAGE_CONTENT", { messageId: "before", delta: "Before guidance" }),
      event(7, "TEXT_MESSAGE_END", { messageId: "before" }),
      event(8, "TEXT_MESSAGE_START", { messageId: "after", role: "assistant" }),
      event(9, "TEXT_MESSAGE_CONTENT", { messageId: "after", delta: "After guidance" }),
    ];
    expect(deriveCurrentTurnDisplayMessages(events).map(message => message.content)).toEqual(["Before guidance", "After guidance"]);
  });
});

const sdkLine = (seq: number, text: string): AgentDisplayShellLine => ({
  id: `sdk-${seq}`,
  seq,
  stream: "stdout",
  text,
  createdAt: `2026-08-21T00:00:0${seq}Z`,
});

describe("buildAgentDisplayShellTranscript", () => {
  it("uses Terminal as the authoritative source instead of duplicating SDK Shell output", () => {
    const result = buildAgentDisplayShellTranscript([
      { seq: 1, kind: "command", data: "Get-ChildItem", exit_code: null },
      { seq: 2, kind: "stdout", data: "alpha\r\n", exit_code: null },
      { seq: 3, kind: "stdout", data: "\r\nbeta\r\n\r\ngamma\r\n", exit_code: null },
      { seq: 4, kind: "exit", data: "", exit_code: 0 },
    ], [sdkLine(1, "alpha\r\n\r\nbeta\r\n\r\ngamma")]);

    expect(result.text).toBe("$ Get-ChildItem\nalpha\nbeta\n\ngamma\nProcess exited with code 0");
    expect(result.text).not.toContain("alpha\nalpha");
  });

  it("concatenates raw chunks without inventing a visual line boundary", () => {
    const result = buildAgentDisplayShellTranscript([
      { seq: 1, kind: "stdout", data: "Power", exit_code: null },
      { seq: 2, kind: "stdout", data: "Shell\r\n中文", exit_code: null },
    ], []);

    expect(result.text).toBe("PowerShell\n中文");
  });

  it("uses normalized SDK Shell records until Terminal events are available", () => {
    const result = buildAgentDisplayShellTranscript([], [
      sdkLine(2, "second\r\n"),
      sdkLine(1, "first\r\n"),
    ]);

    expect(result.text).toBe("first\nsecond");
  });

  it("strips terminal color controls while preserving unicode", () => {
    const result = buildAgentDisplayShellTranscript([
      { seq: 1, kind: "stdout", data: "\u001b[32m成功\u001b[0m\r\n", exit_code: null },
    ], []);

    expect(result.text).toBe("成功");
  });

  it("groups output by command and keeps exit metadata", () => {
    const commands = buildAgentDisplayShellCommands([
      { seq: 1, kind: "command", command_id: "one", data: "pwd", exit_code: null, created_at: "2026-09-05T01:00:00Z" },
      { seq: 2, kind: "stdout", command_id: "one", data: "/work\n", exit_code: null, created_at: "2026-09-05T01:00:01Z" },
      { seq: 3, kind: "exit", command_id: "one", data: "", exit_code: 0, created_at: "2026-09-05T01:00:02Z" },
      { seq: 4, kind: "command", command_id: "two", data: "false", exit_code: null, created_at: "2026-09-05T01:00:03Z" },
      { seq: 5, kind: "exit", command_id: "two", data: "", exit_code: 1, created_at: "2026-09-05T01:00:04Z" },
    ], []);

    expect(commands.map((item) => [item.command, item.exitCode])).toEqual([["pwd", 0], ["false", 1]]);
    expect(commands[0].transcript.text).toContain("/work");
  });
});
