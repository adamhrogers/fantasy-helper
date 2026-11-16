import { render, screen } from '@testing-library/react';
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

  it('shows text as text, never as markup', () => {
    const { container } = render(<CardView card={card({ title: '<img src=x onerror=alert(1)>' })} />);
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByRole('heading').textContent).toBe('<img src=x onerror=alert(1)>');
  });
});
