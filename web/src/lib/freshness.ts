// Is the latest report from today? A stale report that looks current is the failure this
// product can't have, so this is plain, tested code.

const DAY = /^\d{4}-\d{2}-\d{2}$/;

/** The device's own time zone, for when the user's isn't a zone this browser knows. */
function deviceZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function zoneOrDevice(timeZone: string): string {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone });
    return timeZone;
  } catch {
    return deviceZone();
  }
}

/** Today's date (YYYY-MM-DD) in the user's time zone. */
export function todayIn(timeZone: string, now: Date): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: zoneOrDevice(timeZone),
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now);
}

function dayNumber(day: string): number {
  const [year, month, date] = day.split('-').map(Number);
  return Math.round(Date.UTC(year ?? 0, (month ?? 1) - 1, date ?? 1) / 86_400_000);
}

export type Freshness =
  | { state: 'empty' }
  | { state: 'current' }
  | { state: 'stale'; forDate: string; daysBehind: number };

/** `forDate` is the report's date, `today` is today in the user's zone. A report dated in the
 *  future (a clock or zone mismatch) is not stale; it is simply the newest there is. */
export function freshness(forDate: string | null, today: string): Freshness {
  if (forDate === null || !DAY.test(forDate) || !DAY.test(today)) return { state: 'empty' };
  const daysBehind = dayNumber(today) - dayNumber(forDate);
  return daysBehind > 0 ? { state: 'stale', forDate, daysBehind } : { state: 'current' };
}

/** "Mon, Nov 16" for a YYYY-MM-DD date (a calendar date: no zone involved). */
export function formatDay(day: string): string {
  if (!DAY.test(day)) return day;
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${day}T12:00:00Z`));
}

/** "6:19 AM" in the user's zone; "6:19 AM EST" with `zoneName`. */
export function formatTime(iso: string, timeZone: string, { zoneName = false } = {}): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return '';
  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: zoneOrDevice(timeZone),
    ...(zoneName ? { timeZoneName: 'short' as const } : {}),
  }).format(moment);
}
