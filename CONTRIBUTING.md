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
pytest            # 475 offline unit tests (deterministic, no LLM/network)
ruff check .      # lint
```

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
