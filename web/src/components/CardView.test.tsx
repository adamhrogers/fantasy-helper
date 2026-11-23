import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Card } from '../api/types';
import { card } from '../test-fixtures';
import { CardView } from './CardView';

describe('CardView', () => {
  it('shows the title, the body, and the reason on its own line', () => {
    render(<CardView card={card()} />);
    expect(screen.getByRole('heading', { level: 3, name: 'Add a player, drop another' })).toBeInTheDocument();
    const body = screen.getByText('A short body that says what to do.');
    const reason = screen.getByText('Because the numbers say so.');
    expect(body.tagName).toBe('P');
    expect(reason.tagName).toBe('P');
    expect(reason).not.toBe(body); // separate elements: the reason is on its own line
    expect(reason).toHaveClass('card-reason');
  });

  it('labels the kinds it knows', () => {
    render(<CardView card={card({ kind: 'lock_or_pass' })} />);
    expect(screen.getByText('Lock or pass')).toBeInTheDocument();
  });

  it('renders a kind it has never heard of as a plain card: same content, no label, no crash', () => {
    const unknown = card({ kind: 'brand_new_kind' as Card['kind'], title: 'Something new' });
    const { container } = render(<CardView card={unknown} />);
    expect(screen.getByRole('heading', { name: 'Something new' })).toBeInTheDocument();
    expect(screen.getByText('A short body that says what to do.')).toBeInTheDocument();
    expect(screen.getByText('Because the numbers say so.')).toBeInTheDocument();
    expect(container.querySelector('.card-kind')).toBeNull();
  });

  it('does not mistake a name from JavaScript’s own objects for a kind it knows', () => {
    const { container } = render(<CardView card={card({ kind: 'constructor' as Card['kind'] })} />);
    expect(container.querySelector('.card-kind')).toBeNull();
  });

  it('leaves out a reason or a body that is not there', () => {
    const { container } = render(<CardView card={card({ reason: null, body: '' })} />);
    expect(container.querySelector('.card-reason')).toBeNull();
    expect(container.querySelector('.card-body')).toBeNull();
    expect(screen.getByRole('heading')).toBeInTheDocument();
  });

  it('shows each item as its own bullet in a real list, between the body and the reason', () => {
    const { container } = render(
      <CardView card={card({ body: 'The best games of Sat Nov 21.', items: ['Player One 61.5 (yours)', 'Player Two 55.0'] })} />,
    );
    const list = screen.getByRole('list');
    expect(within(list).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Player One 61.5 (yours)',
      'Player Two 55.0',
    ]);
    const order = Array.from(container.querySelector('.card')?.children ?? []).map((el) => el.className);
    expect(order).toEqual(['card-kind', 'card-title', 'card-body', 'card-items', 'card-reason']);
  });

  it('draws no list for a card without items, and copes with a report saved before items existed', () => {
    const { container, rerender } = render(<CardView card={card({ items: [] })} />);
    expect(container.querySelector('ul')).toBeNull();
    const saved: Partial<Card> = card();
    delete saved.items; // what a report saved before items existed looks like
    rerender(<CardView card={saved as Card} />);
    expect(container.querySelector('ul')).toBeNull();
    expect(screen.getByText('A short body that says what to do.')).toBeInTheDocument();
    rerender(<CardView card={{ ...saved, items: null } as unknown as Card} />);
    expect(container.querySelector('ul')).toBeNull();
  });

  it('gives a kind it has never heard of its bullets too', () => {
    render(<CardView card={card({ kind: 'brand_new_kind' as Card['kind'], items: ['One', 'Two'] })} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('shows text as text, never as markup', () => {
    const { container } = render(
      <CardView card={card({ title: '<img src=x onerror=alert(1)>', items: ['<img src=y onerror=alert(2)>'] })} />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('listitem').textContent).toBe('<img src=y onerror=alert(2)>');
    expect(screen.getByRole('heading').textContent).toBe('<img src=x onerror=alert(1)>');
  });
});
