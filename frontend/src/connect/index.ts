/**
 * Role: Public surface of the Connect feature — the route-level pages. Internal modules
 *       (clients, hooks, drawer, cards, tabs, consent modal, providers) are not re-exported.
 * Used by: App.tsx (the /connectors admin route + the /connections member self-connect routes).
 * Key invariants:
 *   - ConnectorsPage is the legacy COMPANY-ADMIN console (org-owned/shared mailboxes) at /connectors
 *     (AdminRoute). MyConnectionsPage + ConnectorDetailPage are the Tier-3 SELF-CONNECT plane at
 *     /connections (ProtectedRoute — any authenticated user, scoped to their OWN connections).
 */
export { ConnectorsPage } from "./ConnectorsPage";
export { MyConnectionsPage } from "./MyConnectionsPage";
export { ConnectorDetailPage } from "./ConnectorDetailPage";
