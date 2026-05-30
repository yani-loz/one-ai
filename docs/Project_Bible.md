# One AI — Project Bible

> **Purpose.** This is the single source of context for building One AI. Read it once and you understand *what* One AI is, *why* it exists, and *how* it is architected. It is the complete information needed to build the product — vision, product concepts, architecture, principles, and scope.
>
> **Tagline:** *One Company. One AI.*
> **Company:** Ethera Technologies · **Founder:** Yani Lozanov
> **Status:** living document · **Last revision:** 2026-05-30

---

## 0. How to use this document

**If you are an AI coding agent:** §1–§3 give the mental model. §5–§14 are the architecture you implement. §11 (principles) constrains *every* implementation choice — when a low-level decision is ambiguous, default to the relevant principle. §15 is the technical architecture and stack. §18 is the current build scope.

**Living Document Principle.** Nothing here is sacred. Vision, architecture, business model, and positioning evolve as the idea sharpens. If new evidence or reasoning suggests a better direction, challenge it and propose the change. Past conclusions are starting points, not constraints.

---

## 1. The one-paragraph pitch

One AI is an **enterprise AI product** (a subscription product, **not** a consulting project) that places AI at the **center of an organization as its central nervous system**. It holds the company's complete knowledge, learns from every interaction, and works alongside every employee — so people shift from *executing tasks* to *directing intelligence*. One AI arrives ready to work, learns the organization from day one, and **compounds in value daily** — the longer it runs, the smarter and more indispensable it becomes.

---

## 2. The core idea — the living organism

The central product metaphor: **companies are living, intelligent organisms.**

- Companies today are **broken organisms** — scattered memory, disconnected departments, no collective learning. Context is lost every time an employee closes a chat session; knowledge lives in individual heads and leaves when people do.
- One AI gives the company a **functioning brain and nervous system** — a unified intelligence that remembers everything, connects every department, and learns continuously.
- **Intelligence compounds like interest.** Every interaction makes the system smarter. The gap between a company that builds its own intelligence and one that doesn't widens every day.

**Why this matters for engineering:** the metaphor dictates architecture. "Living organism" means persistent shared memory (not stateless chat), continuous learning loops (not static models), proactive behavior (not request-response only), and personal adaptation (the system grows *with* each person). If a design choice makes the product feel more like a static tool and less like a living system, it is probably the wrong choice.

---

## 3. Vision — the three-stage journey (+ Stage X)

The product meets companies where they are and guides them along a journey. The vision *includes* the journey.

- **Stage 1 — Unified Memory** — Company knowledge unified into a single secure AI foundation. Nothing forgotten, no context lost.
- **Stage 2 — Intelligent Agents** — Specialized AI *Nexus Agents* handle operational heavy-lifting per function; employees supervise and direct.
- **Stage 3 — The Living Organization** — AI becomes the operational core; the company operates at multiples of its size with compounding intelligence.
- **Stage X — The Adaptive Organism** (long-term) — The AI gains *neural plasticity*: it reorganizes its own connections, proposes new modules, restructures knowledge schemas, and identifies organizational blind spots. It evolves its own structure — always through Human-in-the-Loop approval. The most literal expression of the metaphor.

> Inter-company AI communication (organisms talking to each other) is a long-term vision — **not** part of initial positioning.

---

## 4. Who it's for

**Target market:** mid-market European companies (focus: **DACH industrial mid-market, 100–500 employees**) that already have *some* AI experience — employees using ChatGPT — but have hit the ceiling:

- Context is lost every session; there is no institutional memory.
- Employees aren't skilled at prompt engineering.
- Knowledge is siloed; nothing the company learns is retained collectively.

**Buying committee (DACH Mittelstand):**
- **Champion** — CTO / Head of IT (technical evaluator, internal advocate).
- **Decision Maker** — CEO / Geschäftsführer (signs the deal).
- **Gatekeepers** — DPO / Legal / Works Council (Betriebsrat) / CFO (can veto on privacy, compliance, or cost).

**Why this shapes the product:** DACH mid-market buyers are acutely sensitive to **data sovereignty, GDPR, and works-council politics**. This is why security and data ownership are *foundational*, not features (§13). A product that can't credibly answer "where does our data live and who can see it?" does not pass the gatekeepers.

---

## 5. The product — core concepts

### 5.1 Personal AI Model (base tier)
Every employee gets **their own personal AI assistant** — not a shared bot. It connects to shared organizational memory but **adapts to the individual** over time: communication style, working patterns, priorities, preferences. Personal adaptation is included in the base tier — the AI grows *with* the user (§11.4).

### 5.2 Nexus Modules (add-on tier)
Pre-built **capability packs** (tools + permissions + domain knowledge + proactive behaviors) that enhance the personal AI for a specific function (HR, Sales, Operations, Finance…). An employee can have multiple modules. Without a module, the AI is a general assistant with org-memory access + personal adaptation. Modules are customizable.

### 5.3 Overnight Autonomy — "Night Shift"
Agents work overnight on user-assigned tasks (research, analysis) and AI-initiated exploration (finding relevant patterns, preparing meeting context). **Research and analysis only — never external actions.** Results are presented as a morning report.

### 5.4 Connectors
Modules that plug into company data sources (email, documents, chat, CRM, HR, project management…) with authentication, initial sync, continuous sync, and schema mapping.
- **Priority set:** Email, Documents, Slack; meeting transcription (Fathom).
- **Engineering discipline:** build connectors **one at a time**, fully verify each before the next, keep each **self-contained** in its own folder so it can be plugged in/out without affecting others (`BaseConnector` ABC).

---

## 6. Architecture — the Memory Foundation (4 layers)

The heart of the system. Memory is a **database** (queryable, dynamic, access-controlled) — **not** files.

### Layer 1 — Organizational Memory (shared) — 4 types
1. **Structured Memory** — relational DB (PostgreSQL), queryable with SQL. For numbers, metrics, quantifiable questions.
2. **Semantic Memory** — vector embeddings (pgvector) **+ keyword index (BM25)**, merged with Reciprocal Rank Fusion. For natural-language search over documents, emails, policies.
3. **Relationship Graph** — graph mapping connections between people, companies, projects, documents, events. For relationship questions, dependency/risk analysis.
4. **Explorable Memory** — raw data accessible via a code-execution environment. The agent writes scripts to explore / filter / cross-reference for complex multi-hop reasoning.

### Layer 2 — Agent Identity (per-agent)
Role definition, permissions, tools, domain knowledge, behavioral norms. Includes the **SOUL** — the self-evolving agent personality.

### Layer 3 — Interaction Memory (per-agent, per-user)
Four-part context: **SOUL + USER + PROFESSIONAL + PAST CONVERSATIONS (RAG)**. Two-Speed Adaptation: immediate signal capture during conversation + nightly self-reflection. Included in the base tier for all employees. This is the per-user memory that makes the AI personal.

### Layer 4 — Learning Loop / "The Nightly Board" (system-wide)
The compounding-intelligence engine. Personal agents curate knowledge reports → the **Knowledge Nexus** deliberates → organizational memory updates. Two layers of *deliberative* curation, not mechanical classification. Includes an **"Announce"** action for urgent knowledge sharing.

> Layers 3 and 4 — interaction memory and the learning loop — are the **core differentiator**. They are what turns One AI from a cross-source search tool into the living organism the vision describes. Build them well; they are the most valuable part of the system.

---

## 7. Privacy model — three tiers + five guarantees

Privacy is architectural, because the DACH buyer demands it.

**Three tiers of data:**
- **Tier 1 — Personal Intelligence:** 100% private (conversations, profiles, corrections). A hard boundary — **not** accessible to admins.
- **Tier 2 — Distilled Organizational Knowledge:** facts/processes extracted from interactions, **stripped of personal context**, curated by agents + Knowledge Nexus.
- **Tier 3 — Aggregate Patterns:** statistical, anonymous patterns across many users. No individual attribution.

**Five Trust Guarantees:** (1) default private, (2) visible extraction (the user sees what is promoted to org knowledge), (3) vault mode, (4) admins see *knowledge*, not *conversations*, (5) optional attribution.

---

## 8. Knowledge Access Architecture — three levels

How the agent gets the right knowledge into context without exhausting the budget:

- **Level 1 — DNA Core (~2K tokens):** compressed company identity, **always** in agent context. Updated rarely, approved by humans.
- **Level 2 — Domain Summaries (~5–15K tokens each):** per-domain compressed knowledge (Clients, Team, Services, Projects), loaded per-task. Auto-generated by the Knowledge Nexus.
- **Level 3 — Full Detail:** retrieved on demand via RAG from the database layers. Never loaded wholesale.

**Key principle:** organizational memory is a *database* (queryable, dynamic, access-controlled), not a pile of files.

---

## 9. Platform + operational modules architecture

One AI is a **platform**, not a monolith.

- A shared **Core** holds persons, organizations, memory, chat, and connectors.
- **Operational modules** (CRM first; Project, HR, Finance later) run *on top of* the Core. They extend it via **FK-references to shared Core entities** — **zero raw-data duplication**.
- Modules **never embed AI in their own code.** They **expose MCP tools** to the AI layer and consume intelligence through it.
- **Two UX personas from one codebase:** module-first (a user who lives in the CRM) and platform-first (a user who lives in the chat/assistant), driven by tenant config.
- **Verticalizations** (e.g. veterinary clinics, legal firms, EU-funding consultancies) are **config overlays**, *not* code forks.
- **Commercial tiers** (CRM Starter → CRM+Email → CRM+Communications → Full Platform → Sovereign) are **feature-flag combinations** over the same infrastructure.

**Three-tier engagement discipline** (to stay a product company, not a consultancy):
- **Tier 1** — core platform (shared by all customers; never forked).
- **Tier 2** — vertical config (per industry).
- **Tier 3** — client-specific extensions (isolated; never leak into Tier 1).

---

## 10. Multi-agent collaboration

Agents can collaborate on cross-domain questions. **Advisory Council pattern:** multiple agents analyze in parallel, debate, and synthesize a unified recommendation. On-demand, scheduled, or event-triggered.

---

## 11. Core principles — these constrain *every* implementation choice

### 11.1 Human-in-the-Loop (HITL)
Agents **seek approval before taking real-world actions** (sending email, modifying records, creating commitments). The **trust dial is configurable** per agent / action / user. When in doubt, an agent asks rather than acts. (UI: the `clari-pulse` "needs your approval" glow exists for exactly this.)

### 11.2 Self-Evolution
Agents **improve their own behavior** from user corrections, rejections, and feedback — not just learning *facts*, but updating extraction criteria, communication style, and recommendations. The SOUL evolves.

### 11.3 Proactive Intelligence
Agents don't only respond to questions. They deliver **scheduled briefings, event-triggered alerts, and pattern-driven insights** to the right person at the right time.

### 11.4 Emotional Bonding & Retention (ethical)
The personal AI becomes *irreplaceable* through genuine personalization and accrued shared context. This is a deliberate retention strategy with an explicit **ethical line** — bonding through real utility and memory, never through manipulation or dark patterns. Anti-sycophancy is a feature: the AI is a collaborator that can disagree, not a flatterer.

---

## 12. Model architecture — multi-model, provider-agnostic

- **Real-time chat:** a capable mid-tier model (Claude Sonnet-class). Lightweight models are insufficient for cross-source reasoning over enterprise data.
- **Nightly Board + Knowledge Nexus:** a top-tier model (Claude Opus-class) — best judgment, privacy-critical.
- **Embeddings:** a cheap bulk provider (e.g. OpenAI `text-embedding-3-small`).
- **Provider-agnostic abstraction:** no component depends on a specific provider. The same agent loop, tools, and prompts work across **Anthropic, OpenAI, and Google**. Switching providers is a config change, behind an `AIProvider` abstraction.

---

## 13. Security & compliance — foundational, 4 layers

Security is **foundational, not a feature.** The company owns its intelligence completely. Keep an eye on security and data on every part of the system.

1. **Data Security** — AES-256 at rest, TLS 1.3 in transit, **EU data residency**, logical data isolation per company, encrypted backups, full data portability. Dedicated single-tenant infrastructure available as a premium (Sovereign) option.
2. **Access Security** — company-level isolation, **RBAC**, module-level permissions, SSO/MFA, session management. Company admins control all roles/permissions.
3. **AI Security** — input validation (prompt-injection defense, data-poisoning detection, sensitive-data classification) and output control (unauthorized-data-exposure prevention, hallucination guards, external-LLM data-leakage protection, compliance enforcement). Both deterministic *and* AI-based checks.
4. **Compliance & Audit** — **GDPR** (access/deletion/portability, consent, DPO support), **EU AI Act** (transparency, human oversight, risk classification), **immutable audit trail** (who/what/when/where/why for every action), admin dashboard.

---

## 14. Cost tracking & platform operations

Cost-awareness is built in from day one.
- Every LLM call, embedding, and API request is tracked in a unified **`cost_events`** table (provider, model, tokens, cost USD, latency, agent type, user, interaction_id).
- Nightly aggregation into daily / monthly / per-user rollups.
- **Platform Operations Panel** (Ethera team only, separate auth + MFA) — full cost data, system health, margin tracking. Sees **metrics only, never content.**
- **Customer Admin Panel** — usage stats and learning trends only. **No costs, no tokens, no provider names.**

---

## 15. Technical architecture & stack

**Shape of the system:**
- **Two-layer storage.** Layer 1 = source-specific relational tables that preserve each source's natural metadata. Layer 2 = a unified semantic memory of text chunks with **dual indexing**: vector embeddings (HNSW) **and** a keyword index (`tsvector`/GIN).
- **Hybrid retrieval.** Search combines keyword ranking (BM25) and vector similarity, merged via **Reciprocal Rank Fusion** — so exact terms (names, abbreviations, Bulgarian words) and semantic matches both surface. Keyword config is language-agnostic (works for English and Bulgarian).
- **Cross-source entity resolution.** Persons and organizations are merged across all sources **deterministically** (no LLM) via a unique email key, so the same human appearing as an email sender, a meeting participant, and a chat user is one entity.
- **Agentic retrieval.** The AI chooses purpose-built tools via function-calling; an **`AgentLoop`** orchestrates think → act → observe cycles with an iteration cap. Prefer many fine-grained, specific tools over a few generic ones.
- **Provider-agnostic AI layer** (§12) behind an `AIProvider` abstraction.
- **Cost tracking** from the first commit (§14).

**Stack:**
- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2.0 (async). Monorepo with a shared common package. Connectors implement a `BaseConnector` ABC and are independently removable.
- **Database:** PostgreSQL 16 + pgvector. Single database. Keep infrastructure simple until multi-tenant scale genuinely requires more (in-process async is sufficient at single-tenant scale).
- **Frontend:** React 19 + Vite + TailwindCSS v4 + Framer Motion. Design language per `.claude/rules/frontend-design.md` (glassmorphism, aurora palette, living-organism motion).
- **Multi-tenant production requirements** (add when going multi-tenant): `org_id` on every table + Row-Level Security, auth middleware (JWT + RBAC + SSO/MFA), schema migrations, containerization, secret manager, monitoring.
- **Library note:** for PDF extraction prefer **pdfplumber** (MIT) over PyMuPDF (AGPL).

---

## 16. Business model (condensed — context only)

Explains *why* certain features exist.
- **Per-employee base subscription** (~€39/emp/month annual) + **Nexus Module add-ons** (~€19/module-user/month).
- **Sovereign tier** (~€55–59/emp) for full EU-jurisdictional hosting. **Setup fees** €5–10K.
- **Founding Partner Program:** a small set of companies at a discount in exchange for case study / reference.
- **Infrastructure strategy:** API-first → hybrid self-hosting → full sovereign EU cloud.

---

## 17. The Pulse — Impact Event Tracking

The ROI-proof system that makes the product unchurnable. Captures moments where AI intelligence influenced a business decision with a measurable outcome. **Five categories:** Revenue Uplift, Risk Avoidance, Cost Prevention, Knowledge Discovery, Time Recovery. **Three-layer capture:** manual flag button + Nightly Board auto-detection + Knowledge Nexus validation. A monthly management report quantifies AI's business impact in euros.

---

## 18. Build scope — Connect → Ask → Learn

The product is built in three capability layers:

1. **Connect** — connectors ingest company data (Email, Documents, Slack; meeting transcription) into the shared database, with cross-source entity resolution. One connector at a time, fully verified.
2. **Ask** — cross-source agentic retrieval over the unified memory: hybrid search + fine-grained tools, answering questions with source attribution, at the capable mid-tier model.
3. **Learn** — the compounding-intelligence loop: Interaction Memory (Layer 3) + the Nightly Board (Layer 4) + personal adaptation. This is the differentiator (§6) — design it before building it.

Security, privacy, and cost tracking are built in from the start across all three layers, not bolted on later.

---

## 19. Glossary

| Term | Meaning |
|------|---------|
| **Personal AI** | Each employee's own adapting assistant (base tier). |
| **Nexus Module** | A capability pack (tools + permissions + domain knowledge + proactive behaviors) for a function. |
| **Night Shift** | Overnight autonomous research/analysis (never external actions). |
| **Connector** | Self-contained module that ingests a data source into the shared DB. |
| **SOUL** | The self-evolving personality/identity of an agent (Layer 2). |
| **Nightly Board / Knowledge Nexus** | The deliberative nightly learning loop that promotes personal knowledge into org memory (Layer 4). |
| **DNA Core** | ~2K-token always-in-context compressed company identity. |
| **Domain Summary** | Per-domain compressed knowledge (~5–15K tokens), loaded per-task. |
| **The Pulse** | Impact Event Tracking — the ROI-proof system (5 categories). |
| **Hybrid Search / RRF** | BM25 keyword + vector similarity merged via Reciprocal Rank Fusion. |
| **AgentLoop** | Provider-agnostic think → act → observe tool-calling state machine. |
| **HITL** | Human-in-the-Loop — approval before real-world actions. |
| **Core vs Module** | Core = shared persons/orgs/memory/chat/connectors; Modules = CRM/HR/etc. on top, via FK refs, no embedded AI. |
| **Tier 1/2/3** | Core platform / vertical config / client extension. |
| **Sovereign tier** | Full EU-jurisdictional single-tenant hosting. |

---

## 20. Where to dig deeper (source documents)

Authoritative strategy/research lives in the **business folder** (`Desktop\Projects\Business\One AI\`):

- **Vision:** `01_Strategy/One AI Vision_v2.0.docx`
- **Product:** `03_Product/One AI Product Concept_v2.2.docx`, `One AI Agent Architecture_v1.0.md`, `One AI MVP Scope_v1.0.md`, `One AI Connector Catalog.md`, `Modules/CRM/CRM_Module_Spec_v0.1.md`
- **Business:** `04_Business/One AI Business Model & Pricing_v1.0.docx`
- **GTM:** `05_GTM/gtm_strategy_temp.md`
- **Competitive:** `02_Research/Competitive Analysis/One AI Competitive Analysis_v1.0.docx`
- **Retention:** `02_Research/Emotional Bonding & Retention/One AI Emotional Bonding Strategy_v1.0.md`
- **Memory model basis:** `Resources/memory-types.md`
