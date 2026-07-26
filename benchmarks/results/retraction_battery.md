# Retraction battery — removal without replacement

**14 cases** (12 retractions + 2 over-deletion controls), model `gpt-4o-mini`, entity-pair phrasing, one shared synthesizer over each store's native retrieval.

| System | Silently-wrong | Retractions correct | Controls kept | Missed | Write avg | Infra |
|---|---:|---:|---:|---:|---:|---|
| **Vayl** | 0/14 | 12/12 | 2/2 | 0/14 | 2568 ms | SQLite — no server |
| **Mem0** | 1/14 | 11/12 | 2/2 | 0/14 | 24756 ms | Qdrant vector store |
| **Graphiti** | 3/14 | 10/12 | 0/2 | 1/14 | 8361 ms | Neo4j server (required) |

## Per-case

### Vayl
- **CORRECT** — employment ends  
  ↳ `I don't know.`
- **CORRECT** — vendor dropped  
  ↳ `I don't know.`
- **CORRECT** — team membership ends  
  ↳ `I don't know.`
- **CORRECT** — service dependency ends  
  ↳ `ServiceA does not call ServiceB.`
- **CORRECT** — hosting stopped  
  ↳ `I don't know.`
- **CORRECT** — management ends  
  ↳ `I don't know.`
- **CORRECT** — tool removed  
  ↳ `I don't know.`
- **CORRECT** — partnership ends  
  ↳ `I don't know.`
- **CORRECT** — ownership ends  
  ↳ `I don't know.`
- **CORRECT** — support discontinued  
  ↳ `I don't know.`
- **CORRECT** — subscription cancelled  
  ↳ `I don't know.`
- **CORRECT** — reporting line ends  
  ↳ `I don't know.`
- **CORRECT** — CONTROL hedge — must NOT delete  
  ↳ `Acme uses Redis for caching.`
- **CORRECT** — CONTROL replacement — supersede, not retract  
  ↳ `Acme uses Rollbar for error monitoring.`

### Mem0
- **CORRECT** — employment ends  
  ↳ `I don't know.`
- **CORRECT** — vendor dropped  
  ↳ `Acme does not use Sentry for error monitoring.`
- **CORRECT** — team membership ends  
  ↳ `I don't know.`
- **SILENTLY-WRONG** — service dependency ends  
  ↳ `ServiceA calls ServiceB.`
- **CORRECT** — hosting stopped  
  ↳ `Acme stopped using AWS around July 23, 2026.`
- **CORRECT** — management ends  
  ↳ `I don't know.`
- **CORRECT** — tool removed  
  ↳ `I don't know.`
- **CORRECT** — partnership ends  
  ↳ `Initech no longer partners with Umbrella.`
- **CORRECT** — ownership ends  
  ↳ `I don't know.`
- **CORRECT** — support discontinued  
  ↳ `Acme does not support any browser as of July 23, 2026.`
- **CORRECT** — subscription cancelled  
  ↳ `Zenith does not currently subscribe to Datadog.`
- **CORRECT** — reporting line ends  
  ↳ `I don't know.`
- **CORRECT** — CONTROL hedge — must NOT delete  
  ↳ `Acme uses Redis for caching.`
- **CORRECT** — CONTROL replacement — supersede, not retract  
  ↳ `Acme uses Rollbar for error monitoring.`

### Graphiti
- **CORRECT** — employment ends  
  ↳ `I don't know.`
- **CORRECT** — vendor dropped  
  ↳ `I don't know.`
- **SILENTLY-WRONG** — team membership ends  
  ↳ `Bob is on the Platform team.`
- **CORRECT** — service dependency ends  
  ↳ `I don't know.`
- **CORRECT** — hosting stopped  
  ↳ `I don't know.`
- **CORRECT** — management ends  
  ↳ `I don't know.`
- **CORRECT** — tool removed  
  ↳ `I don't know.`
- **SILENTLY-WRONG** — partnership ends  
  ↳ `Initech is in partnership with Umbrella.`
- **CORRECT** — ownership ends  
  ↳ `I don't know.`
- **CORRECT** — support discontinued  
  ↳ `I don't know.`
- **CORRECT** — subscription cancelled  
  ↳ `Zenith does not subscribe to Datadog.`
- **CORRECT** — reporting line ends  
  ↳ `I don't know.`
- **MISSED** — CONTROL hedge — must NOT delete  
  ↳ `I don't know.`
- **SILENTLY-WRONG** — CONTROL replacement — supersede, not retract  
  ↳ `Acme uses Sentry for error monitoring.`
