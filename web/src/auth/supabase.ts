import { createClient, isAuthRetryableFetchError, type AuthError } from '@supabase/supabase-js';
import type { AuthClient, AuthFailure, AuthResult } from './types';

/** Turns Supabase's error into one of the few reasons the screen has words for. */
export function failureFor(error: AuthError): AuthFailure {
  if (isAuthRetryableFetchError(error) || error.status === 0) return 'network';
  const code = 'code' in error && typeof error.code === 'string' ? error.code : '';
  const message = error.message.toLowerCase();
  if (error.status === 429 || code.startsWith('over_')) return 'rate_limited';
  if (code === 'invalid_credentials' || message.includes('invalid login credentials')) {
    return 'bad_credentials';
  }
  // A code requested for an email with no user: sign-ups are off, so Supabase refuses.
  if (code === 'otp_disabled' || code === 'signup_disabled' || code === 'user_not_found') return 'not_a_user';
  if (message.includes('signups not allowed')) return 'not_a_user';
  if (code === 'otp_expired' || message.includes('expired or is invalid')) return 'bad_code';
  return 'other';
}

export function supabaseAuth(url: string, publishableKey: string): AuthClient {
  const client = createClient(url, publishableKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      // Nothing signs in through a link: a tapped link would open Safari, not the installed app.
      detectSessionInUrl: false,
    },
  });

  const result = (error: AuthError | null): AuthResult =>
    error ? { ok: false, reason: failureFor(error) } : { ok: true };

  return {
    watch(onChange) {
      // Never call back into Supabase from this callback (it can deadlock): only report.
      const { data } = client.auth.onAuthStateChange((_event, session) => onChange(session !== null));
      return () => data.subscription.unsubscribe();
    },
    async accessToken() {
      const { data } = await client.auth.getSession();
      return data.session?.access_token ?? null;
    },
    async signInWithPassword(email, password) {
      const { error } = await client.auth.signInWithPassword({ email, password });
      return result(error);
    },
    async sendCode(email) {
      const { error } = await client.auth.signInWithOtp({ email, options: { shouldCreateUser: false } });
      return result(error);
    },
    async verifyCode(email, code) {
      const { error } = await client.auth.verifyOtp({ email, token: code, type: 'email' });
      return result(error);
    },
    async signOut() {
      await client.auth.signOut();
    },
  };
}
