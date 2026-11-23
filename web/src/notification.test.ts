import { describe, expect, it } from 'vitest';
import { FALLBACK, NOTIFICATION_PATHS, describeNotification, safePath } from './notification';

describe('what a push shows', () => {
  it('shows what the server sent', () => {
    const raw = JSON.stringify({
      title: 'Today’s report is ready',
      body: '9 cards in 2 leagues.',
      path: '/week',
      tag: 'daily-2026-11-20',
    });
    expect(describeNotification(raw)).toEqual({
      title: 'Today’s report is ready',
      body: '9 cards in 2 leagues.',
      tag: 'daily-2026-11-20',
      path: '/week',
    });
  });

  it.each([
    ['nothing at all', null],
    ['an empty message', ''],
    ['text that isn’t JSON', 'hello'],
    ['JSON that isn’t an object', '"hello"'],
    ['an array', '[1,2]'],
    ['null', 'null'],
    ['an empty object', '{}'],
  ])('still shows a plain notification for %s, since a push must never show nothing', (_name, raw) => {
    expect(describeNotification(raw)).toEqual(FALLBACK);
  });

  it('fills in only what is missing or unusable', () => {
    const shown = describeNotification(JSON.stringify({ title: '  ', body: 7, path: '/today' }));
    expect(shown.title).toBe(FALLBACK.title);
    expect(shown.body).toBe(FALLBACK.body);
  });

  it('drops a tag that isn’t the plain kind the server makes, rather than trusting it', () => {
    expect(describeNotification(JSON.stringify({ tag: 'Daily 2026!' })).tag).toBeUndefined();
    expect(describeNotification(JSON.stringify({ tag: 'x'.repeat(65) })).tag).toBeUndefined();
    expect(describeNotification(JSON.stringify({ tag: 'test' })).tag).toBe('test');
  });

  it('keeps the text within the limits the server holds itself to', () => {
    const shown = describeNotification(JSON.stringify({ title: 't'.repeat(500), body: 'b'.repeat(500) }));
    expect(shown.title).toHaveLength(100);
    expect(shown.body).toHaveLength(200);
  });
});

describe('where a tap goes', () => {
  it('opens only the app’s own two tabs', () => {
    expect(NOTIFICATION_PATHS).toEqual(['/today', '/week']);
    expect(safePath('/today')).toBe('/today');
    expect(safePath('/week')).toBe('/week');
  });

  it.each(['https://evil.example/', '//evil.example', '/settings', '/week/../x', '', null, undefined, 7, {}])(
    'sends %j to Today instead',
    (value) => {
      expect(safePath(value)).toBe('/today');
    },
  );

  it('applies the same rule to a message', () => {
    expect(describeNotification(JSON.stringify({ path: 'https://evil.example/' })).path).toBe('/today');
  });
});
