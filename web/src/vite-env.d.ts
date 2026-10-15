/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Default API base URL shown on the Connect page (empty = same origin). */
  readonly VITE_DEFAULT_API_BASE?: string;
  /** Per-request timeout in milliseconds (default 15000). */
  readonly VITE_REQUEST_TIMEOUT_MS?: string;
  /** Mock mode only: `1` makes the MSW API behave like `serve --local-no-auth`. */
  readonly VITE_MOCK_LOCAL_NO_AUTH?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
