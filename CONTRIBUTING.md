# Contributing to Vayl

Thanks for your interest in contributing! Bug reports, docs, tests, new LLM providers, and
reconciliation edge cases are especially welcome.

The full contributor guide — setup, the free-threaded workflow, benchmarks, and the project layout —
lives in the README:

👉 **[README → Contributing](README.md#contributing)**

## TL;DR

```bash
git clone https://github.com/vayl-dev/vayl && cd vayl
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server,postgres]"
pytest            # offline unit tests (deterministic, no LLM/network)
ruff check .      # lint
```

## Good first contributions

New here? These are self-contained and have a clear template to copy — look for the
[`good first issue`](https://github.com/vayl-dev/vayl/labels/good%20first%20issue) label, or start with one of these:

- **Add a slot-schema preset for a domain you know** (legal, devops, real-estate, gaming, recruiting…).
  A preset is one JSON file that gives an agent a declared vocabulary so its memory reconciles reliably.
  Copy an existing one (`src/vayl/presets/coding.json` or `support.json`), add per-preset assertions to
  `tests/test_presets.py`, and it auto-bundles into the wheel. Enable with `VAYL_SLOT_SCHEMA=preset:<name>`.
  This is the single easiest way to contribute.
- **Add a framework adapter.** Wrap the client for another agent framework (LlamaIndex, Pydantic AI,
  AutoGen, …). Python adapters live in `src/vayl/integrations/` and subclass `BaseVaylMemory` — use
  `langgraph.py` as the template; TypeScript adapters are subpath modules under
  `clients/typescript/src/integrations/`. Each exposes the same curated tool surface via `_common`.
- **Docs & setup guides** — a dedicated MCP-client walkthrough (Cursor, Windsurf, Zed), a tutorial, or
  a worked example. Docs live in GitBook, but a Markdown draft in a PR is a great start.

## Ground rules

- Keep the **core at two dependencies** (`mcp`, `cryptography`); anything heavier goes behind an
  optional extra in `pyproject.toml`.
- Unit tests stay **offline and deterministic** — LLM-dependent checks belong in `benchmarks/`.
- New behaviour needs a test, and `ruff check .` must pass.
- The audit hash-chain is a security guarantee: changes under `src/vayl/security/audit.py` need a
  concurrency test (see `tests/test_accountability.py`).
- For anything substantial, **open an issue first** so we can align on approach before a big PR.

By contributing, you agree your contributions are licensed under **Apache-2.0**.

## Reporting issues

- **Bugs & feature requests:** open a [GitHub issue](https://github.com/vayl-dev/vayl/issues).
- **Security vulnerabilities:** please do **not** open a public issue — contact the maintainer
  privately instead. Vayl's threat model and shared-responsibility matrix are in
  [`SECURITY.md`](SECURITY.md).
