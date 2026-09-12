import { describe, expect, it } from "vitest";

import { formatTerminalStatusLine, TerminalOutputNormalizer } from "./terminal";

describe("formatTerminalStatusLine", () => {
  it("does not insert a blank line when the cursor is already at the start of a line", () => {
    expect(formatTerminalStatusLine("connected", 0)).toBe("[connected]\r\n");
  });

  it("moves an in-progress terminal line before writing status", () => {
    expect(formatTerminalStatusLine("terminal disconnected", 18)).toBe(
      "\r\n[terminal disconnected]\r\n",
    );
  });
});

describe("TerminalOutputNormalizer", () => {
  it("keeps a CRLF sequence split across live WebSocket frames on one line boundary", () => {
    const normalizer = new TerminalOutputNormalizer();
    expect(normalizer.push("first\r")).toBe("first");
    expect(normalizer.push("\nsecond\r\nthird\n")).toBe("\r\nsecond\r\nthird\r\n");
  });

  it("preserves carriage-return updates and flushes a final pending return", () => {
    const normalizer = new TerminalOutputNormalizer();
    expect(normalizer.push("progress 10%\rprogress 20%\r")).toBe("progress 10%\rprogress 20%");
    expect(normalizer.flush()).toBe("\r");
    expect(normalizer.flush()).toBe("");
  });
});
