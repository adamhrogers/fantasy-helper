import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import {
  LEAGUE_A,
  LEAGUE_B,
  ME,
  NOW,
  fail,
  fakeApi,
  fakeAuth,
  ok,
  report,
  reports,
} from './test-fixtures';

beforeEach(() => {
  window.history.replaceState(null, '', '/');
  vi.useFakeTimers({ toFake: ['Date'], now: NOW });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('signed out', () => {
  it('shows the sign-in screen and nothing else, and asks the API for nothing', async () => {
    const api = fakeApi();
    render(<App auth={fakeAuth(false)} api={api} codeSignIn={false} />);
    expect(await screen.findByRole('form', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByText('Card for Test League A')).toBeNull();
    expect(api.me).not.toHaveBeenCalled();
    expect(api.reports).not.toHaveBeenCalled();
  });

  it('moves to the reports when the sign-in succeeds', async () => {
    const auth = fakeAuth(false);
    render(<App auth={auth} api={fakeApi()} codeSignIn={false} />);
    await screen.findByRole('form', { name: 'Sign in' });
    auth.emit(true);
    expect(await screen.findByText('Card for Test League A')).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Sign in' })).toBeNull();
  });
});

describe('signed in', () => {
  it('opens on Today, with one section per league', async () => {
    const api = fakeApi();
    render(<App auth={fakeAuth(true)} api={api} codeSignIn={false} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Today' })).toBeInTheDocument();
    expect(await screen.findByRole('region', { name: 'Test League A' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Test League B' })).toBeInTheDocument();
    expect(api.reports).toHaveBeenCalledWith('daily');
    expect(window.location.pathname).toBe('/today');
  });

  it('switches to This Week, which shows the weekly report', async () => {
    const api = fakeApi({
      reports: vi.fn(async (kind: string) =>
        ok(
          reports('2026-11-16', [
            report(LEAGUE_A, { kind: kind as 'daily', cards: [{ id: 'x', kind: 'move', title: `The ${kind} card`, body: 'b' }] }),
          ]),
        ),
      ),
    });
    const user = userEvent.setup();
    render(<App auth={fakeAuth(true)} api={api} codeSignIn={false} />);
    expect(await screen.findByText('The daily card')).toBeInTheDocument();
    await user.click(within(screen.getByRole('navigation')).getByRole('link', { name: 'This Week' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'This Week' })).toBeInTheDocument();
    expect(screen.getByText('The weekly card')).toBeInTheDocument();
    expect(screen.queryByText('The daily card')).toBeNull();
    expect(window.location.pathname).toBe('/week');
    expect(screen.getByRole('link', { name: 'This Week' })).toHaveAttribute('aria-current', 'page');
  });

  it('opens straight on This Week from /week', async () => {
    window.history.replaceState(null, '', '/week');
    render(<App auth={fakeAuth(true)} api={fakeApi()} codeSignIn={false} />);
    expect(await screen.findByRole('heading', { level: 1, name: 'This Week' })).toBeInTheDocument();
  });

  it('says so, above the cards, when the latest report is from yesterday', async () => {
    const api = fakeApi({
      reports: vi.fn(async () => ok(reports('2026-11-15', [report(LEAGUE_A, { for_date: '2026-11-15' })]))),
    });
    render(<App auth={fakeAuth(true)} api={api} codeSignIn={false} />);
    expect(await screen.findByRole('status')).toHaveTextContent('This report is from yesterday (Sun, Nov 15)');
  });

  it('shows who is signed in and signs out on request', async () => {
    const auth = fakeAuth(true);
    const user = userEvent.setup();
    render(<App auth={auth} api={fakeApi()} codeSignIn={false} />);
    expect(await screen.findByText(/Signed in as Test User/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(auth.signOut).toHaveBeenCalled();
  });

  it('refreshes on request', async () => {
    const api = fakeApi();
    const user = userEvent.setup();
    render(<App auth={fakeAuth(true)} api={api} codeSignIn={false} />);
    await screen.findByRole('region', { name: 'Test League A' });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled());
    expect(api.me).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(api.me).toHaveBeenCalledTimes(2));
  });
});

describe('when something is wrong', () => {
  it('says this sign-in is not set up, with a way out, when the API answers 403', async () => {
    const auth = fakeAuth(true);
    const user = userEvent.setup();
    render(<App auth={auth} api={fakeApi({ me: vi.fn(async () => fail('forbidden')) })} codeSignIn={false} />);
    expect(await screen.findByRole('heading', { name: "This sign-in isn't set up" })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(auth.signOut).toHaveBeenCalled();
  });

  it('signs out and says why when the API answers 401', async () => {
    const auth = fakeAuth(true);
    auth.signOut.mockImplementation(async () => {
      auth.emit(false);
      return undefined;
    });
    render(<App auth={auth} api={fakeApi({ me: vi.fn(async () => fail('unauthorized')) })} codeSignIn={false} />);
    expect(await screen.findByRole('form', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Your sign-in expired. Please sign in again.');
  });

  it('offers a retry when the API cannot be reached at first', async () => {
    const me = vi.fn().mockResolvedValueOnce(fail('network', "Couldn't reach the server.")).mockResolvedValue(ok(ME));
    const user = userEvent.setup();
    render(<App auth={fakeAuth(true)} api={fakeApi({ me })} codeSignIn={false} />);
    expect(await screen.findByRole('alert')).toHaveTextContent("Couldn't reach the server.");
    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('region', { name: 'Test League A' })).toBeInTheDocument();
  });

  it('keeps showing the reports it has when a refresh fails, and says so', async () => {
    // Each report loads once, then every later request fails (the connection dropped).
    const seen = new Set<string>();
    const api = fakeApi({
      reports: vi.fn(async (kind: string) => {
        if (seen.has(kind)) return fail('network');
        seen.add(kind);
        return ok(reports('2026-11-16', [report(LEAGUE_A), report(LEAGUE_B)]));
      }),
    });
    const user = userEvent.setup();
    render(<App auth={fakeAuth(true)} api={api} codeSignIn={false} />);
    await screen.findByText('Card for Test League A');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled());
    await user.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText(/Couldn.t refresh\. Showing what loaded at 10:00\sAM\./)).toBeInTheDocument();
    expect(screen.getByText('Card for Test League A')).toBeInTheDocument();
  });

  it('offers a retry on a tab whose report could not load, while the other tab still works', async () => {
    const api = fakeApi({
      reports: vi.fn(async (kind: string) => (kind === 'daily' ? fail('server', 'The server had a problem (status 500).') : ok(reports('2026-11-16', [report(LEAGUE_A)])))),
    });
    const user = userEvent.setup();
    render(<App auth={fakeAuth(true)} api={api} codeSignIn={false} />);
    expect(await screen.findByRole('alert')).toHaveTextContent('status 500');
    await user.click(screen.getByRole('link', { name: 'This Week' }));
    expect(await screen.findByText('Card for Test League A')).toBeInTheDocument();
  });
});
