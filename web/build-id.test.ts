import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { buildId } from './build-id';

let root: string;

function write(path: string, text: string) {
  const full = join(root, path);
  mkdirSync(join(full, '..'), { recursive: true });
  writeFileSync(full, text);
}

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'build-id-'));
  write('src/App.tsx', 'app');
  write('src/App.test.tsx', 'test');
  write('src/test-fixtures.ts', 'fixtures');
  write('public/favicon.svg', 'icon');
  write('index.html', 'html');
  write('vite.config.ts', 'config');
  write('package-lock.json', '{}');
});

afterEach(() => rmSync(root, { recursive: true, force: true }));

describe('buildId', () => {
  it('is seven hex digits and the same for the same sources', () => {
    expect(buildId(root)).toMatch(/^[0-9a-f]{7}$/);
    expect(buildId(root)).toBe(buildId(root));
  });

  it('changes when a file that ships changes', () => {
    const before = buildId(root);
    write('src/App.tsx', 'changed');
    expect(buildId(root)).not.toBe(before);
  });

  it('changes when a dependency changes, or an icon', () => {
    const before = buildId(root);
    write('package-lock.json', '{"changed":true}');
    const afterLock = buildId(root);
    expect(afterLock).not.toBe(before);
    write('public/favicon.svg', 'new icon');
    expect(buildId(root)).not.toBe(afterLock);
  });

  it('does not change for tests or their fixtures, which never ship', () => {
    const before = buildId(root);
    write('src/App.test.tsx', 'a different test');
    write('src/test-fixtures.ts', 'different fixtures');
    write('src/new.test.ts', 'another test');
    expect(buildId(root)).toBe(before);
  });
});
