# Astra Agent High-Level Design

## Purpose

Astra Agent is a hosted, multi-tenant Distributed Enriched-Persona Agent (D.E.P.A.). It combines
a user-facing conversational control plane with independently authenticated Astra Remote Agents
(ARA). Astra Agent owns durable state, policy decisions, context assembly, delegation, and audit;
an ARA receives only a bounded task and explicit capabilities.

## Design Principles

- MariaDB is authoritative for durable state and authorization.
- Persona and memory context are compiled, not accumulated into an unbounded prompt.
- ARA delegation is task-scoped, capability-scoped, tenant-scoped, and lease-bound.
- Impactful actions are default-deny or approval-gated.
- Raw user input is persisted before optional model, memory, vector, or ARA work.
- Qdrant ranks authorized memories but never decides authorization.
- MinIO/S3 stores bytes; MariaDB owns artifact metadata and access decisions.
- Important transitions append audit events.

## System Context

```mermaid
flowchart LR
    User[User] -->|Conversation, approvals, memory review| TUI[Astra Textual TUI]
    TUI -->|OIDC bearer token| Agent[Astra Agent API]
    IdP[OIDC Identity Provider] -->|JWT and JWKS| Agent

    Agent -->|Main-model requests| OpenRouter[OpenRouter]
    Agent -->|Memory extraction, optional| LocalLLM[Local LLM / Ollama]

    Agent -->|Authoritative state and authorization| MariaDB[(MariaDB)]
    Agent -->|Authorized vector ranking| Qdrant[(Qdrant)]
    Agent -->|Presigned upload and verified objects| ObjectStore[(S3 / MinIO)]

    ARA[ARA Repository Agent] -->|mTLS, lease polling, progress, result| Ingress[ARA mTLS Ingress]
    Ingress -->|Verified tenant and ARA identity| Agent
    ARA -->|Read-only bounded inspection| Repo[(Configured Repository)]
```

## Runtime Components

```mermaid
flowchart TB
    subgraph Client[Client Layer]
        TUI[Astra TUI]
    end

    subgraph ControlPlane[Astra Agent Control Plane]
        Auth[OIDC User Auth]
        API[FastAPI Routes]
        Conversation[Conversation Orchestrator]
        Compiler[Context Compiler]
        Planner[Delegation Decision]
        Policy[Capability and Approval Policy]
        Registry[ARA Registry and Leasing]
        Memory[Memory Pipeline]
        Artifacts[Artifact Service]
        Audit[Append-only Audit Boundary]
    end

    subgraph Providers[Provider Adapters]
        MainModel[OpenRouter Adapter]
        LocalModel[Local Model Adapter]
        Vector[Qdrant Adapter]
        S3[S3 Adapter]
    end

    subgraph Persistence[Persistence]
        DB[(MariaDB)]
        QD[(Qdrant)]
        OBJ[(Object Storage)]
    end

    subgraph Remote[Remote Execution]
        MTLS[mTLS Ingress]
        ARA[Astra Remote Agent]
    end

    TUI --> Auth --> API
    API --> Conversation
    Conversation --> Compiler
    Conversation --> Planner
    Planner --> Policy
    Planner --> Registry
    Conversation --> MainModel
    Conversation --> Memory
    Memory --> LocalModel
    Memory --> Vector --> QD
    Artifacts --> S3 --> OBJ
    Registry --> DB
    Conversation --> DB
    Policy --> DB
    Memory --> DB
    Audit --> DB
    ARA --> MTLS --> Registry
```

## Conversation Flow

The current conversation route persists the user message first, runs best-effort memory
processing, compiles bounded context, optionally delegates repository inspection, invokes the
main model, and persists the assistant response.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant T as Astra TUI
    participant A as Astra Agent
    participant DB as MariaDB
    participant M as Memory Pipeline
    participant Q as Qdrant
    participant R as ARA
    participant L as OpenRouter

    U->>T: Submit message
    T->>A: POST conversation message + OIDC token
    A->>DB: Persist raw user message and audit event
    A->>M: Extract memory candidates (best effort)
    M->>DB: Deduplicate and persist candidates/promotions
    opt Promoted memory
        M->>Q: Upsert retrieval vector
    end
    A->>DB: Load authorized promoted memories and recent history
    A->>Q: Rank only MariaDB-authorized memory IDs
    Q-->>A: Ranked allowed IDs
    A->>A: Compile bounded persona/memory briefing
    opt Explicit repository inspection request
        A->>DB: Create read-only task
        R->>A: Pull bounded lease over mTLS
        R->>R: Inspect configured repository
        R->>A: Report progress and structured findings
        A->>DB: Persist task result and audit events
    end
    A->>L: Briefing + bounded history + optional ARA findings
    L-->>A: Assistant response
    A->>DB: Persist assistant message and audit event
    A-->>T: Conversation turn response
    T-->>U: Render answer and task state
```

### Current behavior

- **Implemented**: raw user message persistence precedes optional processing.
- **Implemented**: recent history is bounded by configuration.
- **Implemented**: explicit repository inspection language triggers read-only ARA delegation.
- **Implemented**: completed ARA findings are supplied to the model for synthesis.
- **Partial**: delegation planning is deterministic keyword matching, not a general typed planner.
- **Partial**: memory processing is best-effort in the request path, not yet a durable background
  queue.

## ARA Delegation And Leasing

```mermaid
stateDiagram-v2
    [*] --> Pending: Astra Agent creates task
    Pending --> Leased: Eligible ARA atomically claims lease
    Leased --> Leased: Progress and lease renewal
    Leased --> Pending: Lease expires and task is reclaimable
    Leased --> Completed: Verified result accepted
    Leased --> Failed: Structured failure accepted
    Leased --> Cancelled: Cancellation accepted
    Completed --> [*]
    Failed --> [*]
    Cancelled --> [*]
```

An ARA is eligible only when tenant ownership and required capabilities match. Every progress,
renewal, approval, artifact, and completion operation validates tenant ID, ARA ID, task ID, lease
ID, task state, and lease expiry within the persistence boundary.

The implemented repository ARA:

- Reads only under an explicitly configured repository root.
- Ignores symlink escapes, large files, binary files, VCS data, dependencies, and build output.
- Executes no commands and uses no network tools.
- Returns repository inventory plus file and line evidence.

## Security And Trust Boundaries

```mermaid
flowchart LR
    subgraph UserZone[User Trust Zone]
        TUI[Astra TUI]
    end

    subgraph IdentityZone[Identity Zone]
        KC[OIDC Provider / Keycloak in development]
    end

    subgraph HostedZone[Hosted Control Plane]
        API[Astra Agent API]
        DB[(MariaDB Authorization)]
        QD[(Qdrant Retrieval)]
        OBJ[(S3 Objects)]
    end

    subgraph ARAZone[ARA Execution Zone]
        ARA[ARA]
        FS[(Scoped Repository)]
    end

    TUI -->|JWT: issuer, audience, expiry, tenant| API
    KC -->|JWKS| API
    ARA -->|Client certificate| MTLS[mTLS Ingress]
    MTLS -->|Verified Astra headers + proxy secret| API
    API -->|Authorize every record| DB
    API -->|Allowed IDs + tenant + visibility| QD
    API -->|Presigned URL after DB authorization| OBJ
    ARA -->|Explicit file.read scope| FS
```

Trust rules:

- User JWTs are signature, issuer, audience, expiry, subject, and tenant validated.
- ARA certificates encode tenant ID in `OU` and ARA ID in `CN`; nginx validates the certificate
  and forwards identity through protected headers.
- Astra Agent must not be network-reachable around the mTLS ingress in production.
- Direct development headers are explicitly non-production.
- Artifact completion verifies tenant/task key prefix, existence, size, media type, and SHA-256
  metadata.

## Secure Local Deployment

```mermaid
flowchart TB
    Host[Developer Host]

    subgraph Compose[Astra Agent Docker Compose]
        Agent[Astra Agent :8000]
        Ingress[ARA nginx mTLS :8443]
        Keycloak[Keycloak :8080]
        Maria[(MariaDB :3306)]
        Qdrant[(Qdrant :6333)]
        MinIO[(MinIO :9000 / Console :9001)]
        Ollama[Ollama :11434 - optional]
    end

    Host --> Agent
    Host --> Ingress
    Host --> Keycloak
    Agent --> Maria
    Agent --> Qdrant
    Agent --> MinIO
    Agent -. optional .-> Ollama
    Ingress --> Agent
    Keycloak --> Agent
```

The bundled Keycloak account, generated CA, and development secrets are demo assets. Production
requires managed identity, managed PKI, secret rotation, HTTPS for user traffic, network policy,
backups, monitoring, and restore testing.

## Failure Behavior

| Dependency | Current behavior |
|---|---|
| MariaDB | Authoritative dependency; durable routes cannot proceed safely without it. |
| OpenRouter | Model failure is audited and returned as a provider failure; raw user input remains. |
| Local model | Extraction falls back to conservative deterministic rules. |
| Qdrant | Memory processing is best effort; lexical fallback is available for context ranking. |
| MinIO/S3 | Artifact upload/download operations fail; conversation without artifacts remains usable. |
| ARA unavailable | Delegated task remains pending and is visible in task progress. |
| ARA lease expires | Task becomes reclaimable by an eligible ARA. |

## Planned Evolution

The next architecture milestone introduces durable background jobs for memory extraction and
vector synchronization, ARA heartbeat/offline state and cancellation observation, authenticated
artifact downloads, richer typed planning, metrics, and production operations. See
[`../prompt.md`](../prompt.md) for the active continuation brief.
