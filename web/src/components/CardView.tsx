import type { Card } from '../api/types';

// A small label for the kinds the app knows. A kind it doesn't know (the API may add some before
// this app is updated) renders as a plain card: same title, body and reason, no label. Rendering
// stays generic on purpose: a report is its cards, and nothing here depends on which cards exist.
// Typed against the API's own list of kinds (generated from its schema), so a kind added or renamed
// there shows up here as a type error instead of an unlabeled card.
const KIND_LABELS: Record<Card['kind'], string> = {
  move: 'Move',
  lock_or_pass: 'Lock or pass',
  signal: 'Signal',
  recap: 'Recap',
  league_moves: 'League moves',
  news: 'News',
  matchup: 'Matchup',
  self_review: 'Self-review',
  report_card: 'Report card',
};

export function kindLabel(kind: string): string | null {
  return Object.hasOwn(KIND_LABELS, kind) ? KIND_LABELS[kind as Card['kind']] : null;
}

export function CardView({ card }: { card: Card }) {
  const label = kindLabel(card.kind);
  return (
    <article className="card" data-kind={card.kind}>
      {label && <p className="card-kind">{label}</p>}
      <h3 className="card-title">{card.title}</h3>
      {card.body && <p className="card-body">{card.body}</p>}
      {card.reason && <p className="card-reason">{card.reason}</p>}
    </article>
  );
}
