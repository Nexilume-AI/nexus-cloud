import { existsSync, lstatSync, mkdtempSync, mkdirSync, readdirSync, readFileSync, rmdirSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { afterEach, expect, it } from 'vitest';
import { clearCommunityBuildOutput, communityAssets, communityManifestName, writeCommunityAssetManifest } from '../../tooling/communityAssets';

const roots: string[] = [];
const expected = new Set(['index.html','brand/icon.svg']);
function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'nexus-community-manifest-'));
  roots.push(root);
  writeFileSync(join(root, 'index.html'), '<html>Community</html>');
  mkdirSync(join(root, 'brand'));
  writeFileSync(join(root, 'brand/icon.svg'), '<svg/>');
  return root;
}
function removeFixture(path: string) {
  const stat = lstatSync(path);
  if (stat.isSymbolicLink()) { if (process.platform === 'win32') rmdirSync(path); else unlinkSync(path); }
  else if (stat.isDirectory()) { for (const name of readdirSync(path)) removeFixture(join(path,name)); rmdirSync(path); }
  else unlinkSync(path);
}
afterEach(() => { for (const root of roots.splice(0)) { expect(root.startsWith(join(tmpdir(),'nexus-community-manifest-'))).toBe(true); removeFixture(root); expect(existsSync(root)).toBe(false); } });
it('inventories emitted and copied public assets with exact sizes and hashes', () => {
  const root = fixture();
  const files = writeCommunityAssetManifest(root, expected);
  expect(files['brand/icon.svg']).toEqual({ size: 6, sha256: createHash('sha256').update('<svg/>').digest('hex') });
  expect(JSON.parse(readFileSync(join(root, communityManifestName), 'utf8'))).toEqual({ schema_version: 1, distribution: 'community', files });
  expect(writeCommunityAssetManifest(root, expected)).toEqual(files);
});
it('rejects source maps and hidden files rather than publishing them', () => {
  for (const name of ['app.js.map', '.env']) {
    const root = fixture();
    writeFileSync(join(root, name), 'must not be served');
    expect(() => writeCommunityAssetManifest(root, expected)).toThrow('COMMUNITY_ASSET');
  }
});
it('requires the emitted Community entry', () => {
  const root = fixture();
  unlinkSync(join(root, 'index.html'));
  expect(() => writeCommunityAssetManifest(root, expected)).toThrow('COMMUNITY_ENTRY_MISSING');
});
it('does not emit a success inventory from the build-failure cleanup hook', () => {
  const plugin = communityAssets();
  expect(plugin.writeBundle).toBeTypeOf('function');
  expect(plugin.closeBundle).toBeUndefined();
});

it('clears nested prior outputs before building and rejects a different output target', () => {
  const project = mkdtempSync(join(tmpdir(), 'nexus-community-manifest-'));
  roots.push(project);
  const output = join(project, 'dist/community');
  mkdirSync(join(output, 'assets'), {recursive:true});
  writeFileSync(join(output, 'assets/old-private.js'), 'private stale implementation');
  writeFileSync(join(output, communityManifestName), 'old manifest');
  writeFileSync(join(project, 'preserve.txt'), 'source');
  expect(() => clearCommunityBuildOutput(project, project)).toThrow('COMMUNITY_OUTPUT');
  expect(existsSync(join(output, 'assets/old-private.js'))).toBe(true);
  clearCommunityBuildOutput(project, output);
  expect(existsSync(join(output, 'assets/old-private.js'))).toBe(false);
  expect(existsSync(join(output, communityManifestName))).toBe(false);
  expect(readFileSync(join(project, 'preserve.txt'), 'utf8')).toBe('source');
  clearCommunityBuildOutput(project, output);
});

it('never blesses leftover files outside the current bundle and public assets', () => {
  const root = fixture();
  writeCommunityAssetManifest(root, expected);
  writeFileSync(join(root, 'old-private.js'), 'stale private implementation');
  expect(() => writeCommunityAssetManifest(root, new Set(['index.html','brand/icon.svg']))).toThrow('COMMUNITY_ASSET_UNEXPECTED');
  expect(existsSync(join(root, communityManifestName))).toBe(false);
});

it('preflights nested directory links before deleting any existing artifacts', () => {
  const project = mkdtempSync(join(tmpdir(),'nexus-community-manifest-'));
  roots.push(project);
  const output = join(project,'dist/community');
  mkdirSync(output,{recursive:true});
  const outside = join(project,'outside');
  mkdirSync(outside);
  writeFileSync(join(outside,'preserve.txt'),'keep');
  writeFileSync(join(output,'first.js'),'keep until safe');
  symlinkSync(outside,join(output,'linked'),process.platform === 'win32' ? 'junction' : 'dir');
  expect(() => clearCommunityBuildOutput(project,output)).toThrow('COMMUNITY_OUTPUT_LINK_REJECTED');
  expect(readFileSync(join(output,'first.js'),'utf8')).toBe('keep until safe');
  expect(readFileSync(join(outside,'preserve.txt'),'utf8')).toBe('keep');
});

it('rejects missing current assets rather than writing an incomplete manifest', () => {
  const root = fixture();
  expect(() => writeCommunityAssetManifest(root,new Set([...expected,'missing.js']))).toThrow('COMMUNITY_ASSET_MISSING');
  expect(existsSync(join(root,communityManifestName))).toBe(false);
});
