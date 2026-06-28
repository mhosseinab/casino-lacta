// Probe for a usable WebGL context. PixiJS v8 builds a WebGL/WebGPU renderer in
// `Application.init()`; under jsdom (component tests) no GL context exists, so
// this returns false and callers skip the real renderer. Presentation seam only —
// no game/outcome/RNG logic lives in the client.
export function isWebGLAvailable(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}
