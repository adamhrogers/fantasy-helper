import { useCallback, useEffect, useState } from 'react';
import type { Api } from './api/client';
import type { ReportsOut } from './api/types';
import type { AuthClient } from './auth/types';
import { InstallHelp } from './components/InstallHelp';
import { LoadProblem, NotSetUp } from './components/Notices';
import { Notifications, type NotificationsProps } from './components/Notifications';
import { ReportsView } from './components/ReportsView';
import { SignIn } from './components/SignIn';
import { UpdateBar } from './components/UpdateBar';
import { formatTime } from './lib/freshness';
import { TAB_PATHS, tabFromPath, useTab, type Tab } from './route';
import { useNotificationOpens } from './useNotificationOpens';
import { useAuthStatus } from './useAuthStatus';
import { useDashboard, type Resource } from './useDashboard';
import { useNow } from './useNow';

const TAB_TITLES: Record<Tab, string> = { today: 'Today', week: 'This Week' };

export interface AppProps {
  auth: AuthClient;
  api: Api;
  codeSignIn: boolean;
  /** For tests: the browser's push support and the phone, in place of the real ones. */
  push?: Pick<NotificationsProps, 'platform' | 'device'>;
}

export function App({ auth, api, codeSignIn, push }: AppProps) {
  const status = useAuthStatus(auth);
  const [notice, setNotice] = useState<string | null>(null);

  const expired = useCallback(() => {
    setNotice('Your sign-in expired. Please sign in again.');
    void auth.signOut();
  }, [auth]);
  const signOut = useCallback(() => {
    setNotice(null);
    void auth.signOut();
  }, [auth]);

  if (status === 'loading') return <p className="splash">Loading…</p>;
  if (status === 'signed-out') return <SignIn auth={auth} codeSignIn={codeSignIn} notice={notice} />;
  return <SignedIn api={api} onExpired={expired} onSignOut={signOut} push={push} />;
}

interface SignedInProps {
  api: Api;
  onExpired: () => void;
  onSignOut: () => void;
  push: AppProps['push'];
}

function SignedIn({ api, onExpired, onSignOut, push }: SignedInProps) {
  const dashboard = useDashboard(api);
  const [tab, goTo] = useTab();
  const now = useNow();
  const [installDismissed, setInstallDismissed] = useState(false);
  const { me, daily, weekly, refresh } = dashboard;

  // Tapping a notification while the app is open: go to its tab, and show the report it announced.
  const openFromNotification = useCallback(
    (path: string) => {
      goTo(tabFromPath(path));
      refresh();
    },
    [goTo, refresh],
  );
  useNotificationOpens(openFromNotification);

  const signInLost = [me, daily, weekly].some((resource) => resource.error?.kind === 'unauthorized');
  useEffect(() => {
    if (signInLost) onExpired();
  }, [signInLost, onExpired]);

  if (signInLost) return <p className="splash">Signing you out…</p>;
  if (me.error?.kind === 'forbidden') return <NotSetUp onSignOut={onSignOut} />;
  if (me.data === null) {
    return me.error ? (
      <main className="screen">
        <h1>Fantasy Helper</h1>
        <LoadProblem error={me.error} onRetry={dashboard.refresh} />
      </main>
    ) : (
      <p className="splash">Loading…</p>
    );
  }

  const reports: Resource<ReportsOut> = tab === 'today' ? daily : weekly;
  const timeZone = me.data.timezone;

  return (
    <div className="app">
      <UpdateBar />
      <header className="top">
        <h1>{TAB_TITLES[tab]}</h1>
        <button
          type="button"
          className="refresh"
          onClick={dashboard.refresh}
          disabled={dashboard.refreshing}
          aria-label="Refresh"
        >
          {dashboard.refreshing ? '…' : '↻'}
        </button>
      </header>
      <main className="content">
        {!installDismissed && <InstallHelp onDismiss={() => setInstallDismissed(true)} />}
        {reports.data && reports.error && (
          <p className="banner banner-stale" role="status">
            Couldn&rsquo;t refresh. Showing what loaded
            {reports.at ? ` at ${formatTime(reports.at.toISOString(), timeZone)}` : ' earlier'}.
          </p>
        )}
        {reports.data ? (
          <ReportsView reports={reports.data} me={me.data} now={now} />
        ) : reports.error ? (
          <LoadProblem error={reports.error} onRetry={dashboard.refresh} />
        ) : (
          <p className="muted">Loading…</p>
        )}
        <Notifications api={api} {...push} />
        <footer className="foot">
          <p className="muted">
            Signed in as {me.data.name} · build {__APP_BUILD__}
          </p>
          <button type="button" className="link" onClick={onSignOut}>
            Sign out
          </button>
        </footer>
      </main>
      <nav className="tabs" aria-label="Reports">
        {(Object.keys(TAB_PATHS) as Tab[]).map((key) => (
          <a
            key={key}
            href={TAB_PATHS[key]}
            aria-current={tab === key ? 'page' : undefined}
            onClick={(event) => {
              event.preventDefault();
              goTo(key);
            }}
          >
            {TAB_TITLES[key]}
          </a>
        ))}
      </nav>
    </div>
  );
}
