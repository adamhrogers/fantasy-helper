import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { LEAGUE_A, LEAGUE_B, ME, NOW, card, report, reports } from '../test-fixtures';
import { ReportsView } from './ReportsView';

describe('ReportsView', () => {
  it('shows one section per league, each with its own cards, its platform and when it was built', () => {
    render(<ReportsView reports={reports('2026-11-16', [report(LEAGUE_A), report(LEAGUE_B)])} me={ME} now={NOW} />);
    const a = screen.getByRole('region', { name: 'Test League A' });
    expect(within(a).getByText('Card for Test League A')).toBeInTheDocument();
    expect(within(a).getByText(/ESPN/)).toBeInTheDocument();
    expect(within(a).getByText(/Built 6:19\sAM/)).toBeInTheDocument();
    const b = screen.getByRole('region', { name: 'Test League B' });
    expect(within(b).getByText('Card for Test League B')).toBeInTheDocument();
    expect(within(b).getByText(/Sleeper/)).toBeInTheDocument();
  });

  it('shows no banner when the report is from today in the user’s zone', () => {
    render(<ReportsView reports={reports('2026-11-16', [report(LEAGUE_A)])} me={ME} now={NOW} />);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('says so when the latest report is from yesterday', () => {
    const yesterday = report(LEAGUE_A, { for_date: '2026-11-15' });
    render(<ReportsView reports={reports('2026-11-15', [yesterday])} me={ME} now={NOW} />);
    expect(screen.getByRole('status')).toHaveTextContent('This report is from yesterday (Sun, Nov 15). Today’s hasn’t been built yet.');
  });

  it('says how old a report from several days ago is', () => {
    const old = report(LEAGUE_A, { for_date: '2026-11-12' });
    render(<ReportsView reports={reports('2026-11-12', [old])} me={ME} now={NOW} />);
    expect(screen.getByRole('status')).toHaveTextContent('from Thu, Nov 12, 4 days ago');
  });

  it('judges “today” by the user’s time zone: a 10 pm evening is still the same day', () => {
    const lateEvening = new Date('2026-11-17T03:00:00Z'); // 10 pm on the 16th in New York
    render(<ReportsView reports={reports('2026-11-16', [report(LEAGUE_A)])} me={ME} now={lateEvening} />);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('says so when there is no report at all, and still shows the leagues', () => {
    render(<ReportsView reports={reports(null, [])} me={ME} now={NOW} />);
    expect(screen.getByRole('status')).toHaveTextContent('No report has been built yet.');
    expect(screen.getByRole('region', { name: 'Test League A' })).toHaveTextContent('No report was built for this league.');
  });

  it('does not hide a league that has no report on that date', () => {
    render(<ReportsView reports={reports('2026-11-16', [report(LEAGUE_A)])} me={ME} now={NOW} />);
    expect(screen.getByRole('region', { name: 'Test League B' })).toHaveTextContent(
      'No report was built for this league for Mon, Nov 16.',
    );
  });

  it('shows a report whose league the user does not list, under a plain heading', () => {
    const stray = report({ id: '33333333-3333-4333-8333-333333333333', name: 'x', platform: 'espn' });
    render(<ReportsView reports={reports('2026-11-16', [stray])} me={ME} now={NOW} />);
    expect(screen.getByRole('region', { name: 'League' })).toBeInTheDocument();
  });

  it('says so when a report has no cards', () => {
    render(<ReportsView reports={reports('2026-11-16', [report(LEAGUE_A, { cards: [] })])} me={ME} now={NOW} />);
    expect(screen.getByRole('region', { name: 'Test League A' })).toHaveTextContent('This report has no cards.');
  });

  it('keeps the cards in the order the report gave them, even for kinds it does not know', () => {
    const cards = [
      card({ id: '1', title: 'First', kind: 'recap' }),
      card({ id: '2', title: 'Second', kind: 'zzz' as never }),
      card({ id: '3', title: 'Third', kind: 'move' }),
    ];
    render(<ReportsView reports={reports('2026-11-16', [report(LEAGUE_A, { cards })])} me={ME} now={NOW} />);
    const titles = within(screen.getByRole('region', { name: 'Test League A' }))
      .getAllByRole('heading', { level: 3 })
      .map((heading) => heading.textContent);
    expect(titles).toEqual(['First', 'Second', 'Third']);
  });
});
