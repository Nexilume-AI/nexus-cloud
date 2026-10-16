import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const panel = readFileSync(new URL("./AgentConnectPanel.tsx", import.meta.url), "utf8");
describe("Personal Agent connection boundary", () => {
  it("loads only canonical connection data, not enterprise credential APIs", () => {
    expect(panel).toContain("api.exportAgentMcp(apiContext, agentId)");
    expect(panel).not.toMatch(/AgentAPIAccessPanel|createAgentKey|getAgentAPIAccess|updateAgentAPIAccess/);
    expect(panel).toContain("Personal Tool Setup credential");
    expect(panel).toContain('to="/remote-workspaces"');
  });
  it("discards pending configuration responses when caller or scope changes", () => {
    expect(panel).toContain("props.apiContext.token");
    expect(panel).toContain("props.apiContext.tenantId");
    expect(panel).toContain("props.apiContext.projectId");
    expect(panel).toContain("return () => { current = false; }");
  });
  it("both Agent entry points use the shared Personal panel", () => {
    for (const page of ["AgentControlPage", "AgentsPage"]) {
      const source = readFileSync(new URL(`../pages/${page}.tsx`, import.meta.url), "utf8");
      expect(source).toContain("<AgentConnectPanel");
      expect(source).not.toContain("api.createAgentKey(");
    }
  });
});
