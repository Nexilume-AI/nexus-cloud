import { describe, expect, it } from "vitest";
import { runDisplayPolling } from "./runDisplayPolling";

describe("Private Display demand-aware polling", () => {
  const input = { attached: true, shell: false, files: false };
  it("does not poll an unopened Shell and slows auxiliary surfaces", () => {
    const policy = runDisplayPolling({ ...input, status: "running", ready: true });
    expect(policy.terminal).toBe(false);
    expect(policy.run).toBe(5000);
    expect(policy.readiness).toBe(30000);
    expect(policy.history).toBe(30000);
    expect(policy.outputs).toBe(30000);
  });
  it("keeps failure recovery automatic", () => {
    expect(runDisplayPolling({ ...input, status: "failed" }).readiness).toBe(5000);
    expect(runDisplayPolling({ ...input, status: "completed", ready: false }).readiness).toBe(5000);
  });
  it("polls visible panels while a run is active", () => {
    const policy = runDisplayPolling({ ...input, status: "input_required", shell: true, files: true });
    expect(policy.terminal).toBe(3000);
    expect(policy.outputs).toBe(5000);
  });
  it.each(["completed", "failed", "cancelled", "expired"])("stops settled output polling for %s but waits for pending scans", status => {
    expect(runDisplayPolling({ ...input, status }).outputs).toBe(false);
    expect(runDisplayPolling({ ...input, status, pendingOutputs: true }).outputs).toBe(5000);
  });
});
