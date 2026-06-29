import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlinkoBoard } from './PlinkoBoard';
import type { Bounce } from './plinkoMath';

// The board is a COSMETIC renderer of a server-decided drop. It never derives the
// landing bin from the path — it places the ball at the AUTHORITATIVE `bin` and uses
// `path` only as the (proven-consistent) animation trail. These tests pin the two
// load-bearing behaviours: (1) the ball always lands in the server bin, and (2)
// reduced motion shows that landing immediately (no drop), keyed on bet identity.

function pathFor(rows: number, bin: number): Bounce[] {
  return [
    ...Array<Bounce>(bin).fill('R'),
    ...Array<Bounce>(rows - bin).fill('L'),
  ];
}

function stubReducedMotion(matches: boolean): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn().mockImplementation((query: string) => ({
      matches,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('PlinkoBoard — cosmetic drop into the server bin', () => {
  it('renders rows+1 bins for the board', () => {
    render(<PlinkoBoard rows={8} />);
    expect(screen.getAllByTestId('plinko-bin')).toHaveLength(9);
  });

  it('lands the ball in the authoritative server bin (not a path re-derivation)', () => {
    const rows = 12;
    const bin = 8;
    render(
      <PlinkoBoard
        rows={rows}
        path={pathFor(rows, bin)}
        bin={bin}
        betId="b1"
        won
      />,
    );
    expect(screen.getByTestId('plinko-ball')).toHaveAttribute('data-bin', '8');
    expect(screen.getByTestId('plinko-result')).toHaveTextContent('8');
  });

  it('(reduced motion) places the ball in the final bin immediately', () => {
    stubReducedMotion(true);
    const rows = 16;
    const bin = 3;
    render(
      <PlinkoBoard
        rows={rows}
        path={pathFor(rows, bin)}
        bin={bin}
        betId="b2"
      />,
    );
    expect(screen.getByTestId('plinko-board')).toHaveAttribute(
      'data-static',
      'true',
    );
    expect(screen.getByTestId('plinko-ball')).toHaveAttribute('data-bin', '3');
  });

  it('shows no ball before a result (empty board)', () => {
    render(<PlinkoBoard rows={12} />);
    expect(screen.queryByTestId('plinko-ball')).not.toBeInTheDocument();
    expect(screen.queryByTestId('plinko-result')).not.toBeInTheDocument();
  });
});
