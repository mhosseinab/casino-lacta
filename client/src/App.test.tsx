import { render, screen } from '@testing-library/react';
import { App } from './App';

describe('App', () => {
  it('renders the app heading', () => {
    render(<App />);
    expect(
      screen.getByRole('heading', { name: /casino-lacta/i }),
    ).toBeInTheDocument();
  });

  it('surfaces the persistent play-money notice', () => {
    render(<App />);
    expect(screen.getByText(/not real money/i)).toBeInTheDocument();
  });
});
