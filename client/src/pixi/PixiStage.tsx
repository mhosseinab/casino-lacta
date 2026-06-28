import { Application } from 'pixi.js';
import { useEffect, useRef } from 'react';
import { useReducedMotion } from './useReducedMotion';
import { isWebGLAvailable } from './webgl';

export interface PixiStageContext {
  /** True when the user prefers reduced motion — render a static end-state. */
  reducedMotion: boolean;
}

export interface PixiStageProps {
  /**
   * Called once the Pixi Application is initialised, so the caller can add its
   * display objects. The server decides every outcome; whatever is drawn here
   * is cosmetic and must resolve to server-authoritative state.
   */
  onReady?: (app: Application, ctx: PixiStageContext) => void;
  className?: string;
}

/**
 * Presentation host that owns a PixiJS v8 Application lifecycle: it mounts the
 * renderer into a container, hands it to `onReady`, resizes with the container,
 * and tears everything down cleanly on unmount (no leaked tickers, canvases, or
 * GL contexts). Thin renderer only — holds NO game/outcome/RNG/balance logic.
 *
 * Test seam: jsdom exposes no WebGL/WebGPU context, so the real renderer is
 * skipped when none is available; component tests mount the React wrapper alone.
 */
export function PixiStage({ onReady, className }: PixiStageProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const reducedMotion = useReducedMotion();

  // Keep the latest callback/preference without re-initialising Pixi on each
  // render — the renderer is created once per mount.
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const reducedMotionRef = useRef(reducedMotion);
  reducedMotionRef.current = reducedMotion;

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    // No GL context (jsdom / unsupported browser): skip the real renderer.
    if (!isWebGLAvailable()) {
      return;
    }

    const app = new Application();
    let cancelled = false;
    let ready = false;

    app
      .init({
        resizeTo: container,
        background: 0x000000,
        backgroundAlpha: 0,
        antialias: true,
        resolution: window.devicePixelRatio,
        autoDensity: true,
        // The app owns its ticker (sharedTicker defaults false) so destroy()
        // stops it — no global ticker leak across mounts.
        preference: 'webgl',
      })
      .then(() => {
        if (cancelled) {
          app.destroy(true, { children: true, texture: true });
          return;
        }
        ready = true;
        container.appendChild(app.canvas);
        onReadyRef.current?.(app, { reducedMotion: reducedMotionRef.current });
      })
      .catch(() => {
        // Renderer init failed (e.g. GL context lost) — nothing to tear down.
      });

    return () => {
      cancelled = true;
      if (ready) {
        app.destroy(true, { children: true, texture: true });
      }
    };
  }, []);

  return (
    <div ref={containerRef} className={className} data-testid="pixi-stage" />
  );
}
