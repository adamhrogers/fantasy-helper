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

describe('the push calls', () => {
  it('asks for the public key', async () => {
    const { client, fetchMock } = api(() => json({ public_key: 'abc' }));
    expect(await client.pushPublicKey()).toEqual({ ok: true, data: { public_key: 'abc' } });
    const request = fetchMock.mock.calls[0]![0];
    expect(request.method).toBe('GET');
    expect(request.url).toBe('https://api.example.run.app/v1/push/public-key');
    expect(request.headers.get('Authorization')).toBe('Bearer abc');
  });

  it('saves a device with exactly the address and the two keys', async () => {
    const body = { endpoint: 'https://push.example.test/x', keys: { p256dh: 'p', auth: 'a' } };
    const { client, fetchMock } = api(() => json({ endpoint: body.endpoint }));
    expect(await client.saveSubscription(body)).toEqual({ ok: true, data: { endpoint: body.endpoint } });
    const request = fetchMock.mock.calls[0]![0];
    expect(request.method).toBe('PUT');
    expect(request.url).toBe('https://api.example.run.app/v1/push/subscriptions');
    expect(JSON.parse(await request.text())).toEqual(body);
  });

  it('removes a device by address, and reads the empty 204 answer as success', async () => {
    const { client, fetchMock } = api(() => new Response(null, { status: 204 }));
    expect(await client.removeSubscription('https://push.example.test/x')).toEqual({ ok: true, data: null });
    const request = fetchMock.mock.calls[0]![0];
    expect(request.method).toBe('DELETE');
    expect(JSON.parse(await request.text())).toEqual({ endpoint: 'https://push.example.test/x' });
  });

  it('still reads a failed removal as a failure, not as the empty success', async () => {
    const { client } = api(() => json({ detail: 'no' }, 500));
    expect(await client.removeSubscription('https://push.example.test/x')).toMatchObject({
      ok: false,
      error: { kind: 'server' },
    });
  });

  it('answers 503 from the key route as a server problem the app can show', async () => {
    const { client } = api(() => json({ detail: 'notifications aren’t set up yet' }, 503));
    expect(await client.pushPublicKey()).toMatchObject({ ok: false, error: { kind: 'server' } });
  });
});

describe('when the sign-in service can’t be reached to get a token', () => {
  it('says the server couldn’t be reached, rather than throwing or saying the sign-in expired', async () => {
    const api = createApi({
      baseUrl: 'https://api.example.test',
      getToken: async () => {
        throw new Error('offline');
      },
      fetch: vi.fn(),
    });
    const result = await api.me();
    expect(result).toMatchObject({ ok: false, error: { kind: 'network' } });
  });
});
