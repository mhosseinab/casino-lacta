import { GameCard } from './GameCard';

// The 16 in-scope games (plan §1 — all games EXCEPT multiplayer PvP poker).
// Static display metadata only; the lobby decides no outcomes. Each entry routes
// to /play/:gameId, where the registry lazy-loads the view (or "coming soon").
interface LobbyGame {
  gameId: string;
  name: string;
  category: string;
}

const GAMES: readonly LobbyGame[] = [
  { gameId: 'originals.dice', name: 'Dice', category: 'Originals' },
  { gameId: 'originals.limbo', name: 'Limbo', category: 'Originals' },
  {
    gameId: 'originals.pocketdice',
    name: 'Pocket Dice',
    category: 'Originals',
  },
  { gameId: 'originals.mines', name: 'Mines', category: 'Originals' },
  { gameId: 'originals.plinko', name: 'Plinko', category: 'Originals' },
  { gameId: 'originals.hilo', name: 'HiLo', category: 'Originals' },
  { gameId: 'originals.keno', name: 'Keno', category: 'Originals' },
  { gameId: 'originals.roulette', name: 'Roulette', category: 'Originals' },
  { gameId: 'originals.crash', name: 'Crash', category: 'Originals' },
  { gameId: 'slots.machine01', name: 'Slot Machine 01', category: 'Slots' },
  { gameId: 'slots.machine02', name: 'Slot Machine 02', category: 'Slots' },
  { gameId: 'slots.machine03', name: 'Slot Machine 03', category: 'Slots' },
  { gameId: 'table.blackjack', name: 'Blackjack', category: 'Table' },
  { gameId: 'table.baccarat', name: 'Baccarat', category: 'Table' },
  { gameId: 'table.roulette', name: 'European Roulette', category: 'Table' },
  { gameId: 'table.video_poker', name: 'Video Poker', category: 'Table' },
];

export function Lobby() {
  return (
    <section style={{ padding: 24 }}>
      <h2 style={{ margin: '0 0 16px', fontSize: 20 }}>Lobby</h2>
      <div
        style={{
          display: 'grid',
          gap: 16,
          gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        }}
      >
        {GAMES.map((game) => (
          <GameCard
            key={game.gameId}
            gameId={game.gameId}
            name={game.name}
            category={game.category}
          />
        ))}
      </div>
    </section>
  );
}
