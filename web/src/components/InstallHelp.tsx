import { currentDevice, installHelp } from '../lib/install';

/** On an iPhone, outside the installed app: how to add it to the Home Screen. Signing in inside
 *  Safari doesn't carry over to the Home Screen app, so this comes before sign-in. */
export function InstallHelp({ onDismiss }: { onDismiss?: () => void }) {
  const help = installHelp(currentDevice());
  if (!help.show) return null;
  return (
    <aside className="install" aria-label="Install on your iPhone">
      <h2>Add this to your Home Screen</h2>
      {help.inSafari ? (
        <ol>
          <li>
            Tap the <strong>Share</strong> button (the square with an arrow) in Safari&rsquo;s toolbar.
          </li>
          <li>
            Scroll down and tap <strong>Add to Home Screen</strong>, then <strong>Add</strong>.
          </li>
          <li>
            Open <strong>Fantasy</strong> from your Home Screen and sign in there. Signing in here, in
            Safari, won&rsquo;t carry over.
          </li>
        </ol>
      ) : (
        <p>Open this page in Safari to add it to your Home Screen. Other browsers can&rsquo;t install it.</p>
      )}
      {onDismiss && (
        <button type="button" className="link" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </aside>
  );
}
