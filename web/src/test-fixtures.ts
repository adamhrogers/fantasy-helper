import type { AuthClient } from './auth/types';
import type { Api, ApiError } from './api/client';
import type { Card, League, Me, ReportDocument, ReportsOut } from './api/types';
import { vi } from 'vitest';

export const LEAGUE_A: League = { id: '11111111-1111-4111-8111-111111111111', name: 'Test League A', platform: 'espn' };
export const LEAGUE_B: League = { id: '22222222-2222-4222-8222-222222222222', name: 'Test League B', platform: 'sleeper' };

export const ME: Me = { name: 'Test User', timezone: 'America/New_York', leagues: [LEAGUE_A, LEAGUE_B] };

/** 10:00 on 2026-11-16 in New York. */
export const NOW = new Date('2026-11-16T15:00:00Z');

export function card(overrides: Partial<Card> = {}): Card {
  return {
    id: 'card-1',
    kind: 'move',
    title: 'Add a player, drop another',
    body: 'A short body that says what to do.',
    items: [],
    reason: 'Because the numbers say so.',
    ...overrides,
  };
}

export function report(league: League, overrides: Partial<ReportDocument> = {}): ReportDocument {
  return {
    id: `report-${league.id}`,
    user_id: '99999999-9999-4999-8999-999999999999',
    league_id: league.id,
    kind: 'daily',
    for_date: '2026-11-16',
    generated_at: '2026-11-16T11:19:00Z', // 6:19 AM in New York
    cards: [card({ id: `${league.id}-1`, title: `Card for ${league.name}` })],
    engine_only: true,
    ...overrides,
  };
}

export function reports(forDate: string | null, documents: ReportDocument[]): ReportsOut {
  return { for_date: forDate, reports: documents };
}

export const ok = <T,>(data: T) => ({ ok: true as const, data });
export const fail = (kind: ApiError['kind'], message = 'went wrong') => ({
  ok: false as const,
  error: { kind, message },
});

export function fakeApi(overrides: Partial<Record<keyof Api, ReturnType<typeof vi.fn>>> = {}) {
  return {
    me: vi.fn(async () => ok(ME)),
    reports: vi.fn(async () => ok(reports('2026-11-16', [report(LEAGUE_A), report(LEAGUE_B)]))),
    ...overrides,
  } as unknown as Api & { me: ReturnType<typeof vi.fn>; reports: ReturnType<typeof vi.fn> };
}

export function fakeAuth(signedIn: boolean) {
  let emit: (signedIn: boolean) => void = () => undefined;
  const auth = {
    watch: vi.fn((onChange: (signedIn: boolean) => void) => {
      emit = onChange;
      onChange(signedIn);
      return () => undefined;
    }),
    accessToken: vi.fn(async () => 'token'),
    signInWithPassword: vi.fn(async () => ({ ok: true as const })),
    sendCode: vi.fn(async () => ({ ok: true as const })),
    verifyCode: vi.fn(async () => ({ ok: true as const })),
    signOut: vi.fn(async () => undefined),
  };
  return Object.assign(auth as unknown as AuthClient & typeof auth, { emit: (value: boolean) => emit(value) });
}
