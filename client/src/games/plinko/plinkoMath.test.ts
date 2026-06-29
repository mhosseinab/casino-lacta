import { describe, expect, it } from 'vitest';
import {
  type Bounce,
  binCount,
  binFromPath,
  binLeftPct,
  columnToLeftPct,
  columnsToBin,
  pathToColumns,
} from './plinkoMath';

// Pure GEOMETRY for the cosmetic Plinko drop — no economy, no multiplier, no
// payout math lives here (the multiplier table is SERVER-OWNED; the client never
// previews it). These helpers only map the server `path`/`bin` to screen geometry
// and prove the path↔bin invariant the board relies on to drop the ball into the
// authoritative server bin.

describe('plinkoMath — pure board geometry (no economy)', () => {
  describe('binCount — a board of N rows has N+1 bins', () => {
    it.each([
      [8, 9],
      [12, 13],
      [16, 17],
    ])('rows %i → %i bins', (rows, bins) => {
      expect(binCount(rows)).toBe(bins);
    });
  });

  describe('pathToColumns — cumulative L=-1 / R=+1 offset per bounce', () => {
    it('accumulates left/right bounces into a running column offset', () => {
      const path: Bounce[] = ['R', 'R', 'L'];
      expect(pathToColumns(path)).toEqual([1, 2, 1]);
    });

    it('all-left drifts to -rows, all-right to +rows', () => {
      expect(pathToColumns(['L', 'L', 'L'])).toEqual([-1, -2, -3]);
      expect(pathToColumns(['R', 'R', 'R'])).toEqual([1, 2, 3]);
    });

    it('returns one offset per bounce', () => {
      expect(pathToColumns(['L', 'R', 'L', 'R']).length).toBe(4);
    });
  });

  describe('binFromPath — bin is the count of right bounces', () => {
    it('counts the R bounces', () => {
      expect(binFromPath(['R', 'R', 'L'])).toBe(2);
      expect(binFromPath(['L', 'L', 'L'])).toBe(0);
      expect(binFromPath(['R', 'R', 'R'])).toBe(3);
    });
  });

  describe('columnsToBin — invert the column offset back to a bin index', () => {
    it('maps a final offset of (2·bin − rows) back to bin', () => {
      // 12 rows, bin 8 → offset 2·8 − 12 = 4
      expect(columnsToBin(4, 12)).toBe(8);
      expect(columnsToBin(-12, 12)).toBe(0);
      expect(columnsToBin(12, 12)).toBe(12);
    });
  });

  describe('path↔bin invariant — the trail always ends in the server bin', () => {
    // The load-bearing property: the cosmetic drop derived from `path` lands in
    // exactly the bin the server reported (rightBounces). Proven for every bin of
    // a 12-row board.
    it.each(Array.from({ length: 13 }, (_, bin) => bin))(
      'a 12-row path with %i right bounces lands in bin %i',
      (bin) => {
        const rows = 12;
        const path: Bounce[] = [
          ...Array<Bounce>(bin).fill('R'),
          ...Array<Bounce>(rows - bin).fill('L'),
        ];
        const columns = pathToColumns(path);
        const finalColumn = columns[columns.length - 1];
        expect(columnsToBin(finalColumn, rows)).toBe(bin);
        expect(columnsToBin(finalColumn, rows)).toBe(binFromPath(path));
      },
    );
  });

  describe('binLeftPct — slot and ball share ONE horizontal mapping', () => {
    it('places bin 0 at the left edge and bin=rows at the right edge', () => {
      expect(binLeftPct(0, 12)).toBe(0);
      expect(binLeftPct(12, 12)).toBe(100);
      expect(binLeftPct(6, 12)).toBe(50);
    });

    it('centres a single-bin degenerate board (rows 0)', () => {
      expect(binLeftPct(0, 0)).toBe(50);
    });
  });

  describe('columnToLeftPct — the cosmetic trail shares the bin mapping', () => {
    it('a full path final offset (2·bin − rows) lands on binLeftPct(bin, rows)', () => {
      const rows = 12;
      for (let bin = 0; bin <= rows; bin++) {
        const finalColumn = 2 * bin - rows;
        expect(columnToLeftPct(finalColumn, rows)).toBeCloseTo(
          binLeftPct(bin, rows),
          10,
        );
      }
    });

    it('keeps an intermediate offset centred (column 0 → 50%)', () => {
      expect(columnToLeftPct(0, 12)).toBe(50);
    });
  });
});
