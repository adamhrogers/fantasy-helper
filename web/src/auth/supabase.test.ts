import { AuthApiError, AuthRetryableFetchError } from '@supabase/supabase-js';
import { describe, expect, it } from 'vitest';
import { failureFor } from './supabase';

describe('failureFor', () => {
  it('knows a wrong password', () => {
    expect(failureFor(new AuthApiError('Invalid login credentials', 400, 'invalid_credentials'))).toBe('bad_credentials');
  });

  it('knows an email with no user, when a code is requested and sign-ups are off', () => {
    expect(failureFor(new AuthApiError('Signups not allowed for otp', 422, 'otp_disabled'))).toBe('not_a_user');
  });

  it('knows a wrong or expired code', () => {
    expect(failureFor(new AuthApiError('Token has expired or is invalid', 403, 'otp_expired'))).toBe('bad_code');
  });

  it('knows a rate limit', () => {
    expect(failureFor(new AuthApiError('rate limit', 429, 'over_email_send_rate_limit'))).toBe('rate_limited');
  });

  it('knows when the service could not be reached', () => {
    expect(failureFor(new AuthRetryableFetchError('Failed to fetch', 0))).toBe('network');
  });

  it('has an answer for anything else', () => {
    expect(failureFor(new AuthApiError('something new', 500, 'unexpected_failure'))).toBe('other');
  });
});
