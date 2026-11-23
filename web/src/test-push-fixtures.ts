// A fake browser for the push tests: permission, one subscription, and a log of what was asked, in order.
import { vi, type Mock } from 'vitest';
import type { DeviceInfo } from './lib/install';
import type { PushPlatform } from './push';
import { urlBase64ToBytes } from './push';
import { PUSH_KEY } from './test-fixtures';

export const ENDPOINT = 'https://push.example.test/send/device-one';

export type FakeSubscription = PushSubscription & { unsubscribe: Mock<() => Promise<boolean>> };

/** A browser's subscription made with `key` (a URL-safe key text, as the API sends it). Its JSON has
 *  the extra `expirationTime` a real one has, which the API would reject. */
export function subscription(endpoint = ENDPOINT, key: string | null = PUSH_KEY): FakeSubscription {
  return {
    endpoint,
    options: { applicationServerKey: key === null ? null : urlBase64ToBytes(key).buffer },
    toJSON: () => ({ endpoint, expirationTime: null, keys: { p256dh: 'device-p256dh', auth: 'device-auth' } }),
    unsubscribe: vi.fn(async () => true),
  } as unknown as FakeSubscription;
}

export class FakeBrowser implements PushPlatform {
  supported = true;
  state: NotificationPermission = 'default';
  /** What the person answers when asked. */
  answer: NotificationPermission = 'granted';
  current: FakeSubscription | null = null;
  /** What happens, in order: 'permission', 'subscribe'. (Tests add API calls to it.) */
  log: string[] = [];
  subscribeError: Error | null = null;

  permission = () => this.state;

  requestPermission = vi.fn(async () => {
    this.log.push('permission');
    this.state = this.answer;
    return this.answer;
  });

  subscription = vi.fn(async () => this.current);

  subscribe = vi.fn(async (key: Uint8Array<ArrayBuffer>) => {
    this.log.push('subscribe');
    if (this.subscribeError) throw this.subscribeError;
    // Made with the key it was given, so a later check can compare.
    const made = subscription(ENDPOINT, null);
    Object.assign(made, { options: { applicationServerKey: key.buffer } });
    this.current = made;
    return made;
  });
}

export const IPHONE_APP: DeviceInfo = {
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
  platform: 'iPhone',
  maxTouchPoints: 5,
  standalone: true,
  displayModeStandalone: true,
};
export const IPHONE_SAFARI: DeviceInfo = { ...IPHONE_APP, standalone: false, displayModeStandalone: false };
export const DESKTOP: DeviceInfo = {
  userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36',
  platform: 'MacIntel',
  maxTouchPoints: 0,
  standalone: false,
  displayModeStandalone: false,
};
