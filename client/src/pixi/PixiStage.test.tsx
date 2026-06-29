import { render, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PixiStage } from './PixiStage';
import { useReducedMotion } from './useReducedMotion';

// jsdom implements no WebGL/WebGPU context, so PixiStage must skip the real
// renderer and render only its React wrapper — asserting the test seam holds.

function mockMatchMedia(matches: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('PixiStage', () => {
  it('mounts without throwing under jsdom (no WebGL context)', () => {
    expect(() => render(<PixiStage />)).not.toThrow();
  });

  it('unmounts and cleans up without throwing', () => {
    const { unmount } = render(<PixiStage />);
    expect(() => unmount()).not.toThrow();
  });
});

describe('useReducedMotion', () => {
  it('reflects matchMedia matches:true', () => {
    mockMatchMedia(true);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(true);
  });

  it('reflects matchMedia matches:false', () => {
    mockMatchMedia(false);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(false);
  });
});
