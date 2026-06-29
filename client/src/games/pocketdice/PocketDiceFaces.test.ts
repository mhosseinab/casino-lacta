import { describe, expect, it } from 'vitest';
import { diceFacesLanding } from './PocketDiceFaces';

// jsdom can't run a real roll animation, so (mirroring LimboMeter's limboLanding)
// the tested seam is the pure "server dice → displayed faces + sum" mapping.
describe('diceFacesLanding — pure server-dice → display mapping', () => {
  it('passes the two faces through and sums them', () => {
    expect(diceFacesLanding([2, 3], false)).toEqual({
      faces: [2, 3],
      sum: 5,
      immediate: false,
    });
  });

  it('reduced motion → immediate (show the result with no roll animation)', () => {
    expect(diceFacesLanding([6, 6], true)).toEqual({
      faces: [6, 6],
      sum: 12,
      immediate: true,
    });
  });
});
