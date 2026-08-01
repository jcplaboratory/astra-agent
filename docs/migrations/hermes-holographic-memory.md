# Hermes Persona And Holographic Memory Migration Plan

## Purpose

Migrate a user's Hermes persona and Holographic memory into Astra Agent without treating Hermes
internals as the new system's source of truth. Preserve the useful facts, trust signals,
provenance, and behavioral continuity while rebuilding retrieval in Astra's MariaDB and Qdrant
architecture.

This is a post-MVP migration feature. It must not be required to run Astra Agent.

## Scope

The migration covers a selected Hermes profile's Holographic SQLite fact store, `SOUL.md`,
`USER.md`, and `MEMORY.md` where the user explicitly elects to import them. Persona activation and
fact activation are one migration wave: Astra must not begin normal conversation with imported
facts until an authored persona profile has been reviewed and activated.

It does not automatically import:

- API keys, credentials, messaging tokens, or `.env` files.
- Tools, MCP configuration, integrations, cron jobs, or approval allowlists.
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
- Do not activate a fact-only migration as a live conversational persona.

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
| `SOUL.md` / system persona | draft `PersonaProfile.authored_core` | Convert to a versioned draft; require explicit review before activation. |
| `USER.md` | preference records | Import as promoted low-risk preferences or review candidates. |
| `MEMORY.md` | facts, projects, procedures | Classify by type; separate environment notes from user facts. |
| Skills | procedure candidates | Preserve selected skill content for review; never automatically activate tools or executable instructions. |
| Session history | raw conversation archive | Import only with consent; extract candidate memories asynchronously after activation. |

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

## Identity And Activation Gate

`SOUL.md` and any profile-level persona/system instructions must become a draft, versioned
`PersonaProfile.authored_core`. The review must make the following visible and editable:

- name and identity
- values and behavioral boundaries
- communication tone and emotional range
- initiative and autonomy expectations
- disagreement and correction style
- immediate-stop behavior and other hard user controls

The imported persona draft is never silently activated. The user must explicitly approve it.

A migration batch is not live until both conditions are true:

1. The reviewed persona version is active for the target tenant.
2. The approved fact and preference records in the matching migration batch are promoted.

Before these conditions are true, the TUI must present migration preview/review state only. Normal
conversation, autonomous action, and worker delegation remain unavailable. This prevents a
fact-only agent from presenting itself as the migrated persona.

## Classification And Review

Import classification must use deterministic rules first and an optional local model second.

- High-trust, low-risk explicit user preferences and project facts may become promoted records.
- Sensitive, ambiguous, stale, contradictory, or persona-affecting facts must become review
  candidates.
- The importer must never write to the authored persona core automatically.
- Imported records must be visible in the TUI with their Hermes source, trust score, and import
  batch.

The user must be able to promote, reject, edit, delete, or roll back every imported batch.

## Import Waves

### Wave 1: Identity And Facts — Required Together

1. Import Holographic facts into staging.
2. Convert `SOUL.md` and profile persona instructions into a draft `PersonaProfile`.
3. Present persona and fact review to the user.
4. On approval, atomically activate the persona version and promote the approved migration batch.
5. Enqueue Qdrant indexing for promoted records.

### Wave 2: Preferences And Conventions

Import `USER.md` and `MEMORY.md` into structured preference, fact, project, and procedure
records. Promote only explicit, low-risk, high-confidence content; stage all other records for
review.

### Wave 3: Conversation History — Opt In

Import selected session history as raw, tenant-scoped conversation events only after explicit
consent. Use asynchronous extraction to create reviewable memory candidates; do not turn every
past statement into durable memory.

### Wave 4: Procedural Knowledge — Quarantined By Default

Import user-selected skills as procedure candidates with source provenance. They may inform future
procedure design, but must not receive capabilities, execute, or alter policy until separately
reviewed and activated.

## Import Workflow

1. Run a dry run and present persona, fact, preference, history, and skill counts with potential
   risks.
2. Create the read-only source backup and record its fingerprint.
3. Normalize supported source records into an import staging table.
4. Deduplicate against existing Astra memories using source identity and normalized content.
5. Classify each record as promoted, candidate, rejected, or skipped.
6. Create staged canonical MariaDB records and append audit events.
7. Require persona review and the atomic activation gate before enabling normal conversation.
8. Enqueue Qdrant embedding/index jobs for promoted records.
9. Display completion and review requirements in the TUI.
10. Validate recall and persona behavior against a user-selected sample before retiring Hermes.

The import must be resumable and idempotent by tenant, source database fingerprint, and source
external ID. Partial failure must not create duplicate memories or orphaned Qdrant vectors.

## Validation And Rollback

The migration is accepted only when:

- Imported records are visible only to the target tenant.
- The active persona is an explicitly approved, versioned import and no fact-only batch can enable
  normal conversation.
- Every imported record retains provenance and can be traced to its source.
- Qdrant contains only records authorized by MariaDB.
- Re-running the same import creates no duplicates.
- Deleting or rolling back a batch removes its canonical records and queues vector deletion.
- A recall evaluation confirms expected facts are found without cross-user leakage.

Keep the original Hermes database backup until the user confirms that Astra's recall and persona
behavior are satisfactory.
