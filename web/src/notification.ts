// What a push message shows and where a tap goes. Pure text handling, shared by the service worker
// (which has no DOM) and the page, so it is tested like any other module.
//
// The server sends {title, body, path, tag} (src/fantasy_helper/domain/push.py). A push that can't be
// read still has to show something: on an iPhone, Safari takes notifications away from an app that
// receives pushes and shows none ("silent push" isn't allowed).

/** The app's own two tabs: the only places a notification may open. */
export const NOTIFICATION_PATHS = ['/today', '/week'] as const;
export type NotificationPath = (typeof NOTIFICATION_PATHS)[number];

export interface ShownNotification {
  title: string;
  body: string;
  /** Replaces a notification already on the screen that has the same tag. */
  tag: string | undefined;
  path: NotificationPath;
}

export const FALLBACK: ShownNotification = {
  title: 'Fantasy Helper',
  body: 'Open the app to see what’s new.',
  tag: undefined,
  path: '/today',
};

const MAX_TITLE = 100;
const MAX_BODY = 200;

/** A path the app can open, else Today. Anything else (another site, another page) is never opened. */
export function safePath(value: unknown): NotificationPath {
  return NOTIFICATION_PATHS.find((path) => path === value) ?? '/today';
}

function text(value: unknown, fallback: string, max: number): string {
  return typeof value === 'string' && value.trim() !== '' ? value.trim().slice(0, max) : fallback;
}

/** The notification to show for what the push carried (its text, if any). Never throws and never
 *  returns nothing: an empty, broken or unexpected message becomes the plain fallback. */
export function describeNotification(raw: string | null | undefined): ShownNotification {
  let data: unknown;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    return FALLBACK;
  }
  if (typeof data !== 'object' || data === null || Array.isArray(data)) return FALLBACK;
  const fields = data as Record<string, unknown>;
  const tag = typeof fields.tag === 'string' && /^[a-z0-9-]{1,64}$/.test(fields.tag) ? fields.tag : undefined;
  return {
    title: text(fields.title, FALLBACK.title, MAX_TITLE),
    body: text(fields.body, FALLBACK.body, MAX_BODY),
    tag,
    path: safePath(fields.path),
  };
}
