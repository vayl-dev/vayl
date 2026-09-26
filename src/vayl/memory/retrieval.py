"""Ranking stored facts for a question: hybrid semantic + lexical retrieval fused by reciprocal rank,
with a bounded cache of query embeddings. Degrades to lexical ranking when the embedder is unavailable.
"""
import logging
import re

from vayl.config import env_int
from vayl.memory import llm_client

log = logging.getLogger(__name__)


def _cos(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b, strict=True))   # mixed embedding dims must not rank silently
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_STOP = {"the", "a", "an", "we", "our", "do", "does", "did", "use", "used", "using", "is", "are",
         "what", "which", "how", "who", "when", "where", "for", "of", "to", "on", "in", "at", "and",
         "or", "with", "you", "your", "i", "me", "my", "it", "that", "this", "have", "has", "was",
         "were", "be", "been", "now", "still", "currently", "us"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 1 and w not in _STOP}


# Query embeddings are cached because the call is a network round-trip and the same question
# recurs constantly — an agent asking "what plan is this customer on?" for every request pays a
# fixed ~1s each time otherwise. Measured on a benchmark run: median retrieval 1055.5ms with a
# max of 1056.9ms, a 1.4ms spread across every question. Compute that scales with data does not
# look like that; a fixed network cost does. Bounded so a long-lived process cannot grow it
# without limit, and keyed by the exact text since a paraphrase is a different vector.
_QEMB_CACHE: dict[str, list[float]] = {}


_QEMB_CACHE_MAX = env_int("VAYL_QUERY_CACHE", 512)


def _embed_query(question):
    """Embed a question, reusing a recent identical one. Returns None if the embedder is down."""
    if _QEMB_CACHE_MAX <= 0:
        return llm_client._embed([question])[0]
    hit = _QEMB_CACHE.get(question)
    if hit is not None:
        return hit
    vec = llm_client._embed([question])[0]
    if len(_QEMB_CACHE) >= _QEMB_CACHE_MAX:
        _QEMB_CACHE.pop(next(iter(_QEMB_CACHE)), None)   # FIFO: oldest out
    _QEMB_CACHE[question] = vec
    return vec


def embed_retrieve(question, statements, k=12):
    """HYBRID top-k retrieval: fuse a semantic ranking (embedding cosine) and a lexical ranking
    (keyword overlap) via reciprocal rank fusion. This surfaces exact-term matches the embedding
    might rank lower, and still works when embeddings are missing (lexical carries it). Falls back
    to all facts when memory is small, or to first-k when there's no signal at all."""
    if len(statements) <= k:
        return statements

    qtok = _tokens(question)

    # semantic ranking
    sem_rank = {}
    embedded = [s for s in statements if getattr(s, "_emb", None)]
    if embedded:
        try:
            qv = _embed_query(question)
            for rank, s in enumerate(sorted(embedded, key=lambda s: _cos(qv, s._emb), reverse=True)):
                sem_rank[id(s)] = rank
        except Exception as e:
            log.warning("semantic ranking unavailable (%s); recall uses lexical ranking", type(e).__name__)
            sem_rank = {}   # embedder down / mismatched embedding dims → lexical carries the query

    # lexical ranking
    scored = [(s, len(qtok & _tokens(f"{s.subject} {s.value} {getattr(s, 'raw', '')}"))) for s in statements]
    lex_rank = {}
    for rank, (s, _sc) in enumerate(sorted([p for p in scored if p[1] > 0], key=lambda p: p[1], reverse=True)):
        lex_rank[id(s)] = rank

    if not sem_rank and not lex_rank:
        return statements[:k]   # no signal → bounded fallback

    RRF = 60   # reciprocal-rank-fusion constant; larger = flatter contribution from tail ranks

    def fused(s):
        score = 0.0
        if id(s) in sem_rank:
            score += 1.0 / (RRF + sem_rank[id(s)])
        if id(s) in lex_rank:
            score += 1.0 / (RRF + lex_rank[id(s)])
        return score

    candidates = [s for s in statements if id(s) in sem_rank or id(s) in lex_rank]
    return sorted(candidates, key=fused, reverse=True)[:k]


def _rank_triples(question, triples, k=15):
    """Relevance-rank graph edges to the question and keep top-k — bounds the LLM context even
    when a high-degree hub returns a big neighborhood. Degrades to first-k if embedding fails."""
    if len(triples) <= k:
        return triples
    try:
        vecs = llm_client._embed([question] + [f"{h} {rel} {t}" for h, rel, t in triples])
        qv = vecs[0]
        ranked = sorted(zip(triples, vecs[1:], strict=True), key=lambda x: _cos(qv, x[1]), reverse=True)
        return [t for t, _ in ranked[:k]]
    except Exception:
        return triples[:k]
