// The client consumes the server contract ONLY through these generated types —
// never hand-rolled API shapes (see CLAUDE.md "Client ↔ server contract" seam).
import type { components } from '@casino/contracts';

export type BetObject = components['schemas']['BetObject'];
export type MeResponse = components['schemas']['MeResponse'];

// Typed proof of consumption: `tsc` fails if these server-stamped fields go missing
// (camelCase keys — the API serializes by alias). This is the S2 "test".
export const _betShapeProof: Pick<BetObject, 'betId' | 'stakeMinor'> = {
  betId: '',
  stakeMinor: 0,
};
