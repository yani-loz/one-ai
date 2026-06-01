/**
 * Role: Per-company detail screen (/platform/orgs/:orgId) — the operator's cockpit for one
 *       tenant: crest + metadata, lifecycle (suspend/reactivate), legal hold, and the
 *       governance posture slots (filled in PC-03b). Metadata only — content stays sealed.
 * Used by: App.tsx (the /platform/orgs/:orgId route, behind PlatformRoute).
 * Depends on: ../identity (useAuth, AuthRequestError), ./platformClient, ./format,
 *             ./AuditTrail, ./StatusBadge, ./SealedBanner, ../components/BrandMark,
 *             react-router-dom, motion.
 * Key invariants:
 *   - Rendered only for an authenticated platform admin (PlatformRoute gates it); a 401
 *     means the session lapsed → logout, and the guard routes to /login.
 *   - Shows operational metadata only — never tenant content.
 *   - Mutations re-render from the server's authoritative response; a transient failure
 *     reloads the detail rather than trusting stale local state.
 *   - Each lifecycle mutation bumps auditReloadSignal so the audit trail re-fetches and
 *     the new event shows on the same screen (AC8).
 */
import { useCallback, useEffect, useState } from "react";
import { motion } from "motion/react";
import { useNavigate, useParams } from "react-router-dom";

import { BrandMark } from "../components/BrandMark";
import { seedFromString } from "../components/insignia/generateInsignia";
import { AuthRequestError, useAuth } from "../identity";
import { AuditTrail } from "./AuditTrail";
import { formatShortDate } from "./format";
import {
  getOrganization,
  updateOrganizationLegalHold,
  updateOrganizationStatus,
} from "./platformClient";
import { SealedBanner } from "./SealedBanner";
import { StatusBadge } from "./StatusBadge";
import type { OrganizationDetail } from "./types";

type LoadState = "loading" | "loaded" | "error";

/** Governance posture axes — shown as slots now; configured in PC-03b. */
const GOVERNANCE_SLOTS = [
  "Data residency / region",
  "Deployment mode (shared / sovereign)",
  "DPA status",
  "Works-council mode",
  "EU AI Act risk tier",
  "Retention policy",
];

export function OrganizationDetailPage(): React.JSX.Element {
  const { orgId } = useParams<{ orgId: string }>();
  const navigate = useNavigate();
  const { logout } = useAuth();
  const [org, setOrg] = useState<OrganizationDetail | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [busy, setBusy] = useState(false);
  const [auditReloadSignal, setAuditReloadSignal] = useState(0);

  // Stable so the AuditTrail's fetch effect doesn't re-run every render (refetch loop).
  const handleAuthExpired = useCallback((): void => {
    void logout();
  }, [logout]);

  const load = useCallback(async (): Promise<void> => {
    if (orgId === undefined) return;
    setLoadState("loading");
    try {
      setOrg(await getOrganization(orgId));
      setLoadState("loaded");
    } catch (error) {
      if (error instanceof AuthRequestError && error.status === 401) {
        await logout();
        return;
      }
      setLoadState("error");
    }
  }, [orgId, logout]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Run a lifecycle mutation, re-rendering from the server's authoritative response. */
  const runUpdate = useCallback(
    async (update: () => Promise<OrganizationDetail>): Promise<void> => {
      setBusy(true);
      try {
        setOrg(await update());
        setAuditReloadSignal((signal) => signal + 1); // the action just wrote an audit row
      } catch (error) {
        if (error instanceof AuthRequestError && error.status === 401) {
          await logout();
          return;
        }
        await load(); // reload the authoritative state on any other failure
      } finally {
        setBusy(false);
      }
    },
    [logout, load],
  );

  function goBack(): void {
    navigate("/platform", { state: { nav: "back" } });
  }

  return (
    <motion.main
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.32, 0.72, 0, 1] }}
      className="min-h-screen px-6 py-10"
    >
      <div className="mx-auto max-w-2xl">
        <button
          type="button"
          onClick={goBack}
          className="mb-6 inline-flex items-center gap-1 text-sm font-medium text-text-secondary transition-colors duration-200 hover:text-brand-teal"
        >
          ‹ Back to companies
        </button>

        {loadState === "loading" && <DetailSkeleton />}

        {loadState === "error" && (
          <div className="animate-fade-in rounded-xl border border-brand-red/30 bg-brand-red/10 p-6 text-center">
            <p className="text-sm text-brand-red">Couldn&apos;t load this company.</p>
            <button
              type="button"
              onClick={() => void load()}
              className="mt-3 rounded-lg border border-white/50 bg-white/60 px-4 py-2 text-sm font-medium text-text-primary transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
            >
              Retry
            </button>
          </div>
        )}

        {loadState === "loaded" && org !== null && (
          <>
            <header className="flex items-center gap-4">
              <BrandMark seed={seedFromString(org.id)} size={64} assembleSeconds={1.2} className="" />
              <div className="min-w-0">
                <h1 className="truncate text-h2 font-bold text-text-primary">{org.name}</h1>
                <p className="text-sm text-text-muted">{org.slug}</p>
                <div className="mt-1.5 flex items-center gap-2">
                  <StatusBadge status={org.status} />
                  <span className="text-xs text-text-muted">
                    {org.user_count === 1 ? "1 seat" : `${org.user_count} seats`} ·{" "}
                    {formatShortDate(org.created_at)}
                  </span>
                </div>
              </div>
            </header>

            <Section title="Lifecycle">
              <Row
                label={org.status === "active" ? "Active" : "Not active"}
                hint={
                  org.status === "active"
                    ? "Company users can sign in."
                    : "Company sign-in is blocked for this organization."
                }
              >
                {org.status === "active" ? (
                  <ActionButton
                    tone="danger"
                    busy={busy}
                    onClick={() => void runUpdate(() => updateOrganizationStatus(org.id, "suspended"))}
                  >
                    Suspend access
                  </ActionButton>
                ) : (
                  <ActionButton
                    tone="primary"
                    busy={busy}
                    onClick={() => void runUpdate(() => updateOrganizationStatus(org.id, "active"))}
                  >
                    Reactivate
                  </ActionButton>
                )}
              </Row>
            </Section>

            <Section title="Legal hold">
              <Row
                label={org.legal_hold ? "On" : "Off"}
                hint="Blocks data erasure while set (GDPR-conflict guard — enforced in PC-06)."
              >
                <ActionButton
                  tone="neutral"
                  busy={busy}
                  onClick={() =>
                    void runUpdate(() => updateOrganizationLegalHold(org.id, !org.legal_hold))
                  }
                >
                  {org.legal_hold ? "Clear hold" : "Place hold"}
                </ActionButton>
              </Row>
            </Section>

            <Section title="Governance posture">
              <p className="mb-3 text-xs text-text-muted">
                Region, residency, DPA, works-council, EU AI Act risk and retention are
                configured in a later release.
              </p>
              <div className="space-y-2">
                {GOVERNANCE_SLOTS.map((slot) => (
                  <div key={slot} className="flex items-center justify-between gap-3">
                    <p className="text-sm text-text-secondary">{slot}</p>
                    <span className="text-xs text-text-muted">Not yet configured</span>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="Audit trail">
              <AuditTrail
                orgId={org.id}
                reloadSignal={auditReloadSignal}
                onAuthExpired={handleAuthExpired}
              />
            </Section>

            <div className="mt-8">
              <SealedBanner />
            </div>
          </>
        )}
      </div>
    </motion.main>
  );
}

/** A titled glass section card. */
function Section({ title, children }: { title: string; children: React.ReactNode }): React.JSX.Element {
  return (
    <section className="mt-6 rounded-xl border border-white/50 bg-white/65 p-5 shadow-sm backdrop-blur-xl">
      <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-text-secondary">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A label + hint on the left, an action on the right. */
function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-text-primary">{label}</p>
        <p className="text-xs text-text-muted">{hint}</p>
      </div>
      {children}
    </div>
  );
}

/** A small tone-styled action button with a busy/disabled state. */
function ActionButton({
  tone,
  busy,
  onClick,
  children,
}: {
  tone: "primary" | "danger" | "neutral";
  busy: boolean;
  onClick: () => void;
  children: React.ReactNode;
}): React.JSX.Element {
  const toneClass =
    tone === "danger"
      ? "border-brand-red/40 bg-brand-red/10 text-brand-red"
      : tone === "primary"
        ? "border-brand-teal/40 bg-brand-teal/10 text-brand-teal"
        : "border-white/50 bg-white/50 text-text-primary";
  return (
    <button
      type="button"
      disabled={busy}
      onClick={onClick}
      className={`shrink-0 rounded-lg border px-3 py-2 text-xs font-medium transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40 ${toneClass}`}
    >
      {children}
    </button>
  );
}

/** Shimmer placeholder while the detail loads. */
function DetailSkeleton(): React.JSX.Element {
  return (
    <div className="space-y-4">
      <div className="relative h-20 overflow-hidden rounded-xl border border-white/50 bg-white/40">
        <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
      </div>
      <div className="relative h-24 overflow-hidden rounded-xl border border-white/50 bg-white/40">
        <div className="absolute inset-0 animate-shimmer bg-gradient-to-r from-transparent via-white/50 to-transparent" />
      </div>
    </div>
  );
}
