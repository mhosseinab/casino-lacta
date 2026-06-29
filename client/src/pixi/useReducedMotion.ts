import { useEffect, useState } from 'react';

const QUERY = '(prefers-reduced-motion: reduce)';

function hasMatchMedia(): boolean {
  return (
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
  );
}

// Reads the user's `prefers-reduced-motion` preference and tracks live changes.
// Views use it to render a static end-state instead of animating; PixiStage
// surfaces it to its `onReady` callback. Cosmetic-only — never gates an outcome.
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() =>
    hasMatchMedia() ? window.matchMedia(QUERY).matches : false,
  );

  useEffect(() => {
    if (!hasMatchMedia()) {
      return;
    }
    const mql = window.matchMedia(QUERY);
    const onChange = (event: MediaQueryListEvent): void => {
      setReduced(event.matches);
    };
    setReduced(mql.matches);
    mql.addEventListener('change', onChange);
    return () => {
      mql.removeEventListener('change', onChange);
    };
  }, []);

  return reduced;
}
