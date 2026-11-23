import { describe, expect, it } from 'vitest';
import { bodyOf, checkState, turnOff, turnOn, urlBase64ToBytes } from './push';
import { ENDPOINT, DESKTOP, FakeBrowser, IPHONE_APP, IPHONE_SAFARI, subscription } from './test-push-fixtures';
import { PUSH_KEY, fail, fakeApi, keyText, ok } from './test-fixtures';

describe('the key', () => {
  it('is read from the URL-safe text the server sends into the bytes a browser wants', () => {
    const bytes = urlBase64ToBytes(PUSH_KEY);
    expect(bytes).toHaveLength(65);
    expect(bytes[0]).toBe(4); // an uncompressed point
    expect(urlBase64ToBytes('-_8')).toEqual(new Uint8Array([251, 255]));
  });
});

describe('what is sent to the server about a device', () => {
  it('is only the address and the two keys: the API refuses anything else', () => {
    expect(bodyOf(subscription())).toEqual({
      endpoint: ENDPOINT,
      keys: { p256dh: 'device-p256dh', auth: 'device-auth' },
    }); // (the fake's own JSON also has `expirationTime`)
  });

  it('is nothing when the browser’s subscription lacks a part', () => {
    const missing = { toJSON: () => ({ endpoint: ENDPOINT, keys: { p256dh: 'x' } }) } as unknown as PushSubscription;
    expect(bodyOf(missing)).toBeNull();
    expect(bodyOf({ toJSON: () => ({}) } as unknown as PushSubscription)).toBeNull();
  });
});

describe('turning notifications on', () => {
  it('asks for permission first, in the tap itself, before anything else is awaited', async () => {
    const browser = new FakeBrowser();
    const api = fakeApi();
    const pending = turnOn(api, browser);
    expect(browser.requestPermission).toHaveBeenCalledTimes(1); // already, with no await in between
    expect(api.pushPublicKey).not.toHaveBeenCalled();
    expect(browser.subscribe).not.toHaveBeenCalled();
    await pending;
  });

  it('subscribes with the server’s key and saves the device, and only then says on', async () => {
    const browser = new FakeBrowser();
    const api = fakeApi();
    expect(await turnOn(api, browser)).toEqual({ state: 'on', problem: null });
    expect(browser.subscribe).toHaveBeenCalledWith(urlBase64ToBytes(PUSH_KEY));
    expect(api.saveSubscription).toHaveBeenCalledTimes(1);
    expect(api.saveSubscription).toHaveBeenCalledWith({
      endpoint: ENDPOINT,
      keys: { p256dh: 'device-p256dh', auth: 'device-auth' },
    });
  });

  it('says blocked, and subscribes to nothing, when permission is refused', async () => {
    const browser = new FakeBrowser();
    browser.answer = 'denied';
    const api = fakeApi();
    expect(await turnOn(api, browser)).toEqual({ state: 'denied', problem: null });
    expect(browser.subscribe).not.toHaveBeenCalled();
    expect(api.pushPublicKey).not.toHaveBeenCalled();
    expect(api.saveSubscription).not.toHaveBeenCalled();
  });

  it('stays off, and says to tap again, when the prompt is dismissed', async () => {
    const browser = new FakeBrowser();
    browser.answer = 'default';
    const result = await turnOn(fakeApi(), browser);
    expect(result.state).toBe('off');
    expect(result.problem).toMatch(/tap again/i);
    expect(browser.subscribe).not.toHaveBeenCalled();
  });

  it('stays off, saying why, when the server has no key yet', async () => {
    const browser = new FakeBrowser();
    const api = fakeApi({ pushPublicKey: async () => fail('server', 'The server had a problem (status 503).') });
    const result = await turnOn(api, browser);
    expect(result).toEqual({ state: 'off', problem: 'The server had a problem (status 503).' });
    expect(browser.subscribe).not.toHaveBeenCalled();
  });

  it('gives the subscription back when the server can’t save it, so the phone never thinks it is on', async () => {
    const browser = new FakeBrowser();
    const api = fakeApi({ saveSubscription: async () => fail('network', 'Couldn’t reach the server.') });
    const result = await turnOn(api, browser);
    expect(result).toEqual({ state: 'off', problem: 'Couldn’t reach the server.' });
    expect(browser.current?.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it('uses a subscription the phone already has when it was made with the current key', async () => {
    const browser = new FakeBrowser();
    browser.current = subscription();
    const api = fakeApi();
    expect(await turnOn(api, browser)).toEqual({ state: 'on', problem: null });
    expect(browser.subscribe).not.toHaveBeenCalled();
    expect(api.saveSubscription).toHaveBeenCalledTimes(1); // the server still gets told
  });

  it('replaces a subscription made with an older key, and lets the server forget its address', async () => {
    const browser = new FakeBrowser();
    const old = subscription('https://push.example.test/send/old', keyText(3));
    browser.current = old;
    const api = fakeApi();
    expect(await turnOn(api, browser)).toEqual({ state: 'on', problem: null });
    expect(old.unsubscribe).toHaveBeenCalledTimes(1);
    expect(api.removeSubscription).toHaveBeenCalledWith('https://push.example.test/send/old');
    expect(browser.subscribe).toHaveBeenCalledWith(urlBase64ToBytes(PUSH_KEY));
  });

  it('says what went wrong, by its name, when the browser can’t subscribe', async () => {
    const browser = new FakeBrowser();
    browser.subscribeError = Object.assign(new Error('push service error'), { name: 'AbortError' });
    const result = await turnOn(fakeApi(), browser);
    expect(result.state).toBe('off');
    expect(result.problem).toContain('AbortError');
    expect(result.problem).not.toContain('push service error');
  });

  it('refuses a subscription with a missing key rather than sending half of one', async () => {
    const browser = new FakeBrowser();
    browser.subscribe.mockImplementationOnce(async () => {
      const broken = subscription();
      Object.assign(broken, { toJSON: () => ({ endpoint: ENDPOINT, keys: {} }) });
      browser.current = broken;
      return broken;
    });
    const api = fakeApi();
    const result = await turnOn(api, browser);
    expect(result.state).toBe('off');
    expect(api.saveSubscription).not.toHaveBeenCalled();
    expect(browser.current?.unsubscribe).toHaveBeenCalled();
  });

  it('says so, without throwing, when the key the server sends is not valid', async () => {
    const api = fakeApi({ pushPublicKey: async () => ok({ public_key: '***' }) });
    const result = await turnOn(api, new FakeBrowser());
    expect(result.state).toBe('off');
    expect(result.problem).toMatch(/key/i);
  });
});

describe('turning notifications off', () => {
  it('drops the phone’s subscription, then the server’s copy', async () => {
    const browser = new FakeBrowser();
    browser.current = subscription();
    const api = fakeApi();
    expect(await turnOff(api, browser)).toEqual({ state: 'off', problem: null });
    expect(browser.current.unsubscribe).toHaveBeenCalledTimes(1);
    expect(api.removeSubscription).toHaveBeenCalledWith(ENDPOINT);
  });

  it('is off even when the server can’t be told: its copy answers “gone” and is deleted at the next send', async () => {
    const browser = new FakeBrowser();
    browser.current = subscription();
    const api = fakeApi({ removeSubscription: async () => fail('network') });
    expect(await turnOff(api, browser)).toEqual({ state: 'off', problem: null });
  });

  it('stays on, saying so, when the phone won’t drop its subscription', async () => {
    const browser = new FakeBrowser();
    browser.current = subscription();
    browser.current.unsubscribe.mockResolvedValueOnce(false);
    const api = fakeApi();
    const result = await turnOff(api, browser);
    expect(result.state).toBe('on');
    expect(result.problem).toMatch(/turn off/i);
    expect(api.removeSubscription).not.toHaveBeenCalled();
  });

  it('has nothing to do when there is no subscription', async () => {
    const api = fakeApi();
    expect(await turnOff(api, new FakeBrowser())).toEqual({ state: 'off', problem: null });
    expect(api.removeSubscription).not.toHaveBeenCalled();
  });
});

describe('where things stand when the app opens', () => {
  it('on an iPhone outside the Home Screen: no button, a hint to install (iOS allows push only there)', async () => {
    expect(await checkState(fakeApi(), new FakeBrowser(), IPHONE_SAFARI)).toBe('install');
  });

  it('where the browser has no push support: a hint', async () => {
    const browser = new FakeBrowser();
    browser.supported = false;
    expect(await checkState(fakeApi(), browser, DESKTOP)).toBe('unsupported');
  });

  it('where permission was refused: blocked', async () => {
    const browser = new FakeBrowser();
    browser.state = 'denied';
    expect(await checkState(fakeApi(), browser, IPHONE_APP)).toBe('denied');
  });

  it('with no subscription: off, asking the server for nothing', async () => {
    const api = fakeApi();
    expect(await checkState(api, new FakeBrowser(), IPHONE_APP)).toBe('off');
    expect(api.pushPublicKey).not.toHaveBeenCalled();
  });

  it('with a subscription made with the current key: on, and the server’s copy is refreshed', async () => {
    const browser = new FakeBrowser();
    browser.state = 'granted';
    browser.current = subscription();
    const api = fakeApi();
    expect(await checkState(api, browser, IPHONE_APP)).toBe('on');
    expect(api.saveSubscription).toHaveBeenCalledTimes(1);
  });

  it('with a subscription made with an older key: off, since it can never receive again', async () => {
    const browser = new FakeBrowser();
    browser.state = 'granted';
    browser.current = subscription(ENDPOINT, keyText(3));
    const api = fakeApi();
    expect(await checkState(api, browser, IPHONE_APP)).toBe('off');
    expect(api.saveSubscription).not.toHaveBeenCalled();
  });

  it('with a subscription and no way to ask the server for its key: on, since the subscription is there', async () => {
    const browser = new FakeBrowser();
    browser.state = 'granted';
    browser.current = subscription();
    const api = fakeApi({ pushPublicKey: async () => fail('network') });
    expect(await checkState(api, browser, IPHONE_APP)).toBe('on');
    expect(api.saveSubscription).not.toHaveBeenCalled();
  });

  it('with a subscription but permission since withdrawn: off', async () => {
    const browser = new FakeBrowser();
    browser.state = 'default';
    browser.current = subscription();
    expect(await checkState(fakeApi(), browser, IPHONE_APP)).toBe('off');
  });
});
