import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelCapabilitiesEditor } from "./ModelCapabilities";
import { capabilityDraftError, effortInput, type ModelCapabilities } from "../lib/modelCapabilities";

const unknown = { context_window: null, max_input_tokens: null, max_output_tokens: null, supports_reasoning: null,
  reasoning_effort_levels: null, default_reasoning_effort: null, supported_openai_params: null };
const data: ModelCapabilities = { override: null, model_info: unknown, inherited_model_info: unknown, fields: {}, inherited_fields: {}, can_edit: true };
const render = (value = data) => renderToStaticMarkup(<ModelCapabilitiesEditor data={value} provider busy={false} saved={false} onSave={() => {}} onRefresh={() => {}} onReload={() => {}} />);

describe("Model capability configuration", () => {
  it("keeps unknown metadata unknown without suggesting invented choices", () => {
    const html = render();
    expect(html).toContain("Upstream has not reported selectable levels.");
    expect(html).toContain("Service default");
    expect(html).not.toContain('value="high"');
  });
  it("distinguishes false, explicit none and automatic inheritance", () => {
    const info = { ...unknown, supports_reasoning: true, reasoning_effort_levels: ["none", "ultra"], default_reasoning_effort: "none" };
    const html = render({ ...data, model_info: info, inherited_model_info: info, override: { reasoning_effort_levels: ["none", "ultra"], default_reasoning_effort: "none" } });
    expect(html).toContain('value="none" selected=""');
    expect(html).toContain('value="ultra"');
    expect(render({ ...data, model_info: { ...unknown, supports_reasoning: false, reasoning_effort_levels: [] } })).toContain("No selectable reasoning levels.");
    expect(effortInput("none, low low, ultra")).toEqual(["none", "low", "ultra"]);
    expect(effortInput(" ")).toBeUndefined();
  });
  it("does not expose mutation controls for read-only resources", () => {
    const html = render({ ...data, can_edit: false, override: { supports_reasoning: false } });
    expect(html).not.toContain("Save capabilities");
    expect(html).not.toContain("Override model capabilities");
    expect(html).not.toContain("Restore automatic discovery");
  });
  it("validates explicit lists and capacity overrides without constraining future API enums", () => {
    expect(capabilityDraftError({ reasoning_effort_levels: ["none", "ultra"], default_reasoning_effort: "ultra" })).toBeNull();
    expect(capabilityDraftError({ reasoning_effort_levels: ["low"], default_reasoning_effort: "high" })).toContain("default");
    expect(capabilityDraftError({ supports_reasoning: false, reasoning_effort_levels: ["low"] })).toContain("Disabled");
    expect(capabilityDraftError({ max_output_tokens: 2.5 })).toContain("whole");
    expect(capabilityDraftError({ reasoning_effort_levels: ["bad value"] })).toContain("short");
  });
});
