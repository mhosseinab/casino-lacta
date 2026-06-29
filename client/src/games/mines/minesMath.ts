// Mines PRESENTATION/LAYOUT helpers — pure, board-geometry only. The 5×5 grid is
// fixed (25 cells, row-major, indices 0..24, mirroring engine/games/mines.py
// GRID_SIZE=25). These helpers decide NO outcome, payout, or multiplier: the
// settlement multiplier (currentMultiplier/nextMultiplier) and the disclosed
// minePositions arrive on the SERVER projection and are rendered verbatim. This
// module never computes C(25,k)/C(25-M,k) — that is the server's job (the iron
// "server-authoritative" rule).

/** Cells per row / per column on the fixed 5×5 board. */
export const GRID_COLS = 5;
/** Total cell count (5×5). */
export const GRID_LEN = GRID_COLS * GRID_COLS;

/** The 25 cell indices, row-major: [0, 1, …, 24]. */
export function gridCells(): number[] {
  return [...Array(GRID_LEN).keys()];
}

/** Row-major index → {row, col} on a 5-wide grid. */
export function cellToRowCol(cell: number): { row: number; col: number } {
  return { row: Math.floor(cell / GRID_COLS), col: cell % GRID_COLS };
}

/** Inverse of {@link cellToRowCol}. */
export function rowColToCell(row: number, col: number): number {
  return row * GRID_COLS + col;
}

/** True if `cell` is in the server-disclosed `minePositions` (terminal only). */
export function isMine(cell: number, minePositions: number[]): boolean {
  return minePositions.includes(cell);
}

/** Cash-out is permitted only after at least one safe reveal (k >= 1), mirroring
 *  the engine's `_cashout` guard. A display gate only — the server re-checks. */
export function canCashout(k: number): boolean {
  return k >= 1;
}
