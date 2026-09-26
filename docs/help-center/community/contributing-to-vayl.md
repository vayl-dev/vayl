---
description: Report bugs, suggest features, and open pull requests.
icon: hand-holding-heart
---

# Contributing to Vayl

Vayl is open source under Apache-2.0: [github.com/vayl-dev/vayl](https://github.com/vayl-dev/vayl). Bug reports, docs, tests and reconciliation edge cases are especially welcome, and issues labelled [good first issue](https://github.com/vayl-dev/vayl/labels/good%20first%20issue) are a good start.

```bash
git clone https://github.com/vayl-dev/vayl && cd vayl
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,server,postgres]"
pytest && ruff check . && mypy
```

The unit suite runs offline: it blocks network access, so no model or key is needed. New behaviour needs a test and a CHANGELOG entry under Unreleased. Pull requests to `main` need the CI checks and a review.

Report security issues privately, as described in [SECURITY.md](https://github.com/vayl-dev/vayl/blob/main/SECURITY.md), not in a public issue.

Full guides: [Local development](https://vayl.gitbook.io/vayl-docs/documentation/development/local-development) and [Contributing](https://vayl.gitbook.io/vayl-docs/documentation/development/contributing).
