# MVP Specification — Distributed Enriched-Persona Agent

## 1. Product Summary

Build a hosted, multi-tenant agent platform centered on an **enriched persona**: an agent that is conversationally coherent, remembers reliably, maintains stable behavioral principles, and can delegate work to remote workers.

The system is intentionally narrow:

- One hosted coordinator per tenant context.
- One main model provider: OpenRouter.
- A TUI as the only user interface in v1.
- Remote workers authenticated through mTLS.
- Strict capability-based permissions and auditable task execution.
- A hybrid memory system designed for useful context at low token cost.

The product is a clean, modular architecture where orchestration, persona, memory, workers, and integrations have explicit boundaries.

## 2. MVP Outcome

The MVP succeeds when a user can ask the TUI:

> “Research this repository’s authentication flow.”

The coordinator should:

1. Understand the request within a coherent ongoing conversation.
2. Compile only relevant persona and memory context.
3. Decide to delegate code inspection to an authorized remote worker.
4. Send a bounded task with only necessary context and capabilities.
5. Show progress and request approval for impactful actions.
6. Return an auditable answer with worker findings.
7. Learn approved, relevant project context for future conversations.

## 3. Product Principles

- **Persona is stable, not a giant prompt.** The agent has explicit values, style, boundaries, and initiative rules.
- **Memory is user-controlled.** Every durable memory has provenance and can be inspected, edited, or deleted.
- **Context is compiled.** The main model sees a compact task-specific briefing, not the entire conversation history.
- **Delegation is a primitive.** Remote workers are first-class, independently authenticated execution nodes.
- **Capabilities are explicit.** No worker receives implicit access to files, credentials, network, or external communication.
- **Default-deny security.** Impactful actions require policy permission and, where configured, user approval.
- **Everything important is auditable.** Tasks, delegation, tool use, approvals, memory writes, and outputs create events.

## 4. Architecture

```text
TUI
  ↓
Hosted Coordinator
  ├── Core Runtime
  ├── Persona & Context Service
  ├── Memory Service
  ├── Policy Engine
  ├── Task Queue / Event Log
  ├── OpenRouter Model Adapter
  └── Worker Registry
          ↓ mTLS
      Remote Workers
          ├── Code worker
          ├── Research worker
          └── Future specialist workers
```

### 4.1 Core Runtime

The core runtime is shared infrastructure. It does not decide how to solve a task.

Responsibilities:

- task lifecycle and durable state
- event logging and audit trails
- model-call interface
- context assembly interface
- policy evaluation
- worker protocol handling
- artifact storage and retrieval
- tenant and identity boundaries

### 4.2 Coordinator

The coordinator is the decision layer built on the runtime.

Responsibilities:

- maintain the user-facing conversation
- plan and decompose requests
- decide when delegation is appropriate
- choose a worker by capability, trust, availability, cost, and policy
- synthesize worker results
- schedule future work later, outside MVP scope

Delegation triggers can include explicit user request, task complexity, specialist capability requirements, time/cost limits, and available worker capacity.

### 4.3 TUI

The TUI is the sole v1 client.

Required views:

- conversation
- live task/delegation progress
- approval prompts
- memory inspection, including “why do you remember this?”

The TUI communicates with the hosted coordinator; it does not directly control workers or own durable memory.

## 5. Enriched Persona

The persona is not represented by an ever-growing `soul.md`. It is a versioned, structured contract with two layers.

### 5.1 Authored Core

The user or administrator explicitly controls:

- values and principles
- hard behavioral boundaries
- communication tone
- emotional range
- initiative level
- disagreement and correction style
- safety rules
- external-action limits

The system must not autonomously rewrite this layer.

### 5.2 Learned Adaptation

The system may learn, with provenance:

- communication preferences
- recurring workflows
- preferred degree of detail
- project context
- soft interaction patterns
- contextual preferences

This layer can evolve, but must remain inspectable, reversible, and subordinate to the authored core.

## 6. Memory and Context

### 6.1 Memory Model

Use one unified memory service with multiple retrieval methods:

- **Raw event store:** conversation turns, task events, tool calls, and artifacts.
- **Structured records:** facts, preferences, projects, relationships, decisions, commitments.
- **Lexical index:** exact search for names, code identifiers, dates, and phrases.
- **Vector index:** semantic recall across conversations and documents.
- **Trust/provenance metadata:** source, timestamp, confidence, user confirmation, freshness, sensitivity, and contradiction state.
- **Optional associative layer:** a future holographic/HRR-style retrieval index, only if testing proves it adds value.

Holographic retrieval should not be required for the MVP. It may later augment hybrid retrieval; it should not replace structured, lexical, or vector search.

### 6.2 Memory Write Pipeline

The local model is the default memory-processing worker.

For every conversation or completed task:

1. Persist the raw event.
2. Extract candidate memories asynchronously.
3. Classify type, sensitivity, confidence, and retention policy.
4. Deduplicate against existing records.
5. Detect possible contradictions.
6. Generate embeddings and retrieval summaries.
7. Promote or queue memories according to policy.

The main OpenRouter model is used only for nuanced or low-confidence cases: complex decisions, uncertain relationship inference, or semantic conflicts the local model cannot resolve.

### 6.3 Context Compiler

Before a main-model request, a local context compiler produces a compact briefing from the task and relevant memory.

Suggested initial limits:

- persona kernel: 500–800 tokens
- retrieved briefing: 1,000–1,500 tokens
- bounded number of source memories
- raw transcripts excluded unless explicitly required

The briefing includes only relevant stable persona rules, active project context, pertinent prior decisions, and user preferences.

## 7. Models

### 7.1 Main Model

- Single provider in v1: OpenRouter.
- Used for conversation, planning, coordinator reasoning, and final synthesis.
- Accessed through a narrow internal provider adapter.
- The rest of the system must not depend directly on OpenRouter-specific APIs.

### 7.2 Local Model

Used for low-cost asynchronous work:

- memory extraction
- classification
- sensitivity detection
- deduplication
- reranking
- summarization
- context briefing generation

If the local model is unavailable, the system should preserve raw events and defer non-critical memory processing rather than block the conversation.

## 8. Technology Decisions

- **Language and APIs:** Python 3.12+ with FastAPI for the hosted coordinator and worker control plane.
- **User interface:** a Textual/Rich TUI for conversations, task progress, approvals, and memory inspection.
- **Canonical database:** MariaDB stores tenants, users, workers, tasks, leases, approvals, structured memory, and audit events.
- **Vector database:** Qdrant stores embeddings and retrieval metadata. Every vector must include tenant and visibility metadata; MariaDB remains the source of truth for authorization and memory records.
- **Artifact storage:** S3-compatible object storage such as MinIO in development and S3-compatible hosted storage in production.
- **Model providers:** OpenRouter is the only main-model provider in v1, behind an internal adapter. A local-model adapter handles asynchronous memory and context processing.

PostgreSQL and pgvector are explicitly out of scope for the MVP. Qdrant tenant filtering is a retrieval constraint, not the sole authorization boundary; the coordinator must validate access against MariaDB before returning a memory or artifact.

## 9. Worker Network

### 9.1 Worker Properties

Each worker has:

- worker identity and mTLS certificate
- tenant and ownership assignment
- declared capabilities
- versioned runtime metadata
- health status
- execution quotas
- task lease state

Workers receive only task-specific context and capabilities. They do not receive unrestricted user history, broad credentials, or the complete persona contract.

### 9.2 Worker Protocol

Initial authenticated endpoints:

- `GET /health` — liveness/readiness
- `POST /register` — authenticated registration and capability advertisement
- `POST /lease` — request or receive a bounded task lease
- `POST /events` — heartbeat, progress, and audit events
- `POST /complete` — structured result and artifact references
- `POST /renew` — extend an active lease
- `POST /cancel` — stop an active task

Task packets include task ID, tenant ID, objective, compact context briefing, allowed capabilities, deliverable contract, deadline, and task lease.

No recursive worker-to-worker delegation in v1.

## 10. Security and Approvals

The policy engine evaluates every action based on:

- actor: coordinator, worker, or user
- tenant and ownership
- worker identity
- declared and granted capability
- execution environment
- data classification
- requested action impact
- approval state

Initial default policy:

- read access: explicitly scoped
- writes: approval required by default
- command execution: approval required unless pre-authorized
- network access: approval required unless pre-authorized
- external messaging: always requires approval
- credentials: short-lived, scoped leases; never broad environment access

## 11. Audit and Observability

Append-only events record:

- conversation received
- context compiled
- model request
- task created
- worker selected
- task leased
- worker progress
- tool invocation
- approval requested/granted/denied
- artifact produced
- memory candidate created/promoted/deleted
- task completed, cancelled, or failed

The user should be able to inspect why a response, delegation choice, or memory write occurred.

## 12. Explicit Non-Goals for MVP

Do not build these in v1:

- multiple user channels
- desktop or web UI
- plugin marketplace
- broad integrations
- autonomous scheduling
- browser automation
- recursive delegation
- self-modifying persona core
- unrestricted remote tool execution
- a mandatory holographic memory backend

## 13. MVP Acceptance Criteria

The MVP is complete when it can demonstrate:

1. A user holds a multi-turn TUI conversation with a stable, coherent persona.
2. The agent remembers approved project context in a later session.
3. The TUI explains why a memory exists and allows deletion.
4. The coordinator delegates repo inspection to a registered remote worker over mTLS.
5. The worker receives only task-scoped context and explicit read capability.
6. The coordinator returns a synthesized, auditable result.
7. A write, network, command, or external-message attempt triggers the defined approval policy.
8. Context sent to the main model stays within configured persona and briefing budgets.
