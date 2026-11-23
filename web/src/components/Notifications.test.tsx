import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { fail, fakeApi } from '../test-fixtures';
import { DESKTOP, FakeBrowser, IPHONE_APP, IPHONE_SAFARI, subscription } from '../test-push-fixtures';
import { Notifications } from './Notifications';

describe('the notifications control', () => {
  it('offers to enable them, and asks the phone for permission only when it is tapped', async () => {
    const browser = new FakeBrowser();
    render(<Notifications api={fakeApi()} platform={browser} device={IPHONE_APP} />);
    const button = await screen.findByRole('button', { name: 'Enable notifications' });
    expect(browser.requestPermission).not.toHaveBeenCalled(); // never on load
    expect(browser.subscribe).not.toHaveBeenCalled();
    await userEvent.click(button);
    expect(await screen.findByRole('button', { name: 'Turn off' })).toBeInTheDocument();
    expect(browser.requestPermission).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Notifications are on/)).toBeInTheDocument();
  });

  it('turns them off again, back to the offer', async () => {
    const browser = new FakeBrowser();
    browser.state = 'granted';
    browser.current = subscription();
    const api = fakeApi();
    render(<Notifications api={api} platform={browser} device={IPHONE_APP} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Turn off' }));
    expect(await screen.findByRole('button', { name: 'Enable notifications' })).toBeInTheDocument();
    expect(api.removeSubscription).toHaveBeenCalledTimes(1);
  });

  it('on an iPhone outside the Home Screen shows a hint and no button', async () => {
    render(<Notifications api={fakeApi()} platform={new FakeBrowser()} device={IPHONE_SAFARI} />);
    expect(await screen.findByText(/add this app to your Home Screen/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('where the browser can’t do push shows a hint and no button', async () => {
    const browser = new FakeBrowser();
    browser.supported = false;
    render(<Notifications api={fakeApi()} platform={browser} device={DESKTOP} />);
    expect(await screen.findByText(/can’t show notifications/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('says how to unblock them after a refusal, with no button that can’t work', async () => {
    const browser = new FakeBrowser();
    browser.state = 'denied';
    render(<Notifications api={fakeApi()} platform={browser} device={IPHONE_APP} />);
    expect(await screen.findByText(/blocked for this app/)).toBeInTheDocument();
    expect(screen.queryByRole('button')).toBeNull();
  });

  it('turns a refusal at the prompt into the blocked hint', async () => {
    const browser = new FakeBrowser();
    browser.answer = 'denied';
    render(<Notifications api={fakeApi()} platform={browser} device={IPHONE_APP} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Enable notifications' }));
    expect(await screen.findByText(/blocked for this app/)).toBeInTheDocument();
  });

  it('says what went wrong and keeps the button, so it can be tried again', async () => {
    const browser = new FakeBrowser();
    let failing = true;
    const api = fakeApi({
      saveSubscription: async (body: { endpoint: string }) =>
        failing ? fail('network', 'Couldn’t reach the server. Check your connection and try again.') : { ok: true, data: body },
    });
    render(<Notifications api={api} platform={browser} device={IPHONE_APP} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Enable notifications' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Couldn’t reach the server');
    failing = false;
    await userEvent.click(screen.getByRole('button', { name: 'Enable notifications' }));
    expect(await screen.findByRole('button', { name: 'Turn off' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull(); // the old problem is gone once a tap works
  });

  it('can’t be tapped twice while it is working', async () => {
    const browser = new FakeBrowser();
    let release: () => void = () => undefined;
    browser.requestPermission.mockImplementationOnce(
      () =>
        new Promise<NotificationPermission>((resolve) => {
          release = () => resolve('granted');
        }),
    );
    render(<Notifications api={fakeApi()} platform={browser} device={IPHONE_APP} />);
    await userEvent.click(await screen.findByRole('button', { name: 'Enable notifications' }));
    expect(screen.getByRole('button', { name: 'Working…' })).toBeDisabled();
    release();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Turn off' })).toBeInTheDocument());
  });

  it('shows nothing until it knows where things stand, instead of a button that then changes', async () => {
    const api = fakeApi();
    const browser = new FakeBrowser();
    browser.state = 'granted';
    browser.current = subscription();
    api.pushPublicKey.mockImplementationOnce(() => new Promise(() => undefined)); // never answers
    render(<Notifications api={api} platform={browser} device={IPHONE_APP} />);
    await waitFor(() => expect(api.pushPublicKey).toHaveBeenCalled());
    expect(screen.queryByRole('button')).toBeNull();
  });
});
