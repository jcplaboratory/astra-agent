# Hermes Holographic Memory Migration Plan

## Purpose

Migrate a user's Hermes Holographic memory into Astra Agent without treating Hermes internals as
the new system's source of truth. Preserve the useful facts, trust signals, provenance, and review
history while rebuilding retrieval in Astra's MariaDB and Qdrant architecture.

This is a post-MVP migration feature. It must not be required to run Astra Agent.

## Scope

The migration covers a selected Hermes profile's Holographic SQLite fact store and associated
persona/memory files where the user explicitly elects to import them.

It does not automatically import:

- API keys, credentials, messaging tokens, or `.env` files.
- Skills, tools, MCP configuration, integrations, cron jobs, or approval allowlists.
- Hermes internal indexes, cached context, or implementation-specific HRR vectors.
- Project instruction files such as `AGENTS.md`.

## Design Principles

- Require an explicit source path and explicit user confirmation.
- Start with a read-only source snapshot and never mutate Hermes files.
- Preserve original identifiers, timestamps, and source metadata where available.
- Treat imported facts as data to evaluate, not unconditional prompt content.
- Rebuild embeddings in Qdrant; do not copy Hermes HRR or retrieval indexes.
- Keep MariaDB as Astra's authorization and canonical-memory source of truth.
- Make every import record tenant-scoped, auditable, inspectable, and reversible.

## Source Discovery

Hermes Holographic memory is a local SQLite fact store. The importer should accept an explicit
database path rather than assuming an internal schema or a fixed home directory:

```text
astra import hermes-holographic \
  --tenant <tenant-id> \
  --database /path/to/memory_store.db \
  --dry-run
```

Before any import, the command must:

1. Confirm that the source exists and is a readable SQLite database.
2. Create a timestamped, read-only copy in a user-selected backup location.
3. Detect and report the schema version or supported table layout.
4. Report a dry-run inventory: facts, trust-score range, entities, contradictions, and records that
   need review.
5. Refuse to proceed when the schema is unknown instead of guessing field meanings.

## Data Mapping

| Hermes Holographic concept | Astra destination | Migration behavior |
|---|---|---|
| Fact text | `MemoryRecord.content` | Normalize and retain original text as an import source artifact. |
| Fact identifier | `source_external_id` metadata | Preserve for idempotency and traceability. |
| Trust score | `confidence` plus provenance metadata | Map into a documented range; never treat it as proof of truth. |
| Helpful/unhelpful feedback | usefulness metadata | Preserve when available; do not use it as authorization. |
| Entities/relations | structured memory metadata | Import only when schema meaning is known. |
| Contradiction/update links | replacement/contradiction relationship | Preserve links when both records are imported. |
| Creation/update time | source timestamps | Preserve as source metadata rather than overwriting Astra audit time. |
| HRR vectors and FTS indexes | not imported | Recreate retrieval indexes from canonical imported records. |

Every imported record must include:

```text
tenant_id
source_system = "hermes_holographic"
source_profile
source_database_fingerprint
source_external_id
import_batch_id
source_created_at / source_updated_at, when available
```

## Classification And Review

Import classification must use deterministic rules first and an optional local model second.

- High-trust, low-risk explicit user preferences and project facts may become promoted records.
- Sensitive, ambiguous, stale, contradictory, or persona-affecting facts must become review
  candidates.
- The importer must never write to the authored persona core automatically.
- Imported records must be visible in the TUI with their Hermes source, trust score, and import
  batch.

The user must be able to promote, reject, edit, delete, or roll back every imported batch.

## Import Workflow

1. Run a dry run and present counts and potential risks.
2. Create the read-only source backup and record its fingerprint.
3. Normalize supported source records into an import staging table.
4. Deduplicate against existing Astra memories using source identity and normalized content.
5. Classify each record as promoted, candidate, rejected, or skipped.
6. Create canonical MariaDB memory records and append audit events.
7. Enqueue Qdrant embedding/index jobs for promoted records.
8. Display completion and review requirements in the TUI.
9. Validate recall against a user-selected sample before retiring Hermes.

The import must be resumable and idempotent by tenant, source database fingerprint, and source
external ID. Partial failure must not create duplicate memories or orphaned Qdrant vectors.

## Validation And Rollback

The migration is accepted only when:

- Imported records are visible only to the target tenant.
- Every imported record retains provenance and can be traced to its source.
- Qdrant contains only records authorized by MariaDB.
- Re-running the same import creates no duplicates.
- Deleting or rolling back a batch removes its canonical records and queues vector deletion.
- A recall evaluation confirms expected facts are found without cross-user leakage.

Keep the original Hermes database backup until the user confirms that Astra's recall and persona
behavior are satisfactory.

## Future Extension: Full Hermes Profile Migration

After Holographic memory migration is stable, a separate importer may offer selected migration of
`SOUL.md`, `USER.md`, `MEMORY.md`, and conversation history. These sources must follow the same
review and provenance model:

- Convert `SOUL.md` into a draft, versioned authored persona profile that requires approval.
- Convert `USER.md` into high-confidence preference candidates or promoted preferences.
- Convert `MEMORY.md` into classified facts, project context, and procedures.
- Import sessions as raw, consented conversation history; extract memory candidates asynchronously.
