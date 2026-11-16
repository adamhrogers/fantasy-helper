// What the app needs from a sign-in service, so screens and tests don't depend on Supabase.

export type AuthFailure =
  | 'bad_credentials'
  | 'not_a_user'
  | 'bad_code'
  | 'rate_limited'
  | 'network'
  | 'other';

export type AuthResult = { ok: true } | { ok: false; reason: AuthFailure };

export interface AuthClient {
  /** Reports whether someone is signed in now, and whenever it changes. Returns a way to stop. */
  watch(onChange: (signedIn: boolean) => void): () => void;
  /** The current access token, refreshed if it ran out, or null when nobody is signed in. */
  accessToken(): Promise<string | null>;
  signInWithPassword(email: string, password: string): Promise<AuthResult>;
  /** Emails a 6-digit code. Never creates a user: sign-ups are off. */
  sendCode(email: string): Promise<AuthResult>;
  verifyCode(email: string, code: string): Promise<AuthResult>;
  signOut(): Promise<void>;
}

export const FAILURE_MESSAGES: Record<AuthFailure, string> = {
  bad_credentials: "That email and password don't match.",
  not_a_user: "That email isn't set up for this app.",
  bad_code: "That code didn't work. It may have expired: ask for a new one.",
  rate_limited: 'Too many tries. Wait a minute and try again.',
  network: "Couldn't reach the sign-in service. Check your connection.",
  other: "Couldn't sign in. Try again.",
};
