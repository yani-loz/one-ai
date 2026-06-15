/**
 * Role: Public barrel for the Company Admin console module — the single import surface the
 *       app shell uses to mount the console and its route guard.
 * Used by: src/App.tsx.
 * Depends on: the sibling admin files.
 * Key invariants: callers import from "./admin", not deep paths — keeps the module boundary
 *   swappable.
 */
export { AdminConsolePage } from "./AdminConsolePage";
export { ConnectorGovernancePage } from "./ConnectorGovernancePage";
export { AdminRoute } from "./AdminRoute";
