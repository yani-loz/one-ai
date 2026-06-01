/**
 * Role: Root route shell — wires the public /login, the protected company home "/", and
 *       the platform-admin console "/platform" with the iOS-style directional page
 *       transitions, wrapped in AnimatePresence.
 * Used by: src/main.tsx (inside BrowserRouter + AuthProvider).
 * Depends on: react-router-dom (Routes/Route/Navigate), motion (AnimatePresence),
 *             ./identity (LoginPage, ProtectedRoute, useAuth), ./platform
 *             (PlatformConsolePage, PlatformRoute), ./HomePage, ./pageTransition.
 * Key invariants: screen-level motion.divs are `absolute inset-0` so the incoming and
 *   outgoing pages overlap during the slide; routing must run inside <BrowserRouter>.
 *   "/" routes a platform admin onward to /platform so each role lands on its own home.
 */
import { AnimatePresence, motion } from "motion/react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { HomePage } from "./HomePage";
import { LoginPage, ProtectedRoute, useAuth } from "./identity";
import { PlatformConsolePage, PlatformRoute } from "./platform";
import { useDirectionalTransition } from "./pageTransition";

/** Wrap a screen in the directional page transition (absolute-positioned overlay). */
function AnimatedScreen({ children }: { children: React.ReactNode }): React.JSX.Element {
  return (
    <motion.div
      {...useDirectionalTransition()}
      className="absolute inset-0 flex flex-col overflow-y-auto"
    >
      {children}
    </motion.div>
  );
}

/**
 * The "/" home, resolved by role: a platform admin is sent to the platform console,
 * everyone else gets the company HomePage. Rendered inside ProtectedRoute, so `user`
 * is non-null. The role read is a UX convenience (it picks a screen, not data access —
 * which stays server-enforced); see AuthProvider's display-only-role note.
 */
function RoleHome(): React.JSX.Element {
  const { user } = useAuth();
  if (user?.role === "platform_admin") {
    return <Navigate to="/platform" replace />;
  }
  return <HomePage />;
}

export function App(): React.JSX.Element {
  const location = useLocation();

  return (
    <div className="relative min-h-screen overflow-hidden">
      <AnimatePresence initial={false}>
        <Routes location={location} key={location.pathname}>
          <Route
            path="/login"
            element={
              <AnimatedScreen>
                <LoginPage />
              </AnimatedScreen>
            }
          />
          <Route
            path="/"
            element={
              <AnimatedScreen>
                <ProtectedRoute>
                  <RoleHome />
                </ProtectedRoute>
              </AnimatedScreen>
            }
          />
          <Route
            path="/platform"
            element={
              <AnimatedScreen>
                <PlatformRoute>
                  <PlatformConsolePage />
                </PlatformRoute>
              </AnimatedScreen>
            }
          />
        </Routes>
      </AnimatePresence>
    </div>
  );
}
