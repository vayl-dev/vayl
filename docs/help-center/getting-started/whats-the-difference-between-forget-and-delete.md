---
description: Both stop a fact being current. One keeps its history, one erases it.
icon: code-compare
---

# What's the difference between forget and delete?

| | `forget` | `delete` |
| --- | --- | --- |
| What it takes | A sentence, such as `We dropped Sentry.` | A subject key from `list_memories`, such as `monitoring` |
| What happens | The fact is retracted: marked superseded, with a tombstone in history | Every record of the subject is erased, history included |
| History | Kept, so `history` and audits still show it was once true | Gone, and its values are redacted from decision snapshots |
| Receipt | None | A signed erasure receipt, when something was erased |
| Capability | `write` (agents have it) | `delete` (admin and member roles only) |

Use `forget` for correctness and `delete` for privacy, such as a GDPR erasure request. `delete_all` erases a whole user and needs `admin`.

On an approval-gated slot, `forget` doesn't remove anything yet: it replies "Proposed for removal, awaiting approval" and the value stays current until someone approves it with `confirm_change`.

More in [Memory tools](https://vayl.gitbook.io/vayl-docs/documentation/reference/mcp-tools/memory) and [Compliance (GDPR)](https://vayl.gitbook.io/vayl-docs/documentation/reference/mcp-tools/compliance-gdpr).
