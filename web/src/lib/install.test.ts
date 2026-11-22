import { describe, expect, it } from 'vitest';
import { installHelp, type DeviceInfo } from './install';

const IPHONE_SAFARI =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
const IPHONE_CHROME =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1';
const DESKTOP = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36';

function device(overrides: Partial<DeviceInfo>): DeviceInfo {
  return {
    userAgent: IPHONE_SAFARI,
    platform: 'iPhone',
    maxTouchPoints: 5,
    standalone: false,
    displayModeStandalone: false,
    ...overrides,
  };
}

describe('installHelp', () => {
  it('shows the steps on an iPhone in Safari', () => {
    expect(installHelp(device({}))).toEqual({ show: true, inSafari: true });
  });

  it('says to open Safari when it is another iPhone browser', () => {
    expect(installHelp(device({ userAgent: IPHONE_CHROME }))).toEqual({ show: true, inSafari: false });
  });

  it('is quiet once the app runs from the Home Screen', () => {
    expect(installHelp(device({ standalone: true })).show).toBe(false);
    expect(installHelp(device({ displayModeStandalone: true })).show).toBe(false);
  });

  it('is quiet on a computer, and sees an iPad that calls itself a Mac', () => {
    expect(installHelp(device({ userAgent: DESKTOP, platform: 'MacIntel', maxTouchPoints: 0 })).show).toBe(false);
    expect(installHelp(device({ userAgent: DESKTOP, platform: 'MacIntel', maxTouchPoints: 5 })).show).toBe(true);
  });
});
