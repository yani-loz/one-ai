/**
 * Role: Public barrel for the Identity frontend module — the single import surface
 *       the rest of the app uses (App.tsx, main.tsx, HomePage.tsx).
 * Used by: src/App.tsx, src/main.tsx, src/HomePage.tsx.
 * Depends on: the sibling identity files.
 * Key invariants: callers import from "./identity", not from deep paths — keeps the
 *   module boundary swappable.
 */
export { AuthProvider } from "./AuthProvider";
export type { AuthContextValue, AuthStatus } from "./authContext";
export { useAuth } from "./useAuth";
export { ProtectedRoute } from "./ProtectedRoute";
export { LoginPage } from "./LoginPage";
export type { AuthScope, AuthUser, Role, TokenPair } from "./types";
