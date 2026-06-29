import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { FairnessDisclosure } from '../contracts';
import type { FairnessResult, GameClient } from '../lib/transport';
import { GameClientProvider } from '../lib/transport';
import { FairnessDrawer } from './FairnessDrawer';

// A real-shaped /fairness disclosure (field names from contracts FairnessDisclosure
// / app/api/fairness.py). The verifierUrl mirrors app's `_verifier_link` convention
// VERBATIM — keyed on the fairness triple (gameId, clientSeed, nonce, serverSeedHash),
// NOT on betId — so this documents the real link shape the server stamps.
const FIXTURE: FairnessDisclosure = {
  betId: 'bet_abc123',
  gameId: 'originals.dice',
  serverSeedHash:
    'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6abcd',
  serverSeed: null,
  revealed: false,
  clientSeed: 'player-client-seed-xyz',
  nonce: 7,
  derivation:
    "bytes = HMAC_SHA256(serverSeed, f'{clientSeed}:{nonce}:{cursor}'); float = uint32(bytes[:4]) / 2**32",
  verifierUrl:
    '/verifier/index.html?gameId=originals.dice&clientSeed=player-client-seed-xyz&nonce=7&serverSeedHash=a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6abcd',
};

// Full GameClient stub; only `isDemo` + `fairness()` matter for this view (the rest
// throw — proving the drawer touches ONLY the fairness seam).
function makeClient(opts: {
  isDemo: boolean;
  fairness: () => Promise<FairnessResult>;
}): GameClient {
  return {
    isDemo: opts.isDemo,
    async startGuestSession() {
      throw new Error('not used');
    },
    async me() {
      throw new Error('not used');
    },
    async bet() {
      throw new Error('not used');
    },
    async spin() {
      throw new Error('not used');
    },
    async action() {
      throw new Error('not used');
    },
    async state() {
      throw new Error('not used');
    },
    fairness: opts.fairness,
    crashSocket() {
      return { close: () => undefined };
    },
  };
}

function renderDrawer(client: GameClient, betId = FIXTURE.betId) {
  return render(
    <GameClientProvider client={client}>
      <FairnessDrawer betId={betId} />
    </GameClientProvider>,
  );
}

describe('FairnessDrawer', () => {
  it('surfaces the server disclosure fields after opening the drawer', async () => {
    const client = makeClient({
      isDemo: false,
      fairness: async () => ({ available: true, disclosure: FIXTURE }),
    });
    renderDrawer(client);

    // Proof fields are hidden until the "Verify this bet" affordance is used.
    expect(screen.queryByText(FIXTURE.serverSeedHash)).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole('button', { name: /verify this bet/i }),
    );

    // serverSeedHash + nonce + clientSeed + derivation come straight from the
    // disclosure — the drawer renders the REAL server fields, nothing fabricated.
    expect(await screen.findByText(FIXTURE.serverSeedHash)).toBeInTheDocument();
    expect(screen.getByText(String(FIXTURE.nonce))).toBeInTheDocument();
    expect(screen.getByText(FIXTURE.clientSeed)).toBeInTheDocument();
    expect(screen.getByText(/HMAC_SHA256/)).toBeInTheDocument();
  });

  it('links out to the server-provided verifier URL verbatim', async () => {
    const client = makeClient({
      isDemo: false,
      fairness: async () => ({ available: true, disclosure: FIXTURE }),
    });
    renderDrawer(client);

    await userEvent.click(
      screen.getByRole('button', { name: /verify this bet/i }),
    );

    const link = await screen.findByRole('link', {
      name: /open the verifier/i,
    });
    // Asserted with the relative attribute (not `.href`, which jsdom would resolve
    // to an absolute URL). The href MUST equal the disclosure's verifierUrl exactly
    // — the client surfaces the server's link, it never reconstructs it.
    expect(link).toHaveAttribute('href', FIXTURE.verifierUrl);
  });

  it('reveals the raw server seed only once the seed has been rotated', async () => {
    const revealedSeed =
      'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef';
    const revealed: FairnessDisclosure = {
      ...FIXTURE,
      revealed: true,
      serverSeed: revealedSeed,
    };
    const client = makeClient({
      isDemo: false,
      fairness: async () => ({ available: true, disclosure: revealed }),
    });
    renderDrawer(client);

    await userEvent.click(
      screen.getByRole('button', { name: /verify this bet/i }),
    );

    // Post-rotation: the raw seed is shown and the pre-reveal placeholder is gone.
    expect(await screen.findByText(revealedSeed)).toBeInTheDocument();
    expect(screen.queryByText(/revealed after/i)).not.toBeInTheDocument();
  });

  it('shows the demo notice (no proof fields) when the client is demo', async () => {
    const client = makeClient({
      isDemo: true,
      // Must never be called: isDemo short-circuits before touching fairness().
      fairness: async () => {
        throw new Error('fairness() must not be called in demo mode');
      },
    });
    renderDrawer(client);

    await userEvent.click(
      screen.getByRole('button', { name: /verify this bet/i }),
    );

    expect(
      await screen.findByText(/unavailable in demo mode/i),
    ).toBeInTheDocument();
    // No fabricated proof — the disclosure fields are absent.
    expect(screen.queryByText(FIXTURE.serverSeedHash)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: /open the verifier/i }),
    ).not.toBeInTheDocument();
  });

  it('shows the server reason (no proof fields) when fairness is unavailable', async () => {
    const reason = 'no fairness record for bet_abc123';
    const client = makeClient({
      isDemo: false,
      fairness: async () => ({ available: false, reason }),
    });
    renderDrawer(client);

    await userEvent.click(
      screen.getByRole('button', { name: /verify this bet/i }),
    );

    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.queryByText(FIXTURE.serverSeedHash)).not.toBeInTheDocument();
  });
});
