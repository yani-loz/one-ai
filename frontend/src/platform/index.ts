/**
 * Role: Public barrel for the Platform console module — the single import surface the
 *       app shell uses to mount the console and its route guard.
 * Used by: src/App.tsx.
 * Depends on: the sibling platform files.
 * Key invariants: callers import from "./platform", not deep paths — keeps the module
 *   boundary swappable.
 */
export { PlatformConsolePage } from "./PlatformConsolePage";
export { OrganizationDetailPage } from "./OrganizationDetailPage";
export { PlatformRoute } from "./PlatformRoute";
export type { OrganizationStatus, OrganizationSummary } from "./types";
