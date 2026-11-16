import { describe, expect, it } from 'vitest';
import { readConfig } from './config';

const GOOD = {
  VITE_API_URL: 'https://api.example.run.app/',
  VITE_SUPABASE_URL: 'https://abcdefgh.supabase.co',
  VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_test',
};

describe('readConfig', () => {
  it('reads the three build variables and trims the address to its origin', () => {
    const result = readConfig(GOOD);
    expect(result).toEqual({
      ok: true,
      config: {
        apiUrl: 'https://api.example.run.app',
        supabaseUrl: 'https://abcdefgh.supabase.co',
        supabasePublishableKey: 'sb_publishable_test',
        codeSignIn: false,
      },
    });
  });

  it('names every variable that is missing or unusable', () => {
    expect(readConfig({})).toEqual({
      ok: false,
      problems: ['VITE_API_URL', 'VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY'],
    });
    expect(readConfig({ ...GOOD, VITE_API_URL: 'not a url' })).toEqual({ ok: false, problems: ['VITE_API_URL'] });
    expect(readConfig({ ...GOOD, VITE_SUPABASE_PUBLISHABLE_KEY: '  ' })).toEqual({
      ok: false,
      problems: ['VITE_SUPABASE_PUBLISHABLE_KEY'],
    });
  });

  it('refuses plain http except on this machine', () => {
    expect(readConfig({ ...GOOD, VITE_API_URL: 'http://api.example.com' }).ok).toBe(false);
    expect(readConfig({ ...GOOD, VITE_API_URL: 'http://localhost:8080' }).ok).toBe(true);
  });

  it('turns the emailed-code sign-in on only when asked', () => {
    const on = readConfig({ ...GOOD, VITE_SIGN_IN_WITH_CODE: 'true' });
    expect(on.ok && on.config.codeSignIn).toBe(true);
    const other = readConfig({ ...GOOD, VITE_SIGN_IN_WITH_CODE: 'yes' });
    expect(other.ok && other.config.codeSignIn).toBe(false);
  });
});

describe('a secret key never ships', () => {
  it('refuses the new secret keys', () => {
    const result = readConfig({ ...GOOD, VITE_SUPABASE_PUBLISHABLE_KEY: 'sb_secret_abc123' });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.problems[0]).toContain('SECRET');
  });

  it('refuses an old-style token whose role is service_role, and accepts an anon one', () => {
    const token = (role: string) =>
      `x.${btoa(JSON.stringify({ role })).replace(/=+$/, '')}.y`;
    expect(readConfig({ ...GOOD, VITE_SUPABASE_PUBLISHABLE_KEY: token('service_role') }).ok).toBe(false);
    expect(readConfig({ ...GOOD, VITE_SUPABASE_PUBLISHABLE_KEY: token('anon') }).ok).toBe(true);
  });
});
