// Money utilities for the thin client: integer minor-units formatting + stake
// input validation ONLY. The server decides every outcome and balance — this file
// never does payout/multiplier/outcome math (see CLAUDE.md "Money is integer minor
// units"). All math here is integer/string/BigInt: no float math on money, ever.

/**
 * Fixed decimal scale per currency (number of minor units per major unit, as a
 * power of ten). GOLD is the only play-money currency and is scale-2: 198 minor
 * units displays as "1.98".
 */
export const CURRENCY_SCALE: Record<string, number> = {
  GOLD: 2,
};

const MAX_SAFE = BigInt(Number.MAX_SAFE_INTEGER);

function scaleOf(currency: string): number {
  const scale = CURRENCY_SCALE[currency];
  if (scale === undefined) {
    // Unknown currency is a programmer error, not user input — fail loudly
    // rather than silently producing garbage via padStart(NaN, ...).
    throw new Error(`Unknown currency: ${currency}`);
  }
  return scale;
}

/**
 * Format integer minor units as a display string for the given currency.
 * Pure integer/string work — no float division — so it never drifts, even for
 * values up to Number.MAX_SAFE_INTEGER. E.g. formatMinor(198, 'GOLD') === '1.98'.
 */
export function formatMinor(amountMinor: number, currency: string): string {
  const scale = scaleOf(currency);
  const sign = amountMinor < 0 ? '-' : '';
  const digits = Math.abs(amountMinor).toString();
  if (scale === 0) {
    return `${sign}${digits}`;
  }
  const padded = digits.padStart(scale + 1, '0');
  const whole = padded.slice(0, padded.length - scale);
  const frac = padded.slice(padded.length - scale);
  return `${sign}${whole}.${frac}`;
}

/**
 * Parse a user-entered stake into integer minor units, validating strictly at the
 * (hostile) client boundary. Returns an Error for any expected-invalid input
 * rather than throwing.
 *
 * Accepted forms (no surrounding whitespace, no sign):
 *   - bare digits  -> interpreted as minor units directly ("100" -> 100)
 *   - a decimal    -> interpreted as MAJOR units at the fixed scale ("1.00" -> 100)
 *
 * Rejects: empty, NaN/non-numeric, negative, junk, surrounding whitespace, more
 * decimal places than the scale allows, and values beyond MAX_SAFE_INTEGER.
 */
export function parseStakeToMinor(
  input: string,
  currency = 'GOLD',
): number | Error {
  const scale = scaleOf(currency);
  const match = /^(\d+)(?:\.(\d+))?$/.exec(input);
  if (match === null) {
    return new Error(`Invalid stake: ${JSON.stringify(input)}`);
  }
  const [, wholePart, fracPart] = match;

  let minorDigits: string;
  if (fracPart === undefined) {
    // Bare digits: already minor units.
    minorDigits = wholePart;
  } else {
    if (fracPart.length > scale) {
      return new Error(
        `Too many decimal places for scale ${scale}: ${JSON.stringify(input)}`,
      );
    }
    // Decimal: major units. Right-pad the fraction to the fixed scale and
    // concatenate — building the minor-unit digit string without float math.
    minorDigits = wholePart + fracPart.padEnd(scale, '0');
  }

  const minor = BigInt(minorDigits);
  if (minor > MAX_SAFE) {
    return new Error(
      `Stake exceeds safe integer range: ${JSON.stringify(input)}`,
    );
  }
  return Number(minor);
}
