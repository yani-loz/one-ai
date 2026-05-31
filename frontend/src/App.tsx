/**
 * Role: Root route shell — wires the public /login and protected / routes with the
 *       iOS-style directional page transitions, wrapped in AnimatePresence.
 * Used by: src/main.tsx (inside BrowserRouter + AuthProvider).
 * Depends on: react-router-dom (Routes/Route), motion (AnimatePresence),
 *             ./identity (LoginPage, ProtectedRoute), ./HomePage, ./pageTransition.
 * Key invariants: screen-level motion.divs are `absolute inset-0` so the incoming and
 *   outgoing pages overlap during the slide; routing must run inside <BrowserRouter>.
 */
import { AnimatePresence, motion } from "motion/react";
import { Route, Routes, useLocation } from "react-router-dom";

import { HomePage } from "./HomePage";
import { LoginPage, ProtectedRoute } from "./identity";
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
                  <HomePage />
                </ProtectedRoute>
              </AnimatedScreen>
            }
          />
        </Routes>
      </AnimatePresence>
    </div>
  );
}
