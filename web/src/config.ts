// The build variables the app needs. Vite bakes them into the bundle, so none can be secret: the
// publishable key is meant for browsers, and the addresses are public by nature.

export interface AppConfig {
  apiUrl: string;
  supabaseUrl: string;
  supabasePublishableKey: string;
  /** Shows "Email me a code instead" on the sign-in screen (needs the beta's email setup). */
  codeSignIn: boolean;
}

export type ConfigResult =
  | { ok: true; config: AppConfig }
  | { ok: false; problems: string[] };

/** An address the app may talk to: https, or http on this machine for development. */
function origin(value: unknown): string | null {
  if (typeof value !== 'string' || value.trim() === '') return null;
  try {
    const url = new URL(value.trim());
    const local = url.hostname === 'localhost' || url.hostname === '127.0.0.1';
    if (url.protocol === 'https:' || (local && url.protocol === 'http:')) return url.origin;
    return null;
  } catch {
    return null;
  }
}

export function readConfig(env: Record<string, unknown>): ConfigResult {
  const apiUrl = origin(env.VITE_API_URL);
  const supabaseUrl = origin(env.VITE_SUPABASE_URL);
  const key = typeof env.VITE_SUPABASE_PUBLISHABLE_KEY === 'string' ? env.VITE_SUPABASE_PUBLISHABLE_KEY.trim() : '';

  const problems: string[] = [];
  if (!apiUrl) problems.push('VITE_API_URL');
  if (!supabaseUrl) problems.push('VITE_SUPABASE_URL');
  if (!key) problems.push('VITE_SUPABASE_PUBLISHABLE_KEY');
  if (!apiUrl || !supabaseUrl || !key) return { ok: false, problems };

  return {
    ok: true,
    config: {
      apiUrl,
      supabaseUrl,
      supabasePublishableKey: key,
      codeSignIn: env.VITE_SIGN_IN_WITH_CODE === 'true',
    },
  };
}
