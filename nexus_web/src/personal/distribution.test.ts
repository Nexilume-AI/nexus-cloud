import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { personalDistribution } from "./application";
import { personalContextDirectory } from "./contextDirectory";
import { isPrivateWebModule, hasPrivateApiReference } from "../../tooling/communityBoundary";
import communityConfig from "../../vite.community.config";

afterEach(() => vi.unstubAllGlobals());
describe("Community build-selected Web host", () => {
  it("has no commercial extensions or private navigation", () => {
    expect(personalDistribution.id).toBe('community');
    expect(personalDistribution.contextDirectory).toBe(personalContextDirectory);
    for (const name of ['resourcePublishing', 'resourceSharing', 'dataLibrary', 'organizationSettings', 'contextSwitcher', 'datasetImagePreview', 'runPresentation', 'agentDiscovery']) {
      expect(personalDistribution).not.toHaveProperty(name);
    }
    expect(personalDistribution.workspaceRoutes).toEqual([]);
    const destinations = personalDistribution.navigation.flatMap(domain => domain.lanes.flatMap(lane => lane.items.map(item => item.to)));
    for (const path of ['/agents', '/remote-workspaces', '/providers', '/routers', '/data-assets', '/settings']) expect(destinations).toContain(path);
    expect(destinations.some(path => /^\/(access|billing|tokenbank|marketplace)/.test(path))).toBe(false);
  });
  it("uses the real personal context endpoint and propagates failures", async () => {
    const fetcher = vi.fn(async (_path: RequestInfo | URL) => Response.json({ tenants: [{ id: 'owner' }], projects: [{ id: 'project' }] }));
    vi.stubGlobal('fetch', fetcher);
    expect(await personalContextDirectory.tenants({})).toEqual([{ id: 'owner' }]);
    expect(await personalContextDirectory.projects({ tenantId: 'owner' })).toEqual([{ id: 'project' }]);
    expect(fetcher.mock.calls.every(([path]) => path === '/api/v1/personal/context/')).toBe(true);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 403 })));
    await expect(personalContextDirectory.tenants({})).rejects.toThrow();
  });
  it("rejects private module paths including lazy imports and escaped API paths", () => {
    for (const path of ['/repo/src/enterprise/application.tsx', 'C:\\repo\\src\\enterprise\\pages\\BillingPage.tsx?x', '/src/main.tsx', '/src/pages/tokenbank/index.ts']) expect(isPrivateWebModule(path)).toBe(true);
    expect(isPrivateWebModule('/src/personal/main.tsx')).toBe(false);
    for (const path of ['/api/v1/access/grants/', '/api/v1/billing/plans/', String.raw`\/api\/v1\/marketplace\/datasets`, String.raw`/api/v1/(datasets|dataset-acquisitions)`]) expect(hasPrivateApiReference(path)).toBe(true);
    expect(hasPrivateApiReference('/api/v1/personal/context/')).toBe(false);
  });
  it.skipIf(!existsSync(new URL("../../vite.config.ts", import.meta.url)))("preserves the Enterprise build when its source is present", () => {
    const current = readFileSync(new URL('../../vite.config.ts', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
    const helper = readFileSync(new URL('../../tooling/pdfAssets.ts', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
    const source = [
      'import { defineConfig, type Plugin } from "vite";',
      'import { readFileSync, readdirSync } from "node:fs";',
      'import { dirname, join } from "node:path";',
      'import { createRequire } from "node:module";',
      'import react from "@vitejs/plugin-react";', '',
      helper.slice(helper.indexOf('// Keep font data')).replace('export function pdfFontAssets()', 'function pdfFontAssets()'),
      current.slice(current.indexOf('export default')),
    ].join('\n');
    expect(createHash('sha256').update(source).digest('hex')).toBe('95098d1a7381f2f33723a942eb272736933a3cb818ab2be7168262599b02ae29');
  });
  it("builds Community without proxying the Enterprise development API", async () => {
    if (typeof communityConfig !== 'function') throw new Error('Expected explicit build factory');
    const built = await communityConfig({ command: 'build', mode: 'production' });
    expect(built.build?.outDir?.replaceAll('\\', '/')).toMatch(/\/nexus_web\/dist\/community$/);
    expect(built.server).toBeUndefined();
    expect(() => communityConfig({ command: 'serve', mode: 'development' })).toThrow('does not proxy');
  });
});
