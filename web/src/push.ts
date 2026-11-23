// Turning notifications on and off for this phone (ADR 0030). The browser parts sit behind
// `PushPlatform` so the flow can be tested with a fake; `browserPlatform()` is the real one.
//
// Rules this file keeps, each learned from how iOS treats web apps:
//  - The permission prompt comes from a tap, so `turnOn` asks for permission as its very first
//    step, before anything is awaited (the tap handler calls it directly).
//  - Only the address and the two keys go to the API (it rejects anything else, and the
//    subscription's own JSON also carries an expiry time).
//  - "On" means the server has the subscription. If saving it fails, the browser's subscription
//    is dropped again rather than leaving a phone that thinks it is subscribed.
//  - A subscription is tied to the public key it was made with. When the key has been replaced
//    (scripts/vapid-setup.sh --rotate) the old subscription can never receive again, so it counts
//    as off, and turning notifications on replaces it.
import type { Api } from './api/client';
import type { SubscriptionBody } from './api/types';
import { installHelp, type DeviceInfo } from './lib/install';

export interface PushPlatform {
  /** Whether this browser has service workers, push and notifications at all. */
  supported: boolean;
  permission(): NotificationPermission;
  requestPermission(): Promise<NotificationPermission>;
  /** This phone's current subscription, if it has one. */
  subscription(): Promise<PushSubscription | null>;
  subscribe(applicationServerKey: Uint8Array<ArrayBuffer>): Promise<PushSubscription>;
}

/** How long to wait for the service worker to be ready before saying it isn't. */
const WORKER_WAIT_MS = 8000;

export function browserPlatform(): PushPlatform {
  const supported =
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof window !== 'undefined' &&
    'PushManager' in window &&
    'Notification' in window;
  return {
    supported,
    permission: () => Notification.permission,
    requestPermission: () => Notification.requestPermission(),
    async subscription() {
      // getRegistration (not .ready) so a page with no service worker answers "none" instead of waiting.
      const registration = await navigator.serviceWorker.getRegistration();
      return registration ? registration.pushManager.getSubscription() : null;
    },
    async subscribe(applicationServerKey) {
      const waited = new Promise<never>((_resolve, reject) =>
        window.setTimeout(() => reject(new Error('The service worker isn’t ready.')), WORKER_WAIT_MS),
      );
      const registration = await Promise.race([navigator.serviceWorker.ready, waited]);
      return registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey });
    },
  };
}

/** The server's key as the bytes `pushManager.subscribe` wants (it is sent URL-safe, unpadded). */
export function urlBase64ToBytes(value: string): Uint8Array<ArrayBuffer> {
  const standard = value.replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(standard + '='.repeat((4 - (standard.length % 4)) % 4));
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function sameKey(subscribed: ArrayBuffer | null, wanted: Uint8Array): boolean {
  if (!subscribed) return false;
  const have = new Uint8Array(subscribed);
  return have.length === wanted.length && have.every((byte, i) => byte === wanted[i]);
}

/** What the API takes for a subscription, or null when the browser's one is missing a part. */
export function bodyOf(subscription: PushSubscription): SubscriptionBody | null {
  const { endpoint, keys } = subscription.toJSON();
  if (!endpoint || !keys?.p256dh || !keys.auth) return null;
  return { endpoint, keys: { p256dh: keys.p256dh, auth: keys.auth } };
}

/** Where things stand for this phone. `install` and `unsupported` have no button, only a hint. */
export type PushState = 'checking' | 'install' | 'unsupported' | 'denied' | 'off' | 'on';

export interface Result {
  state: PushState;
  /** A sentence for the person when something went wrong, else null. */
  problem: string | null;
}

/** Reads the state when the app opens. When notifications are on, this also re-saves the
 *  subscription with the server (an idempotent save), so a phone whose browser rotated its
 *  subscription, or a server that lost the row, is put right by opening the app. */
export async function checkState(api: Api, platform: PushPlatform, device: DeviceInfo): Promise<PushState> {
  if (installHelp(device).show) return 'install';
  if (!platform.supported) return 'unsupported';
  const permission = platform.permission();
  if (permission === 'denied') return 'denied';
  const subscription = await platform.subscription().catch(() => null);
  if (!subscription || permission !== 'granted') return 'off';
  const key = await api.pushPublicKey();
  if (!key.ok) return 'on'; // can't compare while offline: the subscription is there
  let wanted: Uint8Array<ArrayBuffer>;
  try {
    wanted = urlBase64ToBytes(key.data.public_key);
  } catch {
    return 'on';
  }
  if (!sameKey(subscription.options.applicationServerKey, wanted)) return 'off';
  const body = bodyOf(subscription);
  if (body) await api.saveSubscription(body);
  return 'on';
}

const generic = (name = '') => `Couldn’t turn on notifications on this phone${name}. Try again in a minute.`;

/** Asks for permission, subscribes and tells the server. Call it straight from the tap handler:
 *  the permission request is its first step, before anything is awaited, which is how iOS knows
 *  the person asked for it. */
export async function turnOn(api: Api, platform: PushPlatform): Promise<Result> {
  const asked = platform.requestPermission(); // first, and not after an await
  try {
    const permission = await asked;
    if (permission === 'denied') return { state: 'denied', problem: null };
    if (permission !== 'granted') {
      return { state: 'off', problem: 'Notifications weren’t turned on. Tap again and choose Allow.' };
    }
    const key = await api.pushPublicKey();
    if (!key.ok) return { state: 'off', problem: key.error.message };
    let wanted: Uint8Array<ArrayBuffer>;
    try {
      wanted = urlBase64ToBytes(key.data.public_key);
    } catch {
      return { state: 'off', problem: 'The server’s notification key isn’t valid. Try again later.' };
    }
    let subscription = await platform.subscription();
    if (subscription && !sameKey(subscription.options.applicationServerKey, wanted)) {
      // Made with an older key, so it can never receive from the new one: replace it, and let the
      // server forget its address (it would only be told it is gone, at the next send).
      const old = subscription.endpoint;
      await subscription.unsubscribe();
      await api.removeSubscription(old);
      subscription = null;
    }
    subscription ??= await platform.subscribe(wanted);
    const body = bodyOf(subscription);
    if (!body) {
      await subscription.unsubscribe();
      return { state: 'off', problem: generic() };
    }
    const saved = await api.saveSubscription(body);
    if (!saved.ok) {
      await subscription.unsubscribe().catch(() => false); // never leave a subscription the server lacks
      return { state: 'off', problem: saved.error.message };
    }
    return { state: 'on', problem: null };
  } catch (error) {
    // The name (say, AbortError when the browser's push service refused) helps when it is read out.
    const name = error instanceof Error && error.name !== 'Error' ? ` (${error.name})` : '';
    return { state: 'off', problem: generic(name) };
  }
}

/** Drops this phone's subscription and tells the server. The phone's own subscription goes first:
 *  if the server then can't be told, its copy points at an address that now answers "gone", and
 *  the next send deletes it. */
export async function turnOff(api: Api, platform: PushPlatform): Promise<Result> {
  try {
    const subscription = await platform.subscription();
    if (!subscription) return { state: 'off', problem: null };
    const endpoint = subscription.endpoint;
    if (!(await subscription.unsubscribe())) {
      return { state: 'on', problem: 'Couldn’t turn off notifications on this phone. Try again.' };
    }
    await api.removeSubscription(endpoint);
    return { state: 'off', problem: null };
  } catch {
    return { state: 'on', problem: 'Couldn’t turn off notifications on this phone. Try again.' };
  }
}
