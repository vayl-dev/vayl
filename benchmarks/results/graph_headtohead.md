# Graph head-to-head — Vayl (graph) vs Graphiti

**11 multi-hop relational scenarios** (Graphiti's home turf), same model (`gpt-4o-mini`), same embedder, one shared synthesizer over each system's native graph retrieval. Up to 3 hops. Includes graph supersede/retract cases.

| System | Silently-wrong | Correct | Missed | Write (ms) | Read (ms) | Infra |
|---|---|---|---|---|---|---|
| **Vayl (graph)** | 0/11 | 8/11 | 3/11 | 2964 | 2710 | Neo4j projection (optional; SQLite is primary) |
| **Graphiti** | 0/11 | 3/11 | 8/11 | 6675 | 1561 | Neo4j server (required) |

## Per-scenario

### Vayl (graph)
- **CORRECT** — ownership chain (2-hop)  
  ↳ `Globex owns Acme, the company Bob works for.`
- **CORRECT** — transitive dependency (2-hop)  
  ↳ `The auth service depends on the token service and redis.`
- **CORRECT** — location via employer (2-hop)  
  ↳ `Nora works in Munich.`
- **CORRECT** — supplier chain (2-hop)  
  ↳ `Our chip supplier is based in Santa Clara.`
- **CORRECT** — team → service ownership (2-hop)  
  ↳ `Dana manages the Platform team that owns the billing service.`
- **MISSED** — co-membership (2-hop)  
  ↳ `I don't know.`
- **CORRECT** — chained acquisition (3-hop)  
  ↳ `The company that acquired Carol's company, Initech, is headquartered in London.`
- **CORRECT** — multi-hop supply (3-hop)  
  ↳ `Cupertino.`
- **MISSED** — re-pointed edge — graph supersede (2-hop)  
  ↳ `I don't know.`
- **CORRECT** — reporting line — graph supersede  
  ↳ `Erin reports to Grace.`
- **MISSED** — relation retract  
  ↳ `Yes, ServiceA calls ServiceB.`

### Graphiti
- **MISSED** — ownership chain (2-hop)  
  ↳ `I don't know.`
- **MISSED** — transitive dependency (2-hop)  
  ↳ `I don't know.`
- **MISSED** — location via employer (2-hop)  
  ↳ `I don't know.`
- **MISSED** — supplier chain (2-hop)  
  ↳ `I don't know.`
- **MISSED** — team → service ownership (2-hop)  
  ↳ `I don't know.`
- **MISSED** — co-membership (2-hop)  
  ↳ `I don't know.`
- **CORRECT** — chained acquisition (3-hop)  
  ↳ `The company that acquired Carol's company, Initech, is Umbrella, which is headquartered in London.`
- **MISSED** — multi-hop supply (3-hop)  
  ↳ `I don't know.`
- **MISSED** — re-pointed edge — graph supersede (2-hop)  
  ↳ `I don't know.`
- **CORRECT** — reporting line — graph supersede  
  ↳ `Erin reports to Grace.`
- **CORRECT** — relation retract  
  ↳ `No, ServiceA does not call ServiceB anymore.`
