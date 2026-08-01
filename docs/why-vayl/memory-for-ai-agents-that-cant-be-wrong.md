---
description: >-
  Vayl is memory for AI agents where a wrong answer has consequences — one live
  value per fact, real removal, and a provable history.
---

# Memory for AI agents that can't be wrong

**Vayl is memory for AI agents where a wrong answer has consequences.** Most memory layers _accumulate_ — they save every fact and later hand your agent a stale one. Vayl **reconciles**: a new value supersedes the old, "we dropped X" actually removes X, ambiguous input is flagged instead of guessed, and every change lands on a signed, tamper-evident audit trail. It speaks the Model Context Protocol, so any agent — Claude, Cursor, your own — plugs in.

## Why "remembering more" is the wrong goal

An agent doesn't fail because it forgot. It fails because it confidently returned something that _used to be true_. Additive and vector memories keep every version of a fact and let similarity search pick one — so after a few updates, a stale value can rank as "current." The failure is silent: a confident, wrong answer with no signal that it's wrong.

## What Vayl guarantees instead

* **One live value per fact.** A structural invariant — at most one active value per `(subject, scope)` — so "what's true now" is unambiguous. Even a small model can't leave two contradictory values live; the engine won't store them.
* **Removal is first-class.** "We dropped Sentry" _retracts_ it. It never comes back as current.
* **The past is still there.** Superseded facts move to history, queryable on demand — not returned by accident.
* **Every change is provable.** A hash-chained, Ed25519-signed audit trail; `verify_audit` pinpoints any edit, reorder, or deletion.

| Property                          | Vayl                                                                        |
| --------------------------------- | --------------------------------------------------------------------------- |
| Silently-wrong on the messy suite | **0%** _(single-run, author-written — reproduce it yourself)_               |
| Cost per remembered fact          | **\~2 LLM calls, no graph database**                                        |
| Storage                           | one **SQLite** file by default; **Postgres** for multi-writer scale         |
| Runs                              | **locally** — no telemetry; the only outbound call is the LLM you configure |

## Not for you if

You need broad document Q\&A over a static corpus (that's RAG), or a knowledge graph for deep multi-hop relationship queries (a dedicated graph reads those faster). Vayl is _reconciling state memory_ — it keeps changing facts current, and composes with those tools rather than replacing them.

## Next steps

<table data-view="cards"><thead><tr><th></th><th></th><th data-hidden data-card-target data-type="content-ref"></th></tr></thead><tbody><tr><td><h4><i class="fa-bolt" style="color:$primary;">:bolt:</i> Quickstart</h4></td><td>Store your first reconciled memory in about 5 minutes.</td><td><a href="../getting-started/quickstart.md">quickstart.md</a></td></tr><tr><td><h4><i class="fa-clock-rotate-left" style="color:$primary;">:clock-rotate-left:</i> Why memory goes stale</h4></td><td>The root cause behind stale answers, and the fix.</td><td><a href="why-your-agents-memory-returns-stale-facts.md">why-your-agents-memory-returns-stale-facts.md</a></td></tr><tr><td><h4><i class="fa-book" style="color:$primary;">:book:</i> Core concepts</h4></td><td>The same-slot invariant and how reconciliation works.</td><td><a href="../core-concepts/core-concepts.md">core-concepts.md</a></td></tr></tbody></table>
