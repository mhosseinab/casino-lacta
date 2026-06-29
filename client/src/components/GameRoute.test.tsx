import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { GameRoute } from './GameRoute';

// GameRoute branches: (1) a registered id renders its lazy view, (2) an
// unregistered id falls back to ComingSoon, (3) a missing :gameId param hits the
// same guard. `originals.unbuilt` is intentionally an id NO step registers, so it
// always exercises the ComingSoon branch (originals.dice is registered as of S9).

describe('GameRoute', () => {
  it('renders the ComingSoon placeholder for an unregistered gameId', () => {
    render(
      <MemoryRouter initialEntries={['/play/originals.unbuilt']}>
        <Routes>
          <Route path="/play/:gameId" element={<GameRoute />} />
        </Routes>
      </MemoryRouter>,
    );

    // ComingSoon shows its heading AND echoes the requested id in a <code> block.
    // Not a tautology: the registry has no view for this id, so the only way both
    // assertions pass is if GameRoute resolved the unknown id to ComingSoon
    // (if it had rendered a view or thrown, "Coming soon" + the id text are absent).
    expect(
      screen.getByRole('heading', { name: /coming soon/i }),
    ).toBeInTheDocument();
    expect(screen.getByText('originals.unbuilt')).toBeInTheDocument();
  });

  it('renders ComingSoon when no :gameId param is present (guard branch)', () => {
    render(
      <MemoryRouter initialEntries={['/play']}>
        <Routes>
          <Route path="/play" element={<GameRoute />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(
      screen.getByRole('heading', { name: /coming soon/i }),
    ).toBeInTheDocument();
    expect(screen.getByText('(none)')).toBeInTheDocument();
  });
});
