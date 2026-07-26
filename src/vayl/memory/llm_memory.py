#!/usr/bin/env python3
"""
Vayl with a REAL extractor — the Finding-A fix.
====================================================
The heuristic prototype could only reconcile its ~6 hard-coded topics; on general
input it stored "unknown/(unspecified)" and reconciled nothing. Here the LLM does
BOTH jobs in one call: extract the fact's canonical (subject, value, scope) AND
classify its relationship to existing facts. That is the "unified
extraction+normalization+reconciliation" model the report argues for.

Honest-uncertainty gate preserved: below the confidence threshold on a resolving
action, we FLAG instead of guessing.

    OPENAI_API_KEY=... python3 -m vayl.memory.llm_memory
"""
import itertools
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

from vayl.memory.reconcile import (
    HYPOTHETICAL_MARKERS,
    RETRACT_MARKERS,
    SARCASM_MARKERS,
    Action,
    Statement,
    Status,
    canon,
    has,
)

AUTO_THRESHOLD = 0.7

# Reconciliation context cap — how many active facts the extractor sees per write. A small space
# passes ALL active facts (embed_retrieve returns everything under the cap → no extra embedding,
# behaviour unchanged); a large space passes only the top-k RELEVANT facts, so write cost stays
# bounded as memory grows instead of scaling with the whole space. _apply still reconciles against
# the FULL active set (the same-slot invariant + target lookup), so a conflict outside the top-k is
# still caught — the cap bounds cost, it does not weaken reconciliation.
_RECONCILE_CONTEXT = int(os.environ.get("VAYL_RECONCILE_CONTEXT", "40"))

# Recall context cap — how many facts the synthesizer sees per read. When a space has this many facts
# or fewer, recall passes them ALL (embed_retrieve returns everything under the cap → no query
# embedding at read time, ~700 ms saved, and the model sees every active fact so it can't miss the
# answer). A larger space falls back to top-k semantic+lexical retrieval to keep the context bounded.
_RECALL_CONTEXT = int(os.environ.get("VAYL_RECALL_CONTEXT", "40"))

# Graph relations that hold MANY tails per head (a service depends on several; a team has several
# members) — these coexist. Everything NOT listed is treated as FUNCTIONAL (one tail per head), so a
# re-point (routes_to, reports_to, located_in…) retires the old edge even when the extractor labelled
# it ADD — the graph mirror of the slot store's one-active-per-slot invariant. Conservative denylist:
# when unsure we prefer retiring a stale edge (Vayl's anti-silently-wrong stance) to keeping a dead one.
MULTI_VALUED_RELS = {
    "DEPENDS_ON", "USES", "USED_BY", "MEMBER_OF", "HAS_MEMBER", "PART_OF", "CONTAINS", "HAS",
    "INCLUDES", "CALLS", "CALLED_BY", "SUPPLIES", "SUPPLIED_BY", "BUYS", "BUYS_FROM", "SELLS",
    "IMPORTS", "EXPORTS", "CONNECTS_TO", "CONNECTED_TO", "INTEGRATES_WITH", "RELATED_TO", "LINKS_TO",
    "REFERENCES", "KNOWS", "WORKS_WITH", "COLLABORATES_WITH", "TAGGED_WITH", "HAS_TAG", "OWNS",
    "MANAGES", "SUPPORTS", "PROVIDES", "OFFERS", "PARTICIPATES_IN", "RESPONSIBLE_FOR", "ASSIGNED_TO",
    "COMPRISES", "PRODUCES", "HANDLES",
}


def _norm_rel(rel):
    """Normalize a relation label for the multi-valued lookup: 'routes to' → 'ROUTES_TO'."""
    return re.sub(r"[\s\-]+", "_", str(rel).strip().upper())

SYS = """You are a memory extraction + reconciliation engine for an AI agent.
Given the CURRENT active facts and a NEW statement — which may contain ZERO, ONE, or SEVERAL distinct facts —
EXTRACT every distinct fact worth remembering — both STATE that holds and EVENTS that happened — and
CLASSIFY each one's relationship to the active facts. Return ONLY a JSON object
with a "facts" array (one element per fact):

{"facts":[
  {"subject":"<canonical snake_case key that uniquely identifies WHO/WHAT the fact is about — see ENTITY SCOPING>",
   "value":"<the specific value itself — NOT a paraphrase of the sentence. See CONCRETE VALUES>",
   "kind":"<state|event — see STATE vs EVENT>",
   "scope":"<global, or a qualifier like web/mobile/backend>",
   "head":"<SUBJECT entity of the triple — the NAMED entity from THIS statement; use 'Org' ONLY for the speaker's own unnamed org ('we'/'our')>",
   "relation":"<UPPER_SNAKE predicate, DIRECTED head->tail. See GRAPH TRIPLE DIRECTION>",
   "tail":"<OBJECT entity of the triple, taken from THIS statement>",
   "time_ref":"<current|past|future|unknown>",
   "action":"<ADD|DEDUP|REFINE|SUPERSEDE|COEXIST|RETRACT|FLAG|SKIP>",
   "confidence":<0.0-1.0 — see CONFIDENCE>, "reason":"<=8 words, why this action>",
   "target_id":<int id of the fact being superseded/refined, or null>}
]}

MULTIPLE FACTS: if one statement asserts several distinct facts (e.g. "we moved to postgres, alice is the new
lead, and we dropped redis" = 3 facts), return ONE array element per fact. If the statement records nothing
worth remembering (greeting, chatter, a bare question), return {"facts":[]}. If a statement contradicts ITSELF
("we use mysql, well actually postgres now"), return only the FINAL asserted value as a single fact.

STATE vs EVENT — set "kind" on every fact. Getting this wrong loses information:
- state: a value that HOLDS until something replaces it. "we use Postgres", "Alice is the lead",
    "the customer is on the Free plan". A new state on the same slot REPLACES the old one.
- event: something that HAPPENED at a point in time. "she ran a charity race", "the customer called
    to complain", "he painted a sunrise in 2022", "we shipped v2 on Tuesday".
    Events NEVER replace each other — two races are two races, not a correction. Give each event a
    subject that distinguishes it (alice_charity_race_may_2023, not alice_race).
Do NOT discard an event because it is not a lasting state. "I went to the support group yesterday"
is a fact worth keeping; dropping it loses the only record that it happened.

CONCRETE VALUES — the value must carry the specific detail, not a summary of it:
- "I went yesterday" said on 8 May -> value "2023-05-07", NOT "attended_recently".
- "I won first place with a piece called Nocturne" -> value "first_place_nocturne", NOT "did_well".
- Put names, dates, numbers and titles in the value. A value that reads as a paraphrase of the
  question rather than an answer to it ("exploring_options", "going_well") is not worth storing.
If the statement gives a date, or one can be resolved from the conversation date, put it in the value.
NEVER INVENT PRECISION THE SOURCE DOES NOT CONTAIN — record exactly as much as you can justify:
  "in 2022"           -> "2022"          (NOT "2022-01-01" — the day is unknown)
  "last May"          -> "2023-05"       (NOT a specific day)
  "yesterday", said on 2023-05-08 -> "2023-05-07"   (the day IS derivable, so give it)
A fabricated day looks like evidence and is worse than an honest "2022".

ENTITY SCOPING — the subject must uniquely identify WHOSE fact it is, or different entities collide in one slot:
- If a specific entity is NAMED (a person, company, product), PUT IT IN THE SUBJECT:
    "Alice works at Google" -> subject "alice_employer"; "Bob works at Fitbit" -> subject "bob_employer"
    (NOT a shared "work_employer" — that makes Bob overwrite Alice).
    "Google acquired Fitbit" -> "google_acquisitions"=Fitbit;  "Carol is Alice's manager" -> "alice_manager"=Carol.
- If the fact is about the USER / the org itself (implicit single subject — "we use X", "I prefer Y"),
    the predicate alone is enough — name the ATTRIBUTE this statement is about, with no entity prefix.
    (Any example subject printed in these instructions is illustrative only. NEVER reuse a subject
    from this prompt; derive it from the statement in front of you.)

ONE SUBJECT = ONE ATTRIBUTE. A subject names a single thing that can hold a single value. It is NOT a
bucket to file related facts under. This matters more than it looks: a second fact landing on the same
subject RETIRES the first one, so a subject broad enough to attract unrelated facts will silently
delete them.

THE REPLACEMENT TEST — before emitting a subject, ask: "if a later statement lands on this same
subject, would it be a CORRECTION of this fact?"
  "database" — a later value corrects it. Good subject.
  "recent_work" — a painting, a race and a work habit all land here and none corrects another.
    Bucket. It would destroy two true facts.
If the answer is no, the subject is too broad: split it into the specific attribute each fact is about.

Never emit a subject built from a vague grouping word — activity, work, update, options, details,
information, stuff, things, news, status (alone), notes. Name the actual attribute instead.

CRITICAL: REUSE an existing active fact's exact "subject" ONLY when the new fact is about the SAME entity AND the
same predicate (so they share a slot and reconcile). Facts about DIFFERENT entities must get DIFFERENT subjects.

GRAPH TRIPLE DIRECTION — head/relation/tail must read as a TRUE sentence spoken "head RELATION tail":
- "Carol is Alice's manager" -> head=Alice, relation=HAS_MANAGER, tail=Carol  (reads "Alice HAS_MANAGER Carol" ✓).
    Do NOT emit head=Alice, relation=MANAGER_OF, tail=Carol — that reads "Alice manages Carol", which is FALSE.
- "Carol reports to Dave" -> head=Carol, relation=REPORTS_TO, tail=Dave.
- Name the ACTUAL entity when given: "Acme's cloud provider is AWS" -> head=Acme (NOT 'Org').
- Before emitting each triple, read it back: "head relation tail" must be a true statement.

TIME_REF — THE TEST: is the value TRUE RIGHT NOW? Judge the value's validity, NOT when the change happened.
- current: the value holds at present. A change that happened in the past but STILL HOLDS is current.
    "we use X", "switched to X", "migrated to X last sprint", "I moved to Amsterdam last month", "now X", "is X".
    (The move/migration is past, but the resulting state is current — so time_ref=current.)
- past: names a FORMER state that is NO LONGER true — it has been ended or replaced.
    "used to X", "previously X", "historically X", "back in 2022 we used X", "we dropped X", "no longer X".
- future: not true yet. "will", "planning to", "next quarter".
Litmus: "Historically we billed monthly" → is monthly true now? No → past. "I moved to Amsterdam last month" →
is Amsterdam true now? Yes → current. Never mark a still-in-effect change as past just because it has a past date.

Actions:
- SKIP: hypothetical, question, or sarcasm — nothing asserted, so nothing to record.
- ADD: new topic; no related active fact.
- DEDUP: restates an existing fact, no new info.
- REFINE: same fact, adds detail. Set target_id.
- SUPERSEDE: replaces an existing fact with a NEW value. "switched to Redux", "flag is now disabled". Set target_id.
- COEXIST: differs but different scope (different app/service). Both stay true.
- RETRACT: removes/ends an existing fact with NO replacement value — the slot becomes empty.
    "we dropped Sentry", "we no longer use X", "we stopped supporting IE11", "X was removed". Set target_id.
    (If it removes AND names a new value, that's SUPERSEDE, not RETRACT.)
- FLAG: two STATE facts give different values for the SAME slot and nothing signals which is newer
    (no "now", "switched", "moved to", no date ordering them). Set target_id.
    Do NOT flag: an event (events never conflict), a fact that merely ADDS detail to another
    (that is REFINE), two facts about different things that happen to share a word, or a value
    that elaborates rather than contradicts. Flagging benign narrative makes the record read as
    contradictory when it is not, so flag only a genuine unresolved same-slot disagreement.

CONFIDENCE — this is not decorative: below 0.7 the system will FLAG the fact for human review
instead of applying it, so an uncalibrated guess creates false disputes on ordinary statements.
Score how firmly the STATEMENT asserts the value, not how important it seems:
  0.95  explicitly and unambiguously stated — "we use Postgres", "her name is Alice"
  0.85  clearly stated with ordinary wording — "I went to the group yesterday"
  0.70  stated but with mild imprecision — "we're on Postgres, I think 14"
  0.50  inferred rather than stated — the statement implies it without saying it
  0.30  hedged, second-hand or speculative — "someone mentioned maybe moving to Aurora"
Most plain declarative sentences are 0.85-0.95. Do not mark a clear statement low because the
topic is sensitive or the value surprises you.

HONESTY RULE: if not confident it is a real CHANGE to a state, choose FLAG, never SUPERSEDE/RETRACT.
Sarcasm/hypotheticals are SKIP. This rule is about changes to state — it never applies to events,
which stand alongside each other and need no adjudication."""

def _salvage_facts(raw):
    """Recover the fact objects a weak model got RIGHT when the overall array is malformed.

    A 3B model reliably drops a comma inside a big `{"facts":[...]}` array on complex input — the
    same chunk fails every retry, so re-rolling cannot help. But the other five facts in that array
    are usually well-formed. Dropping the whole observation over one broken element is a hole in
    memory; keeping the valid facts is strictly better and also helps a strong model that fumbles a
    single fact in a large batch.

    Scans for balanced top-level `{...}` objects and parses each independently, keeping those that
    look like a fact (carry a subject). Returns None if nothing usable is found, so the caller still
    raises rather than silently storing an empty extraction."""
    # Try every `{` as the start of a fact object. The fact objects are nested inside a
    # `{"facts":[...]}` wrapper that itself will not parse (that is why we are here), so a scan for
    # only top-level objects would find just the broken wrapper. Matching each brace to its close
    # and parsing that span recovers the inner objects that ARE well-formed.
    facts, seen = [], set()
    for start in (i for i, ch in enumerate(raw) if ch == "{"):
        depth = 0
        for j in range(start, len(raw)):
            if raw[j] == "{":
                depth += 1
            elif raw[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(raw[start:j + 1])
                    except ValueError:
                        pass
                    else:
                        if isinstance(obj, dict) and obj.get("subject") and start not in seen:
                            facts.append(obj)
                            seen.add(start)
                    break
    return {"facts": facts} if facts else None


def _first_json(raw):
    """Extract a JSON object from a model reply — tolerant of the ways free/reasoning
    models wrap it: <think> blocks, ```json fences, or prose around the object."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        candidate = m.group(0) if m else None
    if candidate is None:
        raise ValueError("model returned no JSON object (it may be a reasoning-only model)")
    try:
        return json.loads(candidate)
    except ValueError:
        # The whole object did not parse. Before giving up (and dropping the observation), try to
        # keep the fact objects that ARE well-formed — a missing comma between two facts should not
        # discard the other five.
        salvaged = _salvage_facts(candidate)
        if salvaged is not None:
            return salvaged
        raise

# Connection pool for the LLM/embedding endpoint. Every call previously opened a fresh TCP+TLS
# connection — a 50-200ms handshake per request, which dominates recall latency (the embedding
# round-trip was ~95% of a 1s recall). A pooled, keep-alive sender reuses the connection. urllib3 is
# OPTIONAL: without it we fall back to urllib and lose only the pooling, so the core install keeps
# its two-dependency, minimal-audit-surface property. `pip install vayl-mcp[pooled]` turns it on.
try:
    import urllib3 as _urllib3
    _POOL = _urllib3.PoolManager(
        maxsize=int(os.environ.get("VAYL_HTTP_POOL", "8")),
        retries=False,                       # we do our own 429/5xx backoff below
        headers={"User-Agent": "vayl/0.1"})
except Exception:                            # not installed → transparent urllib fallback
    _urllib3 = None
    _POOL = None


def _retry_after(headers, i):
    wait = headers.get("retry-after") if headers else None
    return float(wait) if wait else min(2 ** i, 30) + random.random()


def _http_json(req, timeout, retries=10):
    for i in range(retries):
        try:
            if _POOL is not None:
                r = _POOL.request(req.get_method(), req.full_url, body=req.data,
                                  headers=dict(req.headers), timeout=timeout)
                if r.status in (429, 500, 502, 503) and i < retries - 1:
                    time.sleep(_retry_after(r.headers, i)); continue
                if r.status >= 400:
                    raise urllib.error.HTTPError(req.full_url, r.status,
                                                 r.data[:200].decode("utf-8", "replace"),
                                                 r.headers, None)
                return json.loads(r.data)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < retries - 1:
                time.sleep(_retry_after(e.headers, i)); continue
            raise
        except (urllib.error.URLError, TimeoutError):  # reset / transient / slow-local-model timeout
            if i < retries - 1:
                time.sleep(min(2 ** i, 20) + random.random()); continue
            raise
        except Exception as exc:               # urllib3 transport errors (pool-specific)
            if _POOL is not None and _urllib3 is not None \
                    and isinstance(exc, _urllib3.exceptions.HTTPError) and i < retries - 1:
                time.sleep(min(2 ** i, 20) + random.random()); continue
            raise

def _provider():
    """Which LLM backend to use. Explicit LLM_PROVIDER wins; else infer from whichever key is
    present; else default to the OpenAI-compatible path — which, with no cloud key, points at a
    LOCAL Ollama (see _openai_config). So out of the box Vayl runs locally with NO data egress."""
    p = os.environ.get("LLM_PROVIDER")
    if p:
        return p.lower()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    return "openai"


def _openai_config():
    """Resolve (base_url, key, model, is_local) for the OpenAI-compatible path. With no
    OPENAI_BASE_URL and no OPENAI_API_KEY, defaults to local Ollama — nothing leaves the machine."""
    base = os.environ.get("OPENAI_BASE_URL")
    key = os.environ.get("OPENAI_API_KEY")
    if not base:
        base = "https://api.openai.com/v1" if key else "http://localhost:11434/v1"
    base = base.rstrip("/")
    local = ("localhost" in base) or ("127.0.0.1" in base)
    key = key or ("ollama" if local else "none")
    # Default to gpt-5-mini: 0% silently-wrong on the messy real-world reconciliation suite
    # (benchmarks/messy_eval.py), ~$0.25/$2 per 1M tok. gpt-5-nano is ~5x cheaper but flags instead
    # of superseding on messy corrections (23% silently-wrong there) — only use it for clean inputs.
    model = os.environ.get("OPENAI_MODEL", "qwen2.5:3b" if local else "gpt-5-mini")
    return base, key, model, local


def _call_anthropic(user):
    key = os.environ["ANTHROPIC_API_KEY"]
    payload = json.dumps({
        "model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        "max_tokens": 300, "system": SYS + SLOT_SCHEMA.prompt_fragment(),
        "messages": [{"role": "user", "content": user}, {"role": "assistant", "content": "{"}],
    }).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    data = _http_json(req, timeout=30)
    return _first_json("{" + data["content"][0]["text"])

def _call_groq(user):
    # Groq is OpenAI-compatible; JSON mode requires the word "json" in the prompt (SYS has it).
    key = os.environ["GROQ_API_KEY"]
    payload = json.dumps({
        "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "max_tokens": 400, "temperature": float(os.environ.get("GROQ_TEMP", "0")),
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYS + SLOT_SCHEMA.prompt_fragment()},
                     {"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", data=payload,
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json",
                 "User-Agent": "vayl-eval/1.0"})   # Cloudflare 403s the default urllib UA
    data = _http_json(req, timeout=float(os.environ.get("LLM_TIMEOUT","60")))
    return _first_json(data["choices"][0]["message"]["content"])

def _openai_gen_params(model, default_max):
    """Chat-completions generation params, adapted per model family. OpenAI reasoning models
    (gpt-5*, o-series) take `max_completion_tokens` (not `max_tokens`), allow only the default
    temperature, and spend tokens on internal reasoning — 'minimal' keeps extraction/QA fast/cheap,
    and the budget must cover reasoning + output. gpt-4o, local, and other OpenAI-compatible
    endpoints keep the classic `max_tokens` + temperature. Override via OPENAI_MAX_TOKENS /
    OPENAI_REASONING_EFFORT / OPENAI_TEMP."""
    if model.startswith("gpt-5") or (model[:1] == "o" and model[1:2].isdigit()):
        return {"max_completion_tokens": int(os.environ.get("OPENAI_MAX_TOKENS", str(max(default_max, 2000)))),
                "reasoning_effort": os.environ.get("OPENAI_REASONING_EFFORT", "minimal")}
    return {"max_tokens": int(os.environ.get("OPENAI_MAX_TOKENS", str(default_max))),
            "temperature": float(os.environ.get("OPENAI_TEMP", "0"))}


def _call_openai(user):
    # Works with ANY OpenAI-compatible endpoint via OPENAI_BASE_URL — including the FREE ones:
    #   Gemini    : OPENAI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai  OPENAI_MODEL=gemini-2.0-flash
    #   OpenRouter: OPENAI_BASE_URL=https://openrouter.ai/api/v1  OPENAI_MODEL=meta-llama/llama-3.3-70b-instruct:free
    #   Cerebras  : OPENAI_BASE_URL=https://api.cerebras.ai/v1    OPENAI_MODEL=llama-3.3-70b
    #   Ollama    : OPENAI_BASE_URL=http://localhost:11434/v1     OPENAI_MODEL=llama3.1  OPENAI_API_KEY=ollama
    base, key, model, _local = _openai_config()
    body = {
        "model": model,
        "messages": [{"role": "system",
                      "content": os.environ.get("OPENAI_SYSTEM_PREFIX", "") + SYS
                                 + SLOT_SCHEMA.prompt_fragment()},
                     {"role": "user", "content": user}],
        **_openai_gen_params(model, 400),
    }
    if os.environ.get("OPENAI_JSON", "on") != "off":     # some local models don't support json mode
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json",
                 "User-Agent": "vayl/0.1"})
    data = _http_json(req, timeout=float(os.environ.get("LLM_TIMEOUT","60")))
    return _first_json(data["choices"][0]["message"]["content"])

def _embed(texts):
    """Embed a batch of texts. Defaults to a local Ollama embedder (free); overridable via env.
    Any OpenAI-compatible /embeddings endpoint works (OpenAI text-embedding-3-small, etc.)."""
    base = (os.environ.get("EMBED_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
            or "http://localhost:11434/v1").rstrip("/")
    key = os.environ.get("EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY", "ollama")
    model = os.environ.get("EMBED_MODEL", "nomic-embed-text")
    payload = json.dumps({"model": model, "input": list(texts)}).encode()
    req = urllib.request.Request(base + "/embeddings", data=payload,
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json", "User-Agent": "vayl/0.1"})
    data = _http_json(req, timeout=float(os.environ.get("LLM_TIMEOUT", "60")))
    return [row["embedding"] for row in data["data"]]

def _cos(a, b):
    import math
    dot = sum(x * y for x, y in zip(a, b))
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
_QEMB_CACHE = {}
_QEMB_CACHE_MAX = int(os.environ.get("VAYL_QUERY_CACHE", "512"))


def _embed_query(question):
    """Embed a question, reusing a recent identical one. Returns None if the embedder is down."""
    if _QEMB_CACHE_MAX <= 0:
        return _embed([question])[0]
    hit = _QEMB_CACHE.get(question)
    if hit is not None:
        return hit
    vec = _embed([question])[0]
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
        except Exception:
            sem_rank = {}   # embedder down → lexical carries the query

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
        vecs = _embed([question] + [f"{h} {rel} {t}" for h, rel, t in triples])
        qv = vecs[0]
        ranked = sorted(zip(triples, vecs[1:]), key=lambda x: _cos(qv, x[1]), reverse=True)
        return [t for t, _ in ranked[:k]]
    except Exception:
        return triples[:k]

def _qa(context, question):
    """Read layer: answer a question (incl. multi-hop) over the stored facts. Free text, not JSON.

    The READ is synthesis over facts we already retrieved — simpler than the write-path extraction —
    so VAYL_READ_MODEL can point it at a cheaper model (e.g. a nano tier) than OPENAI_MODEL. Note this
    is a COST lever, not a latency one: for a short one-sentence answer, latency is network + output
    generation bound, so a smaller model is not measurably faster (a reasoning nano is slower). It cuts
    per-recall token cost, which matters at scale. Unset → the extraction model."""
    read_model = os.environ.get("VAYL_READ_MODEL")
    system = ("You answer questions about a user/organization using ONLY the FACTS provided, chaining across "
              "them for multi-hop questions. Facts tagged (history) are FORMER/superseded values, not current. "
              "If the facts don't support an answer, say you don't know. Answer in one short sentence.")
    user = f"FACTS:\n{context}\n\nQUESTION: {question}"
    system = os.environ.get("OPENAI_SYSTEM_PREFIX", "") + system   # e.g. "/no_think" for reasoning models
    provider = _provider()
    if provider == "anthropic":
        key = os.environ["ANTHROPIC_API_KEY"]
        payload = json.dumps({"model": read_model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
            "max_tokens": 160, "system": system, "messages": [{"role": "user", "content": user}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=payload,
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return _http_json(req, 30)["content"][0]["text"].strip()
    if provider == "openai":   # local Ollama by default; respects OPENAI_BASE_URL
        base, key, model, _local = _openai_config()
        model = read_model or model
        url = base + "/chat/completions"
    else:
        url, key = "https://api.groq.com/openai/v1/chat/completions", os.environ["GROQ_API_KEY"]
        model = read_model or os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    payload = json.dumps({"model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        **_openai_gen_params(model, 300)}).encode()
    hdr = {"Authorization": f"Bearer {key}", "content-type": "application/json", "User-Agent": "vayl/0.1"}
    req = urllib.request.Request(url, data=payload, headers=hdr)
    return _http_json(req, 30)["choices"][0]["message"]["content"].strip()

_EXTRACT_JSON_RETRIES = int(os.environ.get("VAYL_EXTRACT_RETRIES", "2"))


def llm_extract_classify(text, active):
    facts = [{"id": s.id, "subject": s.subject, "value": s.value, "scope": s.scope} for s in active]
    user = f'CURRENT active facts:\n{json.dumps(facts)}\n\nNEW statement:\n"{text}"\n\nJSON:'
    call = {"groq": _call_groq, "openai": _call_openai}.get(_provider(), _call_anthropic)
    # A model that returns malformed JSON is a re-roll away from valid JSON — the call is stochastic,
    # so retrying the SAME request usually succeeds. `_http_json` already retries transport errors
    # (429/5xx), but a parse failure happens on the returned body and was not retried at all, so one
    # bad roll dropped the whole observation. A weak/local model does this a few percent of the time.
    obj = None
    for attempt in range(_EXTRACT_JSON_RETRIES + 1):
        try:
            obj = call(user)
            break
        except (ValueError, KeyError):        # unparseable JSON / missing field
            if attempt == _EXTRACT_JSON_RETRIES:
                raise
    out = obj.get("facts")
    if out is None:                       # fallback: model returned a single-fact object
        out = [obj] if obj.get("subject") else []
    return out if isinstance(out, list) else []


# ── critical categories ──────────────────────────────────────────────────────
# Facts a caller cannot afford to have ranked out of context. Ordinary recall is semantic
# top-k, which is probabilistic: an allergy that does not make the cut is not merely ranked
# low, it is INVISIBLE, and the answer comes back confident and incomplete. Safety gates
# (safe_recall, check) can only judge facts they were given, so a retrieval miss defeats them
# silently. Facts in these categories bypass ranking entirely.
#
# Off unless configured — a general-purpose deployment has no such categories, and inventing
# some would be worse than none.
# The declared slot vocabulary, if this deployment has one. Loaded once; a malformed file raises
# at import rather than degrading to "no schema", because failing open here would mean believing
# critical slots are canonicalised when they are not.
try:
    from vayl.memory.schema import load as _load_schema
    SLOT_SCHEMA = _load_schema()
except FileNotFoundError:
    raise
except Exception:                       # unreadable/malformed JSON — surface it, don't swallow it
    raise

_CRITICAL_CATEGORIES = tuple(
    c.strip().lower() for c in os.environ.get("VAYL_CRITICAL_CATEGORIES", "").split(",") if c.strip())
_CRITICAL_BUDGET = int(os.environ.get("VAYL_CRITICAL_BUDGET", "200"))

# Fragment resolution. A weak or verbose extractor names the same slot several ways —
# `caroline_self_care_realization`, then `..._8_may_2023`, then `..._14_may_2023` — with the SAME
# value each time. Measured: every fragmentation case shared an identical value across near-
# identical subjects. Folding those is dedup-only and cannot destroy data: it turns an ADD into a
# DEDUP, never a supersede. OFF by default; it changes stored subjects, which some callers key on.
_SLOT_RESOLVE = os.environ.get("VAYL_SLOT_RESOLVE", "").lower() in ("1", "true", "yes")

# Sources whose changes are already authorized and therefore bypass the confirmation gate. The gate
# protects against a change inferred by an LLM reading conversational text ("stop the warfarin" in a
# note is not an order). A structured feed — a FHIR MedicationRequest with status=stopped, an HL7
# order — IS an authorized order; the clinical sign-off already happened upstream, and re-queuing it
# for approval is friction, not safety. Empty by default (everything is gated, unchanged); a clinical
# deployment sets VAYL_TRUSTED_SOURCES=fhir,hl7.
_TRUSTED_SOURCES = tuple(
    x.strip().lower() for x in os.environ.get("VAYL_TRUSTED_SOURCES", "").split(",") if x.strip())
_SLOT_RESOLVE_SIM = float(os.environ.get("VAYL_SLOT_RESOLVE_SIM", "0.6"))
_DATE_TOK = re.compile(r"^(\d+|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
                       r"january|february|march|april|june|july|august|september|october|"
                       r"november|december|st|nd|rd|th)$")


def _subject_tokens(subject):
    """Content tokens of a subject, with date/ordinal noise dropped.

    The observed fragmentation was a model appending a date to a stable stem
    (`..._realization` -> `..._realization_8_may_2023`), so stripping date tokens makes the two
    match near-exactly instead of relying on a fragile similarity threshold."""
    return {t for t in re.split(r"[^a-z0-9]+", str(subject or "").lower())
            if t and not _DATE_TOK.match(t)}


def _subject_similar(a, b, threshold=None):
    """True if two subjects are the same slot named differently — Jaccard over content tokens.

    Deterministic and offline: no embedding call, no data-tuned threshold on the hot path, and the
    result is explainable ('they share these tokens'). Semantic similarity is deliberately NOT used
    here — two subjects can be about the same topic yet hold different, both-true facts, and merging
    those would be the data loss this is meant to avoid. Same-value is required by the caller, which
    is what makes even a loose token match safe."""
    ta, tb = _subject_tokens(a), _subject_tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    thr = _SLOT_RESOLVE_SIM if threshold is None else threshold
    return len(ta & tb) / len(ta | tb) >= thr


class CriticalOverflow(RuntimeError):
    """More critical facts than the context budget allows.

    Raised rather than truncated. Dropping the tail of an always-include set would reintroduce
    exactly the silent miss the set exists to prevent — and would do it in the categories where
    a miss matters most. A caller that sees this must raise the budget or narrow the categories;
    it must not be allowed to proceed on a quietly incomplete answer.
    """


def _category(s):
    return str(((getattr(s, "metadata", None) or {}).get("category") or "")).lower()


def is_critical(s, categories=None):
    """True if this fact belongs to a category that must never be ranked out of context."""
    cats = _CRITICAL_CATEGORIES if categories is None else tuple(c.lower() for c in categories)
    return bool(cats) and _category(s) in cats


def _ctx_line(s):
    """One fact as the answering model sees it: the reconciled slot, plus the sentence it came from.

    A statement is two things — a normalized slot, which is what makes reconciliation possible, and
    the utterance it was extracted from. Normalizing is lossy by design: "I went to the group
    yesterday" becomes `attended_recently`, which is true and cannot answer "when?". The slot
    carries the verdict (this value is current, that one is retired); the raw sentence carries the
    detail the slot dropped. Sending only the slot throws away half of what is stored.
    """
    line = f"{s.subject}={s.value}"
    raw = (getattr(s, "raw", "") or "").strip()
    return f'{line} ("{raw[:300]}")' if raw else line


_LIST_ID_CUT = re.compile(r"\s+[—–-]\s+|\s\d")


def _list_identity(value):
    """The stable identity of a list item — the substance or drug name, before its reaction or dose.

    A list item is identified by WHAT it is, not its full description. "Penicillin — Anaphylaxis
    (severe)" and a later "Penicillin — Anaphylaxis" (a refute carrying less reaction detail) are the
    same allergy; "Warfarin 5 mg PO daily" and "Warfarin 3 mg" are the same drug at different doses.
    Cutting at the first separator or dose number yields "penicillin" / "warfarin", so a removal or
    change finds the right list member even when the description text differs.
    """
    v = str(value or "")
    m = _LIST_ID_CUT.search(v)
    return (v[:m.start()] if m else v).strip().lower()


def _slot_target(active, subject, value, is_multi):
    """Which active fact a change/removal applies to. On a single-valued slot it is the one fact on
    the subject. On a LIST slot it must be the matching item — otherwise removing one list member
    (stop warfarin) would hit an arbitrary other member (metformin)."""
    on_slot = [s for s in active if s.subject == subject]
    if is_multi:
        exact = next((s for s in on_slot if canon(s.value) == canon(value)), None)
        if exact is not None:
            return exact
        # fall back to clinical identity: a refuted allergy or a stop order may carry different
        # description text than what was charted, but the substance/drug name still identifies it.
        vid = _list_identity(value)
        return next((s for s in on_slot if vid and _list_identity(s.value) == vid), None)
    return on_slot[0] if on_slot else None


def _is_event(s):
    """True if this statement records something that HAPPENED rather than a value that HOLDS.

    Events are exempt from the same-slot invariant: two races, two phone calls, two paintings are
    all true at once, and superseding one with another destroys the record. State facts keep the
    invariant — exactly one active value per (subject, scope) — because that is what makes "which
    is current?" answerable. Stored in metadata so no schema migration is needed.
    """
    return ((getattr(s, "metadata", None) or {}).get("kind") == "event")


class LLMMemory:
    def __init__(self, graph=None, ns="", policy=None):
        self.statements = []
        # Per-instance id allocation. Ids only need to be unique WITHIN a memory space, and a fresh
        # memory is a fresh space, so a per-instance counter starting at 1 is correct and — unlike a
        # process-global counter reassigned on every load() — carries NO shared mutable state across
        # spaces. That reassignment was the race the server's global lock existed to hide; removing
        # it is the prerequisite for per-space locking. Store.load() seeds this from the space's MAX.
        self._id_counter = itertools.count(1)
        self.graph = graph
        self.ns = ns                # (user/agent/run) namespace stamped on graph edges for scoped erasure
        self.policy = policy        # optional source-aware ReconcilePolicy for a shared space (Feature 4)

    def active(self):
        return [s for s in self.statements if s.status == Status.ACTIVE]

    def _mk(self, *args, **kwargs):
        """Construct a Statement with an id minted from THIS memory's counter, so id allocation
        never touches process-global state (the choke point every _apply site routes through)."""
        kwargs.setdefault("id", next(self._id_counter))
        return Statement(*args, **kwargs)

    def _gretire(self, subject):
        """Retire a superseded/retracted slot's projected edges by subject (head-agnostic), so the
        graph never serves an edge whose slot fact is no longer active — even when the superseding
        fact named a different head entity or carried no graph triple at all."""
        if self.graph and subject:
            try:
                self.graph.retire_subject_edges(str(subject), ns=self.ns)
            except Exception:
                pass

    def _gwrite(self, o, act):
        """Mirror a fact into the Neo4j projection as an entity triple, if a graph is attached."""
        if not self.graph:
            return
        head, rel = o.get("head"), o.get("relation")
        tail = o.get("tail") or o.get("value")
        if not (head and rel and tail):
            return
        subj = str(o.get("subject") or "")
        # Entity resolution: keep a slot's edges on ONE head entity. If this subject already has
        # edges, reuse that head rather than the model's fresh naming of the same thing — otherwise
        # the update lands on a new node and a query about the original entity can never reach it.
        try:
            anchored = self.graph.head_for_subject(subj, ns=self.ns)
            if anchored and anchored != str(head):
                head = anchored
        except Exception:
            pass
        if act in (Action.SUPERSEDE, Action.REFINE):
            self.graph.supersede_edge(str(head), str(rel), str(tail), ns=self.ns, subject=subj)
        elif act == Action.RETRACT:
            self.graph.retract_edge(str(head), str(rel), ns=self.ns)
        elif act == Action.COEXIST:
            self.graph.add_edge(str(head), str(rel), str(tail), ns=self.ns, subject=subj)  # both true
        elif act == Action.ADD:
            # Graph-edge invariant: for a FUNCTIONAL relation (one tail per head — routes_to,
            # reports_to, located_in…) a new tail retires the old edge, so a re-point the extractor
            # mislabelled ADD can't leave two live edges. Additive relations (MULTI_VALUED) coexist.
            self.graph.add_edge(str(head), str(rel), str(tail), ns=self.ns, subject=subj,
                                functional=_norm_rel(rel) not in MULTI_VALUED_RELS)

    def add(self, text, source=""):
        # A message may carry several facts; extract all, reconcile each in order.
        # `source` is stamped on every fact created — belief provenance. Show the extractor only the
        # top-k RELEVANT active facts (all of them when the space is small) so write cost stays
        # bounded as memory grows; _apply still reconciles against the full active set.
        candidates = embed_retrieve(text, self.active(), k=_RECONCILE_CONTEXT)
        return [self._apply(o, text, source) for o in llm_extract_classify(text, candidates)]

    def forget(self, text, source=""):
        """An explicit 'forget X' must RETRACT — retire the fact — rather than depend on the
        extractor's own action guess (which might come back ADD/SUPERSEDE). We reuse the extractor
        only to resolve WHICH fact is meant, then force removal at high confidence."""
        out = []
        for o in llm_extract_classify(text, embed_retrieve(text, self.active(), k=_RECONCILE_CONTEXT)):
            o["action"] = "RETRACT"
            o["confidence"] = max(float(o.get("confidence") or 0), 0.9)
            out.append(self._apply(o, text, source))
        return out

    def get(self, mid):
        """Fetch a single statement by id (or None)."""
        return next((s for s in self.statements if s.id == mid), None)

    def update(self, mid, new_value, source=""):
        """Audit-preserving edit by id: retire the old value (SUPERSEDED) and add a new ACTIVE
        statement carrying `new_value`. The old value stays visible in history() — unlike a silent
        overwrite. Returns the new statement, or None if the id isn't found."""
        old = self.get(mid)
        if old is None:
            return None
        old.status = Status.SUPERSEDED
        new = self._mk(old.slot, old.subject, new_value, old.scope,
                        confidence=old.confidence, supersedes=old.id, raw=f"(edit) {new_value}",
                        metadata=old.metadata, source=source or getattr(old, "source", ""))
        self.statements.append(new)
        return new

    def history(self, subject):
        """The full change-log for a subject (active + superseded + historical), oldest first."""
        return sorted([s for s in self.statements if s.subject == subject], key=lambda s: s.id)

    def _apply(self, o, text, source=""):
        act = Action(o.get("action", "ADD")); conf = float(o.get("confidence", 0.5))
        # coerce: the LLM occasionally emits null / list / number for a field
        subj = str(o.get("subject") or "unknown")
        val = str(o.get("value") or "(unspecified)")
        scope = str(o.get("scope") or "global")
        time_ref = str(o.get("time_ref") or "unknown").lower()
        kind = str(o.get("kind") or "state").lower()
        is_event = kind == "event"
        # Fold the extractor's chosen subject onto a declared slot when one matches, so a later
        # statement about the same thing reconciles against this one instead of opening a second
        # slot beside it. Undeclared subjects pass through untouched.
        spec = SLOT_SCHEMA.resolve(subj) if SLOT_SCHEMA else None
        if spec is not None:
            subj = spec.name
        is_multi = spec is not None and spec.multi   # slot holds a LIST (allergies, med list)
        target = next((s for s in self.active() if s.id == o.get("target_id")), None)
        if is_event:
            # An event never replaces anything: it is a new thing that happened, not a correction.
            # Forcing this here rather than trusting the extractor means a mislabelled SUPERSEDE on
            # an event cannot silently delete a previous event.
            if act in (Action.SUPERSEDE, Action.REFINE, Action.RETRACT):
                act = Action.ADD
            target = None

        # ── VALID-TIME GATE ──
        # A PAST statement is history, not present truth: record it, but never let it become
        # active or retire the current fact. Closes the out-of-order failure ("Historically we
        # billed monthly" must not overwrite "we now bill annually"). A FUTURE statement isn't true yet.
        # (RETRACT is exempt: "datadog is gone" is past-framed but means retire the CURRENT slot,
        #  not archive a historical value — otherwise the gate swallows the retraction.)
        if time_ref == "past" and act != Action.RETRACT:
            # The same past fact stated twice is still one fact. This gate returns before the
            # RESTATEMENT check below, so without its own guard every repetition appended another
            # HISTORICAL row — additive growth in the one place nothing ever prunes, since history
            # is never superseded. Observed: one subject accumulating five identical archived rows.
            if any(x.subject == subj and x.scope == scope and canon(x.value) == canon(val)
                   and x.status is Status.HISTORICAL for x in self.statements):
                return Action.DEDUP, subj, val
            st = self._mk(f"{subj}@{scope}", subj, val, scope,
                           status=Status.HISTORICAL, confidence=conf, raw=text, source=source)
            self.statements.append(st)
            return Action.ARCHIVE, subj, val
        if time_ref == "future" and act in (Action.SUPERSEDE, Action.COEXIST, Action.ADD):
            act = Action.FLAG   # pending — surface it, don't assert it as current

        # ── DETERMINISTIC RETRACT UPGRADE ──
        # A removal the extractor mislabelled ("ServiceA no longer calls ServiceB" coming back as
        # ADD). Like the same-slot invariant, this does not ask the model to be smarter — it fires
        # only when the language is DEFINITE (hedges and sarcasm are excluded) *and* the extracted
        # value names the fact being removed rather than a replacement, i.e. an active fact on this
        # slot already holds that value. That second condition is what separates "we dropped Sentry"
        # (RETRACT) from "we moved from Sentry to Rollbar" (SUPERSEDE, which names a new value), and
        # is why a hesitant "considering dropping X" can never delete a still-true fact.
        if (not is_event
                and act in (Action.ADD, Action.SUPERSEDE, Action.COEXIST, Action.REFINE)
                and has(text, RETRACT_MARKERS)
                and not has(text, HYPOTHETICAL_MARKERS) and not has(text, SARCASM_MARKERS)
                and any(s.subject == subj
                        and (canon(s.value) == canon(val)
                             or (val == "(unspecified)" and not is_multi))
                        for s in self.active())):
            act = Action.RETRACT

        # ── RESTATEMENT ──
        # The same fact said again carries no new information. Without this, a repeated assertion
        # appends a second identical ACTIVE row — additive behaviour leaking into a reconciling
        # store, and a slow drift away from "one active value per slot".
        if (act in (Action.ADD, Action.SUPERSEDE, Action.COEXIST, Action.REFINE)
                and any(s.subject == subj and s.scope == scope and canon(s.value) == canon(val)
                        for s in self.active())):
            return Action.DEDUP, subj, val

        # ── FRAGMENT RESOLUTION ──
        # Same value, near-identical subject, but the subject STRING differs — a fragmented
        # restatement the exact check above missed. Fold it into the existing slot. Guarded three
        # ways so it can only ever be a dedup: same canonical value (so nothing is overwritten),
        # state only (an event must never fold into another event), and ADD only (a real supersede
        # or retract names a target and is not touched here).
        if (_SLOT_RESOLVE and not is_event and act == Action.ADD and spec is None):
            twin = next((s for s in self.active()
                         if s.scope == scope and not _is_event(s)
                         and canon(s.value) == canon(val)
                         and s.subject != subj and _subject_similar(s.subject, subj)), None)
            if twin is not None:
                return Action.DEDUP, twin.subject, val

        # ── CONFIRMATION GATE ──
        # Some slots are ones where the WRITE is the hazard, not just a wrong read. Reconciliation
        # is driven by an LLM reading conversational text, and "we should stop the warfarin"
        # appearing in a sentence is not an order to stop it. For a confirm-required slot a
        # replacement or removal is PROPOSED: it is recorded as pending, the current value stands
        # untouched, and a person decides. Placed before RETRACT and before the same-slot invariant
        # so it catches both paths.
        if (spec is not None and spec.confirm
                and act in (Action.SUPERSEDE, Action.RETRACT, Action.REFINE)
                and str(source or "").lower() not in _TRUSTED_SOURCES):
            tgt = target or _slot_target(self.active(), subj, val, is_multi)
            if tgt is not None:
                # The same change proposed twice is one decision, not two. Without this, repeating
                # "switch to apixaban" stacked identical rows in the review queue — the same
                # early-return-without-dedup shape as the valid-time gate. A RETRACT proposal is
                # keyed on the target alone (it has no distinct new value to compare).
                dup = next((x for x in self.pending()
                            if x.supersedes == tgt.id and (x.metadata or {}).get("pending") == act.value
                            and (act == Action.RETRACT or canon(x.value) == canon(val))), None)
                if dup is not None:
                    return Action.DEDUP, subj, val
                pend = self._mk(f"{subj}@{scope}", subj, val, scope,
                                 status=Status.FLAGGED, confidence=conf, raw=text, source=source,
                                 supersedes=tgt.id)
                pend.metadata = {"pending": act.value, "category": spec.category or ""}
                self.statements.append(pend)
                return Action.FLAG, subj, val

        # ── RETRACT: removal with no replacement — retire the fact, leave slot empty ──
        if act == Action.RETRACT:
            tgt = target or _slot_target(self.active(), subj, val, is_multi)
            if tgt is None:
                return Action.SKIP, subj, val
            if conf < AUTO_THRESHOLD:                          # unsure — don't delete a possibly-true fact
                self.statements.append(self._mk(f"{subj}@{scope}", subj, tgt.value, scope,
                                                 status=Status.FLAGGED, confidence=conf, raw=text, source=source))
                return Action.FLAG, subj, tgt.value
            tgt.status = Status.SUPERSEDED
            self.statements.append(self._mk(f"{subj}@{scope}", subj, f"(retracted: {tgt.value})", scope,
                                             status=Status.HISTORICAL, confidence=conf,
                                             supersedes=tgt.id, raw=text, source=source))   # tombstone for provenance
            self._gretire(getattr(tgt, "subject", subj))   # drop the edge by subject (head-agnostic)
            self._gwrite(o, Action.RETRACT)
            return Action.RETRACT, subj, tgt.value

        # honest-uncertainty gate
        if act in (Action.SUPERSEDE, Action.COEXIST, Action.REFINE) and conf < AUTO_THRESHOLD:
            act = Action.FLAG
        if act in (Action.SKIP, Action.DEDUP):
            return act, subj, val
        if act == Action.REFINE and target:
            # Source-aware guard (Feature 4): a cross-source REFINE that CHANGES the value is an
            # overwrite path, so gate it by the same policy as SUPERSEDE — else a low-authority
            # source could sneak a change in as a "refinement".
            from vayl.memory import orgmemory
            if (val != target.value and self.policy
                    and orgmemory.resolve(source, getattr(target, "source", ""), self.policy) == "flag"):
                st = self._mk(f"{subj}@{scope}", subj, val, scope,
                               status=Status.FLAGGED, confidence=conf, raw=text, source=source)
                self.statements.append(st)
                return Action.FLAG, subj, val
            target.value = val; target._emb = None   # value changed -> re-embed on save
            self._gwrite(o, Action.REFINE); return act, subj, val
        st = self._mk(f"{subj}@{scope}", subj, val, scope, confidence=conf, raw=text, source=source)
        if is_event:
            st.metadata = {**(st.metadata or {}), "kind": "event"}
        if act == Action.FLAG and o.get("reason"):
            # A flagged fact lands in a human review queue, and "why" is the first thing that reader
            # needs. The extractor already writes this; discarding it paid for the tokens and kept
            # none of the value.
            st.metadata = {**(st.metadata or {}), "reason": str(o.get("reason"))[:200]}
        if spec is not None and spec.category:
            # Declaring a slot's category is what makes the critical-fact channel usable: an
            # operator names the slots once instead of every caller remembering to tag each write.
            st.metadata = {**(st.metadata or {}), "category": spec.category}
        # Keep the graph triple with the fact, so the projection can be rebuilt from the store.
        st.head = str(o.get("head") or "")
        st.relation = str(o.get("relation") or "")
        st.tail = str(o.get("tail") or o.get("value") or "")
        if act == Action.FLAG:
            st.status = Status.FLAGGED
        else:
            # SAME-SLOT INVARIANT: at most one ACTIVE value per (subject, scope). Resolve against the
            # LLM-named target when it gave one; otherwise — the deterministic guard — against any
            # active fact on the SAME slot this one contradicts. This catches the weak-model failure
            # where a real change comes back as ADD (or SUPERSEDE with an unlinked target_id) and both
            # values are left active, which is exactly what makes a store answer with a stale value.
            # Two contradictory actives on one slot never survive: recency wins the slot, unless a
            # source-authority policy forbids the overwrite (then the current value stands and the
            # incoming one is flagged). COEXIST is exempt — it differs by SCOPE, so both stay true.
            # An event has no rival: it does not occupy a slot that something else can hold.
            # Events are also excluded from being rivals, so a later state fact sharing a subject
            # cannot retire a record of something that happened.
            if is_event:
                rival = None
            elif act == Action.SUPERSEDE and target:
                rival = target                            # the extractor/feed named the item
            elif is_multi:
                # A list slot: a bare ADD joins the list (no rival). An explicit SUPERSEDE replaces
                # the matching item, found by drug/substance identity — that is how a dose or route
                # change reconciles ("atorvastatin 20" -> "atorvastatin 80") without an id. A new
                # drug with no identity match has no rival and simply joins the list.
                rival = (_slot_target([s for s in self.active() if not _is_event(s)], subj, val, True)
                         if act == Action.SUPERSEDE else None)
            elif act in (Action.ADD, Action.SUPERSEDE, Action.REFINE):
                rival = next((s for s in self.active()
                              if s.subject == subj and s.scope == scope and s.value != val
                              and not _is_event(s)), None)
            else:
                rival = None
            if rival is not None:
                # Source-aware reconciliation (Feature 4): whether a new source may overwrite
                # another's active fact is a policy decision; on 'flag' the current value STANDS.
                from vayl.memory import orgmemory
                if orgmemory.resolve(source, getattr(rival, "source", ""), self.policy) == "flag":
                    st.status = Status.FLAGGED
                    act = Action.FLAG
                else:
                    rival.status = Status.SUPERSEDED; st.supersedes = rival.id
                    act = Action.SUPERSEDE
                    self._gretire(rival.subject)   # retire the old edge by subject before writing the new
        self.statements.append(st)
        self._gwrite(o, act)
        return act, subj, val

    def view(self):
        act = self.active()
        flg = [s for s in self.statements if s.status == Status.FLAGGED]
        sup = [s for s in self.statements if s.status == Status.SUPERSEDED]
        hist = [s for s in self.statements if s.status == Status.HISTORICAL]
        return act, flg, sup, hist

    @staticmethod
    def provenance(s):
        """The belief-lineage record of one fact the agent consulted — the truthful basis for
        'why did the agent do X?' (Feature 1) and the safety gate (Feature 2). Exactly the fields
        needed to explain and to judge: value, who asserted it, when, confidence, what it superseded."""
        return {"id": s.id, "subject": s.subject, "value": s.value, "status": s.status.value,
                "confidence": round(float(s.confidence), 4), "supersedes": s.supersedes,
                "source": getattr(s, "source", "") or "", "set_at": getattr(s, "created_at", None)}

    def query(self, question, retrieve=None, k=None, with_provenance=False,
              include_history=False, critical_categories=None):
        """Answer a question over the facts — including multi-hop and history ('what did we use first?').
        Uses embedding retrieval by default (top-k relevant) so it scales; falls back to all facts when
        the memory is small or un-embedded. Pass a custom `retrieve(question, statements, k)` to override.

        With `with_provenance=True`, returns (answer, used_facts) where used_facts is the EXACT set of
        statements placed into the model's context — not a re-derivation. That exactness is what makes
        the decision audit trustworthy: it cites what was actually believed, not what looks plausible now.

        `include_history` is OFF by default, and deliberately so. With it off, retired facts are not
        merely filtered out of the answer — they are never loaded, so a superseded value CANNOT be
        returned as current no matter how the model behaves. That structural guarantee is the whole
        product, and it should not be weakened into a tag the model is trusted to respect.

        Turn it on for questions that are explicitly about the past — "what did we use before", "when
        did we switch" — where retired facts ARE the answer. They arrive tagged `(history)`, and the
        answer prompt is instructed to treat them as former values.

        `critical_categories` overrides VAYL_CRITICAL_CATEGORIES for this call. Facts in those
        categories skip ranking and are always present in the context — for domains where a fact
        being ranked out is a safety failure rather than a quality one."""
        ret = retrieve or embed_retrieve
        k = k if k is not None else _RECALL_CONTEXT
        # Vectors are left on disk by load() because they are large and reconciliation never reads
        # them. A ranking pass does — and only happens when the space exceeds the cap — so pull them
        # back at exactly that point, and only then.
        if len(self.statements) > k:
            hydrate = getattr(self, "_hydrate", None)
            if hydrate:
                try:
                    hydrate()
                except Exception:
                    pass                     # ranking degrades to lexical; never fail a read for it
        # Retired facts are the answer to any question about the past, and load() leaves them on
        # disk because the write path never wants them. Pull them in here — the read path — so the
        # (history) branch below can actually fire. Ranked together with active facts so a history
        # row only surfaces when it is genuinely relevant, and held in a separate pool so save()
        # never mistakes a loaded history row for a new statement.
        pool = self.statements + (self._history() if include_history else [])
        # Critical facts are separated BEFORE ranking and rejoined after, so they occupy no
        # ranked slots and can never be crowded out by a better-matching but less important fact.
        critical = [s for s in pool if is_critical(s, critical_categories)]
        if len(critical) > _CRITICAL_BUDGET:
            raise CriticalOverflow(
                f"{len(critical)} facts in critical categories exceeds the context budget of "
                f"{_CRITICAL_BUDGET}. Raise VAYL_CRITICAL_BUDGET or narrow "
                f"VAYL_CRITICAL_CATEGORIES — truncating them would hide exactly the facts that "
                f"must not be hidden.")
        rankable = [s for s in pool if not is_critical(s, critical_categories)]
        try:
            stmts = ret(question, rankable, max(k - len(critical), 1)) if rankable else []
        except Exception:
            stmts = rankable                 # if the embedder is unavailable, degrade to all-facts
        stmts = critical + [s for s in stmts if s not in critical]
        used = [s for s in stmts if s.status in (Status.ACTIVE, Status.SUPERSEDED, Status.HISTORICAL)]
        cur = [_ctx_line(s) for s in stmts if s.status == Status.ACTIVE]
        old = [f"(history) {_ctx_line(s)}" for s in stmts
               if s.status in (Status.SUPERSEDED, Status.HISTORICAL)]
        context = "; ".join(cur + old) or "(no facts stored)"
        answer = _qa(context, question)
        if with_provenance:
            return answer, [self.provenance(s) for s in used]
        return answer

    def pending(self):
        """Proposed changes awaiting a human decision, oldest first."""
        return [s for s in self.statements
                if s.status is Status.FLAGGED and (s.metadata or {}).get("pending")]

    def confirm(self, mid, source=""):
        """Apply a pending change. Returns (Action, subject, value), or None if `mid` isn't pending.

        Only now does the proposed write actually happen — the whole point of the gate is that
        nothing was applied when the sentence was read.
        """
        st = next((s for s in self.pending() if s.id == mid), None)
        if st is None:
            return None
        proposed = (st.metadata or {}).get("pending")
        target = next((s for s in self.statements if s.id == st.supersedes), None)
        if target is None or target.status is not Status.ACTIVE:
            # The value it was going to replace is already gone; applying now would resurrect a
            # decision made against state that no longer holds.
            st.metadata = {**(st.metadata or {}), "pending": None, "resolved": "stale"}
            return None

        target.status = Status.SUPERSEDED
        st.metadata = {**(st.metadata or {}), "pending": None,
                       "resolved": "confirmed", "confirmed_by": source or "unknown"}
        self._gretire(target.subject)
        if proposed == Action.RETRACT.value:
            st.status = Status.HISTORICAL
            st.value = f"(retracted: {target.value})"
            return Action.RETRACT, st.subject, target.value
        st.status = Status.ACTIVE
        return Action.SUPERSEDE, st.subject, st.value

    def reject(self, mid, source=""):
        """Discard a pending change. The current value stands and the proposal is kept as history —
        that someone proposed stopping a medication is itself worth being able to audit."""
        st = next((s for s in self.pending() if s.id == mid), None)
        if st is None:
            return None
        st.status = Status.HISTORICAL
        st.metadata = {**(st.metadata or {}), "pending": None,
                       "resolved": "rejected", "rejected_by": source or "unknown"}
        return Action.SKIP, st.subject, st.value

    def _history(self):
        """Retired statements for this space, fetched once and cached.

        Empty for a memory that was never persisted (nothing has been retired to disk yet) and for
        any caller that did not come through Store.load — both of which already hold everything
        they know in `self.statements`.
        """
        cached = getattr(self, "_history_pool", None)
        if cached is not None:
            return cached
        fetch = getattr(self, "_hydrate_history", None)
        pool = []
        if fetch:
            try:
                pool = fetch() or []
            except Exception:
                pool = []                    # a read must never fail for want of history
        self._history_pool = pool
        return pool

    def check(self, subject, policy=None, now=None):
        """Safety verdict (Feature 2): is it safe to ACT on what we currently know about `subject`?
        Deterministic — evaluates the active/flagged facts for the subject against the policy and
        returns {ok, subject, reasons, facts}. A FLAGGED conflict, low confidence, staleness, or a
        just-changed value blocks the action, with an explicit reason for each."""
        from vayl.security.safety import SafetyPolicy, evaluate_fact
        policy = policy or SafetyPolicy()
        now = now if now is not None else time.time()
        facts = [s for s in self.statements
                 if s.subject == subject and s.status in (Status.ACTIVE, Status.FLAGGED)]
        if not facts:
            return {"ok": False, "subject": subject,
                    "reasons": ["no active fact for this subject — nothing safe to act on"], "facts": []}
        reasons, checked = [], []
        for s in facts:
            p = self.provenance(s)
            ok, rs = evaluate_fact(p, policy, now)
            checked.append({**p, "ok": ok, "reasons": rs})
            reasons += rs
        return {"ok": len(reasons) == 0, "subject": subject, "reasons": reasons, "facts": checked}

    def safe_recall(self, question, policy=None, retrieve=None, k=None, now=None,
                    critical_categories=None):
        """Gated recall (Feature 2): answer only if every current fact behind the answer is safe to
        act on. Reuses Phase-1 provenance to find the facts the answer rests on, checks each against
        the policy, and also catches an unresolved conflict on any of those subjects. Returns
        {ok, answer|None, reasons, used}. When ok is False, `answer` is withheld and `reasons` says why."""
        from vayl.security.safety import SafetyPolicy, evaluate_fact
        policy = policy or SafetyPolicy()
        now = now if now is not None else time.time()
        # Pass the critical categories down: this gate can only judge facts it was handed, so a
        # critical fact ranked out of context would be invisible to it — the gate would pass on a
        # quietly incomplete answer, which is the failure it exists to prevent.
        answer, used = self.query(question, retrieve=retrieve, k=k, with_provenance=True,
                                  critical_categories=critical_categories)
        active_used = [p for p in used if p["status"] == Status.ACTIVE.value]
        reasons = []
        if not active_used:
            reasons.append("no current fact supports this — nothing safe to act on")
        subjects = set()
        for p in active_used:
            _ok, rs = evaluate_fact(p, policy, now)
            reasons += rs
            subjects.add(p["subject"])
        if policy.block_on_flagged:
            for s in self.statements:
                if s.status == Status.FLAGGED and s.subject in subjects:
                    reasons.append(f"unresolved conflict on '{s.subject}' — the value is disputed")
        ok = len(reasons) == 0
        return {"ok": ok, "answer": answer if ok else None, "reasons": reasons, "used": active_used}

    def graph_query(self, question, hops=2, k=15):
        """Deep/relational query via the Neo4j projection. Scales three ways: seed entities via the
        full-text index (O(log N)), pull a CAPPED neighborhood (hubs can't explode), then
        relevance-rank the edges to top-k so the LLM context stays bounded regardless of graph size.
        Returns (answer, seed_entities, retrieved_edges)."""
        if not self.graph:
            return self.query(question), [], []
        ns = self.ns   # scope every graph read to this (user/agent/run) tenant — no cross-tenant leak
        # Primary: in-DB vector index over edge embeddings — sub-second, scales, no hub cap.
        try:
            triples = self.graph.vector_search(_embed([question])[0], k, ns=ns)
            if triples:
                context = "; ".join(f"{h} {rel} {t}" for h, rel, t in triples)
                return _qa(context, question), ["(vector)"], triples
        except Exception:
            pass
        # Fallback (no embeddings/index): index-seed -> capped neighborhood -> python rank.
        seeds = self.graph.search_entities(question, ns=ns)
        if not seeds:
            sample = self.graph.all_edges(valid_only=False, limit=2000, ns=ns)
            names = {h for h, _, _, _ in sample} | {t for _, _, t, _ in sample}
            ql = question.lower(); seeds = [n for n in names if n.lower() in ql]
        edges = (self.graph.neighborhood(seeds, hops=hops, limit=800, ns=ns) if seeds
                 else self.graph.all_edges(limit=800, ns=ns))
        triples = _rank_triples(question, [(h, rel, t) for h, rel, t, vv in edges if vv], k)
        context = "; ".join(f"{h} {rel} {t}" for h, rel, t in triples) or "(no facts)"
        return _qa(context, question), seeds, triples


CASES = [
    ("C1 state-mgmt (Zustand→Redux)", ["We use Zustand for state.", "We switched to Redux Toolkit, dropping Zustand."]),
    ("C2 casing (forget snake_case)", ["API returns snake_case JSON.", "Forget snake_case — we standardized on camelCase."]),
    ("C3 favorite color (blue→green)  [was garbage before]", ["My favorite color is blue.", "Actually my favorite color is green now."]),
    ("C4 general (employer change)     [never seen before]", ["I work at Company A.", "I just started a new job at Company B."]),
]

def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY."); return
    print("\n\033[1mVAYL + LLM extractor — general reconciliation\033[0m")
    print("=" * 66)
    for name, msgs in CASES:
        m = LLMMemory()
        print(f"\n\033[1m{name}\033[0m")
        for t in msgs:
            for act, subj, val in m.add(t):
                print(f"  add {t!r:52} → {act.value:9} [{subj}={val}]")
        act, flg, sup, hist = m.view()
        answer = (act[0].value if len(act) == 1 and not flg
                  else ("⚠ " + ", ".join(s.value for s in act + flg) if flg
                        else " · ".join(f"{s.value}[{s.scope}]" for s in act) or "(nothing)"))
        retired = f"  (retired: {', '.join(s.value for s in sup)})" if sup else ""
        archived = f"  (history: {', '.join(s.value for s in hist)})" if hist else ""
        good = "\033[32m✓\033[0m" if len(act) == 1 and not flg else "\033[33m~\033[0m"
        print(f"  {good} current answer: \033[1m{answer}\033[0m{retired}{archived}")

if __name__ == "__main__":
    main()
