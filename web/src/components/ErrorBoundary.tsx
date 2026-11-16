import { Component, type ReactNode } from 'react';
import { ProblemScreen } from './Notices';

interface Props {
  children: ReactNode;
}

/** A render error anywhere below shows this screen instead of a blank installed app. The message
 *  stays general on purpose: a card's text or an error's details never go on screen or to a log. */
export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  // No componentDidCatch on purpose: nothing is sent anywhere. There is no error reporting yet, and
  // the app holds a signed-in session, so details stay off the wire.

  render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <ProblemScreen
        title="Something went wrong"
        actions={
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        }
      >
        <p>The app hit a problem showing this screen. Reloading usually fixes it. If it keeps happening, tell Claude.</p>
      </ProblemScreen>
    );
  }
}
