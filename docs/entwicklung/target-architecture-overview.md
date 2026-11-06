# GuideOS Target Architecture Overview

## Purpose

This document describes the target architecture for evolving GuideOS from its
current local Streamlit application into a reusable, domain-agnostic AI
assessment platform.

It is the shared baseline for requirements engineering, system design, backlog
refinement, and implementation planning. It distinguishes between:

- the current repository state,
- the next architectural increment,
- and the longer-term product architecture.

Concrete implementation work should be derived from this document as focused
issues, epics, or technical design documents.

## Executive Summary

GuideOS currently provides local Streamlit modes:

- a multi-chat interface backed by local JSON persistence and Ollama,
- guided assessments backed by active file-based domain packs and local
  assessment persistence,
- and an optional local Domain Admin mode gated by `GUIDEOS_ADMIN_ENABLED=1`.

It also contains a first local FastAPI adapter that exposes the same
assessment core through a small development API. This adapter proves the
channel-neutral contract, but it is not yet a production REST API.

For a protected internal pilot, GuideOS additionally provides a Docker Compose
deployment with PostgreSQL-backed assessment sessions, a shared in-app test
password, and participant sessions keyed by a self-chosen user name (since
2026-10-22; before, anonymous per-browser keys). It is limited to
synthetic test data and the Streamlit assessment surface.

The target system builds on these foundations to guide users through structured
assessments, normalize and validate their answers, apply deterministic rules,
and generate explainable recommendations and reports.

Core principles:

```text
Domain-specific behavior lives in versioned domain packs.
Channel-specific behavior lives in adapters.
LLM-provider-specific behavior lives behind the provider interface.
The assessment core owns workflow decisions and remains domain-agnostic.
```

GuideOS should evolve incrementally. The existing Streamlit application remains
usable while assessment behavior is extracted into testable core modules.
A versioned production REST API, a normalized production data model,
OpenWebUI integration, and a custom frontend are target capabilities, not part
of the current baseline. PostgreSQL is part of the baseline as JSONB pilot
persistence only.

## Product Vision

GuideOS should support configurable guided assessments for domains such as:

- Energy optimization
- Insurance needs analysis
- Recruiting or candidate screening
- Funding eligibility checks
- Compliance assessments
- SaaS onboarding
- Real estate or renovation assessments

For each domain, the platform should be able to:

1. Load a validated and versioned domain pack.
2. Create an assessment session.
3. Guide the user through required and optional questions.
4. Accept structured input and natural-language answers.
5. Normalize natural-language answers through an LLM where necessary.
6. Validate normalized values deterministically.
7. Ask clarifying follow-up questions when an answer is incomplete or uncertain.
8. Persist raw answers, normalized values, session state, and events.
9. Apply deterministic rules.
10. Produce prioritized, explainable recommendation candidates.
11. Use an LLM only to formulate, enrich, or answer questions about user-facing
    explanations.
12. Return a structured result and render a report.

The bundled demo pack (`verein-demo`) is a technical demo. It proves the
assessment boundaries, but it must not be treated as production-ready advice.


## Current Repository Baseline

The module layout, commands, and data flow of the current implementation are
maintained in `CLAUDE.md`; the completed phases are listed under
[Recommended Migration Strategy](#recommended-migration-strategy). In summary,
the repository contains:

- Python 3.12 or newer, a Streamlit application in `app.py`, and a reusable
  `guideos` package with the modes `Chat`, `Assessment`, and optional
  `Domain-Admin`
- A provider protocol for streaming chat and structured LLM responses, with
  Ollama as the only implementation; structured calls run at temperature 0
- Versioned, validated domain packs (bundled demo `verein-demo`)
  and a domain catalog with
  file-backed and PostgreSQL stores plus a gated local Streamlit Domain Admin
  with form editors for questions, rules, topics and recommendations
- Channel-neutral assessment sessions, deterministic validation, rules,
  recommendations, and Markdown reports; contract models in
  `guideos.assessment.contract`
- A local FastAPI adapter with a minimal session-oriented `/api/v1` surface;
  its `/chat` endpoint serves `question_help` only, advice after completion
  runs through conversation resources
- Local JSON persistence by default; PostgreSQL JSONB repositories, migrations,
  and optimistic revision checks for the internal pilot
- A pilot-only Streamlit mode with a shared test password, participant IDs
  taken from the user name entered at login, completion feedback, and operator
  commands in `guideos.pilot`
- An explicit `AdvisoryContext`, persistent advisory conversations, and LLM
  recommendations with `source="llm"` next to deterministic `source="rule"`
  recommendations; a completed assessment opens its result conversation
  automatically
- A per-domain knowledge base (`guideos.knowledge`) of documents and curated
  web pages; passages are retrieved deterministically before advisory LLM
  calls, and every answer section and LLM recommendation names its sources
- A stored profile per participant and domain (`guideos.profiles`) with
  confirmed fact changes and feedback on recommendations through the chat
- MCP tool servers registered per domain (`guideos.tools`), a guarded tool
  loop in the advisory, file questions whose uploads a tool server imports,
  a deterministic first analysis with derived answers and metric rules
- Process logging (`GUIDEOS_LOG_LEVEL`), ruff, pytest, GitHub Actions, and the
  REST test-runner suite in `testsuite/`

The current application does not yet contain:

- A versioned production REST API
- Individual authentication, users, or tenants; the internal pilot has only a
  shared test password and an unverified user name as participant key (in
  Streamlit and, since 2026-10-22, as a cookie login of the REST API)
- OpenWebUI integration
- An OpenAI-compatible provider implementation
- Approval workflows for domain packs; drafts and active versions are stored
  in PostgreSQL in the pilot, without review or approval steps
- Sections and prompt templates in domain packs, assumptions on
  recommendations, the rule inputs recorded with a result, and HTML reports,
  all of which the target descriptions below mention
- A production-ready domain authoring workflow with authentication, approval,
  review history, or role-based access
- Cross-field validation beyond existing rule evaluation and ratio-based rule
  checks
- Production observability, audit, export, retention, or consent workflows
- Production-grade idempotency, distributed concurrency control, rate limiting,
  or public API lifecycle governance

The current data model is partly chat-oriented and partly assessment-oriented.
Chats and assessment sessions are related but distinct concepts. A chat is a
conversation transcript. An assessment session is a structured workflow with a
domain version, content hash, answers, validation state, events, results, and a
report. The architecture must not silently reinterpret existing chats as
completed assessments.

## Architecture Evolution

### Stage 1: Current Chat Application

```text
User
  ↓
Streamlit UI (`app.py`)
  ├── Chat state (`guideos.chat`)
  ├── Local JSON storage (`guideos.storage`)
  └── Provider registry (`guideos.providers`)
          ↓
       Ollama
```

Streamlit coordinates UI state, persistence, and calls to the selected LLM
provider for the general chat mode.

### Stage 2: Current Assessment Core With Streamlit Adapter

```text
User
  ↓
Streamlit Assessment Adapter (`guideos.adapters.streamlit`)
  ↓
Assessment Orchestrator
  ├── Domain Pack Loader
  ├── Questionnaire Engine
  ├── Answer Interpreter
  ├── Validation Engine
  ├── Rules Engine
  ├── Report Renderer
  └── Assessment Chat Assistant
          ↓
Local Assessment Repository + LLM Provider Registry
```

This stage is implemented as a local technical demo. Streamlit is the first
product and development channel. It translates UI events into channel-neutral
orchestrator calls and renders the returned structured response.

### Stage 3: Multiple Adapters and Persistent Backend

```text
Streamlit Adapter ──────┐
REST API Adapter ───────┼──→ Assessment Core
OpenWebUI Adapter ──────┤          ├──→ PostgreSQL
Future CLI Adapter ─────┘          └──→ LLM Providers
```

The same assessment core should serve Streamlit, a future API, and optional
chat-channel integrations. A first local FastAPI adapter already exercises this
direction, but it has no authentication and does not expose the final versioned
API shape. It uses the repository selected by the environment: local JSON by
default, PostgreSQL JSONB when `GUIDEOS_DATABASE_URL` is set, as in the internal
pilot. Local JSON storage remains the default development mode.

### Stage 4: Product Frontend

```text
User
  ↓
Custom Web or Mobile Application
  ↓
REST API
  ↓
Authentication, Tenant, and Session Resolution
  ↓
Assessment Core
  ├── PostgreSQL
  └── LLM Providers
```

A custom frontend should be introduced only when product requirements exceed
the Streamlit experience. Streamlit can remain an internal, demonstration, or
debugging adapter.

## Recommended Migration Strategy

- [x] Phase 1: Preserve and stabilize the existing Streamlit chat.
- [x] Phase 2: Introduce channel-neutral assessment models and core interfaces.
- [x] Phase 3: Implement one file-backed, versioned domain pack.
- [x] Phase 4: Add assessment sessions using a repository abstraction.
- [x] Phase 5: Connect the Streamlit UI as the first assessment adapter.
- [x] Phase 6: Add deterministic validation, rules, scoring, and recommendations.
  The score was removed again on 2026-10-06 (see RulesEngine).
- [x] Phase 7: Add structured report rendering.
- [x] Phase 7a: Add conditional question visibility in the assessment core.
- [x] Phase 7b: Add assessment-bound question help and post-completion context chat.
- [x] Phase 7c: Add a second demo domain, a local domain catalog, generic
  assessment selection, and a gated local Streamlit Domain Admin.
- [x] Phase 8: Split deterministic recommendation concerns from rules.
- [x] Phase 8a: Stabilize the channel-neutral core contract with explicit
  response and error models.
- [x] Phase 8b: Add an adapter-facing assessment payload boundary for future
  external adapters.
- [x] Phase 8c: Add a minimal local FastAPI adapter around the same core.
- [x] Phase 9a: Add a minimal versioned `/api/v1` REST surface using
  session-oriented resources and the existing contract response model.
- [ ] Phase 9: Harden the API contract into a versioned external REST surface
  when an external client or deployment needs it.
- [x] Phase 10a: Add a PostgreSQL JSONB session repository with migrations,
  anonymous pilot-session isolation, optimistic concurrency, and pilot feedback.
- [ ] Phase 10: Evolve pilot persistence into the normalized production data
  model when production requirements require it.
- [x] Phase 10b: Add chat-driven answer entry to the Streamlit assessment
  adapter; the chat assistant extracts a value, deterministic validation
  decides, the orchestrator persists.
- [x] Phase 10c: Persistent advisory chat: `AdvisoryContext` builder, advisory
  prompt policy, independent conversations with local/PostgreSQL repositories,
  `ConversationService`, `/api/v1/conversations`, a single continuous Streamlit
  chat, and LLM recommendations with explicit provenance.
- [x] Phase 10d: Remove the session-bound post-completion chat after the
  test-runner suite gained conversation and LLM-recommendation scenarios.
- [x] Phase 10e: Domain knowledge base with source attribution: document and
  curated web-page ingestion in the Domain Admin, deterministic retrieval,
  structured advisory answers with per-section sources, optional
  `web_sources` in the domain pack. Live web search and Domain Admin
  authentication are not part of this phase.
- [x] Phase 10f: Persist knowledge recommendations on the session result,
  generate them at completion with a fallback, report them in a labelled
  section (concept step 1).
- [x] Phase 10g (step 2a): Stored profile per participant and domain,
  created or replaced under a stable id by `complete()`, with local and
  PostgreSQL repositories, REST `/api/v1/profiles`, a profile report and a
  profile view in Streamlit. Conversations still reference the session.
- [x] Phase 10h (step 2b): Conversations and advisory context on the
  profile; the result chat is extended after each newer assessment; the
  test-runner runs every case as its own participant.
- [x] Phase 10i (step 3): Profile changes after completion (deterministic
  fact service with validation, visibility and open follow-up questions;
  the chat proposes via the model, confirms deterministically and applies;
  rules recompute at once, knowledge recommendations regenerate on request)
  and feedback on recommendations (buttons, REST, chat with confirmation;
  effective on regeneration, rule recommendations stay visible).
- [x] Phase 10j (step 4): Topic catalog in the pack, rule kinds exclude and
  prioritize acting on the topic the model assigns to each knowledge
  recommendation; exclusions are deterministic and recorded in
  `result.excluded`.
- [x] Phase 10k (step 5): Rule lifecycle (origin, source_reference, status
  draft/review/approved with an activation guard) and the Domain-Admin rule
  and topic editor.
- [x] Phase 10l (step 6): Hybrid retrieval (reciprocal rank fusion of BM25
  and embeddings) with an optional provider reranker; the retrieval mode is
  part of the generation metadata.
- [x] Phase 10m (step 7): Source versions for web pages: manual re-fetch in
  the Domain Admin, superseded versions stay citable until removed on
  purpose. This completes the knowledge-recommendation concept of
  October 2026 (document removed 2026-11-05, see git history).
- [ ] Phase 11: Add OpenWebUI or another chat-channel adapter only if it serves
  an actual pilot or integration need.
- [ ] Phase 12: Build a custom frontend if justified by product requirements.

Each phase must leave the repository in a usable and tested state. The existing
chat capability should either remain supported or be retired through an explicit
product decision; it must not disappear as an incidental refactor.

## Target Component Responsibilities

### DomainPackLoader

Loads and validates one domain definition.

Responsibilities:

- Load domain metadata, questions, validation constraints, rules,
  recommendation definitions, and prompt templates
- Validate references and pack structure
- Expose an immutable domain version and content hash to an assessment session
- Reject unsupported or malformed packs with actionable errors
- Support file-backed JSON or YAML packs first
- Remain independent from activation, authoring, and runtime catalog concerns

### DomainCatalog

Discovers available packs and resolves active domain versions.

Responsibilities:

- Discover bundled packs in `domain_packs/`
- Discover local admin drafts through a catalog store: `.guideos/domain_packs/`
  locally, the `domain_pack_drafts` table with PostgreSQL
- Resolve active versions from the same store (`.guideos/domain_catalog.json`
  or the `domain_active_versions` table)
- Fall back to the latest bundled version per domain when no manifest exists
- Save local drafts without mutating bundled packs
- Activate only packs that pass loader validation
- Preserve historical session reproducibility by never changing an existing
  session's stored domain ID, version, or content hash
- Remain a local development bridge until a real authoring and approval workflow
  is designed

### QuestionnaireEngine

Selects the next relevant question from structured session state.

Responsibilities:

- Order questions and sections
- Prioritize unanswered required questions
- Evaluate visibility and dependency conditions
- Support optional questions and explicit skip behavior
- Calculate progress from the active domain version and visible required
  questions
- Keep question selection deterministic and independently testable

### AssessmentOrchestrator

Coordinates each assessment interaction.

Responsibilities:

- Resolve the assessment session and immutable domain version
- Determine the current workflow state
- Process structured answers, natural-language answers, answer revisions,
  completion, and assessment-bound chat messages
- Route work to answer interpretation, validation, question selection, rules,
  recommendations, reports, or post-assessment chat
- Persist state changes through the repository interface
- Return a channel-neutral response object

Important rule:

```text
The orchestrator owns workflow decisions.
The LLM does not own workflow decisions.
```

### AnswerInterpreter

Interprets natural-language answers when direct structured input is unavailable.

The implementation may use an LLM, but the core depends on an interface rather
than a concrete provider.

Expected output shape:

```json
{
  "decision": "valid",
  "normalized_value": 120,
  "confidence": 0.92,
  "reason_code": null,
  "follow_up": null
}
```

Supported decisions are:

- `valid`
- `uncertain`
- `invalid`
- `not_answered`

Structured UI input may bypass LLM interpretation, but it must still pass
through deterministic validation.

### ValidationEngine

Determines whether a normalized value is acceptable.

Responsibilities:

- Validate required values and data types
- Validate ranges, formats, and allowed options
- Validate cross-field constraints where required
- Return stable validation codes and user-safe error details
- Reject values that do not conform to the active domain version

Principle:

```text
The AnswerInterpreter proposes a normalized value.
The ValidationEngine decides whether that value is acceptable.
```

### RulesEngine

Produce deterministic outcomes from validated answers.

Responsibilities:

- Evaluate versioned rules against normalized answers
- Support rule operators needed by domain packs, including equality, membership,
  numeric comparisons, missing values, and ratio checks where explicitly modeled
- Produce reason codes and recommendation candidate IDs. There is no score:
  the product owner removed it on 2026-10-06 because a single number carried
  no professional meaning and read as a top grade when answers were missing.
  It never drove a decision; recommendations are ordered by their `priority`.
  Bundled pack versions from before that date still contain a `score` key per
  rule, which is read and ignored
- Record the inputs and rule version used for a result
- Keep business logic explainable and testable without an LLM

Principle:

```text
Rules decide what should be recommended.
The LLM may explain the recommendation in user-friendly language.
```

### RecommendationEngine

Transforms deterministic candidates into structured recommendations.

Responsibilities:

- Resolve candidate definitions from the active domain version
- Attach evidence, assumptions, and reason codes
- Prioritize recommendations deterministically
- Optionally use an LLM to generate user-facing explanations in a later
  enrichment step
- Validate generated text and retain a deterministic fallback when enrichment is
  added

An LLM failure must not erase deterministic recommendation results.

The current repository implements the deterministic part in
`guideos.recommendations.engine`. `guideos.rules.engine` still exposes
`evaluate_assessment` as a compatibility facade, but rule evaluation and
recommendation construction are separate responsibilities.

### ReportRenderer

Creates a structured assessment summary and rendered artifacts.

Responsibilities:

- Build a channel-neutral report model
- Render assessment answers, recommendations, evidence, and assumptions
- Support Markdown or HTML first
- Add PDF export only when required
- Preserve the domain version and result metadata used to create the report

### StateRepository

Abstracts persistence from workflow logic.

Responsibilities:

- Create, list, load, save, and delete assessment sessions
- Store raw and normalized answers
- Persist current state and completion status
- Append assessment events for audit and debugging
- Store deterministic results and report metadata
- Enforce optimistic concurrency or equivalent protection once multiple
  processes can modify the same session

Local development uses a JSON repository implementation. The internal pilot
uses a PostgreSQL JSONB repository behind the same interface.

### Assessment Chat Assistant

Supports assessment-bound LLM chat without allowing the LLM to mutate workflow
state directly.

Responsibilities:

- Explain the active question during an in-progress assessment
- Optionally propose a normalized answer for user acceptance
- Extract a normalized answer from a chat message; the validation engine
  decides and the orchestrator persists
- Preserve chat messages separately from accepted assessment answers
- Advisory dialogue after completion is not a responsibility of this
  component; it lives in conversations.

### Advisory Context and Conversations

`guideos.context` turns a completed assessment session or a profile into an
`AdvisoryContext`: domain metadata, participant, verified facts (raw and
normalized value, confidence), deterministic reason codes and rule
recommendations. It never contains chat history. `build_advisory_messages` renders the advisory policy, the
context as structured JSON, the retrieved knowledge passages, the conversation
history and the user message.

`guideos.conversations` holds independent advisory dialogues. Since
2026-10-09 (concept step 2b) a conversation references the participant's
profile of the domain (`profile_id`) and owns only its own messages. For every
LLM request the `ConversationService` reloads the profile, resolves the domain
pack by version, verifies the content hash and rebuilds the context, so later
profile changes affect subsequent answers. Two conversations on the same
profile receive identical context and keep isolated histories. The "Ergebnis"
chat is created once per profile and extended by one block each time a newer
assessment replaced the profile (`result_session_id` records which completion
it last presented); older sessions keep answers and report but no chat.
Conversations stored before profiles existed (file version 1) are refused
with a hint, and the pilot data is purged instead of migrated.

Advisory answers are generated as structured output (`AdvisoryAnswer`): a list
of sections, each with text and the ids the model cites. `resolve_sources`
maps those ids to `SourceRef` records and the message stores them in
`sections` next to the rendered `content`. The model never writes provenance
itself.

Invariants:

- `AssessmentSession`, `AdvisoryContext`, `Conversation`, rule recommendation
  and LLM recommendation stay separate concepts.
- A conversation statement never mutates verified context.
- Repositories are participant-scoped; one participant cannot read, change or
  delete another participant's conversations.

### LLM Recommendations

`guideos.recommendations.llm` asks the model, via structured generation over
the `AdvisoryContext` and the retrieved knowledge passages, for genuinely
additional recommendations. GuideOS stamps `source="llm"`, drops rewrites of
rule recommendations and duplicates, keeps only evidence fields that exist in
the verified facts and only cited passage ids that were handed out. `sources`
lists the cited passages, or the model's own knowledge when none is cited.
Deterministic recommendations carry `source="rule"` and remain the
authoritative output.

Since 2026-10-08 (knowledge-recommendation concept, step 1) the checked knowledge recommendations are part of the session result:
`AssessmentResult.knowledge_recommendations` holds them and
`knowledge_generation` records model, prompt version, time, every retrieved
passage the model saw and, on failure, the error code. The orchestrator
generates them at completion when the adapter passes endpoint and model and
the pack does not disable them (`knowledge_recommendations.enabled`,
`max_count`); a failure never blocks completion. The on-request path
(`generate_llm_recommendations`, REST `llm-recommendations`) regenerates and
replaces the stored set. The Markdown report renders them in a separate,
labelled section after the rule recommendations; the result chat shows the
stored set instead of the offer. The LLM never writes to the session itself;
the orchestrator and the service persist what GuideOS checked.

### Knowledge Base and Source Attribution

`guideos.knowledge` holds one knowledge base per domain id, independent of
pack versions and participants. Domain experts maintain it in the Domain
Admin:

- `ingestion` extracts text from PDF (pypdf), Markdown, plain text and HTML,
  keeps a citable locator per segment (page or heading) and splits segments
  into chunks. Web pages are fetched on ingestion and again only on an
  admin's explicit re-fetch, which stores changed content as a new source
  version and marks the earlier one superseded (kept citable, not searched);
  there is no live web search, so user questions never leave the environment.
- `store` persists documents with their chunks and the SHA-256 hash of the
  original bytes: local JSON by default, a JSONB table in PostgreSQL.
- `service` adds, lists and deletes documents and retrieves passages for a
  query. Retrieval is a deterministic step before the LLM call, not a tool the
  model invokes. The default ranking is lexical (BM25 over character n-grams);
  with `GUIDEOS_EMBEDDING_MODEL` documents are embedded through the provider
  and the lexical and cosine rankings are fused by reciprocal rank (hybrid)
  in process; with `GUIDEOS_RERANK_MODEL` the provider's `rerank` reorders
  the top candidates and a failure falls back to the fused order. The mode
  is recorded with every knowledge generation. No vector database is used.

A pack version opts into web pages with `web_sources.enabled` and may restrict
them to `web_sources.allowed_domains`. The field is optional and omitted from
serialized drafts while unset, so packs without it hash as before.

Source kinds are `rule`, `user_context`, `document`, `web` and `llm`. The
model receives passages with ids (`D1`, `W1`) and may cite those, a
`recommendation_id` or a `field_key`. Unknown ids are dropped. A section that
cites no rule, document or web page is marked as the model's own knowledge.
This guarantees that a cited passage exists and was supplied for the request;
it does not prove that the statement follows from it, which is why the stored
excerpt is shown to the user.

Invariants:

- Rules, validation and the report never use the knowledge base.
- The knowledge base is not part of the pack content hash; each citation
  stores the document hash and, for web pages, the fetch time instead.
- Retrieved text is untrusted data: the prompt policy forbids following
  instructions inside passages.

### MCP Tools

Status: steps 5a and 5b implemented on 2026-10-19 (`guideos/tools/`, advisory
integration with datasets and `T<n>` citations); costs, tariffs and
charging scenarios open; see `docs/entwicklung/konzept-netzkosten-sne-gv.md`.

Responsibilities:

- Keep tool server registrations per domain (name, URL, token, enabled,
  allowed tools, cached tool specs), independent of pack versions, managed
  in the Domain Admin tab Werkzeuge.
- Give the advisory model only the allowed tools of enabled servers, with
  the `owner` field removed from the schemas.
- Run the tool loop: the model answers with tool calls or text; GuideOS
  checks each call against the allowlist and the input schema, sets `owner`
  to the participant id, executes it, checks the output against the output
  schema and feeds the structured result back as a `tool` message; the loop
  stops at a text answer, at the budget of calls and seconds per message,
  at an unreachable server or when the model cannot call tools.
- Treat tool outputs as data, never as instructions; provenance of tool
  statements (`T1…`) is assigned by GuideOS (`SourceRef(kind="tool")`, the
  calculation id as reference), figures are verified against the tool output.
- Keep the participant's dataset reference and the last result per tool on
  the profile; datasets are deleted on the server when the profile, the
  dataset or the pilot data are deleted.

Not responsible for: writing profiles or facts (verified facts arise only
from the assessment and confirmed profile changes), choosing servers (the
Domain Admin does), calculations (the servers do).

### LLM Provider Registry

The existing `guideos.providers` package is the starting point for provider
abstraction.

Responsibilities:

- Resolve a configured provider by stable identifier
- Stream chat responses where the channel supports streaming
- Support non-streaming structured output for answer interpretation and
  assessment-bound helper responses; structured calls run with temperature 0
  so the same input yields the same normalized value, while streamed chat
  keeps the model default
- Normalize provider failures into GuideOS-specific errors
- Keep endpoint credentials and provider configuration outside domain packs;
  the default model and endpoint come from the environment, and a per-request
  model choice (REST header `X-GuideOS-Model`, offered to testers only while
  the operator enables `GUIDEOS_MODEL_SELECTION`) is an adapter concern that
  never reaches pack content or the core

Ollama is the only currently implemented provider. Additional providers should
implement the shared interface rather than add provider conditionals to the
assessment core or UI.

### Adapters

Adapters connect external channels to the assessment core.

Implemented adapters:

- Streamlit
- Local FastAPI development API

Planned production adapter:

- Versioned FastAPI REST API

Possible future adapters:

- CLI
- OpenWebUI
- Slack
- Teams
- Embedded widget
- Messaging channels, starting with WhatsApp (plan parked, not decided)

Adapter rule:

```text
Adapters translate channel-specific input and output.
Adapters must not contain domain rules or workflow decisions.
```

## Suggested Code Structure

GuideOS uses a top-level `guideos` package; the target structure evolves inside
that package rather than introducing a second package name. The current module
layout is maintained in `CLAUDE.md`. Directional additions are
`guideos/adapters/openwebui.py` for a chat-channel adapter and
`guideos/reports/templates/` once report rendering outgrows direct Markdown.

This is a directional structure, not a requirement for a single large
reorganization. `.guideos/` entries are local runtime and admin artifacts, not
repository files. Existing modules should move only when a feature requires the
new boundary. In particular, `guideos.chat` and `guideos.storage` should remain
stable until their responsibilities are intentionally split.

## Domain Pack Model

A domain pack is an immutable, versioned definition used by an assessment
session. A minimal pack should contain:

```text
domain metadata
questions and sections
field types and input options
validation constraints
visibility and dependency conditions
rules
recommendation definitions
prompt templates
locale-specific text
```

Early packs should live in Git as JSON or YAML and be validated with Pydantic.
Editing, approval workflows, database storage, and an authoring UI should be
introduced only after the file format and runtime behavior are proven.

The current local configuration layer keeps bundled packs in `domain_packs/`
and local admin drafts in `.guideos/domain_packs/`. Activation is stored in the
manifest path `.guideos/domain_catalog.json` once an activation is saved. With
`GUIDEOS_DATABASE_URL` set, drafts and activations are stored in PostgreSQL
instead. If no activation is stored, the latest bundled version per domain is
considered active.
Active versions are immutable from the Admin UI; edits are saved as drafts or
new semantic versions and must validate before activation.

The current file-backed pack model supports one optional `visible_if` condition
per question: `equals`, `in`, and `is_missing` against a previous question's
normalized answer, plus `contains` and `contains_any` for multi-valued answers.
The loader rejects operators that do not fit the referenced field type.
Conditions are intentionally not nested; compound `and` or `or` expressions
should be introduced only when a real domain requires them.

Questions are modelled after SOyA (Semantic Overlay Architecture): the field
type maps to a base attribute datatype, required/unknown handling and option,
range, length and pattern constraints map to the validation overlay, the widget
to the form overlay and texts to the annotation overlay. `guideos.domains.soya`
exports a pack's questions as a SOyA structure and imports one as a draft
(JSON only). Rules and recommendations have no SOyA counterpart and remain
GuideOS-specific; GuideOS details without a counterpart travel under
`options.guideos` of the form controls.

A session stores the exact domain ID, version, and content hash it started
with. Activating a new version must not change historical or in-progress
results unless an explicit migration is performed.

## Target Data Model

The pilot stores sessions, conversations, profiles, the knowledge base, tool
server registrations and the domain catalog as JSONB documents in PostgreSQL
(Phase 10a); a normalized production data model is Phase 10 and not
implemented. The entity sketch and its design notes were parked on 2026-10-22
(the parked document was removed on 2026-11-05 and lives in git history).
Two of those notes stay binding today: raw and normalized answers are both
retained, and identifiers exposed outside the process are stable strings or
UUIDs.

## Channel-Neutral Core Contract

The orchestrator should accept structured commands and return structured state.
Adapters may map chat messages, form submissions, or API calls to these models.
The current `guideos.assessment.contract` module defines channel-neutral input,
response, and error models. Streamlit may continue to use the internal
`AssessmentResponse`, while future external adapters should use the explicit
contract facade.

Example request:

```json
{
  "kind": "submit_text_answer",
  "session_id": "sess_abc123",
  "question_id": "home_size_sqm",
  "text": "ungefähr 120 Quadratmeter",
  "endpoint": "http://localhost:11434",
  "model": "gpt-oss:20b"
}
```

The input kinds are `start`, `submit_answer`, `submit_text_answer`,
`revise_answer`, `complete`, `question_help`, `accept_proposed_answer`, and
`discard_proposed_answer`. The chat-driven answer path of the Streamlit adapter
(`submit_question_chat`) is not yet a contract input.

Example response:

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "status": "in_progress",
  "assistant_message": "Woher stammt der Großteil der Einnahmen?",
  "accepted_answer": {
    "question_id": "members_count",
    "field_key": "members_count",
    "raw_answer": "ungefähr 120 Mitglieder",
    "normalized_value": 120,
    "confidence": 0.92
  },
  "current_question": {
    "question_id": "funding_source",
    "field_key": "funding_source",
    "field_type": "select",
    "text": "Woher stammt der Großteil der Einnahmen?",
    "required": true,
    "options": [
      {"value": "membership_fees", "label": "Mitgliedsbeiträge"},
      {"value": "public_grants", "label": "Öffentliche Förderungen"}
    ]
  },
  "progress": {
    "answered_required": 1,
    "required_total": 5
  },
  "validation_errors": [],
  "errors": []
}
```

The contract must also define responses for:

- invalid input,
- uncertain interpretation requiring a follow-up,
- stale or missing sessions,
- completed sessions,
- unavailable LLM providers,
- answer proposals from assessment-bound chat,
- and deterministic results that exist despite failed LLM phrasing.

## Target REST API

FastAPI is the preferred REST adapter because GuideOS is already a Python
project. The current repository includes a minimal local adapter in
`guideos.adapters.fastapi` with these unversioned endpoints next to the v1
surface below:

```http
GET  /health
GET  /domains
POST /domains/{domain_id}/assessment
```

This current adapter is useful for development, demos, and contract validation.
It should not yet be treated as the public API surface.

The target external API should move toward explicit versioning and
session-oriented resources:

```http
GET  /api/v1/health
GET  /api/v1/domains
POST /api/v1/assessment-sessions
POST /api/v1/assessment-sessions/{session_id}/inputs
POST /api/v1/assessment-sessions/{session_id}/chat
GET  /api/v1/assessment-sessions/{session_id}
GET  /api/v1/assessment-sessions/{session_id}/report
POST /api/v1/assessment-sessions/{session_id}/llm-recommendations
POST /api/v1/conversations
GET  /api/v1/conversations
GET  /api/v1/conversations/{conversation_id}
POST /api/v1/conversations/{conversation_id}/messages
DELETE /api/v1/conversations/{conversation_id}
```

The `/chat` endpoint accepts `question_help` only; advice after completion
uses the conversation resources.

The current v1 surface implements this minimal resource shape for local
integration use. It uses the selected local or PostgreSQL repository and the
existing `AssessmentContractResponse`; it does not yet include authentication,
tenant resolution, idempotency, or rate limits.

The API should:

- Return structured UI state rather than plain assistant text only
- Use the same request and response models as the core where practical
- Return stable machine-readable error codes
- Keep authentication and tenant resolution in the adapter boundary
- Avoid exposing provider endpoints, credentials, prompts, or internal event
  payloads

API versioning, authentication, authorization, idempotency, and rate limits must
be specified before this adapter is considered production-ready.

## Evaluation and Test-Runner Service

GuideOS provides a separate evaluation capability for repeatable, observable,
and persistent testing of the assessment product. The test-runner service is
not part of the assessment workflow and must not introduce workflow decisions
into the assessment core.

It executes versioned JSON scenarios against the GuideOS REST surface, persists
the complete evidence of each run, and produces deterministic pass/fail
results. The implementation lives in `testsuite/`; the scenario contract, CLI,
isolation, PostgreSQL storage, Docker, CI, cleanup, and the calling convention
for other processes are documented in [test-runner.md](../test-runner.md).

### Architectural Boundary

~~~text
Scenario Files ─────────────┐
External orchestrator ──────├──→ Test-Runner Service
(optional, e.g. an agent)   │          ├──→ GuideOS REST API
CI / CLI ───────────────────┘          ├──→ Ollama via GuideOS
                                       ├──→ Assertion Evaluator
                                       └──→ Test-Run Repository
                                                  ↓
                                     JSONL (local) / PostgreSQL
~~~

The runner calls the versioned REST surface rather than importing private
assessment implementation details. This keeps the evaluation close to a real
client while preserving the contract and error semantics used by adapters.

The runner must not:

- change assessment workflow rules,
- write directly to GuideOS assessment tables,
- bypass the REST contract for ordinary end-to-end tests,
- treat free-form LLM prose as the source of truth,
- or depend on an external orchestrator for deterministic execution.

Test sessions, generated artifacts, and test-run metadata remain separate from
pilot data: the runner starts a disposable API with its own JSON storage and
persists evidence in a dedicated `testsuite` schema or local JSONL files.

### Scenario Coverage

Assertions check stable machine-readable outcomes (status, error codes,
normalized values, progress, reason codes, recommendation IDs, report
presence), never exact natural-language wording. Bundled scenarios cover
structured and invalid answers, unknown values and revisions, conditional
visibility, natural-language interpretation, question help, advisory
conversations, LLM recommendations, and regressions for fixed defects (tag
`regression`, defect named in the scenario `id`). Uncertain interpretation,
proposal accept/discard, and provider timeouts or malformed structured output
cannot be forced against a real model and are covered by pytest with scripted
providers through the REST adapter. Model-dependent scenarios can be repeated
with `--repeat N` to measure a pass rate instead of a single verdict.

### External Orchestration

An external orchestrator (for example an agent driving the runner CLI)
may select scenarios, start the runner CLI, read reports,
request reruns, summarize failures, and propose new regression scenarios. It is
never the evaluation authority: it cannot mark a failed assertion as passed or
mutate persisted results, and generated scenarios enter the normal review
process before being committed.

### Expert Review

Whether an advisory answer is professionally correct cannot be asserted
mechanically. Scenarios may therefore carry a `review` block (persona, kind,
check criterion). The Streamlit mode "Beratungstest" renders the answers of
such a run from the persisted evidence alone, and domain experts rate each
answer. Ratings are append-only records in the test-run repository (`ratings`,
table `testsuite.test_ratings` or the run's JSONL file) with reviewer name and
timestamp; they never change a run's verdict, and cleanup keeps rated runs
unless explicitly told otherwise. The view lives in `testsuite/` and is only
mounted by `app.py`; the assessment core does not depend on it. Like the Domain
Admin it has no authentication yet.

### Security and Privacy

The test runner is a development and CI component. It uses synthetic scenario
data only, defaults to localhost or an explicitly allowed host, refuses targets
declared as production without an explicit override, keeps database
credentials outside scenario files, redacts credentials and sensitive headers,
and stores raw prompts and answers only when a scenario sets `persist_raw`.
The local REST API has no authentication and must stay bound to a local or
isolated Docker network during test execution.

## User Experience Target

The current Streamlit chat and configurable assessment mode remain the starting
experiences. Assessment mode should evolve toward a hybrid guided interface
containing:

- A clearly presented current question with field-type hints; earlier answers
  stay available in a collapsed area
- Typed widgets for choice and yes/no questions, and a chat input under the
  question for free answers (text, numbers, dates) and for questions about the
  current question
- An explicit "I do not know" or skip action where allowed
- Progress through visible required questions
- Validation and clarification feedback
- A review step that summarizes answers before completion; typed correction
  controls stay in a collapsible review area
- After completion one continuous transcript for the result and persistent
  advisory conversations: the deterministic result and rule recommendations as
  a chat message, with the clearly labelled knowledge recommendations
  generated at completion
- Domain, session and conversation controls in the sidebar
- An optional report download

The Streamlit adapter implements this form-style answering since 2026-09-24.
It replaces the earlier target of a single chat input for all answers; the
chat input is used only where it is explicitly intended. The product owner
confirmed this on 2026-09-30; see the decision under
[Open Product and Requirements Questions](#open-product-and-requirements-questions).

The frontend must not infer workflow state from assistant text. It should render
the structured state returned by the assessment core.

## Channel Extension Plan: Mobile and Messaging

Proposed on 2026-09-28 and parked; the plan (mobile use of the Streamlit app,
a channel-neutral messaging layer, WhatsApp as first transport, seven open
questions for the product owner) was removed from the repository on
2026-11-05 and lives in git history. It refines
Phase 11 and Phase 12 and starts only after the open questions are answered.

## Tooling Direction

### Current

```text
Application:
- Python 3.12+
- Streamlit
- Pydantic v2

LLM:
- GuideOS provider protocol (`stream_chat`, `generate_structured`,
  `list_models`, `embed`, `rerank`, `chat_with_tools`)
- Ollama (`/api/chat` with `tools` for tool-capable models)

MCP tools:
- fastmcp client in `guideos/tools/` (synchronous facade, one connection per
  call); tool servers registered per domain in the Domain Admin
  (`mcp_servers` / `.guideos/mcp_servers.json`)
- Decision of 2026-10-15: the model may choose and call allowed tools inside
  guard rails (allowlist, schema checks, owner injection, budget); agent
  frameworks stay excluded, the loop is small own code

Persistence:
- Versioned local JSON files for chats, assessment sessions, conversations,
  the knowledge base, and the domain catalog (default)
- PostgreSQL via psycopg for the internal pilot (`GUIDEOS_DATABASE_URL`):
  JSONB repositories, hand-written migrations in
  `guideos/assessment/migrations/`, optimistic revision checks

Domain packs:
- File-backed JSON
- Pydantic validation
- Local catalog manifest for active versions; PostgreSQL catalog store in
  pilot mode
- Streamlit Domain Admin with a form-based question editor, local drafts
  and activation
- SOyA structure export and import (JSON; no PyYAML)

Knowledge base:
- pypdf for PDF text extraction; Markdown, text and HTML with the standard
  library
- Lexical BM25 ranking in process; optional hybrid ranking with embeddings
  through the Ollama provider (`GUIDEOS_EMBEDDING_MODEL`) and an optional
  reranker (`GUIDEOS_RERANK_MODEL`)

Testing and quality:
- pytest (deterministic unit and adapter tests; PostgreSQL tests skipped
  without a database URL)
- REST test-runner suite in `testsuite/` with persisted run evidence
- ruff with pyflakes and pycodestyle error rules (`uv run ruff check .`)
- GitHub Actions: lint, pytest against a PostgreSQL service, and the
  Docker Compose test stack

Observability:
- Python `logging` configured once per process by
  `guideos.config.configure_logging` (`GUIDEOS_LOG_LEVEL`, default INFO);
  `event key=value` lines for session start, accepted and rejected answers,
  completion, conversation creation, sent advisory messages, generated LLM
  recommendations, and failed provider requests; completed provider requests
  with their duration at DEBUG; no answer values or chat content

Development API:
- FastAPI
- Uvicorn

Deployment:
- Docker Compose stack for the pilot (`compose.yaml`) and the isolated test
  stack (`compose.testsuite.yaml`)
```

### Introduce With the Relevant Feature

```text
Schemas and domain packs:
- YAML support only if JSON becomes insufficient
- PyYAML only if YAML is selected

Database:
- SQLAlchemy and Alembic only if the normalized production data model
  outgrows psycopg and the hand-written migrations

Reports:
- Jinja2 when templates outgrow direct Markdown rendering
- Markdown or HTML first
- PDF later through Playwright or WeasyPrint

Observability:
- Structured (JSON) log output and request correlation when a central log
  collector exists
- OpenTelemetry only when deployment requirements justify it
```

Explicitly not planned for the advisory chat: a vector database, agent
frameworks, live web search, conversation summarization, automatic fact
extraction from chats, and automatic profile mutation. Model-driven tool
calling is allowed since the decision of 2026-10-15, but only inside the guard
rails described under MCP Tools (allowlist, schema checks, owner injection,
budget), with a small loop of own code instead of an agent framework.
Retrieval over the domain knowledge base was decided on 2026-10-01 (see
Knowledge Base and Source Attribution); it stays a deterministic step in
GuideOS code, with optional embeddings through the provider and no separate
vector store. The proposal-and-confirmation workflow for profile updates
(user message, proposed fact, validation, user confirmation, context update)
is implemented since 2026-10-10 (Phase 10i); verified context is never
updated from LLM interpretation alone. Since 2026-10-21 a pack may also derive
answers deterministically from measurement data (`origin: measurement`,
pack block `tools.derive`); the model is not involved in that path either.

Dependencies should be added when they support an implemented capability, not
only because they appear in the target architecture.

## Security, Privacy, and Reliability Principles

1. Provider endpoints and credentials are configuration, not domain-pack data.
2. LLM output is untrusted input and must be parsed and validated.
3. Domain-pack prompts and user answers must not be allowed to override workflow
   invariants.
4. Personal data collection and retention must be defined per domain.
5. Session export and deletion requirements must be settled before production
   persistence.
6. Logs and assessment events must avoid leaking secrets or unnecessary
   personal data.
7. Deterministic results must remain available when LLM explanation generation
   fails.
8. Concurrent updates must be detected once sessions can be accessed by more
   than one process or client.
9. The LLM never persists an answer itself. A proposed value is stored only
   after deterministic validation, either directly in the chat-driven answer
   flow or after explicit user acceptance in the proposal flow.
10. Every recommendation and every advisory answer section exposed to callers
    carries explicit provenance (`rule`, `user_context`, `document`, `web`,
    `tool` or `llm`), assigned by GuideOS and never by the model; LLM
    recommendations never replace or alter deterministic results, and a
    conversation statement never becomes verified user data.
12. Tool outputs are untrusted data as well: GuideOS checks them against the
    output schema, feeds them to the model as JSON, verifies cited figures
    against them and sets the dataset owner itself; the model never sees or
    sets `owner`.
11. Knowledge-base content and fetched web pages are untrusted input: they are
    passed to the model as quoted data, and web pages are fetched only when an
    administrator adds them, never in response to a user message.

## Key Architectural Principles

1. The LLM is not the source of truth for workflow or business rules.
2. Domain behavior is configured through immutable, versioned domain packs.
3. Channels are implemented as adapters.
4. Provider-specific behavior remains behind the existing provider boundary.
5. The assessment core is reusable across Streamlit, REST, and future channels.
6. Raw and normalized answers are both retained.
7. Validation, rules, and prioritization are deterministic and testable.
8. Recommendations are explainable through reason codes and evidence.
9. Historical sessions retain the domain version and content hash used to
   produce their result.
10. Structured UI state is part of the core contract; assistant text alone is
    not sufficient.
11. New infrastructure is introduced incrementally and must solve a current
    capability or deployment need.

## Open Product and Requirements Questions

All questions below were answered by the product owner on 2026-09-11, unless
a decision names a later date. Each answer is recorded as a decision; open
follow-ups are marked explicitly.

### Product Scope

- Decided: general-purpose chat without assessment context is not a product
  goal. The product flow is: complete the assessment on first use, then
  continue permanently in the advisory chat that knows the verified profile
  from the assessment. The existing chat mode stays a local prototype and is
  not part of the pilot or of production.
- Decided: the first deployment is internal at the operating organisation: Docker on an
  internal VM behind a reverse proxy, as in the current pilot.
- Decided: users, authentication, and tenants come after the pilot and before
  production. The pilot keeps the shared test password plus anonymous
  participant key.

### User Experience

- Decided by the product owner on 2026-09-30, replacing the decision of
  2026-09-11 for one continuous chat covering answers, result and advisory
  conversations: the assessment is answered form-style. The Streamlit adapter
  has implemented this since 2026-09-24, as a deviation from the decision
  valid at that time; the product owner confirmed it afterwards. The app shows
  only the current question; choice and yes/no questions use typed widgets.
  The chat input is used only where it is explicitly intended: free answers
  (text, numbers, dates) and questions about the current question through the
  chat input under the question. Under choice and yes/no questions the chat
  input is meant for questions about the question; an answer typed there is
  still accepted through the same deterministic-first chat path. The review
  step summarizes answers in the chat and keeps typed correction controls in a
  collapsible area. The result and the advisory conversations after completion
  remain one continuous chat.
- Decided: pause and resume across devices comes together with real user
  accounts after the pilot. During the pilot an assessment can only be
  continued in the same browser.
- Decided: accepted answers can be revised until completion; results are
  computed at completion. The decision of 2026-09-11 that a changed profile
  after completion means a new assessment was replaced on 2026-10-08: single
  facts can be changed in the advisory chat after an explicit confirmation
  (rules recompute at once, knowledge recommendations regenerate on request),
  and a new assessment replaces the profile under a stable profile id.
- Decided: each recommendation shows the answers it is based on, and rule
  and LLM provenance stay separately labelled.

### Domain Pack Authoring

- Decided: domain experts author and review domain packs themselves through
  the Domain Admin; a version is released by activating it there. Follow-up:
  the Domain Admin must gain authentication before it is used by experts
  outside a developer machine.
- Decided: because experts work through the Admin, pack storage moves from
  local files to the database before production (Admin UI with database-backed
  packs). Git-managed files remain the format for the bundled demo packs.
  Done for the pilot: with `GUIDEOS_DATABASE_URL` set, drafts and active
  versions live in PostgreSQL (`PostgresCatalogStore`). Approval and review
  history are still open.
- Decided: the first production pack supports German only; `locale` is not
  developed further for now.

### Rules and Recommendations

- Decided: rule-based recommendations and validation are fully
  deterministic. Decided 2026-10-06: the result has no score any more. Everything that enters the result and the report comes from
  rules; the LLM adds only clearly labelled hints and explanations.
- Decided: each recommendation carries its referenced fields, the rule reason
  code for rule-based recommendations, and additionally a source reference
  (professional source or standard) maintained in the pack. LLM
  recommendations must name referenced fields that support their reasoning.
  Done 2026-09-11: recommendations carry an optional `source_reference`
  that chat and report show as the professional source; the bundled demo
  packs leave it empty, the production pack must fill it.
- Decided: if the LLM is unavailable, the app reports a German error and
  waits; there is no assessment without the LLM.
- Decided: domain experts approve recommendation quality through test-runner
  scenarios with expected outcomes; a pack counts as approved when the run is
  green, with a pass rate for LLM output measured via `--repeat`.

### Data and Compliance

- Decided: the energy domain collects building and consumption data only; no
  names, addresses, or contact data. Consumption data is treated as
  personally relatable but not as a special category.
- Decided: pilot data (sessions, conversations, events, reports) is kept until
  the end of the pilot and then deleted. A fixed retention period for
  production is defined together with user accounts.
- Decided: users can delete their own sessions and conversations (already
  available in the pilot). Export, consent capture, and audit logging are not
  required for now.
- Decided: only local Ollama is used; no data leaves the operating organisation. An
  OpenAI-compatible provider is not needed.
- Decided 2026-10-01: domain knowledge comes from a per-domain knowledge base
  that domain experts maintain in the Domain Admin (PDF, Markdown, text, web
  pages). Web content is limited to curated pages fetched once on ingestion,
  which keeps the decision above intact; a live web search would send user
  questions out and is not implemented. Source attribution per answer section
  is sufficient. Domain Admin authentication stays a follow-up and is
  deliberately scheduled after the pipeline works.

## Implementation Plan

### Immediate Stabilization

1. Keep `app.py`, `guideos.adapters.streamlit`, and
   `guideos.adapters.fastapi` thin by routing assessment behavior through the
   orchestrator and contract service only.
2. Treat `guideos.assessment.contract` as the external adapter boundary.
   Extend it deliberately when Streamlit, FastAPI, or a future channel needs
   new state.
3. Keep local JSON persistence as the default development mode. PostgreSQL
   pilot sessions use optimistic revision checks; local JSON remains unsuitable
   for parallel multi-client writes.

### API Increment

1. The minimal session-oriented `/api/v1` surface is implemented for local
   development and integration. Define its external compatibility and HTTP
   semantics only when an external client is planned.
2. Add idempotency and stale-session behavior before allowing repeated or
   concurrent client submissions.
3. Keep provider endpoint and model selection out of public request payloads
   unless there is an explicit product requirement to expose them.

### Domain Pack and Authoring Increment

1. Validate the bundled demo-domain questions, rules, and recommendation
   quality with domain experts before treating them as product behavior.
2. Define a domain-pack review and approval process before production domain
   versions can be activated.
3. Improve the Domain Admin UX for non-technical authors only if local
   authoring remains a priority after the first real domain is selected.
4. Add compound visibility conditions, cross-field validation, or richer field
   types only when a real domain requires them.

### Persistence and Operations Increment

1. Define retention, deletion, privacy, audit, and consent requirements for
   assessment data before production persistence.
2. PostgreSQL is available behind the existing assessment repository boundary
   for the internal pilot, including an initial migration and optimistic
   concurrency checks.
3. Define backup/restore expectations and structured operational logging before
   treating the pilot persistence as production-ready.
4. Keep `.guideos/` as local runtime state and continue excluding it from
   version control.

### Frontend and Integration Increment

1. Keep Streamlit as the primary product and debugging adapter until a custom
   frontend has concrete workflow requirements.
2. Build a custom frontend only when Streamlit blocks required UX, branding,
   embedding, authentication, or multi-user flows.
3. Add OpenWebUI or another chat-channel adapter only for a concrete pilot or
   integration need.
4. Mobile use and messaging channels are parked (plan removed from the
   repository on 2026-11-05, see git history) until their open questions
   are answered.

### Evaluation Increment

1. Continue adding regression scenarios (tag `regression`) for assessment or
   provider fixes.
2. Add a programmatic run-control interface for an external orchestrator only
   when the process-based CLI contract is no longer sufficient.

## Glossary

- **Chat**: A conversation transcript containing user and assistant messages.
- **Assessment**: A structured guided process that collects validated answers
  and produces a result.
- **Assessment Session**: One concrete run of an assessment against an immutable
  domain version.
- **Domain Pack**: A versioned definition containing questions, validation,
  rules, prompts, recommendations, and localized text.
- **Adapter**: A channel-specific integration layer such as Streamlit or a REST
  API.
- **Assessment Core**: The reusable engine that coordinates sessions, questions,
  validation, rules, recommendations, and reports.
- **Answer Interpreter**: A component that converts natural language into a
  proposed structured answer.
- **Assessment Chat Assistant**: A component that answers assessment-bound
  questions without directly mutating accepted answers.
- **Reason Code**: A stable deterministic marker explaining why a rule or
  recommendation applies.
- **Recommendation Candidate**: A deterministic recommendation result before
  optional user-facing LLM phrasing.
- **Advisory Context**: The verified facts and deterministic results of one
  completed assessment session or profile prepared for advisory LLM use; never
  contains chat history.
- **Conversation**: One independent advisory dialogue that references the
  participant's profile of a domain and owns only its own message history.
- **LLM Recommendation** (knowledge recommendation): An additional,
  probabilistic suggestion from the model with `source="llm"`, grounded in
  cited knowledge passages or the model's domain knowledge; persisted on the
  session result together with how it was generated, never a rule.
- **Knowledge Base**: The documents and curated web pages of one domain that
  the advisory may search and cite; independent of pack versions.
- **Source Reference**: The provenance record GuideOS attaches to an answer
  section or recommendation (`rule`, `user_context`, `document`, `web`,
  `tool`, `llm`).
- **Tool Result**: The structured output of one allowed MCP tool call
  (`T1…`), stored on the conversation message and, as the last result per
  tool, on the profile; cited with its `calculation_id` as source kind
  `tool`.
- **Dataset**: The reference (`dataset_id`, content hash, masked file name,
  import summary) to a load profile that a tool server holds for one
  participant; GuideOS never stores the file content.
