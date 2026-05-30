/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the One AI backend API (set per environment). */
  readonly VITE_API_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
