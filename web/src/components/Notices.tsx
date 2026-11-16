import type { ApiError } from '../api/client';

/** A whole-screen problem: a bad build, a sign-in that isn't ours, a server that didn't answer. */
export function ProblemScreen({
  title,
  children,
  actions,
}: {
  title: string;
  children?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <main className="screen">
      <h1>{title}</h1>
      {children}
      {actions && <div className="actions">{actions}</div>}
    </main>
  );
}

export function ConfigProblem({ problems }: { problems: string[] }) {
  return (
    <ProblemScreen title="This build isn't set up">
      <p>
        The app was built without: <strong>{problems.join(', ')}</strong>. Set them as the Worker&rsquo;s
        build variables in Cloudflare and build again (docs/runbooks/phone-app.md).
      </p>
    </ProblemScreen>
  );
}

export function NotSetUp({ onSignOut }: { onSignOut: () => void }) {
  return (
    <ProblemScreen
      title="This sign-in isn't set up"
      actions={
        <button type="button" onClick={onSignOut}>
          Sign out
        </button>
      }
    >
      <p>You&rsquo;re signed in, but this app has no account for that email. Sign in with the right one.</p>
    </ProblemScreen>
  );
}

export function LoadProblem({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  return (
    <div className="problem" role="alert">
      <p>{error.message}</p>
      <button type="button" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}
