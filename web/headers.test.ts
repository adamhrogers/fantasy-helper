// @vitest-environment node
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { renderHeaders } from './headers';

const template = readFileSync(new URL('./headers.template', import.meta.url), 'utf8');
const env = {
  VITE_API_URL: 'https://api-1.example.run.app/',
  VITE_SUPABASE_URL: 'https://abcdefgh.supabase.co',
};

describe('renderHeaders', () => {
  it('lets the app call its own API and Supabase project, by exact address, and nothing else', () => {
    const csp = /Content-Security-Policy: (.*)/.exec(renderHeaders(template, env))?.[1] ?? '';
    expect(csp).toContain("connect-src 'self' https://api-1.example.run.app https://abcdefgh.supabase.co;");
    expect(csp).not.toContain('*');
    expect(csp).not.toContain('{{');
  });

  it('allows no outside address when a build variable is missing or unusable', () => {
    const csp = /Content-Security-Policy: (.*)/.exec(renderHeaders(template, {}))?.[1] ?? '';
    expect(csp).toContain("connect-src 'self' ;");
    expect(csp).not.toContain('https:');
    const bad = renderHeaders(template, { ...env, VITE_API_URL: 'http://api.example.com' });
    expect(bad).not.toContain('api.example.com');
  });
});
