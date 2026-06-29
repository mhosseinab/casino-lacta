import { Link } from 'react-router-dom';

// A single lobby tile. Clicking routes to /play/:gameId (a router <Link>, so it is
// a real anchor — keyboard + middle-click friendly). Presentation only.
export function GameCard(props: {
  gameId: string;
  name: string;
  category: string;
}) {
  return (
    <Link
      to={`/play/${props.gameId}`}
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        padding: 16,
        borderRadius: 12,
        textDecoration: 'none',
        color: 'inherit',
        background: '#111827',
        border: '1px solid #1f2937',
        minHeight: 96,
        justifyContent: 'space-between',
      }}
    >
      <span style={{ fontSize: 11, opacity: 0.6, textTransform: 'uppercase' }}>
        {props.category}
      </span>
      <span style={{ fontSize: 16, fontWeight: 700 }}>{props.name}</span>
    </Link>
  );
}
