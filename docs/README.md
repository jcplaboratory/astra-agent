# Astra Agent Design Documents

These documents describe Astra Agent, a Distributed Enriched-Persona Agent (D.E.P.A.), at a
high level. They reflect the current repository and separate implemented behavior from planned
work.

## Documents

- [Astra Agent Architecture](architecture.md): system context, runtime components, conversation
  flow, ARA delegation, trust boundaries, and deployment topology.
- [Memory Architecture](memory.md): memory extraction, persistence, curation, retrieval,
  authorization, provenance, and lifecycle.
- [Why Astra Agent](why-astra-agent.md): current strengths, limitations, and the roadmap toward
  meaningful differentiation.
- [Astra Agent Compared With Hermes Agent](astra-vs-hermes.md): a sourced comparison of their
  core architecture, execution boundaries, context strategy, and especially memory governance.
- [Coordinator Local Tool Execution](coordinator-tool-execution.md): planned — making the
  coordinator a first-class tool executor with a model-driven loop, shared capability model,
  and unified local/ARA dispatch.

## Status Legend

- **Implemented**: present in the repository and covered by tests or a live smoke path.
- **Partial**: usable now, but intentionally narrower than the target architecture.
- **Planned**: design direction, not current product behavior.

The source product specification remains under `sources/` and is immutable reference material.
