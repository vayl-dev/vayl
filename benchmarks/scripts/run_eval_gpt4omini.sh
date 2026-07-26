#!/usr/bin/env bash
# Run Vayl's reconciliation eval on gpt-4o-mini — the number that confirms or breaks the core claim.
# LLM + embeddings both via OpenAI, so nothing else (Ollama etc.) needs to be running.
#
# Usage:
#   export OPENAI_API_KEY=sk-...
#   bash benchmarks/run_eval_gpt4omini.sh
#
# Override the model:  OPENAI_MODEL=gpt-4o bash benchmarks/run_eval_gpt4omini.sh
set -euo pipefail

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "Set your key first:  export OPENAI_API_KEY=sk-..." >&2
  exit 1
fi

cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

rm -f /tmp/vayl_eval_gpt4omini.db /tmp/vayl_eval_gpt4omini.db.key /tmp/vayl_eval_gpt4omini.db.salt

echo "Model: ${OPENAI_MODEL:-gpt-4o-mini}  ·  embeddings: text-embedding-3-small"
echo "Baseline (measured, local): qwen2.5:3b = 22.2% silently-wrong · SmolLM3-3B = 55.6%."
echo "Target for a production model: near 0%."
echo "------------------------------------------------------------------------"

LLM_PROVIDER=openai \
OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}" \
EMBED_BASE_URL="https://api.openai.com/v1" \
EMBED_MODEL="text-embedding-3-small" \
VAYL_ENCRYPT=off \
VAYL_DB=/tmp/vayl_eval_gpt4omini.db \
  "$PY" benchmarks/eval_reconcile.py

echo "------------------------------------------------------------------------"
echo "If the silently-wrong rate is ~0%, that clean scorecard is your demo/README proof."
