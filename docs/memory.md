# Astra Agent Memory High-Level Design

## Goals

Astra Agent memory is designed to provide useful, compact, user-controlled context without
turning the entire conversation history into a prompt. A durable memory must be inspectable,
traceable to its source, reviewable, reversible, tenant-isolated, and subordinate to the authored
persona core.

Memory is not a single vector database. It is a service boundary over:

- Raw conversation and audit history in MariaDB.
- Structured memory records in MariaDB.
- Lexical ranking over authorized structured records.
- Optional semantic ranking in Qdrant.
- A local extraction/classification model with deterministic fallback.

## Memory System Context

```mermaid
flowchart LR
    Message[Persisted User Message] --> Extractor[Memory Extractor]
    LocalLLM[Local LLM / Ollama] --> Extractor
    Rules[Deterministic Fallback Rules] --> Extractor

    Extractor --> Candidate[Structured Memory Candidate]
    Candidate --> MariaDB[(MariaDB Memory Records)]
    Candidate --> Audit[(Append-only Audit Events)]

    MariaDB --> Review[User Review and Curation]
    Review -->|Promote / reject / replace / delete| MariaDB
    Review --> Audit

    MariaDB --> Authorization[Authorized Promoted Record Set]
    Authorization --> Lexical[Lexical Ranking]
    Authorization --> Qdrant[(Qdrant Ranking)]
    Lexical --> Compiler[Context Compiler]
    Qdrant --> Compiler
    Compiler --> Briefing[Bounded Persona + Memory Briefing]
```

## Authoritative Data Model

MariaDB stores each `MemoryRecord`. Qdrant stores only a retrieval vector and minimal filtering
metadata; it is not the canonical record.

```mermaid
erDiagram
    CONVERSATION_MESSAGES ||--o{ MEMORY_RECORDS : "source_message_id"
    AUDIT_EVENTS ||--o{ MEMORY_RECORDS : "source_event_id"
    MEMORY_RECORDS o|--o{ MEMORY_RECORDS : "contradiction_of"

    CONVERSATION_MESSAGES {
        uuid id PK
        uuid tenant_id
        uuid conversation_id
        enum role
        text content
        datetime created_at
    }

    MEMORY_RECORDS {
        uuid id PK
        uuid tenant_id
        enum kind
        text content
        text normalized_content
        uuid source_event_id
        uuid source_message_id FK
        float confidence
        boolean confirmed
        enum state
        string sensitivity
        string visibility
        string retention
        uuid contradiction_of FK
        datetime created_at
        datetime updated_at
        datetime reviewed_at
        uuid reviewed_by
        datetime deleted_at
    }

    AUDIT_EVENTS {
        uuid id PK
        uuid tenant_id
        enum event_type
        enum actor_type
        uuid actor_id
        json payload
        datetime occurred_at
    }
```

### Memory kinds

- `fact`: durable factual information explicitly supplied by the user.
- `preference`: communication or workflow preferences.
- `project`: project identity and context.
- `decision`: prior decisions that should constrain future work.
- `commitment`: promises, obligations, or expected follow-up.

### Lifecycle states

```mermaid
stateDiagram-v2
    [*] --> Candidate: Extracted with review required
    [*] --> Promoted: Explicit high-confidence statement
    Candidate --> Promoted: User promotes
    Candidate --> Rejected: User rejects
    Candidate --> Promoted: User resolves contradiction
    Promoted --> Deleted: User deletes
    Promoted --> Deleted: Replaced by contradictory candidate
    Rejected --> [*]
    Deleted --> [*]
```

Only `PROMOTED` and non-deleted records are eligible for context retrieval. Candidates are
inspectable but excluded. Rejected and deleted records remain represented in durable history and
audit, but are not returned by normal memory listing or retrieval.

## Extraction And Initial Classification

```mermaid
sequenceDiagram
    autonumber
    participant C as Conversation Orchestrator
    participant DB as MariaDB
    participant E as Memory Extractor
    participant LM as Local Model
    participant F as Deterministic Fallback
    participant Q as Qdrant

    C->>DB: Persist raw user message and conversation.received event
    C->>E: Process message with source IDs
    opt Local-model extraction enabled
        E->>LM: Strict JSON extraction request
        LM-->>E: kind, content, confidence, promoted
    end
    alt Model output unavailable or invalid
        E->>F: Apply conservative explicit patterns
        F-->>E: Validated extracted memories
    end
    E->>E: Normalize content and classify state
    E->>DB: Tenant-scoped idempotent upsert
    E->>DB: Append candidate/promoted audit events
    opt Record is promoted
        E->>Q: Upsert vector + tenant/visibility payload
    end
```

### Current extraction policy

**Implemented deterministic fallback** recognizes conservative explicit forms:

- `remember that ...` creates a promoted fact.
- `I prefer ...` creates a promoted preference.
- `my project is ...` creates promoted project context.
- `I use ...` creates a candidate requiring review.

**Implemented local-model mode** asks an OpenAI-compatible endpoint for strict JSON records. The
result is validated by kind, content, confidence, and promotion flag. Invalid output falls back
to deterministic extraction.

### Deduplication

Content is case-folded and whitespace-normalized into `normalized_content`. MariaDB enforces a
tenant-scoped unique constraint over `(tenant_id, normalized_content)`.

- Repeated active content returns the existing record.
- A deleted record can be revived using the same normalized key and new provenance.
- Tenant A can never deduplicate against or discover tenant B's record.

## Review And Curation

```mermaid
flowchart TB
    Candidate[Candidate Memory] --> Inspect[Inspect content, confidence, source message]
    Inspect --> Promote{Review decision}
    Promote -->|Promote| Active[Promoted and retrievable]
    Promote -->|Reject| Rejected[Rejected and not retrievable]
    Promote -->|Replace contradiction| Replace[Promote candidate]
    Replace --> Link[Set contradiction_of]
    Link --> Retire[Soft-delete prior promoted memory]
    Active --> Delete[User deletion]
    Delete --> Retired[Soft-deleted and not retrievable]

    Promote --> Audit[Append reviewer and event]
    Replace --> Audit
    Delete --> Audit
```

Review operations are tenant-scoped and transactional in MariaDB:

- Only a `CANDIDATE` can be reviewed.
- Promotion sets `confirmed`, `reviewed_at`, and `reviewed_by`.
- Rejection records the reviewer and excludes the record from retrieval.
- Contradiction replacement locks both records, promotes the candidate, links
  `contradiction_of`, and soft-deletes the previous promoted record atomically.
- A repeated or cross-tenant review fails rather than silently changing state.

The TUI currently shows recent memories and provides promote/reject controls for the first
pending candidate. The API also supports explicit contradiction replacement and provenance
explanation.

## Retrieval And Authorization

Retrieval follows an authorization-first design.

```mermaid
sequenceDiagram
    autonumber
    participant C as Context Compiler
    participant DB as MariaDB
    participant Q as Qdrant

    C->>DB: List tenant's promoted, non-deleted records
    DB-->>C: Authorized memory records
    C->>C: Keep allowed visibility and build allowed ID set
    C->>Q: Query vector + tenant + visibility + allowed IDs
    Q-->>C: Ranked point IDs
    C->>C: Intersect returned IDs with MariaDB-authorized map
    alt No vector result or Qdrant unavailable
        C->>C: Rank authorized records lexically
    end
    C->>C: Apply record count and token budget
    C-->>C: Return briefing and source_memory_ids
```

### Why MariaDB authorization comes first

Qdrant payload filters are defense in depth, not authorization. A compromised, stale, or
misconfigured vector index could return arbitrary point IDs. Astra Agent therefore:

1. Loads promoted records for the tenant from MariaDB.
2. Builds an allowed ID map.
3. Sends tenant, visibility, and allowed IDs to Qdrant.
4. Discards every returned ID not already present in the allowed map.

This invariant is covered by a malicious-vector-result test.

## Context Compilation

```mermaid
flowchart LR
    Persona[Stable Persona Kernel] --> Budget[Context Budget]
    Objective[Current Objective] --> Budget
    Memory[Ranked Approved Memories] --> Budget
    Budget --> Truncate[Bounded Character / Token Estimate]
    Truncate --> Briefing[System Briefing]
    Briefing --> Model[Main Model]
    Recent[Bounded Recent Messages] --> Model
```

The compiled system briefing includes:

- Stable persona rules.
- Relevant promoted memories with memory kind.
- The current objective.
- Source memory IDs in the audit event for explainability.

Raw historical transcripts are not inserted into the briefing. A separately bounded recent
conversation window is sent to the main model.

## Deletion Semantics

Deletion is soft in MariaDB and best-effort in Qdrant:

1. Lock and validate the tenant-owned memory record.
2. Set state `DELETED`, `deleted_at`, and `updated_at`.
3. Append `memory.deleted` with the user actor.
4. Request Qdrant point deletion.
5. Exclude the memory immediately because MariaDB no longer authorizes it, even if Qdrant point
   deletion fails.

This ordering prevents stale vectors from re-authorizing deleted memory.

## Audit Events

Memory behavior produces append-only events:

- `memory.candidate_created`
- `memory.promoted`
- `memory.rejected`
- `memory.contradiction_resolved`
- `memory.deleted`
- `context.compiled` with included source memory IDs

Together with `source_event_id`, `source_message_id`, reviewer fields, and contradiction links,
these events answer: “Why do you remember this?”

## Current Limitations

- **Partial**: extraction runs best-effort during the conversation request. Exceptions are
  suppressed after the raw message is durable, but slow extraction can still add latency.
- **Partial**: deterministic embeddings are useful for integration and authorization testing,
  not high-quality production semantic retrieval.
- **Partial**: the TUI review panel is intentionally minimal.
- **Planned**: durable asynchronous extraction/vector jobs with retries and queue visibility.
- **Planned**: stronger sensitivity classifiers, retention enforcement, freshness decay, and
  richer contradiction detection.
- **Planned**: re-embedding/version migration and Qdrant reconciliation jobs.

## Planned Asynchronous Boundary

```mermaid
flowchart LR
    Message[Persisted Message] --> Job[(MariaDB Background Job)]
    Job --> Claim[Transactional Job Claim]
    Claim --> Extract[Local Model Extraction]
    Extract --> Store[Idempotent Memory Upsert]
    Store --> Vector[Qdrant Sync]
    Extract -. failure .-> Retry[Bounded Backoff]
    Vector -. failure .-> Retry
    Retry --> Claim
    Retry -->|attempt limit| Failed[Terminal Visible Failure]
```

The next milestone moves optional memory work behind this durable boundary so conversation
latency and availability do not depend on the local model or Qdrant. See [`../prompt.md`](../prompt.md).
