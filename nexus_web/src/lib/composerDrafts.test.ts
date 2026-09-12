import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("Composer attachment drafts", () => {
  let storage: Map<string, string>;
  beforeEach(() => {
    vi.resetModules(); storage = new Map();
    vi.stubGlobal("sessionStorage", { getItem: (key: string) => storage.get(key) || null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) });
  });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
  const asset = { id: "file-1", kind: "upload" as const, name: "notes.txt", size: 8, contentType: "text/plain" };
  it("restores only bounded references and execution settings, never credentials or paths", async () => {
    const drafts = await import("./composerDrafts");
    const value = drafts.sanitizeComposerDraft({ assets: [{ ...asset, name: "C:\\personal\\notes.txt", token: "secret", url: "https://signed/?token=secret", file: { contents: "sensitive" } }], tool: "inspect", profileId: "fast", reasoningEffort: "low", updated: Date.now(), authorization: "secret" });
    drafts.writeComposerDraft('["caller","org","project","agent","run"]', value);
    expect(JSON.stringify([...storage.values()])).not.toMatch(/secret|signed|personal|sensitive/);
    vi.resetModules();
    expect((await import("./composerDrafts")).readComposerDraft('["caller","org","project","agent","run"]')).toMatchObject({ assets: [asset], profileId: "fast" });
  });
  it("clears only submitted references and preserves other conversations and newer attachments", async () => {
    const drafts = await import("./composerDrafts");
    const key = '["caller","org","project","agent","run"]';
    const row = drafts.sanitizeComposerDraft({ assets: [asset], profileId: "fast", updated: Date.now() });
    drafts.writeComposerDraft(key, row); drafts.writeComposerDraft(key + "other", row);
    const ticket = { key, assets: drafts.readComposerDraft(key).assets };
    drafts.writeComposerDraft(key, { ...row, assets: [asset, { ...asset, id: "file-2" }] });
    drafts.clearSubmittedAssets(ticket);
    expect(drafts.readComposerDraft(key).assets.map(item => item.id)).toEqual(["file-2"]);
    expect(drafts.readComposerDraft(key + "other").assets).toEqual([asset]);
    expect(drafts.readComposerDraft(key).profileId).toBe("fast");
  });
  it("expires references, bounds entries and tolerates unavailable browser storage", async () => {
    vi.useFakeTimers();
    const drafts = await import("./composerDrafts");
    const row = drafts.sanitizeComposerDraft({ assets: Array.from({ length: 20 }, (_, i) => ({ ...asset, id: `file-${i}` })) });
    expect(row.assets).toHaveLength(12);
    drafts.writeComposerDraft('["run"]', row);
    vi.advanceTimersByTime(24 * 3600_000 + 1);
    expect(drafts.readComposerDraft('["run"]').assets).toEqual([]);
    vi.stubGlobal("sessionStorage", { setItem: () => { throw Error("blocked"); } });
    expect(() => drafts.writeComposerDraft('["run"]', row)).not.toThrow();
    expect(drafts.readComposerDraft('["run"]').assets).toHaveLength(12);
  });
  it("rejects malformed references and clears attachment drafts on logout", async () => {
    const drafts = await import("./composerDrafts");
    expect(drafts.sanitizeComposerDraft({ assets: [null, { ...asset, id: "../../secret" }, { ...asset, id: 123 }, { ...asset, size: -1 }] }).assets).toEqual([]);
    drafts.writeComposerDraft('["run"]', drafts.sanitizeComposerDraft({ assets: [asset] }));
    (await import("./runDrafts")).clearRunDrafts();
    expect(drafts.readComposerDraft('["run"]').assets).toEqual([]);
    expect(storage.has("nexus.composer-assets.v1")).toBe(false);
  });
});
