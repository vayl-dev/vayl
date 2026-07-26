#!/usr/bin/env python3
"""
Messy real-user + wide-audience eval.
=====================================
Real users don't write clean facts. This suite stresses the extractor+reconciler on
the noise that actually shows up: typos, lowercase/no-punctuation, slang/emoji,
hedging ("idk maybe"), self-correction mid-sentence, MULTIPLE facts in one message,
facts buried in rambling, pure chatter (must NOT hallucinate a fact), venting,
code-switching, vague/pronoun updates ("we changed it"), and rumors (must NOT
over-retract). Spanning ~12 domains / personas — not just software.

Outcomes:
  CORRECT        — right current answer despite the mess
  SAFE           — kept the prior fact / skipped noise / flagged a genuine hedge
  MISSED         — dropped facts (e.g. only 1 of 3 facts in a multi-fact message)
  SILENTLY-WRONG — confidently stored / superseded to a false value (the trust-killer)

Needs an LLM (the heuristic can't handle diversity — that's Finding A):
    LLM_PROVIDER=openai OPENAI_MODEL=gpt-4o OPENAI_API_KEY=$(cat key) python3 messy_eval.py
"""
import os, concurrent.futures
from collections import defaultdict
from vayl.memory.llm_memory import LLMMemory

# (id, domain, noise, msgs, expect[], forbid[], mode)
# mode: default | multi | keep | skip | retract | coexist
CASES = [
    # --- typos / misspelling ---
    ("m01", "dev", "typo", ["state is handled by zustnad", "we swithced to redux toolkit"], ["redux"], ["zustand"], "default"),
    ("m02", "dev", "typo", ["we use postgre sql for the db", "sorry i mean postgresql 16 on rds"], ["postgre"], [], "default"),
    # --- lowercase / slang / no punctuation ---
    ("m03", "dev", "slang", ["ci is on jenkins", "ok we ditched jenkins lol gh actions now"], ["github", "gh action"], ["jenkins"], "default"),
    ("m04", "personal", "casual", ["moving to amsterdam next month btw"], ["amsterdam"], [], "default"),
    # --- hedging / uncertainty -> keep original, don't adopt ---
    ("m05", "biz", "hedge", ["we bill monthly", "hmm maybe switch to annual idk"], ["monthly"], ["annual"], "keep"),
    ("m06", "sales", "rumor", ["acme uses salesforce", "heard acme might drop salesforce not sure"], ["salesforce"], [], "keep"),
    # --- self-contradiction within one message ---
    ("m07", "dev", "self-correct", ["we use mysql, well actually postgres now"], ["postgres"], [], "default"),
    ("m08", "health", "self-correct", ["im vegan, well vegetarian, i still eat cheese"], ["vegetarian"], [], "default"),
    # --- MULTIPLE facts in one message (does it get them all?) ---
    ("m09", "dev", "multi-fact", ["we moved to postgres, alice is the new lead, and we're dropping redis"], ["postgres", "alice"], [], "multi"),
    ("m10", "ops", "multi-fact", ["deploy is now tuesdays and the on-call this week is bob"], ["tuesday", "bob"], [], "multi"),
    # --- rambling / buried fact ---
    ("m11", "dev", "rambling", ["so standup ran long everyone was tired anyway the main thing is we're deprecating the v1 api"], ["v1"], [], "default"),
    ("m12", "personal", "rambling", ["ugh traffic was insane today oh also i just got a new job at stripe"], ["stripe"], [], "default"),
    # --- pure noise / non-fact -> must SKIP, not hallucinate ---
    ("m13", "any", "chatter", ["hey how's it going?"], [], [], "skip"),
    ("m14", "any", "chatter", ["lol that meeting was wild"], [], [], "skip"),
    ("m15", "dev", "question", ["what database should we use?"], [], [], "skip"),
    # --- venting wrapping a real fact ---
    ("m16", "dev", "venting", ["this redux migration is killing me... anyway we're fully on redux now"], ["redux"], [], "default"),
    # --- code-switching / non-native ---
    ("m17", "dev", "code-switch", ["usamos react para el frontend"], ["react"], [], "default"),
    ("m18", "dev", "code-switch", ["notre base de données est postgresql"], ["postgre"], [], "default"),
    # --- messy correction / retraction ---
    ("m19", "personal", "correction", ["im based in berlin", "wait no i moved, munich now"], ["munich"], ["berlin"], "default"),
    ("m20", "ops", "retract", ["we monitor with datadog", "we dropped the vendor, datadog is gone"], [], ["datadog"], "retract"),
    # --- emoji / txt-speak ---
    ("m21", "dev", "emoji", ["we use TS 🎉", "switching to plain JS now ugh 😩"], ["javascript", "js"], ["typescript"], "default"),
    ("m22", "personal", "emoji", ["fav color = blue", "nah green now 💚"], ["green"], ["blue"], "default"),
    # --- vague / pronoun update -> don't lose the fact, don't store garbage ---
    ("m23", "dev", "vague", ["we bundle with webpack", "yeah we changed it"], ["webpack"], ["unspecified", "unknown"], "keep"),
    ("m24", "any", "vague", ["yeah we switched"], [], ["unspecified", "unknown"], "skip"),
    # --- wide audience: more domains ---
    ("m25", "finance", "correction", ["our runway is 18 months", "updated, 12 months now after the hire"], ["12"], ["18"], "default"),
    ("m26", "scheduling", "casual", ["standup at 9am", "we pushed standup to 10"], ["10"], ["9am"], "default"),
    ("m27", "product", "casual", ["the free plan has 3 seats", "bumped free plan to 5 seats"], ["5"], ["3 seat"], "default"),
    ("m28", "compliance", "correction", ["we store data in the us", "migrated data to eu-central now"], ["eu"], ["the us"], "default"),
    ("m29", "hr", "correction", ["my manager is sarah", "sarah left, i report to tom now"], ["tom"], ["sarah"], "default"),
    ("m30", "dev", "multi+typo", ["k so postgres now (was mysql) and btw we got soc2 certified last week"], ["postgres", "soc2"], [], "multi"),
]

def score(active_str, retired_str, flagged_str, expect, forbid, mode):
    if mode == "skip":
        # noise/vague-with-no-value: nothing false should be ACTIVE
        bad = any(f in active_str for f in forbid) or (not expect and active_str.strip())
        return "WRONG" if any(f in active_str for f in forbid) else ("SAFE" if not active_str.strip() else "SAFE")
    if mode == "keep":
        if any(f in active_str for f in forbid): return "WRONG"       # adopted the rumor / garbage
        if any(e in retired_str for e in expect): return "WRONG"      # over-deleted the true fact
        return "CORRECT" if all(e in active_str for e in expect) else "SAFE"
    if mode == "retract":
        return "WRONG" if any(f in active_str for f in forbid) else "CORRECT"
    if mode in ("multi", "coexist"):
        got = [e for e in expect if e in active_str]
        if len(got) == len(expect): return "CORRECT"
        return "MISSED" if got else "WRONG"
    # default
    if any(f in active_str for f in forbid): return "WRONG"
    return "CORRECT" if all(e in active_str for e in expect) else "SAFE"

def run_case(c):
    cid, domain, noise, msgs, expect, forbid, mode = c
    try:
        m = LLMMemory()
        for t in msgs: m.add(t)
        act, flg, sup, hist = m.view()
        av = [s.value for s in act]; fv = [s.value for s in flg]; rv = [s.value for s in sup + hist]
        out = score(" | ".join(av).lower(), " | ".join(rv).lower(), " | ".join(fv).lower(), expect, forbid, mode)
        return dict(id=cid, domain=domain, noise=noise, mode=mode, outcome=out, active=av, flagged=fv, retired=rv)
    except Exception as e:
        return dict(id=cid, domain=domain, noise=noise, mode=mode, outcome="ERROR", active=[], flagged=[], retired=[str(e)[:50]])

def main():
    if not (os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY")):
        print("Set a provider key (OPENAI_API_KEY / ANTHROPIC_API_KEY / GROQ_API_KEY)."); return
    model = os.environ.get("OPENAI_MODEL") or os.environ.get("ANTHROPIC_MODEL") or os.environ.get("GROQ_MODEL") or "?"
    print(f"\n\033[1mMESSY REAL-USER EVAL — {len(CASES)} cases · model={model}\033[0m\n" + "=" * 74)
    with concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("EVAL_WORKERS", "6"))) as ex:
        results = list(ex.map(run_case, CASES))

    C = {"CORRECT": "\033[32m", "SAFE": "\033[36m", "MISSED": "\033[33m", "WRONG": "\033[31m", "ERROR": "\033[35m"}
    for r in results:
        c = C.get(r["outcome"], "")
        print(f"\n{c}{r['id']} [{r['domain']}/{r['noise']}] {r['outcome']}\033[0m  active={r['active']} flagged={r['flagged']} retired={r['retired']}")

    tally = defaultdict(int); by_noise = defaultdict(lambda: defaultdict(int))
    for r in results:
        tally[r["outcome"]] += 1; by_noise[r["noise"]][r["outcome"]] += 1
    n = len(results)
    print("\n" + "=" * 74)
    print(f"\033[1mHEADLINE\033[0m  correct={tally['CORRECT']}  safe={tally['SAFE']}  "
          f"missed={tally['MISSED']}  \033[31msilently-wrong={tally['WRONG']}\033[0m  err={tally['ERROR']}  (of {n})")
    print(f"  silently-wrong rate : {100*tally['WRONG']/n:.1f}%   ·   dropped-fact (MISSED) rate : {100*tally['MISSED']/n:.1f}%")
    print(f"\n  by noise type:")
    for noise, d in sorted(by_noise.items()):
        parts = " ".join(f"{k}:{v}" for k, v in d.items())
        print(f"    {noise:14} {parts}")
    wrong = [r for r in results if r["outcome"] in ("WRONG", "MISSED")]
    if wrong:
        print(f"\n\033[1mInspect (wrong / missed):\033[0m")
        for r in wrong:
            print(f"  {r['id']} [{r['noise']}] {r['outcome']}: active={r['active']} retired={r['retired']}")

if __name__ == "__main__":
    main()
