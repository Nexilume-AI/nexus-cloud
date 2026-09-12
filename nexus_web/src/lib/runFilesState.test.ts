import { beforeEach, describe, expect, it, vi } from "vitest";
import { readFilesState } from "./runFilesState";

describe("caller-scoped file browsing preferences", () => {
  beforeEach(() => vi.stubGlobal("sessionStorage", { getItem: () => JSON.stringify({ caller: { at: Date.now(), value: {
    search: "report", kind: "output", turn: "21", selected: "output:one", pdfPage: 7, inputCursors: [""], outputCursors: ["", "signed-page"] } } }) }));
  it("restores filters, selected file, page and cursor without a token", () => {
    expect(readFilesState("caller")).toMatchObject({ search: "report", kind: "output", turn: "21", selected: "output:one", pdfPage: 7, outputCursors: ["", "signed-page"] });
    expect(JSON.stringify(readFilesState("caller"))).not.toContain("token");
  });
  it("does not inherit another caller or Run's browser state", () => expect(readFilesState("other").selected).toBe(""));
  it("survives malformed optional browser storage", () => {
    vi.stubGlobal("sessionStorage", { getItem: () => "invalid" });
    expect(readFilesState("caller").pdfPage).toBe(1);
  });
});
