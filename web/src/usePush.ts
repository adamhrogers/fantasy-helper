import { useCallback, useEffect, useState } from 'react';
import type { Api } from './api/client';
import type { DeviceInfo } from './lib/install';
import { checkState, turnOff, turnOn, type PushPlatform, type PushState, type Result } from './push';

export interface PushControl {
  state: PushState;
  /** A tap is being handled. */
  busy: boolean;
  problem: string | null;
  enable: () => void;
  disable: () => void;
}

/** This phone's notification state and the two taps that change it. `platform` and `device` must
 *  stay the same objects between renders. */
export function usePush(api: Api, platform: PushPlatform, device: DeviceInfo): PushControl {
  const [state, setState] = useState<PushState>('checking');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void checkState(api, platform, device).then((next) => {
      if (current) setState(next);
    });
    return () => {
      current = false;
    };
  }, [api, platform, device]);

  const run = useCallback((action: (api: Api, platform: PushPlatform) => Promise<Result>) => {
    setBusy(true);
    setProblem(null);
    // `action` is called here, inside the tap, so a permission prompt still counts as asked by it.
    void action(api, platform).then((result) => {
      setState(result.state);
      setProblem(result.problem);
      setBusy(false);
    });
  }, [api, platform]);

  const enable = useCallback(() => run(turnOn), [run]);
  const disable = useCallback(() => run(turnOff), [run]);
  return { state, busy, problem, enable, disable };
}
