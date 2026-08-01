# Coordinator Local Tool Execution

## Status

**Planned** — design document. Not yet implemented.

## Summary

Today the Astra Agent coordinator can think, remember, and delegate — but it cannot act
directly. Every concrete action (reading a file, searching code, inspecting a repository)
must be serialized through an ARA task queue and a remote worker's polling loop. This was
the right MVP constraint, but it creates an artificial floor on what the agent can do
without building a new ARA type for every capability.

This document describes making the coordinator a **first-class tool executor** that shares
the same capability model as ARAs. The coordinator runs tools locally where it can, and
delegates to ARAs where it must — for remote execution, strong isolation, or specialized
hardware. The model drives the loop iteratively rather than receiving a single
pre-assembled context.

## Motivation

### What's wrong with delegation-only

The current `ConversationOrchestrator.respond()` flow:

1. Persist user message
2. Run memory pipeline (best-effort)
3. Compile context briefing
4. If `should_delegate()` matches keywords → create task → poll ARA → inject result
5. **Single** model call → response

This means:

- **The model has no agency.** It can't decide to look at a file, run a diagnostic, or
  chain multiple operations. It gets one shot at a response with whatever context the
  coordinator pre-assembled.
- **Adding a new capability means building a new ARA type.** Want to check a log? New
  ARA. Query an API? New ARA. Every tool lives behind a task queue and a polling loop.
- **The keyword trigger is fragile.** `should_delegate()` looks for
  "inspect|research|analyze|review" + "repository|repo|codebase". Anything outside that
  narrow window silently skips delegation with no feedback.
- **No iterative reasoning.** You can't read a file, discover a reference to another
  file, read that one, and synthesize. The model never sees intermediate results.

### What direct tool execution enables

- Read a config file, notice it references another file, read that too — all in one turn
- Run a command, check the exit code, decide whether to retry with different flags
- Search code for a pattern, find matches, inspect the most relevant ones
- Query an API endpoint, parse the response, follow a link in the result
- Combine local inspection with ARA delegation: "Search the repo locally for the auth
  module path, then delegate a deep inspection of that specific path to an ARA"

## Design

### Core idea: the coordinator is a tool-using agent

```
┌─────────────────────────────────────────────────────┐
│                  ConversationOrchestrator            │
│                                                     │
│  ┌──────────┐    ┌──────────────┐    ┌───────────┐  │
│  │  Model   │───▶│ Tool Decision │───▶│ Execute   │  │
│  │  thinks  │◀───│  (model-driven)│   │  tool     │  │
│  └──────────┘    └──────────────┘    └─────┬─────┘  │
│                                            │         │
│                      ┌─────────────────────┤         │
│                      ▼                     ▼         │
│               ┌────────────┐      ┌──────────────┐   │
│               │ Local Tool  │      │  ARA Delegate │   │
│               │ Registry    │      │  (existing    │   │
│               │ (in-process)│      │   protocol)   │   │
│               └────────────┘      └──────────────┘   │
│                      │                     │         │
│                      ▼                     ▼         │
│               ┌────────────┐      ┌──────────────┐   │
│               │ Policy     │      │  Task Queue   │   │
│               │ Evaluation │      │  + Lease Poll │   │
│               └────────────┘      └──────────────┘   │
│                      │                     │         │
│                      ▼                     ▼         │
│               ┌────────────┐      ┌──────────────┐   │
│               │ Audit      │      │  Result back  │   │
│               │ Event      │      │  into loop    │   │
│               └────────────┘      └──────────────┘   │
└─────────────────────────────────────────────────────┘
```

The model receives tool definitions as part of its system context (same capability
types the ARAs use). It decides which tool to invoke, the coordinator executes it
locally or dispatches to an ARA, the result feeds back into the conversation, and the
model decides the next step. This continues until the model emits a final user-facing
response.

### The tool loop

```
while not done:
    model_response = await model.complete(messages, tools)

    if model_response has text for user:
        break  → persist and return

    if model_response has tool_calls:
        for each tool_call:
            capability = resolve_capability(tool_call)
            policy.check(capability, tenant, scope)
            if requires_approval(capability):
                approval = await request_approval(...)
                if denied → skip with audit event

            if tool_call.target == "local":
                result = await local_registry.execute(tool_call)
            elif tool_call.target == "ara":
                result = await delegate_to_ara(tool_call)

            audit_event(tool_call, result, approval)
            messages.append(tool_result_message(result))

        continue loop
```

### Local tool registry

Local tools are in-process (or subprocess) executors registered at startup, each with
an associated capability requirement:

```python
class LocalTool(Protocol):
    name: str
    description: str          # For the model's tool definitions
    capability: Capability    # e.g., FILE_READ:scope=/workspace/**
    parameters: dict          # JSON Schema for the model

    async def execute(self, params: dict, tenant_id: UUID) -> ToolResult:
        ...
```

Initial local tools (matching what the coordinator's host can already do):

| Tool | Capability | Scope |
|------|-----------|-------|
| `read_file` | `FILE_READ` | Path-restricted to workspace |
| `write_file` | `FILE_WRITE` | Path-restricted, approval-gated |
| `search_files` | `FILE_READ` | Path-restricted to workspace |
| `run_command` | `COMMAND_EXECUTE` | Approval-gated, timeout-bounded |
| `web_request` | `NETWORK_ACCESS` | URL allowlist or approval-gated |

A local tool that needs write, command, network, or credential access that exceeds
configured policy triggers the approval path (same as ARA approval requests today).

### ARA as a tool target

From the coordinator's perspective, an ARA-backed tool is a tool like any other — it
just targets a remote execution node instead of the local registry. The coordinator:

1. Creates a `Task` with the required capability and bounded context
2. Waits for an eligible ARA to lease and complete it
3. Returns the result to the model loop

The difference from today: the model *decides* to delegate, and the delegation is
one step in a potentially multi-step reasoning chain, not the entire pre-model phase.

### Policy integration

Every tool invocation — local or ARA — passes through the policy engine before
execution:

```
tool_call
    → resolve Capability from tool definition
    → policy.evaluate(
        actor=ActorType.COORDINATOR,
        tenant_id=tenant_id,
        capability=capability,
        scope=tool_call.params
      )
    → allowed / denied / requires_approval
```

- **Allowed**: execute immediately, audit
- **Denied**: skip tool, inject error into model context, audit
- **Requires approval**: create Approval record, wait for user decision, execute or skip

This is the same policy path ARAs use today (`POST /api/v1/aras/approvals`) but
generalized so the coordinator itself is also a policy subject.

### Approval flow for local tools

When a tool invocation requires user approval:

1. Coordinator creates an `Approval` record (PENDING)
2. Coordinator injects a special response into the conversation stream, visible in the
   TUI: "I need approval to run `rm -rf /data/cache`. [Approve / Deny]"
3. The TUI renders this as an interactive prompt
4. User grants or denies
5. If granted: tool executes normally, result feeds back to model
6. If denied: audit event recorded, error injected into model context

This is equivalent to the ARA approval path but the approval-dialog surface is the
conversation itself rather than a separate task-monitoring view.

### What this replaces

| Removed | Replaced by |
|---------|-------------|
| `ConversationOrchestrator.should_delegate()` | Model decides tool use |
| Single `model_provider.complete()` call | Iterative tool loop |
| Keyword-based delegation trigger | Model-driven capability selection |
| Hardcoded ARA-only execution path | Unified tool dispatch (local + ARA) |

### What stays unchanged

- **ARA protocol**: register, lease, progress, complete, cancel, renew — identical
- **Capability types**: `FILE_READ`, `FILE_WRITE`, `COMMAND_EXECUTE`, `NETWORK_ACCESS`,
  `CREDENTIAL_ACCESS`, `EXTERNAL_MESSAGE` — unchanged, now also consumed by local tools
- **Task/Lease model**: unchanged for ARA dispatch
- **Approval model**: unchanged, now also triggered by local tool calls
- **Audit events**: unchanged pattern, new event types for local tool calls
- **No recursive delegation**: ARAs remain leaf nodes
- **Memory pipeline**: unchanged (still best-effort, still separate from tool loop)
- **Context compiler**: still produces the initial bounded briefing; tool results are
  appended as additional messages

### New audit events

```
tool.local.invoked        — local tool execution started
tool.local.completed      — local tool returned result
tool.local.denied         — policy denied a local tool call
tool.approval_requested   — local tool triggered approval prompt
tool.approval_granted     — user approved
tool.approval_denied      — user denied
```

Existing ARA events (`task.created`, `task.leased`, `ara.progress`, `task.completed`,
etc.) remain unchanged.

### Failure modes

| Scenario | Behavior |
|----------|----------|
| Local tool raises exception | Inject error into model context, audit, continue loop |
| ARA unavailable | Task stays PENDING, inject status into model context, model can retry or work around |
| ARA lease expires mid-execution | Task becomes reclaimable, coordinator detects timeout, injects status |
| Policy denies a tool call | Inject denial reason into model context, model adapts (or asks user) |
| Model requests capability it doesn't have | Coordinator injects available-tools reminder into context |
| Loop exceeds max iterations | Force model to respond with whatever context it has, audit warning |

### Configuration

```python
# New settings (in addition to existing)
tool_loop_max_iterations: int = 10        # Prevent infinite loops
tool_loop_timeout_seconds: int = 120      # Total time budget for a turn
local_tools_enabled: set[CapabilityKind]   # Which capabilities are available locally
approval_required_for: set[CapabilityKind] # Which always require user approval
ara_tools_enabled: bool = True             # Enable/disable ARA delegation entirely
```

## Revised Conversation Flow

```
User message
    │
    ▼
1. Persist raw message + audit event ─────────────── (unchanged)
2. Memory pipeline (best-effort) ─────────────────── (unchanged)
3. Compile initial bounded context briefing ──────── (unchanged)
    │
    ▼
4. TOOL LOOP (new)
    │
    ├── Model receives: briefing + history + tool definitions + available ARAs
    │
    ├── Model responds with:
    │   ├── text for user → exit loop, go to step 5
    │   └── tool_call(s) →
    │       ├── resolve capability
    │       ├── policy check
    │       ├── approval if required
    │       ├── dispatch: local registry OR ARA delegate
    │       ├── audit event
    │       └── feed result back → continue loop
    │
    ▼
5. Persist assistant message + audit event ───────── (unchanged)
6. Return to user
```

## What architecture.md Should Reflect After Implementation

Once implemented, the following sections of `docs/architecture.md` need updating:

1. **System Context diagram** (line 23-39): The coordinator now reaches directly into
   the local filesystem and can execute commands; the "ARA-only" execution path becomes
   one of several. The diagram should show the coordinator with local tool access
   alongside the ARA path.

2. **Runtime Components diagram** (line 44-97): Add a `Local Tool Registry` and
   `Tool Dispatch` component. The `Planner` (currently "Delegation Decision") expands
   into a general tool-routing layer that dispatches to either local tools or the ARA
   registry.

3. **Conversation Flow sequence diagram** (line 105-141): Replace the single model
   call with the iterative tool loop. Show the model making tool decisions, the
   coordinator dispatching locally or to an ARA, and results feeding back. The
   "optional ARA delegation" box becomes "model-directed tool execution."

4. **ARA Delegation and Leasing section** (line 153-178): Clarify that ARA delegation
   is now one execution target among several, not the only one. The leasing model is
   unchanged but the trigger is model-driven rather than keyword-driven.

5. **Failure Behavior table** (line 256-264): Add rows for local tool failures,
   policy denials, and tool-loop timeout/iteration-limit behavior.

6. **Add a new section**: "Local Tool Execution" describing the tool registry, policy
   integration, approval flow, and how local tools and ARAs coexist.

These updates are noted here rather than applied now because `architecture.md`
documents the current implemented system, not planned work. Once the tool loop is
built and tested, the architecture doc should be updated to match reality.

## Relationship to Other Milestones

This milestone should be implemented **before** the "reliable asynchronous operations"
milestone currently listed in `prompt.md`. Rationale:

- The tool loop fundamentally changes `ConversationOrchestrator.respond()`, which is
  the core of the coordinator. Building the async job queue, idempotent extraction, and
  background runner on top of the single-shot orchestrator means reworking them later
  when the tool loop lands.
- The tool loop is the foundation the coordinator is built on. Get the foundation
  right first, then layer async reliability on top.
- It doesn't block progress on the ARA resilience work (heartbeats, lease renewal,
  cancellation observation) — those are ARA-side improvements that integrate cleanly
  with either model.

After this milestone, the async operations milestone can proceed with the tool-loop
orchestrator as its stable foundation.

## Acceptance Criteria

1. The coordinator runs a model-driven tool loop instead of a single `complete()` call.
2. A `read_file` local tool exists with `FILE_READ` capability and path scoping.
3. A `search_files` local tool exists with `FILE_READ` capability and path scoping.
4. A `run_command` local tool exists with `COMMAND_EXECUTE` capability, approval-gated
   by default.
5. Repository inspection requests go through the tool loop: the model decides whether
   to use a local tool or delegate to an ARA.
6. Every tool invocation — local or ARA — produces an audit event.
7. Policy evaluation runs before every tool execution.
8. Write/command/network tools trigger the approval path when not pre-authorized.
9. The ARA protocol, task/lease model, and capability model are unchanged.
10. The TUI renders approval prompts inline in the conversation.
11. Tool-loop timeout and max-iteration limits prevent runaway loops.
12. Existing tests continue to pass; new tests cover the tool loop, local tools,
    policy integration, and approval flow.
13. `should_delegate()` and its keyword matching are removed.
