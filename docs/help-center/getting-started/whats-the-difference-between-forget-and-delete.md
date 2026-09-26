---
description: Both remove a fact — one keeps history, one erases it.
icon: code-compare
---

# What's the difference between forget and delete?

Both stop a fact from being returned as current, but they differ in what they keep:

* **`forget`** retracts a fact but **keeps it in history**. The current answer no longer includes it, yet an audit or a history query can still see that it was once true. Use it for correctness.
* **`delete`** **permanently erases** a subject, history included — the GDPR right to be forgotten. It also redacts the value from decision snapshots and issues a signed erasure receipt. Use it for privacy.

If in doubt, `forget`. Reach for `delete` only when the data itself must be gone.
