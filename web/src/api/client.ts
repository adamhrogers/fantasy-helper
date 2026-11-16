import createClient from 'openapi-fetch';
import type { paths } from './schema';
import type { Me, ReportKind, ReportsOut } from './types';

export type ApiErrorKind = 'unauthorized' | 'forbidden' | 'network' | 'server';

export interface ApiError {
  kind: ApiErrorKind;
  message: string;
}

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: ApiError };

export interface Api {
  me(): Promise<ApiResult<Me>>;
  reports(kind: ReportKind): Promise<ApiResult<ReportsOut>>;
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
  if (status === 403) return failure('forbidden', "This sign-in isn't set up for this app.");
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

  async function call<T>(
    run: (headers: Record<string, string>) => Promise<{ data?: T; response: Response }>,
  ): Promise<ApiResult<T>> {
    const token = await getToken();
    if (!token) return failure('unauthorized', 'Your sign-in expired.');
    try {
      const { data, response } = await run({ Authorization: `Bearer ${token}` });
      if (response.ok && data !== undefined) return { ok: true, data };
      return failureFor(response.status);
    } catch {
      return failure('network', "Couldn't reach the server. Check your connection and try again.");
    }
  }

  return {
    me: () => call((headers) => client.GET('/v1/me', { headers })),
    reports: (kind) =>
      call((headers) => client.GET('/v1/reports/{kind}', { params: { path: { kind } }, headers })),
  };
}
