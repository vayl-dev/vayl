"""
LOCOMO prompts: answerer and judges
===================================

Four prompts, in two pairs.

**Parity pair** (PARITY_ANSWER_PROMPT, PARITY_JUDGE_TEMPLATE) is reproduced verbatim from
mem0ai/memory-benchmarks (Apache-2.0, see NOTICE). Reproducing rather than rewriting is
the point: a Vayl score produced under a *different* answerer or judge is not comparable
to the score Mem0 publishes, and the comparison is the deliverable. Any improvement these
prompts could use is deliberately left unmade.

**Vayl pair** (ABSTAIN_ANSWER_PROMPT, STRICT_JUDGE_TEMPLATE) is original, and exists
because the parity pair cannot measure two things:

  * *Abstention.* The parity answerer is instructed to never say "not mentioned", so
    LOCOMO's adversarial category — where "not mentioned" is the correct answer — fails
    by construction. Upstream resolves this by dropping the category
    (CATEGORIES_TO_EVALUATE = [1, 2, 3, 4]). We keep it and let the answerer abstain.

  * *Staleness.* The parity judge awards CORRECT for "AT LEAST ONE correct item" and
    states "never penalize for being more detailed". An answer that reports a superseded
    value alongside the current one therefore scores exactly like an answer that reports
    only the current one. That is the failure mode a reconciling memory exists to prevent,
    so it needs a judge that can see it.

Both are reported. The parity number is the comparable one; the strict number is the
informative one. Neither is presented without the other.
"""
from __future__ import annotations

# ===============================================================================
# CATEGORIES
# ===============================================================================

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}

#: What upstream evaluates. Category 5 is excluded there.
PARITY_CATEGORIES = [1, 2, 3, 4]

#: What we evaluate by default — the same four, plus abstention.
DEFAULT_CATEGORIES = [1, 2, 3, 4, 5]

ANSWERER_MEMORY_LIMIT = 200


# ===============================================================================
# ANSWER GENERATION — parity (verbatim, Apache-2.0, mem0ai/memory-benchmarks)
# ===============================================================================

PARITY_ANSWER_PROMPT = """You are answering a question using retrieved memories from past conversations. Follow these reasoning steps IN ORDER.

## Step 1: SCAN ALL MEMORIES
Read EVERY memory below from first to last. For each one that contains information relevant to the question, note it. Do NOT stop after finding the first relevant memory — important details are often scattered across many memories, including ones far down the list. Give equal weight to ALL memories regardless of position — a memory near the end is just as likely to contain the answer as one near the beginning. In these memories, "User" refers to the main person whose memories these are.

## Step 2: ENTITY VERIFICATION
Confirm each relevant memory is about the correct person/entity. If the question asks "What does Person A like?" and a memory says "Person B likes X", do NOT use that memory to answer about Person A. In two-person conversations, both speakers' actions are relevant — if the question asks about person A and a memory attributes an action to person B (the other speaker), that information is still valid evidence from their shared conversations, but always check the attribution is correct.

## Step 3: COMBINE AND CROSS-REFERENCE
- COMBINE facts from multiple memories about the same topic. If one memory says "won first place" and another says "performed a piece titled X," those describe the same event — connect them.
- For listing/counting questions, extract EVERY distinct item from ALL memories. A single memory may contain multiple items. Think about what CATEGORIES of answers the question could have, then re-scan specifically for each category.
- For counting questions ("how many times", "how many X"), enumerate each distinct instance explicitly with its date or context BEFORE giving a final count. Do not estimate — list them out, then count the list.
- DECOMPOSE complex sentences: "an immersive X with Y, enjoys Z" contains multiple distinct facts. Each could be the answer.
- Connect related facts across memories: if one says "nearby lake" and another says "Lake Tahoe is great for kayaking", the nearby lake IS Lake Tahoe. If one says "bought X in Paris", infer the country is France.

## Step 4: SELECT THE BEST ANSWER
- Do NOT assume the highest-ranked memory is correct. Multiple memories may describe different events for the same topic. Compare each candidate's relevance to the SPECIFIC question, not its retrieval score. A lower-ranked memory that directly answers the question beats a higher-ranked one that is only tangentially related.
- ALWAYS choose the MOST SPECIFIC detail available. A proper name, title, or number beats a generic description. Rate each candidate as HIGH specificity (name, title, number, specific activity) or LOW (generic description), and prefer HIGH.
- Report what someone actually DID, not what was offered or available to them. "Has not tried X yet" means X was NOT done — disqualify it. "Joined X" or "has done X" means it WAS done — prefer it.
- When multiple memories repeat the same generic fact, that repetition does NOT make it more correct than a single memory with a more specific answer.
- Photos depict what was IN the photo, not facts about someone's daily life. Prefer direct statements over photo descriptions for inferences.
- Re-read the question carefully before answering. If it asks "what aspect/type/kind", answer with the specific aspect. If it asks "what did they discover they both enjoy", answer with the specific thing, not the setting.

## Step 5: TEMPORAL GROUNDING
These conversations took place around {reference_date}. All events occurred in 2022-2024.
- Calculate time relative to this date, NOT today. Never output 2025 or 2026.
- Use dates explicitly stated in memory text. Do not invent or estimate dates.
- When a question asks what someone "shared" or "mentioned" on a date, that date is when they TALKED about it — look for events shortly BEFORE that date.
- For "how long" questions, find the start and end dates explicitly, then compute the duration. Do not guess.
- TEMPORAL DISAMBIGUATION: When you find MULTIPLE instances of similar events at different dates, enumerate them all with their dates before picking. If the question uses past tense + "the" → select the instance closest to (and before) the reference date. If future tense ("plans to", "going to") → select the earliest planned date. NEVER default to the first-mentioned or highest-scored instance — the DATE determines the answer.

## Step 6: INCLUSION CHECK (for lists and counts)
If you found items during reasoning that you're tempted to exclude from your answer — STOP. Include them unless you have STRONG evidence they are wrong. The most common mistake is finding relevant items but then dropping them due to overly strict filtering. More items is better than fewer when there is supporting evidence.
- For counting: after enumerating, re-verify each item. Check for duplicates (same event described differently) and ensure you haven't missed items from memories late in the list.
- The question assumes something happened. Find WHAT happened, don't say nothing happened.

## Step 7: COMMIT AND ANSWER
Give a direct, specific answer. NEVER say "not specified", "not mentioned", "no record", or "the memories don't say" — if ANY memory contains relevant information, give the best answer from available evidence. No hedging, no caveats. If the question asks for a list, include ALL items found. NEVER return an empty answer when relevant memories exist.
- NEVER generate specific names, titles, places, or dates that do not appear in any memory above. If no memory contains the specific detail the question asks for, answer with what the memories DO contain rather than guessing.
- For open-domain/opinion questions ("Would X do Y?", "Is X considered Z?"):
  * Follow the DIRECT causal reasoning in the memories. Do NOT construct elaborate counter-arguments.
  * "Would X still do Y without Z?" — If memories show X does Y BECAUSE of Z, then without Z, answer "likely no."
  * "Would X do Y again soon?" — If the most recent attempt involved a bad experience (accident, scare, trauma), answer "likely no." A recent negative experience outweighs historical positive patterns.
  * For trait questions ("Is X considered Z?"): weigh ALL evidence including symbolic/indirect references. If there is SOME but not strong evidence, answer with a qualified degree ("somewhat") rather than flat "no."

# Instructions

## Misc

1. Make reasonable deductions based on your memories. Memory shows store with a lot of working people -> store employs a lot of people
2. If a memory describes something recognizable (e.g., "romantic drama about memory and relationships"), you may name it (e.g., "Eternal Sunshine of the Spotless Mind").
3. Use domain knowledge to connect facts: a game exclusive to one platform implies ownership of that platform. An unnamed company deal can be linked to a previously expressed brand preference.

{memories}

Question: {question}

Work through Steps 1-7, then give your final answer after "ANSWER:".
"""


# ===============================================================================
# ANSWER GENERATION — abstention-capable (Vayl original)
# ===============================================================================

ABSTAIN_ANSWER_PROMPT = """You are answering a question using retrieved memories from past conversations.

Each memory is tagged with its status:
  [current]     — believed true now
  [superseded]  — was true, has since been replaced by a newer value
  [historical]  — recorded as past at the time it was stated
  [flagged]     — conflicts with another memory and was not resolved

## How to use status
- Answer with [current] values. This is the default and covers most questions.
- Use [superseded] and [historical] memories ONLY when the question asks about the past
  ("what did they use before", "what was it originally", "how did it change").
- NEVER present a [superseded] value as if it were the current state. If a question asks
  what is true now and only a superseded memory is relevant, the current value is unknown —
  say so rather than reporting the old one.
- When a memory is [flagged], say the record is contradictory rather than picking a side.

## Reasoning
1. Read every memory. Relevant details are scattered, and position does not imply relevance.
2. Verify each memory is about the entity the question asks about.
3. Combine facts across memories about the same topic. Decompose compound statements.
4. Prefer the most specific detail available — a name, title or number over a description.
5. These conversations took place around {reference_date}; all events fall in 2022-2024.
   Compute relative dates from that reference, never from today, and never output 2025+.

## Answering
Give a direct, specific answer, then stop.

You MAY answer "Not mentioned" — and you SHOULD, whenever the memories do not actually
contain what was asked. Some questions are deliberately unanswerable from these
conversations. Guessing at one of those is a worse failure than abstaining: a confident
wrong answer is acted on, an abstention is not. Do not invent names, titles, places or
dates that appear in no memory above.

{memories}

Question: {question}

Give your final answer after "ANSWER:".
"""


# ===============================================================================
# JUDGE — parity (verbatim, Apache-2.0, mem0ai/memory-benchmarks)
# ===============================================================================

JUDGE_SYSTEM_PROMPT = ("You are evaluating conversational AI memory recall. "
                       "Return JSON only with the format requested.")

PARITY_JUDGE_TEMPLATE = """Label the generated answer as CORRECT or WRONG.

## Rules

1. **PARTIAL CREDIT**: If the generated answer includes AT LEAST ONE correct item from the gold answer's list, mark CORRECT. Getting 1 out of 2, 2 out of 4, etc. is always acceptable. Only mark WRONG if NONE of the gold answer items appear.

2. **PARAPHRASES COUNT**: Same concept in different words is CORRECT. "Chocolate raspberry tart" = "chocolate cake with raspberries". "Shelter meal service" = "volunteering at a homeless shelter". Emotions and sentiments in the same positive/negative family count as paraphrases: "proud" = "fulfilled" = "accomplished"; "huge success" = "relieved" = "thrilled" (all express positive achievement). Judge semantic meaning, not exact wording.

3. **EXTRA DETAIL IS FINE**: A longer answer that includes the gold answer's key facts plus additional information is CORRECT. Never penalize for being more detailed or specific. If the generated answer adds extra descriptive details beyond the gold answer while still referencing the same core entity or concept, mark CORRECT.

4. **DATE TOLERANCE**: Dates within 14 days of each other are CORRECT. Durations within 50% are CORRECT (e.g., "5 months" matches "six months"; "19 days" matches "two weeks"). Relative dates ("few days before November") match specific dates in the same window. A specific date (e.g., "February 2020") that is consistent with a vague reference (e.g., "a few years ago" relative to 2023) is CORRECT.

5. **SEMANTIC OVERLAP**: Judge whether the generated answer addresses the same topic and captures the core idea of the gold answer. Different wording, phrasing, or level of detail should not result in WRONG if the underlying concept matches. For EMOTIONS and FEELINGS questions, answers expressing sentiments in the same valence (positive/negative) about the same event are CORRECT — do not require the exact same emotion word.

6. **SAME REFERENT**: If the generated answer mentions or references the same named entity, character, person, or concept as the gold answer, mark CORRECT — even if the generated answer provides a different physical description or includes additional details. The key question is: does the generated answer identify the same core entity? If yes, it is CORRECT.

7. **FOCUS ON KNOWLEDGE, NOT WORDING**: The goal is to assess whether the system recalled the right fact. Minor differences in specificity, phrasing, or scope should not result in WRONG. Only mark WRONG when the generated answer demonstrates a genuinely different or incorrect understanding.

## ONLY mark WRONG if:
- The generated answer contains ZERO correct items from the gold answer
- The answer addresses a completely different topic

## Question
Question: {question}
Gold answer: {answer}
Generated answer: {response}

Return JSON with "reasoning" (one sentence) and "label" (CORRECT or WRONG). Do NOT include both labels."""


# ===============================================================================
# JUDGE — strict (Vayl original)
# ===============================================================================

STRICT_JUDGE_TEMPLATE = """Label the generated answer, then separately assess whether it presents outdated information as current.

You are judging a memory system. Two different things matter and they are scored separately:
whether the answer is RIGHT, and whether the answer is CLEAN.

## Part 1 — label: CORRECT or WRONG

1. **PARAPHRASES COUNT**: The same concept in different words is CORRECT. Judge semantic
   meaning, not wording. Sentiments of the same valence about the same event match.

2. **DATE TOLERANCE**: Dates within 14 days are CORRECT. Durations within 50% are CORRECT.

3. **COMPLETENESS**: If the gold answer is a list, the generated answer must contain a
   MAJORITY of its items to be CORRECT. One item out of four is not a recalled fact; it is
   a partial hit that happens to overlap. (This is the main way this rubric is stricter than
   a partial-credit rubric.)

4. **ABSTENTION IS AN ANSWER**: If the gold answer indicates the information is absent,
   unanswerable, or not mentioned, then "not mentioned" / "no record" / "the memories do not
   say" is CORRECT, and any confident specific answer is WRONG. Conversely, if the gold
   answer is a real fact, abstaining is WRONG.

5. Mark WRONG when the answer addresses a different topic, contradicts the gold answer, or
   states a specific fact that the gold answer does not support.

## Part 2 — stale: true or false

Set "stale" to true if the answer presents a value as current that the gold answer shows has
been replaced, OR if it reports several mutually exclusive values for one thing without
identifying which holds now.

This is independent of the label. An answer can be CORRECT and stale — that is precisely the
case worth counting: it mentioned the right value, but buried among superseded ones, leaving a
reader unable to tell which is true. Only set stale to true when values genuinely conflict;
listing several compatible facts is not staleness.

Set "ambiguous" to true if the answer offers more than one candidate value for a single-valued
question without committing to one.

## Question
Question: {question}
Gold answer: {answer}
Generated answer: {response}

Return JSON with:
  "reasoning"  — one sentence
  "label"      — "CORRECT" or "WRONG"
  "stale"      — true or false
  "ambiguous"  — true or false
Do NOT include both labels."""


# ===============================================================================
# DISPATCH
# ===============================================================================

def get_answer_prompt(memories: str, question: str, reference_date: str,
                      allow_abstention: bool = False) -> str:
    template = ABSTAIN_ANSWER_PROMPT if allow_abstention else PARITY_ANSWER_PROMPT
    return template.format(memories=memories, question=question, reference_date=reference_date)


def get_judge_prompt(question: str, gold: str, response: str, mode: str = "parity") -> str:
    template = STRICT_JUDGE_TEMPLATE if mode == "strict" else PARITY_JUDGE_TEMPLATE
    return template.format(question=question, answer=gold, response=response)


def preprocess_answer(category: int, answer: str) -> str:
    """Normalize the gold answer. Category 5 (adversarial) gold answers in LOCOMO are
    phrased many ways ('no information available', 'not mentioned'); collapse them so the
    strict judge's abstention rule has a consistent target."""
    a = (answer or "").strip()
    if category == 5:
        low = a.lower()
        markers = ("no information", "not mentioned", "not specified", "no answer",
                   "cannot be answered", "no record", "unanswerable")
        if any(mk in low for mk in markers) or not a:
            return "Not mentioned — this information does not appear in the conversations."
    return a
