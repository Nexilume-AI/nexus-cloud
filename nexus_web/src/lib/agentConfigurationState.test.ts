import { afterEach, expect, it } from "vitest";
import { setLocale } from "../localization";
import type { Agent } from "./types";
import { agentNeedsConfiguration } from "./agentConfigurationState";

afterEach(() => setLocale("zh-CN"));

it("keeps configuration decisions identical across language changes", () => {
  const base = { status: "active", allowed_actions: ["configure_runtime"], runtime_status: "not_deployed" };
  const cases = [
    [base, true],
    [{ ...base, runtime_status: "running" }, false],
    [{ ...base, runtime_status: "running", configuration_drift: true }, true],
    [{ ...base, lifecycle_status: "archived" }, false],
    [{ ...base, status: "disabled" }, false],
    [{ ...base, allowed_actions: [] }, false],
    [{ ...base, allowed_actions: undefined }, false],
  ] as const;
  for (const locale of ["en-US", "zh-CN", "en-US"] as const) {
    setLocale(locale);
    for (const [agent, expected] of cases) {
      expect(agentNeedsConfiguration(agent as Agent)).toBe(expected);
    }
  }
});
