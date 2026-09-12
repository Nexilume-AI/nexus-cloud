import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("Run draft retention", () => {
  let storage: Map<string, string>;
  beforeEach(() => {
    vi.resetModules();
    storage = new Map();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, value),
      removeItem: (key: string) => storage.delete(key),
    });
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it("does not delete a newer draft when an old submission succeeds", async () => {
    const drafts = await import("./runDrafts");
    const key = drafts.runDraftKey("user", "org", "project", "agent", "run");
    drafts.writeRunDraft(key, "sent");
    const ticket = { key, text: "sent" };
    drafts.writeRunDraft(key, "new text while sending");
    drafts.clearSubmittedDraft(ticket);
    expect(drafts.readRunDraft(key)).toBe("new text while sending");
    drafts.clearSubmittedDraft({ key, text: "new text while sending" });
    expect(drafts.readRunDraft(key)).toBe("");
  });

  it("expires old drafts, rejects malformed storage and caps persistence", async () => {
    vi.useFakeTimers();
    const drafts = await import("./runDrafts");
    const key = drafts.runDraftKey("user", "org", "project", "agent", "run");
    drafts.writeRunDraft(key, "expiring");
    vi.advanceTimersByTime(24 * 60 * 60 * 1000 + 1);
    expect(drafts.readRunDraft(key)).toBe("");
    drafts.writeRunDraft(key, "x".repeat(32001));
    expect(drafts.readRunDraft(key)).toHaveLength(32001);
    expect(JSON.parse(storage.get("nexus.private-run-drafts.v1") || "{}")[key]).toBeUndefined();
    vi.resetModules();
    storage.set("nexus.private-run-drafts.v1", "{broken");
    expect((await import("./runDrafts")).readRunDraft(key)).toBe("");
  });

  it("restores only whitelisted uncertain-submission metadata for safe explicit retry", async () => {
    const drafts = await import("./runDrafts");
    const key = drafts.runDraftKey("user", "org", "project", "agent", "run", "follow-up:queue");
    drafts.writeRunDraft(key, "next");
    drafts.rememberFollowUp(key, { mode: "queue", content: "next", turn_index: 1, idempotency_key: "request-one" });
    vi.resetModules();
    const restored = await import("./runDrafts");
    expect(restored.pendingFollowUp(key, "queue", "next")?.turn_index).toBe(1);
    expect(restored.pendingFollowUp(key, "steer", "next")).toBeUndefined();
    expect(restored.pendingFollowUp(key, "queue", "edited")).toBeUndefined();
    restored.clearRunDrafts();
    expect(storage.has("nexus.private-run-drafts.v1")).toBe(false);
    expect(restored.pendingFollowUp(key, "queue", "next")).toBeUndefined();
  });

  it("retains unresolved delivery when editing or clearing text, and clears only a confirmed request", async () => {
    const drafts = await import("./runDrafts");
    const key = drafts.runDraftKey("user", "org", "project", "agent", "run", "follow-up:queue");
    drafts.writeRunDraft(key, "original");
    drafts.rememberFollowUp(key, { mode: "queue", content: "original", turn_index: 1, idempotency_key: "one" });
    drafts.writeRunDraft(key, "");
    expect(drafts.readPendingFollowUp(key)?.idempotency_key).toBe("one");
    drafts.writeRunDraft(key, "new draft");
    drafts.settleFollowUp(key, "another-request");
    expect(drafts.readPendingFollowUp(key)?.idempotency_key).toBe("one");
    drafts.settleFollowUp(key, "one");
    expect(drafts.readRunDraft(key)).toBe("new draft");
    expect(drafts.readPendingFollowUp(key)).toBeUndefined();
    drafts.writeRunDraft(key, "  second  ");
    drafts.rememberFollowUp(key, { mode: "queue", content: "second", turn_index: 2, idempotency_key: "two" });
    drafts.settleFollowUp(key, "two");
    expect(drafts.readRunDraft(key)).toBe("");
    expect(drafts.readPendingFollowUp(key)).toBeUndefined();
  });

  it("restores the exact attachment IDs without URLs or arbitrary metadata", async () => {
    const drafts = await import("./runDrafts");
    const key = drafts.runDraftKey("caller", "org", "project", "agent", "run", "follow-up:queue");
    drafts.rememberFollowUp(key, { mode: "queue", content: "read", turn_index: 2, idempotency_key: "one",
      attachments: [{ asset_id: "image-one", url: "https://secret.example/token" } as { asset_id: string }], files: ["file-one"] });
    vi.resetModules(); const reloaded = await import("./runDrafts");
    expect(reloaded.readPendingFollowUp(key)).toEqual({ mode: "queue", content: "read", turn_index: 2, idempotency_key: "one", attachments: [{ asset_id: "image-one" }], files: ["file-one"] });
    expect(storage.get("nexus.private-run-drafts.v1")).not.toContain("secret.example");
    expect(() => drafts.rememberFollowUp(key, { mode: "queue", content: "read", turn_index: 2, idempotency_key: "one", files: ["https://secret.example"] })).toThrow();
  });
});
