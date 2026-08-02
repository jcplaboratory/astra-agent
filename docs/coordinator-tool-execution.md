# Coordinator Tool Execution

## Status

Implemented for bounded local workspace tools and read-only ARA delegation.

## Tool Loop

`ConversationOrchestrator` gives the main model the tools available to the tenant and runs up to
eight tool iterations per conversation turn. Each tool call is persisted as a `ToolInvocation`
before it is dispatched. Tool results are bounded to 12,000 characters before they are returned to
the model as the next loop message.

The currently implemented local tools are:

| Tool | Capability | Behavior |
| --- | --- | --- |
| `read_file` | `file.read:workspace` | Reads a workspace-relative text file with a size limit. |
| `search_files` | `file.read:workspace` | Performs bounded literal search under a workspace-relative path. |
| `run_command` | `command.execute:workspace` | Runs an argv command in the configured container sandbox. |

Local tools are exposed only when the tenant has a configured workspace and policy allows the
capability. `run_command` can require a coordinator approval. The approval references the tool
invocation, pauses the owning turn, and makes the turn pending again when a user grants or denies
the request. The TUI keeps polling the active turn and displays Grant and Deny controls inline in
the conversation as well as in the approval panel.

## ARA Tool Target

The model also receives `delegate_ara` when delegation is enabled. It accepts an `objective` and
optional `context`. The coordinator bounds both values, creates a task requiring
`file.read:repository`, and records the task ID on the ARA-targeted `ToolInvocation`.

The turn is paused while an eligible ARA leases the task. A terminal `finish_task` operation marks
the linked invocation completed or failed and atomically returns the owning turn to pending. The
coordinator then resumes the turn, adds the bounded task result to the model messages, and lets the
model continue the loop.

ARA delegation is read-only repository inspection. ARAs remain leaf workers: they do not receive
the coordinator's local-tool authority and cannot recursively delegate.

## Durable State

`tool_invocations.task_id` is nullable because local invocations have no task. It has a foreign key
to `tasks` and is populated only for ARA dispatch. The task, invocation, turn state, and audit
events are tenant-scoped in the runtime stores.

## Limits

- The tool loop has an eight-iteration maximum.
- Local tool and ARA result messages are capped at 12,000 characters.
- ARA task objective, context, and deliverable fields use the domain model limits; delegation
  context is additionally capped at 12,000 characters.
- A pending delegated task has no coordinator-side completion timeout; it remains visible and
  leaseable until an ARA finishes it.
