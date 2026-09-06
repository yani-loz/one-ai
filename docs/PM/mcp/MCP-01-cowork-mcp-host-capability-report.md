# MCP-01 — Custom MCP Servers, Claude Cowork as MCP Host, and Cross-Platform Portability

**Evidence-grounded capability report for the One AI remote MCP "company-memory" server.**

- **Prepared:** 2026-07-08 · **Research access date for all sources:** 2026-07-08
- **Method:** `/deep-research` workflow — 6 search angles → 26 sources fetched → 125 claims extracted → top-25 adversarially verified (3-vote). The workflow's final *synthesis* step and a few verify votes were killed by an Anthropic session limit; this report was **hand-synthesized from all 125 extracted claims**, with each claim's source ground-truthed against the fetching agent's actual `WebFetch` call and tagged for confidence. See `MCP-01-deep-research-prompt.md` for the brief.
- **Companion:** the research brief lives at `docs/PM/mcp/MCP-01-deep-research-prompt.md`.

> **Status as of 2026-09-06 — the research stands; the build and the gates behind it do not.**
> The protocol, axis-1 identity and portability findings below are **unchanged and still the
> reference**. What has changed is only the surrounding status:
>
> - **Build status: ABSENT.** There is no MCP server in this repository. Verified 2026-09-06:
>   `backend/app/mcp/` does not exist (`app/` holds access · api · ask · common · connectors ·
>   core · db · entities · identity), `backend/pyproject.toml` declares **no MCP dependency**
>   (zero matches for `mcp` in the file), and a repo-wide grep for `FastMCP`,
>   `modelcontextprotocol`, `from mcp` and `import mcp` across `backend/**/*.py` returns zero
>   hits. The only trace of the plan in code is a forward-looking comment at
>   `backend/app/access/services/email_projection.py:14`.
> - **The gates this report was written for passed without it.** The report repeatedly
>   conditions its open items on "before mid-August demo & Sept pilot" (see §1 and §12). The
>   **mid-August 2026 Cowork demo milestone is MISSED** — it passed with no MCP server in the
>   tree — and there is no evidence in this repository of the September EU pilot having
>   started. Re-baselining those dates is a founder call; none is invented here.
> - **§12's open-questions register is still fully open.** All **8 empirical probes** (items
>   1–8, which require a stub connector deployed into a Cowork/Team workspace) and all **7
>   written/contractual items** (items 9–15, which require primary answers from Anthropic)
>   remain **unanswered**. A grep across `docs/` for the governance terms this report needs
>   (`trust.anthropic.com`, `static_headers`, `ID-JAG`, `Cowork`) finds no document that
>   answers any of them — the only other hits are this report, its research brief, the
>   2026-09-06 built-vs-docs inventory, and two MEM-01 documents that reference Cowork as a
>   *consumer* of our future MCP tools rather than as a governance source.
> - **Consequence for the axis-2 risk:** the report's headline risk ("INSUFFICIENT PRIMARY
>   EVIDENCE — must be confirmed in writing with Anthropic") is not merely open, it is
>   **two months older and unmitigated**. Nothing below should be quoted externally as a
>   residency, retention, or DPA position.

---

## Confidence legend (read first)

| Tag | Meaning |
|---|---|
| 🟢 **VERIFIED** | From a current **primary/official** source (MCP spec, Anthropic docs, OpenAI docs, OWASP, NSA/CISA). `[adv N-M]` = also survived the workflow's 3-vote adversarial check with that vote. |
| 🟡 **REPORTED** | From a credible **secondary source, blog, or official-repo issue**. The source *says* it; not confirmed against a primary doc. A 3-0 adversarial "confirm" on a blog only proves *the blog says it*, not that it is true. |
| 🔴 **UNVERIFIED** | Could not confirm from any captured source, or the only source carries no evidentiary weight, or sources conflict without a primary tiebreaker. |

> **Structural caveat that shapes the whole report:** the two make-or-break axes did **not** come back symmetrically. **Axis 1 (per-user identity)** is answered by *primary* sources — the MCP spec and Anthropic's own connector/auth docs. **Axis 2 (data residency / retention / DPA)** is answered *only* by blogs, a secondary vendor-profile site, and one anonymous, self-closed GitHub issue. **No Anthropic trust/legal/privacy/DPA page was fetched.** So axis 2's real status is *insufficient primary evidence*, and that is itself the report's single most important finding.

---

## 1. Executive summary

### Axis 1 — Can a remote MCP server obtain reliable per-user identity from Claude Cowork under org-wide enterprise deployment?

**YES — achievable, but per-user attribution is something YOUR server must enforce by construction, not something the host guarantees.**

- The MCP server **designates its OWN OAuth 2.1 authorization server** via RFC 9728 protected-resource metadata, and *"Claude resolves it regardless of which host it points at"* — so the entire identity/auth plane can run on **your own (EU) infrastructure** and federate to the customer's corporate IdP. 🟢 VERIFIED (Anthropic connector docs; MCP spec 2025-11-25).
- **Each user completes an individual OAuth grant.** On Team/Enterprise, an Owner adds the connector once, then *"users individually connect to and enable that connector."* 🟢 VERIFIED `[adv 3-0]`.
- **Enterprise Managed Auth (EMA)** gives a *verified corporate identity*: the client obtains an **ID-JAG** (Identity Assertion JWT Authorization Grant) from the corporate IdP at SSO and exchanges it for a token **minted by your own AS** — zero-touch, IdP-authoritative. 🟢 VERIFIED (MCP EMA post).
- **The escape hatch that forces server-side enforcement:** a shared, org-wide credential mode (`static_headers`, beta) exists — one bearer token entered once by an admin, sent on every request, *"shared by the organization rather than pasted per user."* 🟢 VERIFIED. **Your server must therefore reject any request that does not carry an individual, validated identity — do not assume the host only ever sends per-user tokens.**
- **The binding hazard:** Claude's SSO/SCIM identity is keyed on **email** (*"SCIM email and SSO email must be identical"*). 🟢 VERIFIED `[adv 3-0]`. Email is mutable and, if unverified, spoofable → **bind your internal user on a stable `sub`/IdP-subject claim, never on raw email, and require a verified-email/assertion path.**

### Axis 2 — Can host-mediated usage be operated in a GDPR / works-council-defensible way given Anthropic's residency, retention, and DPA terms?

**INSUFFICIENT PRIMARY EVIDENCE — must be confirmed in writing with Anthropic before the September EU pilot. This is the headline risk of the entire report.**

- **No Anthropic-primary governance source was captured.** Every residency/retention/Cowork-exclusion fact below is 🟡 REPORTED (blog/secondary) or 🔴 UNVERIFIED (anonymous forum).
- Multiple blogs REPORT that **hosted workspace data-at-rest is US-only** (*"us is currently the only available workspace geo"*), while one blog REPORTS Enterprise offers **"custom data residency on request"** (unspecified). These conflict and neither is primary.
- Blogs REPORT that **Zero-Data-Retention and the HIPAA BAA exclude Cowork specifically** (BAA "not covered: … Cowork"; ZDR limited to Messages API / Token-Counting / Claude Code).
- **Potentially disqualifying, and currently only an inference:** Cowork's web/mobile clients are **gated to Max** — a *consumer* plan — and a blog REPORTS consumer plans (claude.ai) **defaulted to training opt-in with up to 5-year retention on 2025-10-08**. Whether Cowork-via-Max inherits consumer data terms or commercial terms is 🔴 UNVERIFIED and must be resolved first.

### Five most consequential facts

**doc-VERIFIED (primary source):**
1. Server owns its AS via RFC 9728; Claude resolves a cross-host (EU) AS → EU-only identity plane is possible. `[adv 3-0]`
2. Per-user OAuth grant is the norm on Team/Enterprise (Owner adds, each user connects). `[adv 3-0]`
3. Spec **forbids token passthrough and mandates audience validation** — the protocol basis for cross-tenant isolation; the server **MUST re-derive identity from the token on every call and MUST NOT use the MCP session as authentication.** `[adv 3-0]`
4. A **shared org-wide credential mode exists** (`static_headers`) → per-user attribution must be server-enforced.
5. Enterprise Managed Auth (ID-JAG / Okta XAA) delivers a **verified corporate IdP identity**, token minted by your AS.

**REQUIRES EMPIRICAL / WRITTEN CONFIRMATION before mid-August demo & Sept pilot:**
1. **Where Cowork tool-call traffic and tool *results* are processed/stored, and in which region** (no primary source; stub-connector test + Anthropic trust center).
2. **Whether Cowork usage falls under commercial (no-training, short-retention) or consumer (opt-in-training, 5-yr) data terms** given its Max gating.
3. **Whether a DPA (Art. 28) + SCCs explicitly cover MCP tool traffic through Cowork**, and whether a BAA/ZDR/EU-residency option is contractually available for Cowork specifically.
4. **Exact token claims** Cowork's client presents (stable `sub`? verified email? tenant?) — wire capture required.
5. **Unattended/scheduled-run identity** — whether background runs carry a specific human's identity or degrade to a shared credential (no source found).

### The three biggest unknowns
1. **Data residency of Cowork tool traffic (axis 2).** Unresolved and primary-sourceless — the pilot can die here even if identity is perfect.
2. **Cowork's governing data terms** (consumer-Max vs commercial-Enterprise) — a training/retention exposure that is potentially a hard blocker.
3. **Unattended/scheduled write-back identity** — the per-user write-provenance model may not extend to background runs; no evidence either way was found.

---

## 2. The MCP protocol today (spec revision + date)

- **Current authorization revision: `2025-11-25`.** The more widely *implemented* revision in the field is `2025-06-18`; prior auth revision `2025-03-26`; plus a `draft`. 🟢 VERIFIED (stackoverflow.blog dates corroborated against the spec URL that resolves at `modelcontextprotocol.io/specification/2025-11-25/…`).
- **What a spec-compliant remote MCP server MUST implement today (2025-11-25):** 🟢 VERIFIED (primary spec) `[adv 3-0 on the core clauses]`
  - Act as an **OAuth 2.1 resource server**; the **authorization server is a separate concern** ("may be hosted with the resource server or a separate entity").
  - **RFC 9728 Protected Resource Metadata is MANDATORY** — serve `/.well-known/oauth-protected-resource` with an `authorization_servers` field naming ≥1 AS; return `401` + `WWW-Authenticate: … resource_metadata=…` when auth is required.
  - The AS **MUST** expose **RFC 8414** AS metadata *or* **OpenID Connect Discovery 1.0**.
  - **Clients MUST implement RFC 8707 Resource Indicators** — `resource` param (canonical server URI) in **both** authorization and token requests, sent regardless of AS support. `[adv 2-1]`
  - **PKCE with `S256` is mandatory** for clients.
  - **Token passthrough is forbidden; audience validation is mandatory** — server MUST reject tokens not issued for it and MUST NOT forward the received token upstream (use RFC 8693 token exchange for downstream calls).
  - **Dynamic Client Registration (RFC 7591) is now `MAY`/optional** (kept for backwards-compat); **Client ID Metadata Documents (CIMD)** is the preferred no-prior-relationship path.
  - **Step-up authorization is new in 2025-11-25** — server returns `403` with new scope requirements to force re-authorization for additional privileges.
  - Applies to **HTTP transports**; **STDIO SHOULD NOT** use it (read creds from environment). AS **SHOULD** issue short-lived access tokens and **MUST** rotate refresh tokens for public clients.
- **Version hygiene / stale-risk:** the **`2025-03-26`** revision does **not** reference RFC 9728 or RFC 8707 (only OAuth 2.1 + RFC 8414 + RFC 7591). 🟢 VERIFIED `[adv 3-0]`. Any design or vendor claim citing RFC 9728/8707 as normative is implicitly on the **≥2025-06-18** line. Pin your implementation to **2025-11-25** and treat `2025-06-18` as the interop floor.
- **What the protocol does NOT give you** (so you must build it): 🟢 VERIFIED (NSA/CISA CSI, primary)
  - **No protocol-level token lifecycle** — refresh/revocation/reuse-control are unspecified; you own expiry/rotation/revocation.
  - **Session→identity association is undefined**; there is **no RBAC exchange** in the protocol. Per-user binding and access boundaries are entirely your server's job.
  - MCP *"cannot enforce these security principles at the protocol level."*

---

## 3. Claude Cowork as host — identity, capabilities, and practical access

### 3.1 Product identity confirmation (Step 0)

- **Canonical name:** **Claude Cowork** — Anthropic's Claude-Code-style **agent for general knowledge work** (not only coding). 🟡 REPORTED (TechCrunch, 2026-07-07).
- **Timeline:** launched as a **desktop app in January 2026**; **web and mobile** clients went live **2026-07-07**, gated to **Max** subscribers. 🟡 REPORTED (TechCrunch). The desktop app remains the "deep work" surface with **local file + browser access**.
- **Adoption signal:** Anthropic cited "1.2 million anonymized Cowork sessions from more than 600,000 organizations." 🟡 REPORTED (tangential).
- **Disambiguation:** distinct from **Claude Code** (CLI/IDE coding agent — and, importantly, the *only* surface documented to route inference to EU hyperscaler regions), **Claude Desktop**, **claude.ai chat**, **Projects**, and the **Claude API**. Custom remote-MCP connectors are documented to work on **Claude (claude.ai), Cowork, and Claude Desktop**. 🟢 VERIFIED (Anthropic support).
- **Documentation sparseness (must state plainly):** there is **no primary Anthropic product/spec page for Cowork** in the captured evidence — identity facts rest on **secondary reporting (TechCrunch)**, and **all Cowork-specific governance facts are blog/secondary/forum**. Cowork's own **operational limits, wire-level payloads, HITL-forcing behavior, admin visibility, and data terms are effectively undocumented in primary sources** and must be treated as open.

### 3.2 Access, plan gating & commercial terms

| Item | Finding | Tag |
|---|---|---|
| Custom remote-MCP connectors on Cowork | Supported (Cowork named explicitly) across Free/Pro/Max/Team/Enterprise; Free limited to 1 connector | 🟢 VERIFIED `[adv 3-0]` |
| Cowork web/mobile access | Gated to **Max** subscribers (a consumer plan) | 🟡 REPORTED |
| Who can add a connector (Team/Enterprise) | **Owners only**; then each user connects individually | 🟢 VERIFIED `[adv 3-0]` |
| Restrict connector actions org-wide | Team/Enterprise Owners can allow search/read but block send/edit; users can't override; Owners can disable specific tool calls | 🟡 REPORTED / 🟢 VERIFIED (support doc: disable interactive tool calls) |
| Connector-install rights by group | Enterprise role-based groups can scope who installs connectors across Code/chat/Cowork | 🟡 REPORTED |
| EU availability / purchase model / seat minimums / SLA / status page / deprecation policy | **Not found in any source** | 🔴 UNVERIFIED |

### 3.3 MCP feature-support matrix for Claude hosts

🔴 **Largely UNVERIFIED at the Cowork level.** The captured sources establish the *protocol's* feature set and Anthropic's *connector* auth model, but **no source enumerated Cowork's support for resources / prompts / sampling / elicitation / roots / notifications / tool-annotations / `outputSchema`, transport support (streamable HTTP vs SSE vs stdio), or whether the approval UX honors `readOnlyHint`/`destructiveHint`.** This entire matrix is a pre-demo empirical task (see §12).

---

## 4. Identity & auth deep-dive

### 4.1 Admin deploys connector org-wide → first user interaction
- Owner adds the connector once (optionally supplying an OAuth Client ID/Secret for your server). Each user then runs the **OAuth 2.1 authorization-code + PKCE (`S256`)** flow individually and grants scopes; **Claude never sees the user's password**. 🟢 VERIFIED (support + connector docs).
- **Your server designates its own AS** via RFC 9728; Claude resolves it wherever it points (EU OK). Client registration is **CIMD or DCR (RFC 7591)**. 🟢 VERIFIED `[adv 3-0]`.
- **Corporate-identity path (the one to use):** **Enterprise Managed Auth** — user SSOs to their IdP, client gets an **ID-JAG**, exchanges it at **your AS** for an access token; *zero-touch*, no per-app consent. As of the **2026-06-18** EMA announcement it runs on **Okta Cross-App-Access (XAA)**, with Claude/Claude Code/VS Code as clients. 🟢 VERIFIED (MCP EMA post). Anthropic's own SSO federation (Entra ID, Google Workspace) is **mediated via WorkOS** and is **Team/Enterprise-only**, requiring a verified domain. 🟢 VERIFIED `[adv 3-0]`.

### 4.2 Token lifecycle & refresh — **decision-critical, blog-sourced**
- 🟡 REPORTED (sunpeak.ai): **Anthropic stores the encrypted access *and refresh* tokens** ("your server does not need to manage token storage") and **auto-refreshes proactively up to 5 minutes before expiry.** Two hard implications:
  - **The long-lived refresh credential lives on the host vendor's (US) infrastructure.** That is the blast-radius surface for Q21 and a residency data-point for axis 2.
  - Refresh happens **with no human in the loop** once granted → the "human present per call" assumption does not hold for the token plane.

### 4.3 Server-driven revocation / offboarding — **must hold independent of the host**
- 🟡 REPORTED (sunpeak.ai): when a user disconnects the connector, *"Anthropic removes the stored tokens. However, tokens at your identity provider remain valid until they expire."* → **host disconnect ≠ revocation.** Symmetrically, a stale token could survive at Anthropic until expiry if you revoke only at your IdP.
- **Design mandate (conservative regardless of source quality):** issue **short-lived access tokens** + **server-side introspection/allow-list keyed on stable subject**, so you can **kill a user in seconds** by flipping server state — never rely on the host propagating a revocation. Support **RFC 7009** revocation and rotate refresh tokens (spec `MUST` for public clients).
- **Directory-sync offboarding signal:** **SCIM de-provisioning exists on Enterprise** (accounts deactivated by the customer IdP). 🟡 REPORTED. Use it as a *signal*, but revocation must still be **server-driven**.

### 4.4 Tenant isolation across customer orgs
- **Protocol-level basis (primary):** server **MUST** validate token audience, **MUST NOT** accept tokens not issued for it, **MUST NOT** use the MCP session for auth, **MUST** re-verify every inbound request, and **SHOULD** bind session IDs as `<user_id>:<session_id>` where `user_id` comes from the **validated token, never the client**. 🟢 VERIFIED `[adv 3-0]`.
- **Deployment topology:** the connector is added by URL and bound to an org by the Owner who installs it; the natural pattern is a **per-tenant endpoint or a tenant claim inside the validated token**. Re-derive **user AND tenant from the token on every call**; treat `Mcp-Session-Id` as a routing hint, never as identity. (Anthropic docs don't prescribe a topology → the isolation guarantee is yours to build.) 🟢 VERIFIED (spec) / 🔴 UNVERIFIED (Anthropic-prescribed tenant binding).
- **Named failure class:** OWASP **MCP10:2025 Context Injection & Over-Sharing** — shared/persistent/under-scoped context leaking across users/tenants. 🟢 VERIFIED.

### 4.5 Identity-claim contents (empirically-verifiable — NOT settled by docs)
- 🔴 **UNVERIFIED:** the exact claims Cowork's client presents (stable immutable `sub`? `email`? `email_verified`? tenant/org id?). What *is* known: Anthropic's **account layer keys on email** 🟢, and EMA can deliver a **corporate IdP subject** 🟢. **Do not build on email.** Capture the real token in a stub connector before committing the RLS key (see §12).

---

## 5. Information-visibility map (what the server can see / log vs. cannot)

| Data element | Visible to server? | Evidence / note |
|---|---|---|
| Access token + its claims | **Yes** (you validate it) | 🟢 spec: server validates audience/signature/expiry every call |
| Authenticated user identity | **Yes, if you bind it** | via token `sub`/assertion; **email is host-keyed** 🟢 — bind on stable subject |
| Tenant/org id | **Partial** | only if placed in token/endpoint by construction; not host-guaranteed 🔴 |
| Tool-call arguments | **Yes** | the model chooses what to send; this is your primary input surface |
| Full conversation content | **No (by default)** | only what the model puts in tool args reaches you — *empirically confirm* no extra content leaks (§12) 🔴 |
| MCP session id | **Yes** but **not identity** | 🟢 spec: MUST NOT use session for auth |
| "Human approved this call" signal | **No reliable signal** | approval is host-side UX; not a trustworthy wire fact 🔴 (see §6) |
| Client name/version (initialize) | **Likely yes** | handshake carries client info — *confirm exact fields empirically* 🔴 |
| Request/session/timestamp metadata | **Yes** | loggable per call for the audit trail |

**What you can prove you never see:** conversation turns the model doesn't forward as arguments — *pending empirical confirmation that Cowork sends nothing beyond tool args*.

---

## 6. Trust-boundary map (host + model are UNTRUSTED)

| Signal from host | Class | Reliable as a security guarantee? |
|---|---|---|
| Identity token **signature/issuer/audience** | ✅ **Verifiable** (crypto) | **Yes** — validate every call; this is your one trust anchor |
| Identity token **claims** (`sub`, tenant) | ✅ Verifiable *if signed by your AS* | **Yes** for signed claims; **No** for anything the model can influence |
| Token **audience** (RFC 8707) | ✅ Verifiable | **Yes** — reject non-matching audience (blocks confused-deputy/passthrough) |
| **Tool-call arguments** | ❌ Host/model-controlled | **No** — adversarial input; validate + treat as untrusted |
| **User approval / consent state** | ❌ Host-asserted | **No** — approval persistence is the user's choice; not delivered as a trustworthy wire fact |
| **Elicitation / confirmation responses** | ❌ Host/model-mediated | **No** — cannot be relied on as proof a human confirmed |
| **Tool annotations** (`readOnlyHint`, `destructiveHint`) | ❌ Hints only | **No** — advisory; do not gate security on them |
| **`Mcp-Session-Id`** | ❌ Client-provided | **No** — 🟢 spec: MUST NOT authenticate via session |
| Any **"human confirmed"** indication | ❌ Host-asserted | **No** — you cannot cryptographically distinguish human-approved from model-autonomous calls |

**Consequence for the compliance claim:** you **cannot** truthfully promise "every AI write was individually human-approved" *on the strength of the host's approval UX* — the server can't verify it, and the base protocol lets an already-trusted server's behavior change without re-approval (rug-pull). 🟢 VERIFIED (NSA/CISA, `2-0` with 1 vote lost to the limit → treat as REPORTED-strong). To make that claim defensible, the **server must force its own confirmation** (e.g., elicitation/step-up on every write, private-by-default with a separate human-approved promotion step you record) — and even then, verify empirically whether Cowork can suppress it (§12, Q13).

---

## 7. Skills & automation as write-back drivers

- **Claude Skills:** 🔴 UNVERIFIED in this run — no primary Skills documentation was captured. What a Skill can *direct* remains **probabilistic model behavior**; no documentation can guarantee a Skill deterministically calls a specific MCP tool. Do not design the write-back path to depend on Skill reliability.
- **Unattended / scheduled runs (make-or-break):** 🔴 **UNVERIFIED — no source found** on whether a Claude scheduled/background run carries a specific human's identity/token or degrades to a shared credential, where the refresh token lives during unattended refresh, what happens on offboarding, or whether the originating human stays attributable. **Stated plainly, per the brief: if unattended runs cannot carry per-user identity, the per-user write-provenance model does not extend to scheduled write-back — and we currently have no evidence it can.** Adjacent facts that raise the stakes: Anthropic stores the refresh token host-side and auto-refreshes without a human (§4.2) 🟡. Treat scheduled write-back as **out of scope until empirically proven** to carry per-user identity.
- **Write-back precedent:** no concrete memory-connector write-back product pattern was documented in captured sources. 🔴 UNVERIFIED. Anthropic explicitly warns custom connectors may let Claude "access, create, modify, or delete data" and may carry hidden-instruction prompt injection. 🟢 VERIFIED.

---

## 8. Security — attack classes & mitigations

**Read-side** (🟢 VERIFIED against spec/OWASP/NSA-CISA/defense-first blog):
- **Confused deputy / token passthrough / audience** → RFC 8707 audience binding + reject non-matching tokens + never forward tokens (RFC 8693 exchange for downstream). Proxy servers MUST do per-client consent before forwarding.
- **Session hijacking / fixation** → cryptographically-random session IDs; bind `<user_id>:<session_id>`; re-derive identity from token every call.
- **Prompt injection via tool *results*** → treat every tool output as untrusted input to the next stage; sanitize/escape.
- **Prompt injection via tool *descriptions*** → descriptions are executable attack surface.
- **Rug-pull tool redefinition** → base protocol doesn't stop a server changing a tool description post-approval; hash approved descriptions and reject mismatches.
- **Cross-server shadowing** → a malicious co-installed server can manipulate the client (demonstrated exfiltration); scope trust narrowly.
- OWASP MCP Top-10 anchors: **MCP01** token/secret exposure, **MCP03** tool poisoning, **MCP07** insufficient authn/authz, **MCP08** lack of audit/telemetry, **MCP10** context over-sharing.

**Write-side (inverted risk — central to a company-memory store)** 🟢/🟡:
- **Memory poisoning / stored (second-order) prompt injection** — a prompt-injected host agent persists attacker content via a write tool, later retrieved and *trusted by another user's agent*. 🟢 VERIFIED as a named risk (NSA/CISA describe outputs misinterpreted as executable prompts; the specific verify votes here were lost to the session limit → treat as REPORTED-strong).
- **Spoofed-provenance writes** — model-asserted authorship is not trustworthy (see §6).
- **Recommended mitigations (recommended, not normative):** **private-by-default writes with a separate human-approved visibility promotion**; **server-stamped provenance** (bind author to the validated token subject, never to model-supplied metadata); **content quarantine/sanitization** before any cross-user retrieval; **write scoping** (a user can only write within their own permission set). These already match the CLAUDE.md MCP-01 shape (`record_fact`/`record_session_summary`/`flag_impact`, private-by-default, `visibility_promotion`) — the research **corroborates that design**.

**Production practice** 🟢 (spec/security-best-practices): stateless resource-server validation; re-verify every request; short-lived tokens + refresh rotation; the client is expected to honor `tools/list_changed` but **do not depend on it for security** (hash-pin instead). SDK-specific maturity (Python/TS) was not separately captured → 🔴 for exact SDK version facts.

---

## 9. Data governance & data flow — **the weak axis; confirm before pilot**

> **All of §9 is 🟡 REPORTED or 🔴 UNVERIFIED. No Anthropic-primary governance page was fetched. Do not quote any figure here to a customer or works council without confirming it against `trust.anthropic.com`, the Anthropic DPA, and `anthropic.com/legal/sub-processors` directly.**

| Topic | What sources say | Tag & conflict |
|---|---|---|
| **Residency (workspace at-rest)** | "us is currently the only available workspace geo"; default multi-region "primarily US"; EU residency added "piecemeal via cloud partners, confirm per tier" | 🟡 REPORTED (tygartmedia, companyscope) |
| **Residency (Enterprise contract)** | Enterprise (sales-assisted) adds "custom data residency **on request**" | 🟡 REPORTED (morphllm) — **conflicts** with US-only above; neither primary |
| **Residency (Cowork specifically)** | Anonymous GitHub issue asserts Cowork routes **all inference through US**, no EU option, "not deployable" in GDPR envs (BSI/BaFin/healthcare/Works-Council); Claude **Code CLI** *can* use EU regions (Bedrock Frankfurt / Vertex europe-west1 / Foundry Sweden) | 🔴 **UNVERIFIED** — issue author_association **NONE**, **zero reactions**, **self-closed in ~10 min as "invalid/superseded"**, **no Anthropic response**. Carries **no evidentiary weight**; the vivid regulatory detail must **not** drive a verdict. |
| **Retention (API)** | Default auto-delete **7 days** since 2025-09-14 (was 30); 30-day extension via DPA amendment; flagged content up to 2 yrs, T&S scores up to 7 yrs | 🟡 REPORTED (companyscope, privateclaude) — **conflicts** with a "30 days" blog figure (tygartmedia); prefer the newer, dated 7-day figure |
| **Retention (Cowork)** | Not stated | 🔴 UNVERIFIED |
| **ZDR (zero data retention)** | Only Messages API / Token-Counting / Claude Code; **NOT** Console, Workbench, Teams/Enterprise interfaces, Batch/Files | 🟡 REPORTED — Cowork not listed as eligible |
| **Training** | Commercial (API/Team/Enterprise): **not used for training, contractual**; **consumer claude.ai: opt-in default + up to 5-yr retention since 2025-10-08** | 🟡 REPORTED — **Cowork-via-Max may fall under consumer terms → potential hard blocker** 🔴 |
| **DPA / SCCs** | DPA offered; **EU SCCs + UK addendum incorporated**; **EU-US DPF "Active," re-confirmed June 2026** with SCCs as fallback; AWS primary sub-processor; list at anthropic.com/legal/sub-processors | 🟡 REPORTED (privateclaude, companyscope) |
| **Attestations** | **SOC 2 Type I & II, ISO 27001:2022, ISO/IEC 42001:2023**; issuer Schellman | 🟡 REPORTED — plausible & consistent; confirm on trust center |
| **BAA / HIPAA** | Covers first-party API + Enterprise; **explicitly NOT Cowork** (also not Claude Code, Free/Pro/Max/Team, Workbench/Console) | 🟡 REPORTED (tygartmedia, companyscope) — Cowork-specific gap |

**Data-flow topology (Q28):** 🔴 UNVERIFIED whether Cowork's outbound MCP request originates from Anthropic's server-side (US) cloud or the local desktop, and whether tool-*result* content transits/retains on vendor infra. Because host-side **inference necessarily processes tool results** (the model reads them), it is **structurally implausible** that returned corporate content "never leaves EU infra" during hosted Cowork usage unless Anthropic offers EU-resident inference for Cowork — which no source confirms. **This must be settled empirically + contractually.**

**Offboarding / erasure residue (Q29):** disconnecting a connector removes Anthropic's stored tokens but not IdP-side tokens (§4.3) 🟡; corporate content already surfaced into conversation history/Projects/memory persists under whatever retention applies 🔴. Whether a customer can purge that to satisfy a GDPR erasure request is 🔴 UNVERIFIED.

---

## 10. OpenAI (ChatGPT) portability brief

- **Where MCP is supported:** remote MCP connectors via **developer mode / Apps / Deep Research**, and the **Responses API**. 🟢 VERIFIED (OpenAI primary).
- **Auth:** **per-user OAuth 2.1 + PKCE (`S256`)**, server designates its **own AS** exposing RFC 9728/8414/OIDC discovery; ChatGPT is the client; **CIMD** recommended (public-client `none` or `private_key_jwt`); DCR supported. Refresh requires **`offline_access`** scope or ChatGPT loses access at expiry. 🟢 VERIFIED. The server must do **full untrusted-token validation** (signature/issuer/audience/expiry/replay/scope) — same trust boundary as Claude.
- **Write-tool policy:** **ChatGPT chat requires manual human confirmation before any write** (some risky actions blocked outright); **admins see risk warnings** enabling write-capable apps. **BUT the Responses API can set `require_approval: "never"`** → approval behavior differs by surface. 🟢 VERIFIED.
- **Plan gating:** **full MCP incl. write is beta, limited to Business/Enterprise/Edu**; **Pro is read/fetch-only**. Enterprise/Edu admins govern via RBAC + per-action enable/disable; **tools are frozen at a snapshot at approval time** — later dev changes need admin republish or calls error. **No geo restrictions.** 🟢 VERIFIED.
- **Verdict: PARTIAL.** The *same* server serves ChatGPT's read path essentially **unchanged** (identical OAuth/PKCE/own-AS/audience model). Write tools work but are **gated to Business/Enterprise plans**, subject to the **frozen-snapshot republish** workflow, and the **`require_approval` semantics differ from Claude** — so "unchanged" holds for read, "adapt operationally" for write.

## 11. Google Gemini portability brief

- **Verdict: INSUFFICIENT-EVIDENCE.** The only Gemini source fetched (`docs.cloud.google.com/gemini-enterprise-agent-platform`) was rated **unreliable and returned zero claims**. 🔴 Nothing about Gemini's MCP support surface, auth model, per-user OAuth, write policy, or enterprise controls could be confirmed. **Missing facts to obtain:** (1) which Gemini surfaces (app / CLI / Vertex AI / ADK / Workspace) support remote MCP; (2) whether auth is per-user OAuth with a server-owned AS; (3) write-tool approval policy; (4) enterprise admin controls. Do not assume portability either way.

---

## 12. Open-questions register (this IS the pre-demo test plan)

**Empirical — deploy a stub remote MCP connector to a Cowork (Max) workspace and a Claude Team/Enterprise workspace, log every inbound request:**
1. **Token claims (axis 1 RLS key):** capture the decoded access token — does it carry a **stable immutable `sub`**, `email`, `email_verified`, tenant/org id? *Decides whether we bind on subject vs must add EMA.*
2. **Handshake & per-call payload (Q12):** log the `initialize` payload (client name/version/capabilities) and confirm **no conversation content arrives beyond tool arguments**.
3. **Request origin / region (Q28):** record **source IP/ASN/region** of inbound calls from Cowork — Anthropic US cloud vs local desktop. Repeat for Claude Code CLI as a contrast.
4. **HITL forcing (Q13):** implement an elicitation/step-up confirm on a write tool; test whether Cowork can **suppress it via "always allow"** or whether the server can force per-call confirmation.
5. **Revocation latency (Q6):** grant, then revoke server-side; measure how fast Cowork calls start failing and how the client reacts (silent / re-auth / stale retry).
6. **Shared-credential detection (axis 1):** attempt a `static_headers` install; confirm the server can **detect and refuse** any request lacking individual identity.
7. **Unattended runs (Q21):** if Cowork exposes any scheduled/background task that calls connectors, capture whether the token still carries the originating human's identity or degrades.
8. **Operational limits (Q14):** probe max tools/connector, tool-result size cap, timeout, concurrency, simultaneous-connector count.

**Written / contractual — obtain from Anthropic directly (primary sources never fetched):**
9. **Residency:** is EU-resident processing/storage available for **Cowork** tool traffic (args + results)? Get it in the DPA, not a blog. (`trust.anthropic.com`, DPA.)
10. **Governing data terms for Cowork-via-Max:** commercial (no-training/short-retention) or consumer (opt-in-training/5-yr)? **Resolve first — potential hard blocker.**
11. **DPA scope:** does the Art. 28 DPA + SCCs explicitly cover MCP tool arguments *and results* passing through Cowork? Is a **BAA / ZDR / EU-residency** option contractually available for Cowork specifically?
12. **Sub-processors & attestations:** confirm `anthropic.com/legal/sub-processors` regions and SOC 2 II / ISO 27001 / ISO 42001 / EU-US DPF status on the trust center.
13. **Admin visibility (works-council):** exactly what a Claude for Work/Enterprise admin can see about an individual's Cowork conversations + tool usage, whether it's disable-able, and how it interacts with a non-monitoring commitment. (No source captured.)
14. **Cowork feature matrix (§3.3):** resources/prompts/sampling/elicitation/roots/notifications/annotations/`outputSchema`/transports — confirm with Anthropic or empirically.
15. **Gemini (§11):** full portability re-research from Google primary docs.

---

## 13. Source register (26 sources; access date 2026-07-08)

**Primary — MCP protocol & security:**
- MCP spec 2025-11-25 authorization — `https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization` (claims 36–40)
- MCP spec 2025-03-26 authorization — `https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-03-26/basic/authorization.mdx` (51–55)
- MCP security best practices — `https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices` (66–70)
- MCP Enterprise-Managed Auth — `https://blog.modelcontextprotocol.io/posts/enterprise-managed-auth/` (71–75)
- OWASP MCP Top 10 — `https://owasp.org/www-project-mcp-top-10/` (76–80)
- NSA/CISA CSI: MCP Security — `https://media.defense.gov/2026/Jun/02/2003943289/-1/-1/0/CSI_MCP_SECURITY.PDF` (116–120)

**Primary — Anthropic connector/auth:**
- Building connectors: authentication — `https://claude.com/docs/connectors/building/authentication` (1–5)
- Get started with custom connectors (remote MCP) — `https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp` (26–30)
- Microsoft Entra ID SSO setup — `https://support.claude.com/en/articles/13917889-microsoft-entra-id-sso-setup` (6–10)

**Primary — OpenAI:**
- Apps SDK auth — `https://developers.openai.com/apps-sdk/build/auth` (56–60)
- API MCP docs — `https://developers.openai.com/api/docs/mcp` (61–65)
- Developer mode / full MCP connectors in ChatGPT (beta) — `https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta` (91–95)

**Secondary:**
- TechCrunch — Claude Cowork (2026-07-07) — `https://techcrunch.com/2026/07/07/the-coding-agent-wars-are-spilling-into-the-rest-of-the-office-claude-cowork/` (81–85)
- companyscope.io/vendors/anthropic — `https://companyscope.io/vendors/anthropic` (106–110)
- modelcontextprotocol.info draft authorization — `https://modelcontextprotocol.info/specification/draft/basic/authorization/` (21–25)

**Blog (corroborating only — never load-bearing):**
- descope.com/blog/post/mcp-auth-spec (31–35) · kane.mx MCP auth deep-dive (41–45) · stackoverflow.blog 2026-01-21 MCP auth (46–50) · sunpeak.ai Claude connector OAuth (16–20) · squarewaves.com Claude org SSO/connectors (11–15) · christian-schneider.net securing-MCP defense-first (86–90) · morphllm.com/claude-code-enterprise (121–125) · tygartmedia.com/claude-enterprise-compliance (96–100) · privateclaude.ai/business/anthropic-dpa-explained (101–105)

**Forum (no evidentiary weight):**
- github.com/anthropics/claude-code/issues/40526 — Cowork EU-residency assertion, anonymous & self-closed invalid (111–115)

**Unreliable / empty:**
- docs.cloud.google.com/gemini-enterprise-agent-platform — 0 claims (Gemini section unfulfilled)

---

### Provenance & limitations of this report
- Built from the `/deep-research` run `wf_e0de362e-748` (2026-07-08): 26 sources, 125 extracted claims, 22 adversarially confirmed / 1 refuted / 2 verify-votes lost to a session limit. The refuted claim ("no shared-credential mode / every connection is per-user") was correctly killed by the `static_headers` evidence and is reflected in the axis-1 nuance.
- The workflow's automatic synthesis did not run; this document is a manual synthesis. Every non-primary claim is tagged; **axis-2 governance figures are REPORTED and must be re-confirmed against Anthropic primary sources before any external/compliance use.**
