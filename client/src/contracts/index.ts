// The client consumes the server contract ONLY through these generated types —
// never hand-rolled API shapes (see CLAUDE.md "Client ↔ server contract" seam).
import type { components } from '@casino/contracts';

export type BetObject = components['schemas']['BetObject'];
export type MeResponse = components['schemas']['MeResponse'];
export type GuestSessionResponse =
  components['schemas']['GuestSessionResponse'];
export type TokenPair = components['schemas']['TokenPair'];
export type AccessToken = components['schemas']['AccessToken'];
export type BetRequest = components['schemas']['BetRequest'];
export type ActionRequest = components['schemas']['ActionRequest'];
export type Fairness = components['schemas']['Fairness'];
export type FairnessDisclosure = components['schemas']['FairnessDisclosure'];

// Typed proof of consumption: `tsc` fails if these server-stamped fields go missing
// (camelCase keys — the API serializes by alias). This is the S2 "test".
export const _betShapeProof: Pick<BetObject, 'betId' | 'stakeMinor'> = {
  betId: '',
  stakeMinor: 0,
};
