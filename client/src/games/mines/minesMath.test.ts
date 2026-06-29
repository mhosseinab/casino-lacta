import { describe, expect, it } from 'vitest';
import {
  GRID_COLS,
  GRID_LEN,
  canCashout,
  cellToRowCol,
  gridCells,
  isMine,
  rowColToCell,
} from './minesMath';

// minesMath holds ONLY pure presentation/layout helpers for the 5×5 board. It
// decides NO outcome, payout, or multiplier — those arrive on the server
// projection (currentMultiplier/nextMultiplier) and are rendered verbatim.
describe('minesMath — pure 5×5 layout helpers (no outcome math)', () => {
  it('gridCells() is the 25 cell indices 0..24 in row-major order', () => {
    const cells = gridCells();
    expect(cells).toHaveLength(GRID_LEN);
    expect(cells[0]).toBe(0);
    expect(cells[24]).toBe(24);
    expect(cells).toEqual([...Array(25).keys()]);
  });

  it('cellToRowCol maps row-major index to {row, col} on a 5-wide grid', () => {
    expect(cellToRowCol(0)).toEqual({ row: 0, col: 0 });
    expect(cellToRowCol(4)).toEqual({ row: 0, col: 4 });
    expect(cellToRowCol(5)).toEqual({ row: 1, col: 0 });
    expect(cellToRowCol(24)).toEqual({ row: 4, col: 4 });
  });

  it('rowColToCell is the inverse of cellToRowCol', () => {
    for (let i = 0; i < GRID_LEN; i += 1) {
      const { row, col } = cellToRowCol(i);
      expect(rowColToCell(row, col)).toBe(i);
    }
    expect(GRID_COLS).toBe(5);
  });

  it('isMine reports membership in the server-disclosed minePositions', () => {
    const mines = [2, 7, 19];
    expect(isMine(2, mines)).toBe(true);
    expect(isMine(19, mines)).toBe(true);
    expect(isMine(3, mines)).toBe(false);
    expect(isMine(0, [])).toBe(false);
  });

  it('canCashout requires at least one safe reveal (k >= 1)', () => {
    expect(canCashout(0)).toBe(false);
    expect(canCashout(1)).toBe(true);
    expect(canCashout(5)).toBe(true);
  });
});
