import type { League, Me, ReportDocument, ReportsOut } from '../api/types';
import { formatDay, formatTime, freshness, todayIn } from '../lib/freshness';
import { CardView } from './CardView';
import { FreshnessBanner } from './FreshnessBanner';

const PLATFORMS: Record<string, string> = { espn: 'ESPN', sleeper: 'Sleeper' };

function platformLabel(platform: string): string {
  if (Object.hasOwn(PLATFORMS, platform)) return PLATFORMS[platform] ?? platform;
  return platform ? platform.charAt(0).toUpperCase() + platform.slice(1) : '';
}

interface SectionProps {
  league: League | null;
  report: ReportDocument | null;
  forDate: string | null;
  timeZone: string;
}

function LeagueSection({ league, report, forDate, timeZone }: SectionProps) {
  const name = league?.name ?? 'League';
  const details = [league ? platformLabel(league.platform) : '', report ? `Built ${formatTime(report.generated_at, timeZone)}` : '']
    .filter(Boolean)
    .join(' · ');
  return (
    <section className="league" aria-label={name}>
      <h2 className="league-name">{name}</h2>
      {details && <p className="muted">{details}</p>}
      {report === null ? (
        <p className="card card-plain">
          No report was built for this league{forDate ? ` for ${formatDay(forDate)}` : ''}.
        </p>
      ) : report.cards.length === 0 ? (
        <p className="muted">This report has no cards.</p>
      ) : (
        report.cards.map((card) => <CardView key={card.id} card={card} />)
      )}
    </section>
  );
}

interface ReportsViewProps {
  reports: ReportsOut;
  me: Me;
  now: Date;
}

/** One section per league, one card per decision. A league the user has but the report lacks is
 *  still shown, saying no report was built, so a league that failed overnight isn't invisible. */
export function ReportsView({ reports, me, now }: ReportsViewProps) {
  const state = freshness(reports.for_date, todayIn(me.timezone, now));
  const byLeague = new Map(reports.reports.map((report) => [report.league_id, report]));
  const known = new Set(me.leagues.map((league) => league.id));
  const unknown = reports.reports.filter((report) => !known.has(report.league_id));

  return (
    <>
      <FreshnessBanner freshness={state} />
      {me.leagues.map((league) => (
        <LeagueSection
          key={league.id}
          league={league}
          report={byLeague.get(league.id) ?? null}
          forDate={reports.for_date}
          timeZone={me.timezone}
        />
      ))}
      {unknown.map((report) => (
        <LeagueSection
          key={report.id}
          league={null}
          report={report}
          forDate={reports.for_date}
          timeZone={me.timezone}
        />
      ))}
    </>
  );
}
