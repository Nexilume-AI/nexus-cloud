import { beforeEach, describe, expect, it, vi } from "vitest";
import { contextRevision, patchContextView, readContextView, runContextKey } from "./runContextState";

describe("Run context browsing state", () => {
  let data: string | null;
  beforeEach(() => { data = null; vi.stubGlobal("sessionStorage", { getItem: () => data, setItem: (_key: string, value: string) => { data = value; } }); });
  it("restores the panel, mobile context and scroll without persisting content", () => {
    patchContextView("one", { panel: "files", mobile: "context", scroll: { "files:preview:one": { x: 10, y: 650 } }, seen: { files: contextRevision("private text") } });
    expect(readContextView("one")).toMatchObject({ panel: "files", mobile: "context", scroll: { "files:preview:one": { x: 10, y: 650 } } });
    expect(data).not.toContain("private text");
  });
  it("isolates caller, Organization, Project and Run", () => {
    const key = runContextKey(1, "org", "project", "run"); patchContextView(key, { panel: "workspace" });
    for (const other of [runContextKey(2, "org", "project", "run"), runContextKey(1, "other", "project", "run"), runContextKey(1, "org", "other", "run"), runContextKey(1, "org", "project", "other")]) expect(readContextView(other).panel).toBe("plan");
  });
  it("merges scroll and update markers without resetting panel selection", () => {
    patchContextView("one", { panel: "workspace" }); patchContextView("one", { seen: { plan: "ab" } });
    patchContextView("one", { scroll: { "workspace:panel": { x: 0, y: 50 } } });
    expect(readContextView("one")).toMatchObject({ panel: "workspace", seen: { plan: "ab" } });
  });
  it("bounds the history and rejects corrupt or expired positions", () => {
    for (let i = 0; i < 55; i++) patchContextView(String(i), { panel: "files" });
    expect(Object.keys(JSON.parse(data!))).toHaveLength(50);
    data = JSON.stringify({ one: { at: Date.now(), value: { panel: "bad", scroll: { bad: { x: 0, y: -1 }, huge: { x: 0, y: 1e20 } } } } });
    expect(readContextView("one")).toMatchObject({ panel: "plan", scroll: {} });
    data = JSON.stringify({ one: { at: Date.now() - 86400_001, value: { panel: "files" } } });
    expect(readContextView("one").panel).toBe("plan");
  });
  it("migrates Computer to Workspace and removed low-frequency panels to Plan", () => {
    data = JSON.stringify({ one: { at: Date.now(), value: { panel: "computer", mobile: "context", scroll: { "computer:panel": { x: 0, y: 50 } }, seen: { computer: "ab" } } } });
    expect(readContextView("one")).toMatchObject({ panel: "workspace", mobile: "context", scroll: { "workspace:panel": { x: 0, y: 50 } }, seen: { workspace: "ab" } });
    data = JSON.stringify({ one: { at: Date.now(), value: { panel: "instructions", mobile: "context", scroll: {}, seen: { instructions: "ab" } } } });
    expect(readContextView("one")).toMatchObject({ panel: "plan", mobile: "context", seen: {} });
    data = JSON.stringify({ one: { at: Date.now(), value: { panel: "mobile", mobile: "context", scroll: {}, seen: { mobile: "ab" } } } });
    expect(readContextView("one")).toMatchObject({ panel: "plan", mobile: "context", seen: {} });
  });
  it("tolerates unavailable optional browser storage", () => {
    vi.stubGlobal("sessionStorage", { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } });
    expect(patchContextView("one", { panel: "files" }).panel).toBe("files");
    expect(readContextView("one").panel).toBe("plan");
  });
});
