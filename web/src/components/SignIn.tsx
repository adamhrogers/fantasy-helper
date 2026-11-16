import { useState, type FormEvent } from 'react';
import { FAILURE_MESSAGES, type AuthClient, type AuthResult } from '../auth/types';
import { InstallHelp } from './InstallHelp';

type Mode = 'password' | 'code-email' | 'code-entry';

interface SignInProps {
  auth: Pick<AuthClient, 'signInWithPassword' | 'sendCode' | 'verifyCode'>;
  /** Offer "Email me a code instead" (the beta's sign-in; needs email set up in Supabase). */
  codeSignIn: boolean;
  /** Why the person is here again, e.g. "Your sign-in expired." */
  notice?: string | null;
}

/** Email and password, which is how Adam signs in. The 6-digit emailed code is the beta's way in
 *  and sits behind a link, off until the build turns it on. Neither creates a user: sign-ups are
 *  off, and the screen can only open an account that already exists. */
export function SignIn({ auth, codeSignIn, notice }: SignInProps) {
  const [mode, setMode] = useState<Mode>('password');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function run(action: () => Promise<AuthResult>, onOk?: () => void) {
    setBusy(true);
    setProblem(null);
    const result = await action();
    setBusy(false);
    if (result.ok) onOk?.();
    else setProblem(FAILURE_MESSAGES[result.reason]);
    // A successful sign-in needs no handling here: the app sees it and swaps this screen out.
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const trimmed = email.trim();
    if (mode === 'password') void run(() => auth.signInWithPassword(trimmed, password));
    else if (mode === 'code-email') void run(() => auth.sendCode(trimmed), () => setMode('code-entry'));
    else void run(() => auth.verifyCode(trimmed, code.trim()));
  }

  function switchTo(next: Mode) {
    setMode(next);
    setProblem(null);
    setCode('');
  }

  return (
    <main className="screen">
      <h1>Fantasy Helper</h1>
      {notice && (
        <p className="banner" role="status">
          {notice}
        </p>
      )}
      <InstallHelp />
      <form onSubmit={submit} aria-label="Sign in">
        {mode === 'code-entry' ? (
          <>
            <p>
              We emailed a 6-digit code to <strong>{email.trim()}</strong>.
            </p>
            <label>
              Code
              <input
                name="code"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={6}
                value={code}
                onChange={(event) => setCode(event.target.value)}
                required
                autoFocus
              />
            </label>
          </>
        ) : (
          <>
            <label>
              Email
              <input
                name="email"
                type="email"
                inputMode="email"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
            </label>
            {mode === 'password' && (
              <label>
                Password
                <input
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </label>
            )}
          </>
        )}
        {problem && (
          <p className="problem-text" role="alert">
            {problem}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? 'One moment…' : mode === 'code-email' ? 'Send code' : 'Sign in'}
        </button>
      </form>
      <div className="alternatives">
        {codeSignIn && mode === 'password' && (
          <button type="button" className="link" onClick={() => switchTo('code-email')}>
            Email me a code instead
          </button>
        )}
        {mode === 'code-entry' && (
          <button type="button" className="link" onClick={() => switchTo('code-email')}>
            Send a new code
          </button>
        )}
        {mode !== 'password' && (
          <button type="button" className="link" onClick={() => switchTo('password')}>
            Use my password instead
          </button>
        )}
      </div>
    </main>
  );
}
