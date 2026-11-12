import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { Router } from "../lib/types";
import { RouterModelSelection, savedDecisionSettings } from "./RouterModelSelection";

const router = {
  id: "router-test", name: "aggre", strategy: "system_one",
  decision_config: { provider_connection_id: "decision-provider", model: "decision-model-preview", timeout_ms: 3000, model_source: "catalog", model_order: ["strong", "removed", "fast"] },
  child_bindings: [{ id: "strong", status: "active", enabled: true }, { id: "fast", status: "active", enabled: false }, { id: "removed", status: "deleted", enabled: false }],
} as unknown as Router;

describe("Router model selection", () => {
  it("keeps the saved policy compact and the decision form out of the page", () => {
    const html = renderToStaticMarkup(<RouterModelSelection router={router} canEdit />);
    expect(html).toContain("Decision Model");
    expect(html).toContain("decision-model-preview");
    expect(html).toContain("Edit routing");
    expect(html).toContain("<code>auto</code>");
    expect(html).not.toContain("<select");
    expect(html).not.toContain('role="dialog"');
    expect(html).not.toContain("Model preference order");
  });
  it("does not advertise auto when automatic selection is disabled", () => {
    const html = renderToStaticMarkup(<RouterModelSelection router={{ ...router, strategy: "manual_priority" }} canEdit />);
    expect(html).toContain("Specified models");
    expect(html).not.toContain("Call auto");
    expect(html).not.toContain("decision-model-preview");
  });
  it("does not show a decision service for rule routing", () => {
    const html = renderToStaticMarkup(<RouterModelSelection router={{ ...router, strategy: "task_type" }} canEdit />);
    expect(html).toContain("Rules");
    expect(html).toContain("Call auto");
    expect(html).not.toContain("decision-model-preview");
  });
  it("keeps the policy visible but prevents editing for read-only access", () => {
    const html = renderToStaticMarkup(<RouterModelSelection router={router} canEdit={false} />);
    expect(html).toContain('disabled=""');
    expect(html).toContain("decision-model-preview");
  });
  it("creates an independent draft, drops deleted bindings and preserves disabled roles", () => {
    const draft = savedDecisionSettings(router);
    expect(draft.model_order).toEqual(["strong", "fast"]);
    draft.model_order!.reverse();
    draft.model = "new-model";
    expect(router.decision_config?.model).toBe("decision-model-preview");
    expect(router.decision_config?.model_order).toEqual(["strong", "removed", "fast"]);
  });
  it("supplies valid defaults for a new Router", () => {
    expect(savedDecisionSettings({ ...router, decision_config: undefined, child_bindings: [] })).toEqual({ provider_connection_id: "", model: "", timeout_ms: 8000, model_source: "catalog" });
  });
});
