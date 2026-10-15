/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Default API base URL shown on the Connect page (empty = same origin). */
  readonly VITE_DEFAULT_API_BASE?: string;
  /** Per-request timeout in milliseconds (default 15000). */
  readonly VITE_REQUEST_TIMEOUT_MS?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
