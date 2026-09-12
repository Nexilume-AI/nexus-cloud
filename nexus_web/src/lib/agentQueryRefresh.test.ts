import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshAgentQueries, type AgentRefreshTarget } from "./agentQueryRefresh";

afterEach(() => vi.restoreAllMocks());
describe("operational Agent cache refresh", () => {
  it.each([
    [{ surface: "settings" }, [["agents"]]],
    [{ surface: "runtime" }, [["agents"], ["agent-runtime-status"]]],
    [{ surface: "control", agentId: "agent-a" }, [["agent"], ["agents"], ["agent-runtime-status"], ["agent-mobile-grant", "agent-a"], ["agent-interactor"]]],
  ] as const)("invalidates only the intended %j operational queries", async (target, expected) => {
    const client = new QueryClient();
    const keys = [["agents", { projectId: "p" }], ["agent", "agent-a"], ["agent-runtime-status", "agent-a"],
      ["agent-mobile-grant", "agent-a"], ["agent-mobile-grant", "agent-b"], ["agent-interactor", "agent-a"], ["unrelated"]];
    for (const key of keys) client.setQueryData(key, { existing: true });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await refreshAgentQueries(client, target);
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual(expected);
    for (const key of keys) {
      const matches = expected.some(prefix => prefix.every((value, i) => key[i] === value));
      expect(client.getQueryState(key)?.isInvalidated).toBe(matches);
      expect(client.getQueryData(key)).toEqual({ existing: true });
    }
    client.clear();
  });

  it("waits for every invalidation before the active control refetch and awaits that refetch", async () => {
    const client = new QueryClient();
    let resolveInvalidation!: () => void, resolveRefetch!: () => void;
    const pending = new Promise<void>(resolve => { resolveInvalidation = resolve; });
    const pendingRefetch = new Promise<void>(resolve => { resolveRefetch = resolve; });
    const invalidate = vi.spyOn(client, "invalidateQueries").mockReturnValue(pending);
    const refetch = vi.spyOn(client, "refetchQueries").mockReturnValue(pendingRefetch);
    let completed = false;
    const refresh = refreshAgentQueries(client, { surface: "control", agentId: "a" }).then(() => { completed = true; });
    expect(invalidate).toHaveBeenCalledTimes(5);
    expect(refetch).not.toHaveBeenCalled();
    resolveInvalidation();
    await vi.waitFor(() => expect(refetch).toHaveBeenCalledWith({ queryKey: ["agent"], type: "active" }));
    expect(completed).toBe(false);
    resolveRefetch();
    await refresh;
    expect(completed).toBe(true);
  });

  it("updates subscribed Agent data but never fetches an inactive Agent detail", async () => {
    const client = new QueryClient();
    const queryKey = ["agent", "a"];
    client.setQueryData(queryKey, { revision: 0 });
    client.setQueryData(["agent", "b"], { revision: 0 });
    let revision = 0;
    const queryFn = vi.fn(async () => ({ revision: ++revision }));
    const observer = new QueryObserver(client, { queryKey, queryFn, staleTime: Infinity });
    const observed: number[] = [];
    const unsubscribe = observer.subscribe(result => { if (result.data) observed.push(result.data.revision); });
    try {
      expect(queryFn).not.toHaveBeenCalled();
      await refreshAgentQueries(client, { surface: "control", agentId: "a" });
      // The original control flow invalidated active detail then explicitly refetched it.
      expect(queryFn).toHaveBeenCalledTimes(2);
      expect(client.getQueryData(queryKey)).toEqual({ revision: 2 });
      expect(observed).toContain(2);
      expect(client.getQueryData(["agent", "b"])).toEqual({ revision: 0 });
      expect(client.getQueryState(["agent", "b"])?.isInvalidated).toBe(true);
    } finally {
      unsubscribe();
      client.clear();
    }
  });

  it.each(["invalidate", "refetch"])("propagates %s failures without retrying the mutation", async failure => {
    const client = new QueryClient();
    const error = new Error("Refresh unavailable");
    const invalidate = vi.spyOn(client, "invalidateQueries").mockImplementation(() => failure === "invalidate" ? Promise.reject(error) : Promise.resolve());
    const refetch = vi.spyOn(client, "refetchQueries").mockRejectedValue(error);
    await expect(refreshAgentQueries(client, { surface: "control", agentId: "a" })).rejects.toBe(error);
    expect(invalidate).toHaveBeenCalledTimes(5);
    expect(refetch).toHaveBeenCalledTimes(failure === "invalidate" ? 0 : 1);
  });

  it("does not mutate extension keys or start the control refetch for inventory surfaces", async () => {
    const client = new QueryClient();
    const extra = Object.freeze([Object.freeze(["optional-extension", "a"])]);
    const invalidate = vi.spyOn(client, "invalidateQueries").mockResolvedValue();
    const refetch = vi.spyOn(client, "refetchQueries").mockResolvedValue();
    for (const surface of ["runtime", "settings"] as const) {
      await refreshAgentQueries(client, { surface }, extra);
      expect(invalidate).toHaveBeenLastCalledWith({ queryKey: extra[0] });
    }
    expect(refetch).not.toHaveBeenCalled();
    expect(extra).toEqual([["optional-extension", "a"]]);
    const target: AgentRefreshTarget = { surface: "control", agentId: "a" };
    expect(target.agentId).toBe("a");
  });
});
