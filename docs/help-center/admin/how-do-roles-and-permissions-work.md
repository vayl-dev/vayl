---
description: Roles grant capabilities; scopes confine a principal to memory spaces.
icon: user-shield
---

# How do roles and permissions work?

On `vayl-server`, each **principal** (an API key) has a **role** that grants a set of **capabilities**, and every tool checks the capability it needs (fail-closed).

| Role    | Capabilities                       |
| ------- | ---------------------------------- |
| admin   | read, write, delete, verify, admin |
| member  | read, write, delete, verify        |
| agent   | read, write, verify                |
| viewer  | read, verify                       |
| auditor | verify                             |

Capabilities answer _"may this caller read?"_. **Scopes** answer _"whose memory?"_ — assign a principal `scopes` (a list of `user_id`s) to confine it to specific memory spaces. Without scopes, a key can reach any space, so always scope keys in a multi-tenant deployment.
