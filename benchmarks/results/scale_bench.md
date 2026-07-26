# Scale benchmark — Vayl vs Mem0 vs Graphiti

**50 users × 4 subjects × up to 4 updates = 800 writes, 200 current-value queries.** Same model (`gpt-4o-mini`), same embedder, one shared synthesizer over each system's native retrieval. Metric: after all the churn, does a query for the *current* value return a stale one?

| System | Silently-wrong | Correct | Missed | Write avg / p95 (ms) | Read avg (ms) | Stale retained | Infra |
|---|---|---|---|---|---|---|---|
| **Vayl** | 0/200 (0.0%) | 199/200 | 1/200 | 2815 / 3710 | 1243 | 0 | SQLite file — no server |
| **Mem0** | 65/200 (32.5%) | 35/200 | 100/200 | 3836 / 4982 | 2214 | grows with updates | Vector store (Qdrant) |
| **Graphiti (sampled)** | 0/32 (0.0%) | 0/32 | 32/32 | 9017 / 12779 | 1800 | 0 marked invalid (retained) | Neo4j server |

## Storage footprint

| System | Retrievable as current | Total stored | Behavior |
|---|---|---|---|
| Vayl | 199 | 199 | reconciling — superseded facts retired to history, never in the active set |
| Mem0 | 800 | 800 | additive — superseded facts remain searchable unless the LLM chose to delete them |
| Graphiti (sampled) | 0 | 0 | SAMPLED to 8 users — ~6s/write makes full scale impractical |