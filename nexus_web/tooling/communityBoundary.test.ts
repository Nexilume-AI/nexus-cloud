import { describe, expect, it } from "vitest";
import { communityBoundary, hasPrivateStylesheetReference } from "./communityBoundary";

describe("Community stylesheet build boundary", () => {
  it.each([
    '.tokenbank-terminal { color:red; }', '.marketplace-object-row:hover {}', '.provider-publish-review {}',
    '.sidebar-track__link--marketplace {}', String.raw`.\74 okenbank-terminal {}`,
    String.raw`.market\70 lace-row {}`, 'a { background: url(/api/v1/billing/secret); }',
    '.market\\70\r\nlace-row {}', '.market\\\nplace-row {}',
  ])("rejects proprietary CSS even behind escapes: %s", css => {
    expect(hasPrivateStylesheetReference(css)).toBe(true);
  });
  it("preserves generic previews and personal device styling", () => {
    expect(hasPrivateStylesheetReference('.content-markdown code { color:var(--ink); } .computer-workspace {}')).toBe(false);
  });
  it.each([false, true])("rejects injected CSS assets before producing an entry (bytes=%s)", bytes => {
    const hook = communityBoundary().generateBundle;
    if (!hook || typeof hook !== 'object') throw new Error('Expected post-build boundary');
    const bundle = {
      'personal.html': { type: 'asset', fileName: 'personal.html', source: '<div></div>' },
      'assets/theme.css': { type: 'asset', fileName: 'assets/theme.css', source: bytes ? new TextEncoder().encode('.marketplace-row {}') : '.marketplace-row {}' },
    };
    const context = { getModuleIds: () => [], error: (message: string) => { throw new Error(message); } };
    expect(() => hook.handler.call(context as never, {} as never, bundle as never, false)).toThrow('COMMUNITY_PRIVATE_STYLESHEET: assets/theme.css');
    expect(bundle).toHaveProperty('personal.html');
  });
});
