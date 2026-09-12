import { describe, expect, it } from "vitest";
import { composeApplicationRoutes, validateApplicationDistribution, type ApplicationDistribution } from "./distribution";

const base = [{ path: "/agents/:agentId/:section?", element: null }, { path: "*", element: null }];
const valid: ApplicationDistribution = {
  id: "test", workspaceRoutes: [], standaloneRoutes: [], navigation: [], guestOverview: null,
};

describe("explicit application composition", () => {
  it("requires an explicit distribution and never falls back to personal access", () => {
    expect(() => validateApplicationDistribution(undefined as unknown as ApplicationDistribution)).toThrow();
    expect(() => validateApplicationDistribution({ id: "test" } as ApplicationDistribution)).toThrow();
    expect(validateApplicationDistribution(valid)).toBe(valid);
  });

  it("inserts before a stable anchor without mutating either input", () => {
    const extra = [{ before: "*", routes: [{ path: "/extra", element: "extension" }] }];
    const before = JSON.stringify({ base, extra });
    const result = composeApplicationRoutes(base, extra);
    expect(result.map((item) => item.path)).toEqual(["/agents/:agentId/:section?", "/extra", "*"]);
    expect(JSON.stringify({ base, extra })).toBe(before);
    result[0].path = "/changed";
    expect(base[0].path).toBe("/agents/:agentId/:section?");
  });

  it("rejects missing anchors and empty registrations", () => {
    expect(() => composeApplicationRoutes(base, [{ before: "/missing", routes: [{ path: "/x", element: null }] }])).toThrow(/anchor/);
    expect(() => composeApplicationRoutes(base, [{ before: "*", routes: [] }])).toThrow(/Empty/);
  });

  it("rejects duplicate paths including case, trailing slash and renamed parameters", () => {
    for (const path of ["/AGENTS/:agentId/:section?", "/agents/:other/:tab?/", "*"]) {
      expect(() => composeApplicationRoutes(base, [{ before: "*", routes: [{ path, element: null }] }])).toThrow(/Duplicate/);
    }
    expect(() => composeApplicationRoutes([...base, base[0]], [])).toThrow(/Duplicate/);
  });

  it("accepts optional route parameters but rejects query strings and external URLs", () => {
    expect(composeApplicationRoutes(base, [])).toHaveLength(2);
    for (const path of ["", "https://example.com", "/x?q=1", "/x#hash", "//x"]) {
      expect(() => composeApplicationRoutes([{ path, element: null }], [])).toThrow(/Invalid/);
    }
  });

  it("rejects duplicate navigation destinations and domains", () => {
    const domain = { id: "build", lanes: [{ items: [{ to: "/agents" }] }] };
    expect(() => validateApplicationDistribution({ ...valid, navigation: [domain, domain] } as ApplicationDistribution)).toThrow(/Duplicate/);
    expect(() => validateApplicationDistribution({ ...valid, navigation: [
      domain, { ...domain, id: "operate" },
    ] } as ApplicationDistribution)).toThrow(/Duplicate/);
  });
});
