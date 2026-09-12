import { describe, expect, it } from 'vitest';
import { privateDisplayReturnTo, privateDisplayAttachmentUrl } from './agentDisplayNavigation';

describe('personal display return navigation', () => {
  it('returns to the owner launch surface without saved state', () => {
    expect(privateDisplayReturnTo('echo', null)).toBe('/agents/echo/publish');
    expect(privateDisplayReturnTo('', null)).toBe('/agents');
  });
  it('preserves launch filters and removes consumed device intent', () => {
    const state={returnTo:'/agents/echo/publish?q=local&attach=mobile&return=private-display#tools'};
    expect(privateDisplayReturnTo('echo',state)).toBe('/agents/echo/publish?q=local#tools');
    expect(privateDisplayAttachmentUrl('echo',state,'computer')).toBe('/agents/echo/publish?q=local&attach=computer&return=private-display#tools');
  });
  it('rejects missing, external, cross-Agent and non-launch destinations', () => {
    for (const returnTo of [undefined,'https://other.example/','//other.example/','/\\other.example/',
      '/agents/other/publish','/agents/echo/settings','/agents/echo/private-display','/settings','/agents/echo/publish\n']) {
      expect(privateDisplayReturnTo('echo',{returnTo})).toBe('/agents/echo/publish');
    }
  });
});
