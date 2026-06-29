import { Suspense } from 'react';
import { Link, useParams } from 'react-router-dom';
import { gameViews } from '../games/registry';

// Resolves /play/:gameId to its lazy-loaded view from the registry. Unknown or
// not-yet-built ids render a "coming soon" placeholder rather than erroring — so
// the lobby is fully navigable before every game view ships (additive build).
function ComingSoon(props: { gameId: string }) {
  return (
    <section style={{ padding: 24 }}>
      <h2 style={{ margin: '0 0 8px' }}>Coming soon</h2>
      <p style={{ opacity: 0.7 }}>
        <code>{props.gameId}</code> isn’t playable yet.
      </p>
      <Link to="/">← Back to lobby</Link>
    </section>
  );
}

export function GameRoute() {
  const { gameId } = useParams<{ gameId: string }>();
  const View = gameId ? gameViews[gameId] : undefined;

  if (!gameId || !View) {
    return <ComingSoon gameId={gameId ?? '(none)'} />;
  }

  return (
    <Suspense fallback={<p style={{ padding: 24 }}>Loading…</p>}>
      <View />
    </Suspense>
  );
}
