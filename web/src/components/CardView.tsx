import type { Card } from '../api/types';

// A small label for the kinds the app knows. A kind it doesn't know (the API may add some before
// this app is updated) renders as a plain card: same title, body, bullets and reason, no label.
// Rendering stays generic on purpose: a report is its cards, and nothing here depends on which
// cards exist.
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

/** A card's bullet lines. A report saved before cards had them, or sent by an API older than this
 *  app, has none: the field is missing there, so it is read defensively, whatever the type says. */
export function itemsOf(card: Card): string[] {
  const items: unknown = (card as { items?: unknown }).items;
  return Array.isArray(items) ? items.filter((item): item is string => typeof item === 'string' && item !== '') : [];
}

export function CardView({ card }: { card: Card }) {
  const label = kindLabel(card.kind);
  const items = itemsOf(card);
  return (
    <article className="card" data-kind={card.kind}>
      {label && <p className="card-kind">{label}</p>}
      <h3 className="card-title">{card.title}</h3>
      {card.body && <p className="card-body">{card.body}</p>}
      {items.length > 0 && (
        <ul className="card-items">
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      )}
      {card.reason && <p className="card-reason">{card.reason}</p>}
    </article>
  );
}
