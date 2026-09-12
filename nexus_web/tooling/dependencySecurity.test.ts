import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {dirname, resolve} from 'node:path';
import {describe, expect, it} from 'vitest';

const root = new URL('../', import.meta.url);
const require = createRequire(new URL('../package.json', import.meta.url));

describe('reviewed dependency security floors', () => {
  it('keeps the patched sanitizer in both the lock and the actual editor dependency', () => {
    const manifest = JSON.parse(readFileSync(new URL('package.json', root), 'utf8'));
    const lock = JSON.parse(readFileSync(new URL('package-lock.json', root), 'utf8'));
    expect(manifest.overrides['monaco-editor'].dompurify).toBe('3.4.15');
    const editorRequire = createRequire(require.resolve('monaco-editor'));
    const sanitizer = editorRequire.resolve('dompurify');
    const installed = JSON.parse(readFileSync(resolve(dirname(sanitizer), '../package.json'), 'utf8'));
    expect(installed.name).toBe('dompurify');
    expect(installed.version).toBe('3.4.15');
    for (const [path, pkg] of Object.entries(lock.packages) as [string, {version?: string}][]) {
      if (path.endsWith('/dompurify')) expect(pkg.version).toBe('3.4.15');
    }
  });

  it('does not restore the vulnerable direct dependency ranges', () => {
    const manifest = JSON.parse(readFileSync(new URL('package.json', root), 'utf8'));
    expect(manifest.dependencies['react-router-dom']).toBe('^7.18.3');
    expect(manifest.devDependencies.postcss).toBe('^8.5.28');
    expect(manifest.devDependencies.vitest).toBe('^4.1.11');
  });
});
