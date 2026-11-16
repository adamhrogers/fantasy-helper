// A short id of what the app is built from: a hash of its own sources, not the commit. Cloudflare
// rebuilds on every push to main, including pushes that never touch web/, and a commit id in the
// app would make each one look like a new version on the phone ("A new version is ready"). A hash
// of the sources doesn't change then, so the bundle, the service worker and the id all stay put.
import { createHash } from 'node:crypto';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const SINGLE_FILES = ['index.html', 'vite.config.ts', 'package-lock.json', 'headers.template', 'headers.ts'];
const FOLDERS = ['src', 'public'];
/** Tests and their fixtures never reach the bundle, so they don't change the id. */
const NOT_SHIPPED = /(\.test\.[cm]?[jt]sx?$|[\\/]test-[^\\/]*$)/;

function filesIn(dir: string): string[] {
  return readdirSync(dir)
    .sort()
    .flatMap((name) => {
      const path = join(dir, name);
      return statSync(path).isDirectory() ? filesIn(path) : [path];
    });
}

/** `variables` are the build variables: changing one changes the bundle, so it changes the id. */
export function buildId(root: string, variables: Record<string, string> = {}): string {
  const paths = [
    ...FOLDERS.flatMap((folder) => filesIn(join(root, folder))).filter((p) => !NOT_SHIPPED.test(p)),
    ...SINGLE_FILES.map((file) => join(root, file)),
  ];
  const hash = createHash('sha256');
  for (const path of paths) {
    hash.update(relative(root, path)).update('\0').update(readFileSync(path)).update('\0');
  }
  for (const name of Object.keys(variables).sort()) {
    hash.update(name).update('=').update(variables[name] ?? '').update('\0');
  }
  return hash.digest('hex').slice(0, 7);
}
