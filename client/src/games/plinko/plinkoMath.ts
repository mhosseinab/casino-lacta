// Pure GEOMETRY for the cosmetic Plinko drop. THIN-client rule: this module owns
// NO economy — no multiplier table, no payout, no win/loss math. The Plinko
// multiplier table is SERVER-OWNED and the payout is purely server-decided, so the
// client never previews it. These helpers only translate the server `path`/`bin`
// into screen geometry and prove the path↔bin invariant the board relies on to drop
// the ball into the AUTHORITATIVE server bin.

/** A single left/right peg bounce, as reported in the server outcome `path`. */
export type Bounce = 'L' | 'R';

/** A board of N rows has N+1 bins (the §A Plinko triangle). */
export function binCount(rows: number): number {
  return rows + 1;
}

/** Pure: the running horizontal column offset after each bounce (L = −1, R = +1).
 *  One entry per bounce; the cosmetic drop renders THROUGH this trail. */
export function pathToColumns(path: Bounce[]): number[] {
  const columns: number[] = [];
  let offset = 0;
  for (const bounce of path) {
    offset += bounce === 'R' ? 1 : -1;
    columns.push(offset);
  }
  return columns;
}

/** Pure: the bin index is the number of right bounces (= server `rightBounces`). */
export function binFromPath(path: Bounce[]): number {
  return path.filter((bounce) => bounce === 'R').length;
}

/** Pure: invert a final column offset (2·bin − rows) back to its bin index. Used to
 *  PROVE the path-derived trail ends in the same bin the server reported. */
export function columnsToBin(finalColumn: number, rows: number): number {
  return (finalColumn + rows) / 2;
}

/** Pure: the horizontal centre of a bin (and of the landed ball) as a track percent
 *  0–100. The SINGLE owner of the mapping so the slot and the ball share one
 *  coordinate system (bin 0 = left edge, bin = rows = right edge). */
export function binLeftPct(bin: number, rows: number): number {
  if (rows <= 0) return 50;
  return (bin / rows) * 100;
}

/** Pure: a running column offset (from `pathToColumns`) as a track percent 0–100,
 *  for the cosmetic drop trail. Consistent with `binLeftPct`: the FINAL offset of a
 *  full path (2·bin − rows) maps to exactly `binLeftPct(bin, rows)`. */
export function columnToLeftPct(column: number, rows: number): number {
  if (rows <= 0) return 50;
  return ((column + rows) / (2 * rows)) * 100;
}
