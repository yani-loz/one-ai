/**
 * Role: Browser entry point — mounts the React tree under BrowserRouter +
 *       AuthProvider and loads the design system.
 * Used by: index.html (module script).
 * Depends on: ./App, ./identity (AuthProvider), react-router-dom (BrowserRouter),
 *             ./index.css (Tailwind v4 aurora theme).
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { AuthProvider } from "./identity";
import "./index.css";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found in index.html");
}

createRoot(rootElement).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
