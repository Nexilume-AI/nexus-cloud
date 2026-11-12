import { describe, expect, it } from "vitest";
import { decisionRole, orderedDecisionModels } from "./decisionHarness";

describe("decision harness preferences", () => {
  it("matches Desktop profiles for two through five candidates", () => {
    expect(Array.from({length: 5}, (_, i) => decisionRole(i, 5))).toEqual(["Deep reasoning", "Advanced", "Balanced", "Efficient", "Fast"]);
    expect(Array.from({length: 3}, (_, i) => decisionRole(i, 3))).toEqual(["Deep reasoning", "Balanced", "Fast"]);
    expect(decisionRole(1, 2)).toBe("Fast");
  });
  it("preserves configured identities, appends new models and ignores removed mappings", () => {
    const rows = [{id: "strong", priority: 10, status: "active"}, {id: "new", priority: 20, status: "active"}, {id: "fast", priority: 30, status: "active"}, {id: "deleted", priority: 0, status: "deleted"}];
    expect(orderedDecisionModels(rows, ["gone", "fast", "strong"]).map(row => row.id)).toEqual(["fast", "strong", "new"]);
    expect(rows[0].id).toBe("strong");
  });
});
