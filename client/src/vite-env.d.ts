/// <reference types="vite/client" />

// The single client-side config switch (plan §4.1): present → real Http transport,
// absent → the quarantined demo Mock. Read ONLY by the transport factory.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
}
