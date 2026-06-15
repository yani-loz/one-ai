/**
 * Role: Stateful controller for the Tier-2 connector governance panel — owns the fetch of the
 *       governance view (entitlement ceiling + org-wide policy + per-user overrides + §7 health
 *       roll-up) for one connector type and every governance mutation (toggle org-wide, set/clear a
 *       per-user grant/deny). Keeps ConnectorGovernancePanel thin.
 * Used by: ConnectorGovernancePanel.tsx.
 * Depends on: react (hooks), ./connectorGovernanceClient, ./connectorGovernanceTypes,
 *             ../identity (AuthRequestError via statusOf).
 * Key invariants:
 *   - A 401/403 (lapsed session / role downgraded — an admin never legitimately gets 403 from the
 *     GET) calls `logout` and stops; the route guard redirects. A 403 from a WRITE means "beyond
 *     entitlement" and is surfaced as a friendly notice (not a logout) — the panel also pre-disables
 *     those controls when entitled=false, so this is a defensive backstop.
 *   - Every mutation returns the FULL refreshed governance view; the controller re-renders from that
 *     server truth (no optimistic local edits that could drift).
 *   - `busy` is panel-wide (one governance action at a time); a failure surfaces a friendly notice.
 */
import { useCallback, useEffect, useState } from "react";

import { AuthRequestError } from "../identity";
import {
  clearUserOverride,
  getConnectorGovernance,
  setOrgWidePolicy,
  setUserOverride,
} from "./connectorGovernanceClient";
import type { ConnectorGovernance } from "./connectorGovernanceTypes";

export type LoadState = "loading" | "loaded" | "error";

/** Everything ConnectorGovernancePanel needs to render + drive one connector type's governance. */
export interface ConnectorGovernanceController {
  governance: ConnectorGovernance | null;
  loadState: LoadState;
  busy: boolean;
  notice: string | null;
  reload: () => Promise<void>;
  toggleOrgWide: (enabled: boolean) => Promise<void>;
  grant: (userId: string) => Promise<void>;
  deny: (userId: string) => Promise<void>;
  clearOverride: (userId: string) => Promise<void>;
}

const GENERIC_MESSAGE = "Something went wrong. Please try again.";
const ENTITLEMENT_MESSAGE = "This connector isn't included in your company's plan.";

function statusOf(error: unknown): number {
  return error instanceof AuthRequestError ? error.status : 0;
}

/**
 * Manage one connector type's governance for the caller's org.
 *
 * Contract: loads the governance view on mount (and when `connectorType` changes); exposes the
 * load/busy/notice state + the mutation actions. `logout` is called on a 401, or on a 403 from the
 * read (a downgraded admin); a 403 from a write is surfaced as the entitlement message.
 */
export function useConnectorGovernance(
  connectorType: string,
  logout: () => Promise<void>,
): ConnectorGovernanceController {
  const [governance, setGovernance] = useState<ConnectorGovernance | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(async (): Promise<void> => {
    setLoadState("loading");
    try {
      setGovernance(await getConnectorGovernance(connectorType));
      setLoadState("loaded");
    } catch (error) {
      // On the READ a 403 means the admin role was revoked mid-session — re-authenticate.
      if (statusOf(error) === 401 || statusOf(error) === 403) {
        await logout();
        return;
      }
      setLoadState("error");
    }
  }, [connectorType, logout]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Run a governance mutation, applying its refreshed view; 401 → logout, 403 → entitlement notice. */
  const runMutation = useCallback(
    async (mutate: () => Promise<ConnectorGovernance>): Promise<void> => {
      setNotice(null);
      setBusy(true);
      try {
        setGovernance(await mutate());
      } catch (error) {
        if (statusOf(error) === 401) {
          await logout();
          return;
        }
        setNotice(statusOf(error) === 403 ? ENTITLEMENT_MESSAGE : GENERIC_MESSAGE);
      } finally {
        setBusy(false);
      }
    },
    [logout],
  );

  const toggleOrgWide = useCallback(
    (enabled: boolean) => runMutation(() => setOrgWidePolicy(connectorType, enabled)),
    [runMutation, connectorType],
  );

  const grant = useCallback(
    (userId: string) => runMutation(() => setUserOverride(connectorType, userId, "grant")),
    [runMutation, connectorType],
  );

  const deny = useCallback(
    (userId: string) => runMutation(() => setUserOverride(connectorType, userId, "deny")),
    [runMutation, connectorType],
  );

  const clearOverride = useCallback(
    (userId: string) => runMutation(() => clearUserOverride(connectorType, userId)),
    [runMutation, connectorType],
  );

  return { governance, loadState, busy, notice, reload, toggleOrgWide, grant, deny, clearOverride };
}
