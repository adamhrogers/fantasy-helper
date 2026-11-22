import { useCallback, useEffect, useState } from 'react';

export type Tab = 'today' | 'week';

export const TAB_PATHS: Record<Tab, string> = { today: '/today', week: '/week' };

/** Anything that isn't /week is Today, so "/" and unknown paths open the first tab. */
export function tabFromPath(path: string): Tab {
  return path.replace(/\/+$/, '') === TAB_PATHS.week ? 'week' : 'today';
}

/** The tab shown, kept in the address (so a notification can open /today) and the back button. */
export function useTab(): [Tab, (tab: Tab) => void] {
  const [tab, setTab] = useState<Tab>(() => tabFromPath(window.location.pathname));

  useEffect(() => {
    const wanted = TAB_PATHS[tabFromPath(window.location.pathname)];
    if (window.location.pathname !== wanted) window.history.replaceState(null, '', wanted);
    const onPop = () => setTab(tabFromPath(window.location.pathname));
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const go = useCallback((next: Tab) => {
    if (window.location.pathname !== TAB_PATHS[next]) {
      window.history.pushState(null, '', TAB_PATHS[next]);
    }
    setTab(next);
  }, []);

  return [tab, go];
}
