/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the One AI backend API. Omit in production for same-origin API paths. */
  readonly VITE_API_URL?: string;
  readonly VITE_DEV_PLATFORM_EMAIL?: string;
  readonly VITE_DEV_PLATFORM_PASSWORD?: string;
  readonly VITE_DEV_DEMO_ADMIN_EMAIL?: string;
  readonly VITE_DEV_DEMO_ADMIN_PASSWORD?: string;
  readonly VITE_DEV_DEMO_MEMBER_EMAIL?: string;
  readonly VITE_DEV_DEMO_MEMBER_PASSWORD?: string;
  readonly VITE_DEV_GLOBEX_ADMIN_EMAIL?: string;
  readonly VITE_DEV_GLOBEX_ADMIN_PASSWORD?: string;
  readonly VITE_DEV_GLOBEX_MEMBER_EMAIL?: string;
  readonly VITE_DEV_GLOBEX_MEMBER_PASSWORD?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
