import { describe, expect, it } from 'vitest';
import { CURRENCY_SCALE, formatMinor, parseStakeToMinor } from './money';

describe('CURRENCY_SCALE', () => {
  it('declares GOLD as a scale-2 currency', () => {
    expect(CURRENCY_SCALE.GOLD).toBe(2);
  });
});

describe('formatMinor', () => {
  it('formats representative amounts with no float drift', () => {
    expect(formatMinor(0, 'GOLD')).toBe('0.00');
    expect(formatMinor(1, 'GOLD')).toBe('0.01');
    expect(formatMinor(70, 'GOLD')).toBe('0.70'); // naive *0.01 would drift
    expect(formatMinor(99, 'GOLD')).toBe('0.99');
    expect(formatMinor(100, 'GOLD')).toBe('1.00');
    expect(formatMinor(198, 'GOLD')).toBe('1.98');
  });

  it('formats large values exactly (no float drift near MAX_SAFE_INTEGER)', () => {
    // 9007199254740991 minor = MAX_SAFE_INTEGER
    expect(formatMinor(9007199254740991, 'GOLD')).toBe('90071992547409.91');
    expect(formatMinor(999999999999999, 'GOLD')).toBe('9999999999999.99');
  });

  it('round-trips format -> parse for representative amounts', () => {
    for (const minor of [0, 1, 70, 99, 100, 198, 123456789]) {
      const display = formatMinor(minor, 'GOLD');
      expect(parseStakeToMinor(display)).toBe(minor);
    }
  });

  it('throws on an unknown currency (programmer error, not user input)', () => {
    expect(() => formatMinor(100, 'BOGUS')).toThrow();
  });
});

describe('parseStakeToMinor', () => {
  it('accepts a bare integer as minor units directly', () => {
    expect(parseStakeToMinor('0')).toBe(0);
    expect(parseStakeToMinor('1')).toBe(1);
    expect(parseStakeToMinor('198')).toBe(198);
    expect(parseStakeToMinor('100')).toBe(100); // bare digits = minor units...
  });

  it('accepts a decimal at the fixed scale and scales it to minor units', () => {
    expect(parseStakeToMinor('1.00')).toBe(100); // ...whereas a decimal is major units
    expect(parseStakeToMinor('1.98')).toBe(198);
    expect(parseStakeToMinor('0.01')).toBe(1);
    expect(parseStakeToMinor('1.9')).toBe(190); // fewer than scale decimals is padded
  });

  it('does not drift on a decimal that breaks naive *100 (0.29)', () => {
    // parseFloat('0.29') * 100 === 28.999999999999996 -> naive floor gives 28
    expect(parseStakeToMinor('0.29')).toBe(29);
    expect(parseStakeToMinor('0.07')).toBe(7);
  });

  it('rejects empty / non-numeric / junk input', () => {
    expect(parseStakeToMinor('')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('abc')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('1.98abc')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('1,98')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('NaN')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('.5')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('1.')).toBeInstanceOf(Error);
  });

  it('rejects surrounding whitespace (strict boundary)', () => {
    expect(parseStakeToMinor(' 100')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('100 ')).toBeInstanceOf(Error);
  });

  it('rejects negative input', () => {
    expect(parseStakeToMinor('-5')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('-1.00')).toBeInstanceOf(Error);
  });

  it('rejects too many decimal places for the scale', () => {
    expect(parseStakeToMinor('1.999')).toBeInstanceOf(Error);
    expect(parseStakeToMinor('0.001')).toBeInstanceOf(Error);
  });

  it('rejects values that overflow MAX_SAFE_INTEGER', () => {
    // MAX_SAFE_INTEGER + 2, as integer minor units
    expect(parseStakeToMinor('9007199254740993')).toBeInstanceOf(Error);
    // and via the decimal path
    expect(parseStakeToMinor('99999999999999.99')).toBeInstanceOf(Error);
  });

  it('accepts the boundary value MAX_SAFE_INTEGER itself', () => {
    expect(parseStakeToMinor('9007199254740991')).toBe(9007199254740991);
  });
});
