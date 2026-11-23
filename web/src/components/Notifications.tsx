import { useState } from 'react';
import type { Api } from '../api/client';
import { currentDevice, type DeviceInfo } from '../lib/install';
import { browserPlatform, type PushPlatform } from '../push';
import { usePush } from '../usePush';

export interface NotificationsProps {
  api: Api;
  /** For tests: the browser and the phone. */
  platform?: PushPlatform;
  device?: DeviceInfo;
}

const HINTS = {
  install:
    'To get notifications, add this app to your Home Screen from Safari and open it from there.',
  unsupported: 'This browser can’t show notifications from this app.',
  denied:
    'Notifications are blocked for this app. Allow them in your phone’s Settings (Notifications, then Fantasy), then open the app again.',
} as const;

/** "Enable notifications" / "Turn off" for this phone. Where notifications can't work it says why
 *  instead of showing a button that can't do anything (iOS only allows them from the Home Screen). */
export function Notifications({ api, platform, device }: NotificationsProps) {
  const [browser] = useState(() => platform ?? browserPlatform());
  const [phone] = useState(() => device ?? currentDevice());
  const { state, busy, problem, enable, disable } = usePush(api, browser, phone);

  if (state === 'checking') return null;
  return (
    <section className="notify" aria-label="Notifications">
      {state === 'install' || state === 'unsupported' || state === 'denied' ? (
        <p className="muted">{HINTS[state]}</p>
      ) : (
        <>
          <p className="muted">
            {state === 'on'
              ? 'Notifications are on. You’ll get a message when each report is ready.'
              : 'Get a message when each morning’s report is ready.'}
          </p>
          {state === 'on' ? (
            <button type="button" className="link" onClick={disable} disabled={busy}>
              {busy ? 'Working…' : 'Turn off'}
            </button>
          ) : (
            <button type="button" onClick={enable} disabled={busy}>
              {busy ? 'Working…' : 'Enable notifications'}
            </button>
          )}
        </>
      )}
      {problem && (
        <p className="banner banner-stale" role="alert">
          {problem}
        </p>
      )}
    </section>
  );
}
