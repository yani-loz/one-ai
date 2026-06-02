# TC-SG-022: Boundary statement — f8a4fbd is a prod boot-time control, not a runtime RLS enabler

| Field | Value |
|---|---|
| **ID** | TC-SG-022 · **Suite** C · **Type** Negative · **Severity if fail** Info |
| **Result** | ⚠️ Pass-with-concern · **Tag** 📋 CONFIRMS-DOCUMENTED · **Status** Executed |

## Execution result (2026-06-02)
**Scope assertion (synthesis of TC-SG-020 + TC-SG-021; no additional run):** does `f8a4fbd` change the runtime isolation
posture of the running dev stack?

**Evidence**
```
From TC-SG-020: role oneai is rolsuper=rolbypassrls=t, FORCE not set ⇒ RLS inert at runtime, cross-org users readable.
From TC-SG-021: dev secret forgeable ⇒ forged platform token reads all orgs' metadata (200).
Live config: app_env=local ⇒ requires_secure_secrets=False ⇒ the boot guard is NOT engaged on this process.
```
**Verdict:** Precise scope. `f8a4fbd` **PREVENTS** staging / production / a typo'd `app_env` from BOOTING while the dev JWT
secret or DB password is unchanged (`requires_secure_secrets` ⇒ `InsecureConfigurationError`) — a real, valuable
fail-closed prod control. It does **NOT** (and does not claim to) enable runtime RLS, nor change the running dev stack:
the dev process still boots on `app_env=local` with the public secret (forgeable, TC-SG-021) and still connects as the
superuser/owner `oneai` role (RLS bypassed, TC-SG-020). Cross-org reads remain open at runtime until migration `0007`
lands the `oneai_app`/`oneai_global` role + engine split and `FORCE ROW LEVEL SECURITY`. The two controls are orthogonal
and both documented — this is the boundary of done, not a defect.
