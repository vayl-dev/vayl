---
description: Run Vayl fully offline with a local chat model and embedder.
icon: microchip
---

# How do I use a local model instead of OpenAI?

Set no model variables at all. Vayl then talks to Ollama at `http://localhost:11434/v1`, with `qwen2.5:3b` for chat and `nomic-embed-text` for embeddings. Pull both:

```bash
ollama pull qwen2.5:3b
ollama pull nomic-embed-text
```

To use a different model or endpoint, set `LLM_PROVIDER=openai` with `OPENAI_BASE_URL` and `OPENAI_MODEL`, plus `EMBED_BASE_URL` and `EMBED_MODEL` for embeddings. If the model doesn't support JSON mode, set `OPENAI_JSON=off`. Run the `health` tool to check both the chat model and the embedder respond.

{% hint style="warning" %}
Pick your embedder before you store much. Switching it later changes the vector size, and recall falls back to keyword ranking for older facts until they are re-embedded.
{% endhint %}

Full guide, including Docker and mixed setups: [Local models with Ollama](https://vayl.gitbook.io/vayl-docs/documentation/guides/local-models-with-ollama).
