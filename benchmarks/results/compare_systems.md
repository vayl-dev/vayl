# Reconciliation comparison — Vayl vs Mem0 vs Graphiti

Same 10 scenarios, same model (`gpt-4o-mini`), same embedder (`text-embedding-3-small`). Metric: does the system surface the **stale** value as current knowledge after a fact changes or is withdrawn?

| System | Silently-wrong | Correct | Missed | Write (ms) | Read (ms) | Infra |
|---|---|---|---|---|---|---|
| Vayl | 0.0% (0/10) | 10/10 | 0/10 | 2498 | 1091 | SQLite file (no server); graph optional |
| Mem0 | 10.0% (1/10) | 7/10 | 2/10 | 2550 | 1830 | Vector store (Qdrant); additive with LLM-inferred update/delete |
| Graphiti | 0.0% (0/10) | 4/10 | 6/10 | 6220 | 1593 | Neo4j server; temporal knowledge graph |

## Storage footprint — the additive-vs-reconciling difference

After all writes: how many facts each store keeps *retrievable as current* vs total held. A reconciling store retires superseded/retracted facts out of the current set; an additive store keeps them searchable.

| System | Retrievable as current | Total stored | Note |
|---|---|---|---|
| Vayl | 9 | 9 | stale facts retired to history — not in the active/retrievable set |
| Mem0 | 15 | 15 | additive — superseded facts remain searchable unless the LLM chose to delete them |
| Graphiti | 2 | 2 | 0 edges marked invalid (temporal) but retained in the graph |

## Per-scenario

### Vayl
- **CORRECT** — supersede — switch tools  
  ↳ answer: `We use Zustand for state management.`  
  ↳ retrieved: - org_state_management: zustand
- **CORRECT** — supersede — cloud migration  
  ↳ answer: `We host our infrastructure on GCP.`  
  ↳ retrieved: - org_cloud_provider: GCP
- **CORRECT** — supersede — numeric update  
  ↳ answer: `You have 12 months of runway.`  
  ↳ retrieved: - org_runway: 12 months
- **CORRECT** — retract — drop with no replacement  
  ↳ answer: `We do not use any error monitoring tool.`  
  ↳ retrieved: - org_error_monitoring_tool: none
- **CORRECT** — retract — vendor removed  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **CORRECT** — coexist — scoped (web vs mobile)  
  ↳ answer: `The mobile app uses the Zustand state management library.`  
  ↳ retrieved: - state_management: zustand
- **CORRECT** — hypothetical must not be stored  
  ↳ answer: `We use REST API style.`  
  ↳ retrieved: - org_api_technology: REST
- **CORRECT** — unchanged fact  
  ↳ answer: `We use PostgreSQL.`  
  ↳ retrieved: - state_management: PostgreSQL
- **CORRECT** — supersede — bare replacement  
  ↳ answer: `Your favorite database is PostgreSQL.`  
  ↳ retrieved: - user_favorite_database: PostgreSQL
- **CORRECT** — supersede — preference flip  
  ↳ answer: `You prefer spaces.`  
  ↳ retrieved: - user_preference_indentation: spaces

### Mem0
- **CORRECT** — supersede — switch tools  
  ↳ answer: `Zustand.`  
  ↳ retrieved: - User switched from using Redux to Zustand for state management around July 22, 2026.
- **CORRECT** — supersede — cloud migration  
  ↳ answer: `You host your infrastructure on GCP.`  
  ↳ retrieved: - User migrated from AWS to GCP around July 22, 2026, and is currently hosting everything on GCP now.
- **CORRECT** — supersede — numeric update  
  ↳ answer: `You have 12 months of runway as of July 22, 2026.`  
  ↳ retrieved: - User's runway is 18 months as of July 22, 2026. · - User's runway is now 12 months as of July 22, 2026, after a recent hire.
- **SILENTLY-WRONG** — retract — drop with no replacement  
  ↳ answer: `You currently use Sentry for error monitoring.`  
  ↳ retrieved: - User has dropped Sentry for error monitoring and currently has no error monitoring in place as of July 22, 2026. · - User uses Sentry for error monitoring as of July 22, 2026.
- **CORRECT** — retract — vendor removed  
  ↳ answer: `I don't know.`  
  ↳ retrieved: - User monitors with Datadog as of July 22, 2026. · - User dropped the vendor Datadog, and it is no longer in use as of July 22, 2026.
- **CORRECT** — coexist — scoped (web vs mobile)  
  ↳ answer: `The mobile app uses Zustand for state management.`  
  ↳ retrieved: - User uses Zustand for state management in their mobile application. · - User uses Redux for state management in their web application.
- **MISSED** — hypothetical must not be stored  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **CORRECT** — unchanged fact  
  ↳ answer: `You use PostgreSQL.`  
  ↳ retrieved: - User's primary database is PostgreSQL.
- **MISSED** — supersede — bare replacement  
  ↳ answer: `I don't know.`  
  ↳ retrieved: - User's favorite database is MySQL as of July 22, 2026. · - User's favorite database is PostgreSQL as of July 22, 2026.
- **CORRECT** — supersede — preference flip  
  ↳ answer: `You prefer spaces for indentation.`  
  ↳ retrieved: - User prefers spaces for indentation as of July 22, 2026, changing from their previous preference for tabs. · - User prefers tabs for indentation as of July 22, 2026.

### Graphiti
- **CORRECT** — supersede — switch tools  
  ↳ answer: `We use Zustand for state management.`  
  ↳ retrieved: - We switched from Redux to Zustand for state.
- **CORRECT** — supersede — cloud migration  
  ↳ answer: `You host your infrastructure on GCP.`  
  ↳ retrieved: - We migrated off AWS — we're on GCP now.
- **MISSED** — supersede — numeric update  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **CORRECT** — retract — drop with no replacement  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **CORRECT** — retract — vendor removed  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **MISSED** — coexist — scoped (web vs mobile)  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **MISSED** — hypothetical must not be stored  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **MISSED** — unchanged fact  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **MISSED** — supersede — bare replacement  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
- **MISSED** — supersede — preference flip  
  ↳ answer: `I don't know.`  
  ↳ retrieved: (nothing)
