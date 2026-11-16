import { describe, expect, it, vi } from 'vitest';
import { createApi } from './client';
import { ME, reports } from '../test-fixtures';

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

function api(respond: (request: Request) => Response | Promise<Response>, token: string | null = 'abc') {
  const fetchMock = vi.fn(async (request: Request) => respond(request));
  const client = createApi({
    baseUrl: 'https://api.example.run.app',
    getToken: async () => token,
    fetch: fetchMock as unknown as typeof fetch,
  });
  return { client, fetchMock };
}

describe('the API client', () => {
  it('sends the access token in the Authorization header, never in the address', async () => {
    const { client, fetchMock } = api(() => json(ME));
    const result = await client.me();
    expect(result).toEqual({ ok: true, data: ME });
    const request = fetchMock.mock.calls[0]![0];
    expect(request.url).toBe('https://api.example.run.app/v1/me');
    expect(request.headers.get('Authorization')).toBe('Bearer abc');
    expect(request.cache).toBe('no-store');
  });

  it('asks for the report of the right kind', async () => {
    const { client, fetchMock } = api(() => json(reports('2026-11-16', [])));
    await client.reports('weekly');
    expect(fetchMock.mock.calls[0]![0].url).toBe('https://api.example.run.app/v1/reports/weekly');
  });

  it('does not call the API at all without a token', async () => {
    const { client, fetchMock } = api(() => json(ME), null);
    expect(await client.me()).toMatchObject({ ok: false, error: { kind: 'unauthorized' } });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [500, 'server'],
    [503, 'server'],
  ])('reads status %i as %s', async (status, kind) => {
    const { client } = api(() => json({ detail: 'no' }, status));
    expect(await client.me()).toMatchObject({ ok: false, error: { kind } });
  });

  it('reads a failed request as a network problem', async () => {
    const { client } = api(() => {
      throw new TypeError('Failed to fetch');
    });
    expect(await client.me()).toMatchObject({ ok: false, error: { kind: 'network' } });
  });
});
