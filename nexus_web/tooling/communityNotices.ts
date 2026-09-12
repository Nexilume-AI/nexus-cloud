import { existsSync, readFileSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { createRequire } from 'node:module';
import type { Plugin } from 'vite';

const noticeName = /^(?:licen[cs]e|copying|notice|copyright)(?:[._-].*)?$/i;
const inside = (root: string, path: string) => {
  const value = relative(root, path);
  return value !== '..' && !value.startsWith('..' + (process.platform === 'win32' ? '\\' : '/')) && !isAbsolute(value);
};

/** Preserve upstream text verbatim; SPDX labels alone are not license notices.
 * Never execute package scripts or read outside the installed dependency tree.
 */
export function collectCommunityNotices(dependencyRoot: string, moduleIds: Iterable<string>) {
  const root = realpathSync(dependencyRoot);
  const packages = new Map<string, string>();
  for (const id of moduleIds) {
    if (id.includes('\0') || !isAbsolute(id.split('?')[0])) continue;
    const file = resolve(id.split('?')[0]);
    if (!inside(root, file)) continue;
    if (!inside(root, realpathSync(file))) throw new Error('COMMUNITY_NOTICE_PATH_REJECTED');
    let directory = statSync(file).isDirectory() ? file : dirname(file);
    while (directory !== root && !existsSync(join(directory, 'package.json'))) directory = dirname(directory);
    if (directory === root) throw new Error('COMMUNITY_NOTICE_PACKAGE_MISSING');
    if (packages.has(directory)) continue;
    const manifest = join(directory, 'package.json');
    if (!inside(root, realpathSync(manifest))) throw new Error('COMMUNITY_NOTICE_PATH_REJECTED');
    const metadata = JSON.parse(readFileSync(manifest, 'utf8')) as { name?: unknown; version?: unknown };
    if (typeof metadata.name !== 'string' || typeof metadata.version !== 'string') throw new Error('COMMUNITY_NOTICE_IDENTITY_MISSING');
    const names = readdirSync(directory).filter(name => noticeName.test(name)).sort();
    const texts: string[] = [];
    for (const name of names) {
      const path = join(directory, name);
      if (!inside(root, realpathSync(path))) throw new Error('COMMUNITY_NOTICE_PATH_REJECTED');
      const stat = statSync(path);
      if (!stat.isFile() || stat.size > 2 * 1024 ** 2) throw new Error('COMMUNITY_NOTICE_FILE_INVALID');
      const text = readFileSync(path, 'utf8');
      if (!text.trim()) throw new Error('COMMUNITY_NOTICE_EMPTY');
      texts.push(`${name}\n${text}`);
    }
    if (!texts.length) throw new Error(`COMMUNITY_NOTICE_MISSING: ${metadata.name}`);
    packages.set(directory, `${metadata.name}@${metadata.version}\n${texts.join('\n\n')}`);
  }
  if (!packages.size) throw new Error('COMMUNITY_NOTICES_EMPTY');
  return 'Third-party notices for this Nexus Community Web build\n'
    + 'Third-party components retain their respective licenses and copyrights.\n\n'
    + [...packages.values()].sort().join('\n\n' + '='.repeat(72) + '\n\n') + '\n';
}

export function communityNotices(): Plugin {
  let root: string;
  return {
    name: 'community-third-party-notices',
    configResolved(config) { root = config.root; },
    generateBundle(_options, bundle) {
      const modules = Object.values(bundle).flatMap(file => file.type === 'chunk' ? Object.keys(file.modules) : []);
      // pdfFontAssets also copies binary assets; it may not appear in chunk.modules.
      modules.push(createRequire(join(root, 'package.json')).resolve('pdfjs-dist/package.json'));
      this.emitFile({ type: 'asset', fileName: 'THIRD_PARTY_NOTICES.txt',
        source: collectCommunityNotices(join(root, 'node_modules'), modules) });
    },
  };
}
