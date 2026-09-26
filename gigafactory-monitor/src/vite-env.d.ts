/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Optional WebSocket URL of a future ML / backend alert feed.
   * When set (e.g. in a `.env.local` file), the app connects to it and forwards
   * every valid message to `triggerAlert(...)`. Leave unset for pure simulation.
   */
  readonly VITE_ALERT_WS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
