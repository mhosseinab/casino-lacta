import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BetControls } from './BetControls';

// BetControls is a thin renderer: it validates the stake to INTEGER MINOR UNITS at the
// (hostile) client boundary and hands the integer to a parent-provided onBet — it
// decides NOTHING about outcome, payout, or balance. These tests pin the contract:
// integer-minor-units validation (reusing money.ts), integer-only quick-stakes, and
// VERBATIM surfacing of a server rejection reason passed down by the parent.

describe('BetControls', () => {
  it('rejects a sub-cent float stake and does not call onBet', async () => {
    // money.ts reads a decimal as MAJOR units at scale-2, so "1.005" is finer than the
    // currency can represent → parseStakeToMinor returns an Error. The field must
    // surface that and NOT fire onBet (the server never sees an unrepresentable stake).
    const onBet = vi.fn();
    render(<BetControls onBet={onBet} />);

    const field = screen.getByLabelText(/stake/i);
    await userEvent.clear(field);
    await userEvent.type(field, '1.005');
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(onBet).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('emits integer minor units for bare-digit stakes', async () => {
    const onBet = vi.fn();
    render(<BetControls onBet={onBet} />);

    const field = screen.getByLabelText(/stake/i);
    await userEvent.clear(field);
    await userEvent.type(field, '250');
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(onBet).toHaveBeenCalledTimes(1);
    expect(onBet).toHaveBeenCalledWith(250);
  });

  it('parses a decimal stake as major units (1.50 GOLD -> 150 minor)', async () => {
    // Documents the major-units semantics: a valid decimal is NOT rejected; only
    // sub-scale precision (above) is. This is why "float" alone is not the test.
    const onBet = vi.fn();
    render(<BetControls onBet={onBet} />);

    const field = screen.getByLabelText(/stake/i);
    await userEvent.clear(field);
    await userEvent.type(field, '1.50');
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(onBet).toHaveBeenCalledWith(150);
  });

  it('halves with integer floor (odd stake 25 -> 12, never 12.5)', async () => {
    const onBet = vi.fn();
    render(<BetControls onBet={onBet} defaultStakeMinor={25} />);

    await userEvent.click(screen.getByRole('button', { name: /½/ }));
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(onBet).toHaveBeenCalledWith(12);
  });

  it('doubles the current stake (integer math)', async () => {
    const onBet = vi.fn();
    render(<BetControls onBet={onBet} defaultStakeMinor={25} />);

    await userEvent.click(screen.getByRole('button', { name: /2×/ }));
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(onBet).toHaveBeenCalledWith(50);
  });

  it('sets max to the available balance', async () => {
    const onBet = vi.fn();
    render(
      <BetControls onBet={onBet} defaultStakeMinor={25} balanceMinor={1000} />,
    );

    await userEvent.click(screen.getByRole('button', { name: /max/i }));
    await userEvent.click(screen.getByRole('button', { name: /^bet$/i }));

    expect(onBet).toHaveBeenCalledWith(1000);
  });

  it('renders a server rejection reason passed down by the parent', () => {
    render(
      <BetControls
        onBet={vi.fn()}
        rejectionReason="Bet exceeds the table maximum of 500.00 GOLD"
      />,
    );

    expect(
      screen.getByText(/exceeds the table maximum of 500.00 GOLD/i),
    ).toBeInTheDocument();
  });
});
