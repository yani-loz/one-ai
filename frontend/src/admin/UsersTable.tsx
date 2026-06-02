/**
 * Role: Presentational list of the organization's users — one row each, showing identity +
 *       role + active state and the row actions (change role, deactivate / reactivate).
 *       Holds NO data-fetching or mutation logic; it renders props and raises callbacks.
 * Used by: AdminConsolePage.tsx.
 * Depends on: ./RoleBadge, ./types (CompanyUser, CompanyUserRole), ../platform/format
 *             (formatShortDate), the aurora Tailwind theme.
 * Key invariants:
 *   - Pure/presentational: the parent owns the user list and performs every mutation; this
 *     component only signals intent (onChangeRole / onDeactivate / onReactivate).
 *   - The row whose id === currentUserId is tagged "(you)" so an admin sees when an action
 *     targets their own account (the parent gates self-demotion / self-deactivation).
 *   - Controls for the row being mutated (busyUserId) are disabled to prevent double-submits.
 */
import { formatShortDate } from "../platform/format";
import { RoleBadge } from "./RoleBadge";
import type { CompanyUser, CompanyUserRole } from "./types";

const ROLE_OPTIONS: { value: CompanyUserRole; label: string }[] = [
  { value: "member", label: "Member" },
  { value: "company_admin", label: "Administrator" },
];

interface UsersTableProps {
  users: CompanyUser[];
  currentUserId: string;
  busyUserId: string | null;
  onChangeRole: (user: CompanyUser, role: CompanyUserRole) => void;
  onDeactivate: (user: CompanyUser) => void;
  onReactivate: (user: CompanyUser) => void;
}

/**
 * Render the users list.
 *
 * Contract: renders one row per user (the parent passes them newest-first). Each active row
 * exposes a role selector + Deactivate; each inactive row shows a muted role + Reactivate.
 */
export function UsersTable({
  users,
  currentUserId,
  busyUserId,
  onChangeRole,
  onDeactivate,
  onReactivate,
}: UsersTableProps): React.JSX.Element {
  return (
    <ul className="space-y-2">
      {users.map((user) => (
        <UserRow
          key={user.id}
          user={user}
          isSelf={user.id === currentUserId}
          busy={busyUserId === user.id}
          onChangeRole={onChangeRole}
          onDeactivate={onDeactivate}
          onReactivate={onReactivate}
        />
      ))}
    </ul>
  );
}

interface UserRowProps {
  user: CompanyUser;
  isSelf: boolean;
  busy: boolean;
  onChangeRole: (user: CompanyUser, role: CompanyUserRole) => void;
  onDeactivate: (user: CompanyUser) => void;
  onReactivate: (user: CompanyUser) => void;
}

/** One user row — identity on the left, role + state + actions on the right. */
function UserRow({
  user,
  isSelf,
  busy,
  onChangeRole,
  onDeactivate,
  onReactivate,
}: UserRowProps): React.JSX.Element {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/50 bg-white/65 px-4 py-3 shadow-sm backdrop-blur-xl">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-text-primary">
          {user.full_name}
          {isSelf && <span className="ml-1.5 text-xs font-normal text-text-muted">(you)</span>}
        </p>
        <p className="truncate text-xs text-text-muted">
          {user.email} · joined {formatShortDate(user.created_at)}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {user.is_active ? (
          <>
            <RoleBadge role={user.role} />
            <label className="sr-only" htmlFor={`role-${user.id}`}>
              Change role for {user.full_name}
            </label>
            <select
              id={`role-${user.id}`}
              value={user.role}
              disabled={busy}
              onChange={(event) =>
                onChangeRole(user, event.target.value as CompanyUserRole)
              }
              className="rounded-lg border-[1.5px] border-slate-200/70 bg-white/70 px-2.5 py-1.5 text-xs text-text-primary outline-none transition-[border-color,box-shadow] duration-200 focus:border-brand-teal focus:shadow-[0_0_0_3px_rgba(13,148,136,0.1)] disabled:opacity-40"
            >
              {ROLE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={busy}
              onClick={() => onDeactivate(user)}
              className="rounded-lg border border-brand-red/40 bg-brand-red/10 px-3 py-1.5 text-xs font-medium text-brand-red transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
            >
              Deactivate
            </button>
          </>
        ) : (
          <>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-white/50 bg-white/40 px-2.5 py-0.5 text-xs font-medium text-text-muted">
              <span
                className="inline-block h-2 w-2 rounded-full bg-text-muted/60"
                aria-hidden="true"
              />
              Deactivated
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => onReactivate(user)}
              className="rounded-lg border border-brand-teal/40 bg-brand-teal/10 px-3 py-1.5 text-xs font-medium text-brand-teal transition-all duration-200 hover:scale-[1.03] active:scale-[0.97] disabled:opacity-40"
            >
              Reactivate
            </button>
          </>
        )}
      </div>
    </li>
  );
}
