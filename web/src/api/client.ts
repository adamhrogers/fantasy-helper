import createClient from 'openapi-fetch';
import type { paths } from './schema';
import type { Me, PushKey, ReportKind, ReportsOut, SubscriptionBody } from './types';

export type ApiErrorKind = 'unauthorized' | 'forbidden' | 'network' | 'server';

export interface ApiError {
  kind: ApiErrorKind;
  message: string;
}

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

export interface Api {
  me(): Promise<ApiResult<Me>>;
  reports(kind: ReportKind): Promise<ApiResult<ReportsOut>>;
  /** The server's public push key, which the browser needs to make a subscription. */
  pushPublicKey(): Promise<ApiResult<PushKey>>;
  /** Saves this device's subscription (idempotent: the same address replaces itself). */
  saveSubscription(subscription: SubscriptionBody): Promise<ApiResult<{ endpoint: string }>>;
  /** Forgets one device's subscription; there is no body to read in the answer. */
  removeSubscription(endpoint: string): Promise<ApiResult<null>>;
}

export interface ApiOptions {
  baseUrl: string;
  /** The signed-in user's access token, or null when nobody is signed in. */
  getToken: () => Promise<string | null>;
  fetch?: typeof fetch;
}

function failure(kind: ApiErrorKind, message: string): { ok: false; error: ApiError } {
  return { ok: false, error: { kind, message } };
}

function failureFor(status: number): { ok: false; error: ApiError } {
  if (status === 401) return failure('unauthorized', 'Your sign-in expired.');
  if (status === 403) return failure('forbidden', 'This sign-in isn’t set up for this app.');
  return failure('server', `The server had a problem (status ${status}). Try again in a minute.`);
}

/** A typed client for the API (types come from openapi.json). The token goes in the
 *  Authorization header, per request, and nothing signed-in is stored by the browser's HTTP cache. */
export function createApi({ baseUrl, getToken, fetch: fetchImpl }: ApiOptions): Api {
  const client = createClient<paths>({
    baseUrl,
    cache: 'no-store',
    // openapi-fetch calls fetch with a ready-made Request; wrap the caller's fetch the same way.
    ...(fetchImpl ? { fetch: (request: Request) => fetchImpl(request) } : {}),
  });

  /** `empty` is the result of an answer that has no body (204): without it, no data is a failure. */
  async function call<T>(
    run: (headers: Record<string, string>) => Promise<{ data?: T; response: Response }>,
    empty?: T,
  ): Promise<ApiResult<T>> {
    let token: string | null;
    try {
      token = await getToken();
    } catch {
      // The sign-in service couldn't be reached to refresh the token: not the same as signed out.
      return failure('network', 'Couldn’t reach the server. Check your connection and try again.');
    }
    if (!token) return failure('unauthorized', 'Your sign-in expired.');
    try {
      const { data, response } = await run({ Authorization: `Bearer ${token}` });
      if (response.ok && data !== undefined) return { ok: true, data };
      if (response.ok && empty !== undefined) return { ok: true, data: empty };
      return failureFor(response.status);
    } catch {
      return failure('network', 'Couldn’t reach the server. Check your connection and try again.');
    }
  }

  return {
    me: () => call((headers) => client.GET('/v1/me', { headers })),
    reports: (kind) =>
      call((headers) => client.GET('/v1/reports/{kind}', { params: { path: { kind } }, headers })),
    pushPublicKey: () => call((headers) => client.GET('/v1/push/public-key', { headers })),
    saveSubscription: (subscription) =>
      call((headers) => client.PUT('/v1/push/subscriptions', { body: subscription, headers })),
    removeSubscription: (endpoint) =>
      call<null>((headers) => client.DELETE('/v1/push/subscriptions', { body: { endpoint }, headers }), null),
  };
}
