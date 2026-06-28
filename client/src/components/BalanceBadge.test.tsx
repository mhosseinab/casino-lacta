import { render, screen } from '@testing-library/react';
import { BalanceBadge } from './BalanceBadge';

describe('BalanceBadge', () => {
  it('formats a fixture balance via formatMinor', () => {
    // 123456 minor GOLD (scale-2) => "1234.56" — formatted only at display.
    render(<BalanceBadge balanceMinor={123456} currency="GOLD" />);
    expect(screen.getByText(/1234\.56/)).toBeInTheDocument();
  });

  it('shows a placeholder until the server balance has loaded', () => {
    render(<BalanceBadge balanceMinor={null} currency="GOLD" />);
    expect(screen.getByText('…')).toBeInTheDocument();
  });
});
