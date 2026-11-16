import { describe, expect, it } from 'vitest';
import { formatDay, formatTime, freshness, todayIn } from './freshness';

describe('todayIn', () => {
  it("is the date in the user's zone, not UTC's", () => {
    const lateEvening = new Date('2026-11-17T03:00:00Z'); // 10 pm on the 16th in New York
    expect(todayIn('America/New_York', lateEvening)).toBe('2026-11-16');
    expect(todayIn('UTC', lateEvening)).toBe('2026-11-17');
    expect(todayIn('Pacific/Auckland', lateEvening)).toBe('2026-11-17');
  });

  it('changes at the zone’s midnight', () => {
    expect(todayIn('America/New_York', new Date('2026-11-17T04:59:00Z'))).toBe('2026-11-16');
    expect(todayIn('America/New_York', new Date('2026-11-17T05:00:00Z'))).toBe('2026-11-17');
  });

  it('falls back to the device zone for a zone the browser does not know', () => {
    expect(todayIn('Mars/Olympus_Mons', new Date('2026-11-16T15:00:00Z'))).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('freshness', () => {
  it('is current for a report dated today', () => {
    expect(freshness('2026-11-16', '2026-11-16')).toEqual({ state: 'current' });
  });

  it('is stale, and says by how many days, for an older report', () => {
    expect(freshness('2026-11-15', '2026-11-16')).toEqual({ state: 'stale', forDate: '2026-11-15', daysBehind: 1 });
    expect(freshness('2026-11-13', '2026-11-16')).toEqual({ state: 'stale', forDate: '2026-11-13', daysBehind: 3 });
  });

  it('counts days across a month and a year, and the clock change', () => {
    expect(freshness('2026-10-31', '2026-11-02')).toMatchObject({ daysBehind: 2 });
    expect(freshness('2026-12-31', '2027-01-01')).toMatchObject({ daysBehind: 1 });
    expect(freshness('2026-11-01', '2026-11-02')).toMatchObject({ daysBehind: 1 }); // US clocks go back
  });

  it('is not stale for a report dated in the future', () => {
    expect(freshness('2026-11-17', '2026-11-16')).toEqual({ state: 'current' });
  });

  it('is empty when there is no report or no usable date', () => {
    expect(freshness(null, '2026-11-16')).toEqual({ state: 'empty' });
    expect(freshness('yesterday', '2026-11-16')).toEqual({ state: 'empty' });
  });
});

describe('formatting', () => {
  it('writes a calendar date without moving it across zones', () => {
    expect(formatDay('2026-11-16')).toBe('Mon, Nov 16');
    expect(formatDay('nonsense')).toBe('nonsense');
  });

  it("writes a time in the user's zone", () => {
    expect(formatTime('2026-11-16T11:19:00Z', 'America/New_York')).toMatch(/^6:19\sAM$/);
    expect(formatTime('2026-11-16T11:19:00Z', 'UTC')).toMatch(/^11:19\sAM$/);
    expect(formatTime('garbage', 'UTC')).toBe('');
  });
});
