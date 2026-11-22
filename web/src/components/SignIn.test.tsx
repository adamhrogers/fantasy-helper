import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FAILURE_MESSAGES, type AuthClient } from '../auth/types';
import { SignIn } from './SignIn';

function setup(codeSignIn = false, notice: string | null = null) {
  const auth = {
    signInWithPassword: vi.fn<AuthClient['signInWithPassword']>(async () => ({ ok: true })),
    sendCode: vi.fn<AuthClient['sendCode']>(async () => ({ ok: true })),
    verifyCode: vi.fn<AuthClient['verifyCode']>(async () => ({ ok: true })),
  };
  const user = userEvent.setup();
  render(<SignIn auth={auth} codeSignIn={codeSignIn} notice={notice} />);
  return { auth, user };
}

describe('sign-in with a password', () => {
  it('is the screen you get, with no way to sign up', () => {
    setup();
    expect(screen.getByLabelText('Email')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password');
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.queryByText(/sign up|create account|register/i)).toBeNull();
  });

  it('sends the email (trimmed) and the password', async () => {
    const { auth, user } = setup();
    await user.type(screen.getByLabelText('Email'), '  me@example.com ');
    await user.type(screen.getByLabelText('Password'), 'hunter2');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(auth.signInWithPassword).toHaveBeenCalledWith('me@example.com', 'hunter2');
  });

  it('says plainly when the email and password do not match, and lets you try again', async () => {
    const { auth, user } = setup();
    auth.signInWithPassword.mockResolvedValueOnce({ ok: false, reason: 'bad_credentials' });
    await user.type(screen.getByLabelText('Email'), 'me@example.com');
    await user.type(screen.getByLabelText('Password'), 'wrong');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(FAILURE_MESSAGES.bad_credentials);
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled();
  });

  it('says so when the sign-in service cannot be reached', async () => {
    const { auth, user } = setup();
    auth.signInWithPassword.mockResolvedValueOnce({ ok: false, reason: 'network' });
    await user.type(screen.getByLabelText('Email'), 'me@example.com');
    await user.type(screen.getByLabelText('Password'), 'pw');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(FAILURE_MESSAGES.network);
  });

  it('is disabled while a sign-in is in flight, so a double tap sends one request', async () => {
    const { auth, user } = setup();
    let release: (value: { ok: true }) => void = () => undefined;
    auth.signInWithPassword.mockReturnValueOnce(new Promise<{ ok: true }>((resolve) => (release = resolve)));
    await user.type(screen.getByLabelText('Email'), 'me@example.com');
    await user.type(screen.getByLabelText('Password'), 'pw');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(screen.getByRole('button', { name: /one moment/i })).toBeDisabled();
    release({ ok: true });
    expect(auth.signInWithPassword).toHaveBeenCalledTimes(1);
  });

  it('shows why you are back here, when the app signed you out', () => {
    setup(false, 'Your sign-in expired. Please sign in again.');
    expect(screen.getByRole('status')).toHaveTextContent('Your sign-in expired.');
  });

  it('does not offer the emailed code unless the build turns it on', () => {
    setup(false);
    expect(screen.queryByRole('button', { name: /code/i })).toBeNull();
  });
});

describe('sign-in with an emailed code', () => {
  it('sends a code to the email, then takes the 6 digits (numeric keypad, one-time-code autofill)', async () => {
    const { auth, user } = setup(true);
    await user.click(screen.getByRole('button', { name: 'Email me a code instead' }));
    expect(screen.queryByLabelText('Password')).toBeNull();
    await user.type(screen.getByLabelText('Email'), 'friend@example.com');
    await user.click(screen.getByRole('button', { name: 'Send code' }));
    expect(auth.sendCode).toHaveBeenCalledWith('friend@example.com');

    const code = await screen.findByLabelText('Code');
    expect(code).toHaveAttribute('inputmode', 'numeric');
    expect(code).toHaveAttribute('autocomplete', 'one-time-code');
    expect(code).toHaveAttribute('maxlength', '6');
    expect(screen.getByText('friend@example.com')).toBeInTheDocument();

    await user.type(code, '123456');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(auth.verifyCode).toHaveBeenCalledWith('friend@example.com', '123456');
  });

  it('says so when the email has no account here, and stays on the email step', async () => {
    const { auth, user } = setup(true);
    auth.sendCode.mockResolvedValueOnce({ ok: false, reason: 'not_a_user' });
    await user.click(screen.getByRole('button', { name: 'Email me a code instead' }));
    await user.type(screen.getByLabelText('Email'), 'stranger@example.com');
    await user.click(screen.getByRole('button', { name: 'Send code' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(FAILURE_MESSAGES.not_a_user);
    expect(screen.queryByLabelText('Code')).toBeNull();
  });

  it('says so when the code is wrong or expired', async () => {
    const { auth, user } = setup(true);
    auth.verifyCode.mockResolvedValueOnce({ ok: false, reason: 'bad_code' });
    await user.click(screen.getByRole('button', { name: 'Email me a code instead' }));
    await user.type(screen.getByLabelText('Email'), 'friend@example.com');
    await user.click(screen.getByRole('button', { name: 'Send code' }));
    await user.type(await screen.findByLabelText('Code'), '000000');
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(FAILURE_MESSAGES.bad_code);
  });

  it('goes back to the password', async () => {
    const { user } = setup(true);
    await user.click(screen.getByRole('button', { name: 'Email me a code instead' }));
    await user.click(screen.getByRole('button', { name: 'Use my password instead' }));
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
  });
});
