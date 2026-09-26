---
description: Run Vayl fully offline with a local LLM and embedder.
icon: microchip
---

# How do I use a local model instead of OpenAI?

Vayl talks to any OpenAI-compatible endpoint. Point it at a local server (Ollama, vLLM, LM Studio):

```bash
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:3b
```

With no LLM environment variables set at all, Vayl defaults to a local Ollama endpoint — so **no data leaves the machine**. For data residency, point the endpoint at a self-hosted or EU-region deployment; the database on disk stays encrypted either way.
