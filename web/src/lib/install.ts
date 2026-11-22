// Whether to show "add this to your Home Screen". A web app on iOS only gets notifications
// (Phase 4) and its own sign-in once it is installed, so this matters on iPhones.

export interface DeviceInfo {
  userAgent: string;
  platform: string;
  maxTouchPoints: number;
  /** navigator.standalone: iOS's own flag for "opened from the Home Screen". */
  standalone: boolean;
  /** The (display-mode: standalone) media query. */
  displayModeStandalone: boolean;
}

export interface InstallHelp {
  show: boolean;
  /** Installing needs Safari (other iOS browsers differ), so say so when it isn't Safari. */
  inSafari: boolean;
}

export function installHelp(device: DeviceInfo): InstallHelp {
  // iPadOS reports itself as a Mac with a touch screen.
  const ios =
    /iPhone|iPad|iPod/.test(device.userAgent) ||
    (device.platform === 'MacIntel' && device.maxTouchPoints > 1);
  const installed = device.standalone || device.displayModeStandalone;
  const otherBrowser = /CriOS|FxiOS|EdgiOS|OPiOS/.test(device.userAgent);
  return { show: ios && !installed, inSafari: !otherBrowser };
}

export function currentDevice(): DeviceInfo {
  const nav = navigator as Navigator & { standalone?: boolean };
  return {
    userAgent: nav.userAgent,
    platform: nav.platform,
    maxTouchPoints: nav.maxTouchPoints,
    standalone: nav.standalone === true,
    // (Not every browser, and not the test environment, has matchMedia.)
    displayModeStandalone: window.matchMedia?.('(display-mode: standalone)').matches ?? false,
  };
}
