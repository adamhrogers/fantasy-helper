import { formatDay, type Freshness } from '../lib/freshness';

/** Says so when the report on screen isn't from today (or when there is none). Nothing is shown
 *  for a current report: the date and build time sit under each league's name. */
export function FreshnessBanner({ freshness }: { freshness: Freshness }) {
  if (freshness.state === 'current') return null;
  if (freshness.state === 'empty') {
    return (
      <p className="banner banner-stale" role="status">
        No report has been built yet.
      </p>
    );
  }
  const when =
    freshness.daysBehind === 1
      ? `yesterday (${formatDay(freshness.forDate)})`
      : `${formatDay(freshness.forDate)}, ${freshness.daysBehind} days ago`;
  return (
    <p className="banner banner-stale" role="status">
      This report is from {when}. Today&rsquo;s hasn&rsquo;t been built yet.
    </p>
  );
}
