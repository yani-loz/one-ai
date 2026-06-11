/**
 * Role: Status pill for a connection — a status dot + label reflecting the TWO orthogonal states:
 *       the admin on/off (is_enabled) takes precedence, then verification health (status).
 * Used by: ConnectorsPage.tsx.
 * Depends on: ./types, the aurora Tailwind theme + the animate-pulse-dot keyframe.
 * Key invariants:
 *   - Disabled wins over health (a disabled connection shows "Disabled", not its last status).
 *   - Only a CONNECTED + enabled connection gets the living teal pulse-dot; error is a static red
 *     dot; everything else is muted — per the design language's status-indicator rules.
 */
import type { Connection } from "./types";

interface Appearance {
  label: string;
  dot: string;
  text: string;
  pulse: boolean;
}

function appearanceFor(connection: Connection): Appearance {
  if (!connection.is_enabled) {
    return { label: "Disabled", dot: "bg-text-muted", text: "text-text-muted", pulse: false };
  }
  if (connection.status === "connected") {
    return { label: "Connected", dot: "bg-brand-teal", text: "text-brand-teal", pulse: true };
  }
  if (connection.status === "error") {
    return { label: "Error", dot: "bg-brand-red", text: "text-brand-red", pulse: false };
  }
  return { label: "Not tested", dot: "bg-text-muted", text: "text-text-secondary", pulse: false };
}

/** A small status pill (dot + label) for one connection's combined enabled + health state. */
export function ConnectorStatusBadge({
  connection,
}: {
  connection: Connection;
}): React.JSX.Element {
  const look = appearanceFor(connection);
  return (
    <span className={`inline-flex items-center gap-1.5 text-sm font-medium ${look.text}`}>
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full ${look.dot} ${look.pulse ? "animate-pulse-dot" : ""}`}
      />
      {look.label}
    </span>
  );
}
