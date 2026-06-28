import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Lobby } from './Lobby';

function renderLobby() {
  return render(
    <MemoryRouter>
      <Lobby />
    </MemoryRouter>,
  );
}

describe('Lobby', () => {
  it('renders exactly 16 in-scope game cards (no PvP poker)', () => {
    renderLobby();
    // Each card is a router Link to /play/:gameId.
    expect(screen.getAllByRole('link')).toHaveLength(16);
  });

  it('shows human display names for a sample of the games', () => {
    renderLobby();
    expect(screen.getByText('Dice')).toBeInTheDocument();
    expect(screen.getByText('Blackjack')).toBeInTheDocument();
    expect(screen.getByText('Crash')).toBeInTheDocument();
  });

  it('links each card to its /play/:gameId route', () => {
    renderLobby();
    const dice = screen.getByText('Dice').closest('a');
    expect(dice).toHaveAttribute('href', '/play/originals.dice');
  });
});
