import { usePendingUpdate } from '../pwa';

/** Shown when a new version of the app has downloaded. Tapping reloads into it. */
export function UpdateBar() {
  const update = usePendingUpdate();
  if (!update) return null;
  return (
    <div className="update-bar" role="status">
      <span>A new version is ready.</span>
      <button type="button" onClick={update}>
        Update
      </button>
    </div>
  );
}
