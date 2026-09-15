import { mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, describe, expect, it } from 'vitest';
import { collectCommunityNotices } from './communityNotices';

const owned: string[] = [];
afterEach(() => { for (const root of owned.splice(0)) rmSync(root, { recursive: true }); });
function fixture() {
  const base = realpathSync(mkdtempSync(join(tmpdir(), 'nexus-notices-'))); owned.push(base);
  const root = join(base, 'node_modules'); mkdirSync(root);
  const packageRoot = join(root, 'example'); mkdirSync(packageRoot);
  writeFileSync(join(packageRoot, 'package.json'), JSON.stringify({ name: 'example', version: '1.2.3', license: 'MIT' }));
  writeFileSync(join(packageRoot, 'index.js'), 'export default 1');
  return { base, root, packageRoot, module: join(packageRoot, 'index.js') };
}
describe('Community third-party notices', () => {
  it('preserves complete upstream license and notice once per package', () => {
    const f = fixture();
    writeFileSync(join(f.packageRoot, 'LICENSE'), 'Copyright example\r\nComplete license\r\n');
    writeFileSync(join(f.packageRoot, 'NOTICE.txt'), 'Upstream attribution');
    const result = collectCommunityNotices(f.root, [f.module, f.module + '?commonjs-proxy', '\0virtual', join(f.base, 'own.ts')]);
    expect(result).toContain(readFileSync(join(f.packageRoot, 'LICENSE'), 'utf8'));
    expect(result).toContain('Upstream attribution');
    expect(result.match(/example@1.2.3/g)).toHaveLength(1);
    expect(result).not.toContain(f.base);
  });
  it('rejects an SPDX-only package instead of silently omitting its notice', () => {
    const f = fixture(); expect(() => collectCommunityNotices(f.root, [f.module])).toThrow('COMMUNITY_NOTICE_MISSING');
  });
  it('rejects empty license files', () => {
    const f = fixture(); writeFileSync(join(f.packageRoot, 'LICENSE'), ' ');
    expect(() => collectCommunityNotices(f.root, [f.module])).toThrow('COMMUNITY_NOTICE_EMPTY');
  });
  it('does not follow a dependency junction outside node_modules', () => {
    const f = fixture(); const external = join(f.base, 'outside'); mkdirSync(external);
    writeFileSync(join(external, 'index.js'), 'private');
    symlinkSync(external, join(f.root, 'escape'), 'junction');
    expect(() => collectCommunityNotices(f.root, [join(f.root, 'escape', 'index.js')])).toThrow('COMMUNITY_NOTICE_PATH_REJECTED');
  });
});
