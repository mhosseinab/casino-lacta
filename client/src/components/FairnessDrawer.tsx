import { type ReactNode, useState } from 'react';
import type { FairnessDisclosure } from '../contracts';
import { type FairnessResult, useGameClient } from '../lib/transport';

// Shown when the active transport is the quarantined demo (mock) adapter: it CANNOT
// produce a real commit-reveal proof, so we say so plainly rather than fabricate one
// (iron rule: the server decides — and proves — outcomes; the client never invents
// fairness data).
const DEMO_NOTICE =
  'Provable fairness is unavailable in demo mode — outcomes are mocked, not server-verified.';

type DrawerState =
  | { phase: 'closed' }
  | { phase: 'loading' }
  | { phase: 'available'; disclosure: FairnessDisclosure }
  | { phase: 'unavailable'; reason: string };

// The provable-fairness drawer (plan §2.3). Given a betId it pulls the bet's
// commit-reveal disclosure through the GameClient seam and surfaces the REAL server
// fields — serverSeedHash (the pre-bet commitment), clientSeed, nonce, derivation,
// and an outbound link to the open verifier (the server-stamped `verifierUrl`, NEVER
// reconstructed here). It is a thin renderer: it decides nothing and shows only what
// the server proves. S8's ResultPanel mounts <FairnessDrawer betId={…}/> per bet.
export function FairnessDrawer(props: { betId: string }) {
  const client = useGameClient();
  const [state, setState] = useState<DrawerState>({ phase: 'closed' });

  async function open(): Promise<void> {
    // isDemo is a first-class trigger: short-circuit before any fetch.
    if (client.isDemo) {
      setState({ phase: 'unavailable', reason: DEMO_NOTICE });
      return;
    }
    setState({ phase: 'loading' });
    const result: FairnessResult = await client.fairness(props.betId);
    setState(
      result.available
        ? { phase: 'available', disclosure: result.disclosure }
        : { phase: 'unavailable', reason: result.reason },
    );
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => void open()}
        style={{
          font: 'inherit',
          padding: '4px 10px',
          borderRadius: 6,
          border: '1px solid currentColor',
          background: 'transparent',
          color: 'inherit',
          cursor: 'pointer',
        }}
      >
        Verify this bet
      </button>
      {state.phase !== 'closed' && (
        <dialog
          open
          aria-label="Provable fairness"
          style={{
            marginTop: 8,
            padding: 16,
            borderRadius: 8,
            border: '1px solid rgba(127,127,127,0.4)',
            maxWidth: 560,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'baseline',
              justifyContent: 'space-between',
              gap: 12,
            }}
          >
            <h3 style={{ margin: '0 0 8px' }}>Provable fairness</h3>
            <button
              type="button"
              aria-label="Close"
              onClick={() => setState({ phase: 'closed' })}
              style={{
                font: 'inherit',
                background: 'transparent',
                border: 'none',
                color: 'inherit',
                cursor: 'pointer',
                opacity: 0.7,
              }}
            >
              ✕
            </button>
          </div>
          {state.phase === 'loading' && <p>Loading the disclosure…</p>}
          {state.phase === 'unavailable' && (
            <p style={{ opacity: 0.8, margin: 0 }}>{state.reason}</p>
          )}
          {state.phase === 'available' && (
            <Disclosure disclosure={state.disclosure} />
          )}
        </dialog>
      )}
    </div>
  );
}

function Disclosure(props: { disclosure: FairnessDisclosure }) {
  const d = props.disclosure;
  return (
    <>
      <dl
        style={{
          display: 'grid',
          gridTemplateColumns: 'auto 1fr',
          gap: '4px 12px',
          margin: '0 0 12px',
          wordBreak: 'break-all',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        <Field label="Server seed hash">
          <code>{d.serverSeedHash}</code>
        </Field>
        <Field label="Server seed">
          {d.revealed && d.serverSeed !== null ? (
            <code>{d.serverSeed}</code>
          ) : (
            <span style={{ opacity: 0.7 }}>
              Revealed after this seed is rotated
            </span>
          )}
        </Field>
        <Field label="Client seed">
          <code>{d.clientSeed}</code>
        </Field>
        <Field label="Nonce">
          <code>{d.nonce}</code>
        </Field>
        <Field label="Derivation">
          <code>{d.derivation}</code>
        </Field>
      </dl>
      <a href={d.verifierUrl} target="_blank" rel="noopener noreferrer">
        Open the verifier for this bet →
      </a>
    </>
  );
}

function Field(props: { label: string; children: ReactNode }) {
  return (
    <>
      <dt style={{ fontWeight: 600, opacity: 0.7 }}>{props.label}</dt>
      <dd style={{ margin: 0 }}>{props.children}</dd>
    </>
  );
}
