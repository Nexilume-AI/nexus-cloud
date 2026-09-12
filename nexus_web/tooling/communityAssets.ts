import { createHash } from "node:crypto";
import { existsSync, lstatSync, readdirSync, readFileSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import type { Plugin } from "vite";

export const communityManifestName = "community-assets.json";

/** Only this owned artifact directory may be cleared, never an arbitrary outDir.
 * Preflight the entire tree before unlinking; do not follow junctions/symlinks.
 * Explicit unlink/rmdir plus a postcondition avoids silent recursive-rm no-ops.
 */
export function clearCommunityBuildOutput(projectRoot: string, outputRoot: string) {
  const project = resolve(projectRoot);
  const output = resolve(outputRoot);
  if (output !== resolve(project, 'dist/community')) throw new Error('COMMUNITY_OUTPUT_SCOPE_INVALID');
  for (const path of [project, resolve(project,'dist'), output]) {
    if (existsSync(path)) {
      const stat = lstatSync(path);
      if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error('COMMUNITY_OUTPUT_LINK_REJECTED');
    }
  }
  if (!existsSync(output)) return;
  const files: string[] = [];
  const directories: string[] = [];
  function collect(path: string) {
    if (files.length + directories.length > 20000) throw new Error('COMMUNITY_OUTPUT_LIMIT_EXCEEDED');
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) throw new Error('COMMUNITY_OUTPUT_LINK_REJECTED');
    if (stat.isDirectory()) {
      directories.push(path);
      for (const name of readdirSync(path)) collect(join(path,name));
    } else if (stat.isFile()) files.push(path);
    else throw new Error('COMMUNITY_OUTPUT_TYPE_INVALID');
  }
  for (const name of readdirSync(output)) collect(join(output,name));
  for (const path of files) unlinkSync(path);
  for (const path of directories.reverse()) rmdirSync(path);
  if (readdirSync(output).length) throw new Error('COMMUNITY_OUTPUT_CLEAR_FAILED');
}

// Inventory of the built artifact, not a signature or source-release approval.
export function writeCommunityAssetManifest(root: string, expected: ReadonlySet<string>) {
  // A failed attempt must not leave a previous successful inventory usable.
  const manifest = join(root, communityManifestName);
  if (existsSync(manifest)) {
    if (!lstatSync(manifest).isFile() || lstatSync(manifest).isSymbolicLink()) throw new Error('COMMUNITY_ASSET_LINK_REJECTED');
    unlinkSync(manifest);
  }
  const files: Record<string, { size: number; sha256: string }> = {};
  let total = 0;
  function walk(relative: string) {
    for (const name of readdirSync(join(root, relative)).sort()) {
      if (!relative && name === communityManifestName) continue;
      if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]*$/.test(name)) throw new Error("COMMUNITY_ASSET_PATH_INVALID");
      const key = relative ? `${relative}/${name}` : name;
      const stat = lstatSync(join(root, key));
      if (stat.isSymbolicLink()) throw new Error("COMMUNITY_ASSET_LINK_REJECTED");
      if (stat.isDirectory()) { walk(key); continue; }
      if (!stat.isFile() || stat.size > 32 * 1024 ** 2 || key.endsWith('.map')) throw new Error("COMMUNITY_ASSET_INVALID");
      if (!expected.has(key)) throw new Error('COMMUNITY_ASSET_UNEXPECTED');
      total += stat.size;
      if (total > 64 * 1024 ** 2 || Object.keys(files).length >= 4096) throw new Error("COMMUNITY_ASSET_LIMIT_EXCEEDED");
      const body = readFileSync(join(root, key));
      files[key] = { size: body.length, sha256: createHash('sha256').update(body).digest('hex') };
    }
  }
  walk('');
  if (!files['index.html']) throw new Error("COMMUNITY_ENTRY_MISSING");
  if (Object.keys(files).length !== expected.size) throw new Error('COMMUNITY_ASSET_MISSING');
  writeFileSync(join(root, communityManifestName), JSON.stringify({ schema_version: 1, distribution: 'community', files }) + '\n');
  return files;
}

export function communityAssets(): Plugin {
  let root: string;
  let project: string;
  let publicDir: string | false;
  return {
    name: 'community-assets-inventory',
    configResolved(config) {
      project = config.root;
      root = resolve(config.root, config.build.outDir);
      publicDir = config.build.copyPublicDir ? config.publicDir : false;
    },
    buildStart() { clearCommunityBuildOutput(project, root); },
    // Only runs after a successful generate/write, including boundary checks.
    // closeBundle also runs on build failure and must not bless stale output.
    writeBundle(_options, bundle) {
      const expected = new Set(Object.values(bundle).map(file => file.fileName));
      function publicFiles(path: string, relative: string) {
        const stat = lstatSync(path);
        if (stat.isSymbolicLink()) throw new Error('COMMUNITY_ASSET_LINK_REJECTED');
        if (stat.isDirectory()) for (const name of readdirSync(path)) publicFiles(join(path,name), relative ? `${relative}/${name}` : name);
        else if (stat.isFile() && relative !== communityManifestName) expected.add(relative);
        else throw new Error('COMMUNITY_ASSET_INVALID');
      }
      if (publicDir && existsSync(publicDir)) publicFiles(publicDir,'');
      writeCommunityAssetManifest(root, expected);
    },
  };
}
