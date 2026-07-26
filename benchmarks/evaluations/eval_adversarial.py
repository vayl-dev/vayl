#!/usr/bin/env python3
"""
Adversarial eval set — measure the SILENTLY-WRONG rate.
=======================================================
The only number that separates a trustworthy reconciling memory from a plausible
one: how often does it confidently take a WRONG action instead of flagging?

  CORRECT        — produced the right current answer (resolved or coexisted correctly)
  SAFE-degrade   — flagged/skipped when unsure; current answer NOT wrong (honest)
  SILENTLY-WRONG — confidently held a false value as current truth (the trust-killer)

45 cases across 12 categories that attack each decision boundary:
  supersede, ambiguous, hypothetical, sarcasm, question, coexist,
  dedup, refine, negation, out-of-order, entity-distinct, unrelated.

Cases run in parallel (independent); messages within a case run in order.
    ANTHROPIC_API_KEY=$(cat ak.txt) python3 eval_adversarial.py
"""
import os, concurrent.futures
from collections import defaultdict
from vayl.memory.llm_memory import LLMMemory

# mode "default": SILENTLY-WRONG if any `forbid` string is held ACTIVE (confident + false)
# mode "coexist": SILENTLY-WRONG if any `expect` value was RETIRED (a true fact discarded)
CASES = [
    # ---- clean supersede: real change signal -> should SUPERSEDE, retire old ----
    dict(id="sup01", cat="supersede", msgs=["We use Zustand for state.", "We migrated to Redux Toolkit last sprint."], expect=["redux"], forbid=["zustand"]),
    dict(id="sup02", cat="supersede", msgs=["Deploys go out on Fridays.", "We moved deploys to Tuesdays."], expect=["tuesday"], forbid=["friday"]),
    dict(id="sup03", cat="supersede", msgs=["The primary DB is MySQL.", "We've fully migrated to PostgreSQL."], expect=["postgre"], forbid=["mysql"]),
    dict(id="sup04", cat="supersede", msgs=["I live in Berlin.", "I moved to Amsterdam last month."], expect=["amsterdam"], forbid=["berlin"]),
    dict(id="sup05", cat="supersede", msgs=["Our CI runs on Jenkins.", "We replaced Jenkins with GitHub Actions."], expect=["github"], forbid=["jenkins"]),
    dict(id="sup06", cat="supersede", msgs=["The team lead is Sarah.", "Sarah left; Marcus is the new team lead."], expect=["marcus"], forbid=["sarah"]),
    dict(id="sup07", cat="supersede", msgs=["We bill customers monthly.", "We switched all customers to annual billing."], expect=["annual"], forbid=["monthly"]),
    dict(id="sup08", cat="supersede", msgs=["The default branch is master.", "We renamed the default branch to main."], expect=["main"], forbid=["master"]),

    # ---- ambiguous: contradiction, NO firm change signal -> should FLAG, keep original ----
    dict(id="amb01", cat="ambiguous", msgs=["We use PostgreSQL.", "Someone floated maybe moving to MySQL at some point."], expect=["postgre"], forbid=["mysql"]),
    dict(id="amb02", cat="ambiguous", msgs=["The app is written in Python.", "There was some talk about rewriting it in Go."], expect=["python"], forbid=["go"]),
    dict(id="amb03", cat="ambiguous", msgs=["We deploy on AWS.", "GCP came up in a meeting as an alternative."], expect=["aws"], forbid=["gcp"]),
    dict(id="amb04", cat="ambiguous", msgs=["Our design tool is Figma.", "A few people on the team prefer Sketch."], expect=["figma"], forbid=["sketch"]),
    dict(id="amb05", cat="ambiguous", msgs=["Standups are at 9am.", "Maybe we should move standups earlier?"], expect=["9"], forbid=["earlier"]),
    dict(id="amb06", cat="ambiguous", msgs=["We use REST for our API.", "GraphQL might be a better fit down the line."], expect=["rest"], forbid=["graphql"]),

    # ---- hypothetical / conditional -> should SKIP ----
    dict(id="hyp01", cat="hypothetical", msgs=["We use PostgreSQL.", "If we switched to MongoDB it would scale better."], expect=["postgre"], forbid=["mongo"]),
    dict(id="hyp02", cat="hypothetical", msgs=["Auth uses JWT.", "Imagine if we used server sessions instead — simpler maybe."], expect=["jwt"], forbid=["session"]),
    dict(id="hyp03", cat="hypothetical", msgs=["We run a monolith.", "Suppose we broke it into microservices someday."], expect=["monolith"], forbid=["microservice"]),
    dict(id="hyp04", cat="hypothetical", msgs=["The stack is React.", "Had we chosen Vue, onboarding would be faster."], expect=["react"], forbid=["vue"]),

    # ---- sarcasm / rhetorical -> should SKIP ----
    dict(id="sar01", cat="sarcasm", msgs=["We use Python.", "Oh yeah, we totally rewrote everything in COBOL overnight."], expect=["python"], forbid=["cobol"]),
    dict(id="sar02", cat="sarcasm", msgs=["We use TypeScript.", "Sure, and we're TOTALLY switching to raw JavaScript for fun."], expect=["typescript"], forbid=["javascript"]),
    dict(id="sar03", cat="sarcasm", msgs=["Our mascot is a fox.", "Oh definitely, let's put a dragon on everything now."], expect=["fox"], forbid=["dragon"]),

    # ---- question, not a fact -> should SKIP ----
    dict(id="qst01", cat="question", msgs=["We use Zustand.", "Did we move off Zustand to Redux yet?"], expect=["zustand"], forbid=["redux"]),
    dict(id="qst02", cat="question", msgs=["The DB is Postgres.", "Should we consider MongoDB?"], expect=["postgre"], forbid=["mongo"]),
    dict(id="qst03", cat="question", msgs=["I'm based in Berlin.", "Wait, am I still in Berlin or was it Munich?"], expect=["berlin"], forbid=["munich"]),

    # ---- coexist: different scope, BOTH true -> retiring either is silently wrong ----
    dict(id="cox01", cat="coexist", mode="coexist", msgs=["The web app uses React.", "The mobile app uses Swift."], expect=["react", "swift"]),
    dict(id="cox02", cat="coexist", mode="coexist", msgs=["Staging runs Postgres 14.", "Production runs Postgres 16."], expect=["14", "16"]),
    dict(id="cox03", cat="coexist", mode="coexist", msgs=["My work laptop is a Mac.", "My personal laptop runs Linux."], expect=["mac", "linux"]),
    dict(id="cox04", cat="coexist", mode="coexist", msgs=["The US region uses us-east-1.", "The EU region uses eu-central-1."], expect=["us-east-1", "eu-central-1"]),
    dict(id="cox05", cat="coexist", mode="coexist", msgs=["Frontend is deployed on Vercel.", "Backend is deployed on Fly.io."], expect=["vercel", "fly"]),

    # ---- dedup: restatement, no new info -> should DEDUP (single active) ----
    dict(id="ded01", cat="dedup", msgs=["We use Redux for state.", "State management is handled by Redux."], expect=["redux"]),
    dict(id="ded02", cat="dedup", msgs=["The database is PostgreSQL.", "We're on Postgres."], expect=["postgre"]),
    dict(id="ded03", cat="dedup", msgs=["I live in Amsterdam.", "My home city is Amsterdam."], expect=["amsterdam"]),

    # ---- refine: same fact + more detail -> should REFINE ----
    dict(id="ref01", cat="refine", msgs=["The DB is PostgreSQL.", "It's PostgreSQL 16 hosted on RDS."], expect=["postgre"], forbid=[]),
    dict(id="ref02", cat="refine", msgs=["We use Stripe for payments.", "Specifically Stripe Billing for subscriptions."], expect=["stripe"], forbid=[]),
    dict(id="ref03", cat="refine", msgs=["Auth is via OAuth.", "OAuth 2.0 with PKCE, through Auth0."], expect=["oauth"], forbid=[]),

    # ---- negation: removal/denial -> stale must NOT stay active; and don't over-delete ----
    dict(id="neg01", cat="negation", msgs=["We use Sentry for error tracking.", "We dropped Sentry entirely."], expect=[], forbid=["sentry"]),
    dict(id="neg02", cat="negation", msgs=["Feature flag X is enabled.", "Flag X is now disabled."], expect=["disabled"], forbid=["enabled"]),
    dict(id="neg03", cat="negation", msgs=["We support IE11.", "We no longer support IE11."], expect=[], forbid=["ie11"]),
    dict(id="neg04", cat="negation", msgs=["We use Datadog for monitoring.", "We've removed Datadog from the stack."], expect=[], forbid=["datadog"]),
    dict(id="neg05", cat="negation", msgs=["The app has a comments feature.", "We killed the comments feature."], expect=[], forbid=["comments"]),
    dict(id="neg06", cat="negation", mode="coexist", msgs=["We use Redis for caching.", "We might get rid of Redis soon."], expect=["redis"]),  # unsure -> keep/flag, don't delete

    # ---- out-of-order: stale/historical arrives AFTER current -> must NOT revert ----
    dict(id="ooo01", cat="out_of_order", msgs=["As of this year we use Redux.", "Back in 2022 we used Zustand."], expect=["redux"], forbid=["zustand"]),
    dict(id="ooo02", cat="out_of_order", msgs=["We now bill annually.", "Historically we billed monthly."], expect=["annual"], forbid=["monthly"]),
    dict(id="ooo03", cat="out_of_order", msgs=["I currently work at Company B.", "I used to work at Company A."], expect=["company b"], forbid=["company a"]),
    dict(id="ooo04", cat="out_of_order", msgs=["We use Postgres now.", "We previously used MongoDB before the migration."], expect=["postgres"], forbid=["mongo"]),
    dict(id="ooo05", cat="out_of_order", msgs=["Back in 2021 the team used Angular.", "We use React today."], expect=["react"], forbid=["angular"]),

    # ---- entity-distinct: two distinct facts -> coexist, NOT supersede ----
    dict(id="ent01", cat="entity", mode="coexist", msgs=["Redis handles our caching.", "Kafka handles our event streaming."], expect=["redis", "kafka"]),
    dict(id="ent02", cat="entity", mode="coexist", msgs=["We use pytest for unit tests.", "We use Playwright for end-to-end tests."], expect=["pytest", "playwright"]),
    dict(id="ent03", cat="entity", mode="coexist", msgs=["The marketing site is on WordPress.", "The app dashboard is on Next.js."], expect=["wordpress", "next"]),

    # ---- unrelated: independent facts -> both stored, no interference ----
    dict(id="unr01", cat="unrelated", mode="coexist", msgs=["The office is in Berlin.", "We use Slack for team chat."], expect=["berlin", "slack"]),
    dict(id="unr02", cat="unrelated", mode="coexist", msgs=["Our logo is blue.", "The CEO is Jane."], expect=["blue", "jane"]),
]


def run_case(c):
    try:
        m = LLMMemory()
        actions = []
        for t in c["msgs"]:
            for act, subj, val in m.add(t):
                actions.append(act.value)
        active, flagged, retired, hist = m.view()
        av = [s.value for s in active]; fv = [s.value for s in flagged]; rv = [s.value for s in retired]
        astr = " | ".join(av).lower(); rstr = " | ".join(rv).lower()
        expect = c.get("expect", []); forbid = c.get("forbid", [])

        if c.get("mode") == "coexist":
            lost = [e for e in expect if e in rstr]                 # a true fact was retired
            here = all(e in astr for e in expect)
            silently_wrong = bool(lost)
            correct = here and not silently_wrong
        else:
            silently_wrong = any(f in astr for f in forbid)         # false value held active
            correct = all(e in astr for e in expect) and not silently_wrong

        outcome = "WRONG" if silently_wrong else ("CORRECT" if correct else "SAFE")
        return dict(id=c["id"], cat=c["cat"], outcome=outcome, act=(actions[-1] if actions else "SKIP"),
                    active=av, flagged=fv, retired=rv)
    except Exception as e:
        return dict(id=c["id"], cat=c["cat"], outcome="ERROR", act=str(e)[:40],
                    active=[], flagged=[], retired=[])


def main():
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        print("Set a provider key."); return
    print(f"\n\033[1mADVERSARIAL EVAL — {len(CASES)} cases · silently-wrong rate\033[0m")
    print("=" * 74)
    workers = int(os.environ.get("EVAL_WORKERS", "8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(run_case, CASES))

    by_cat = defaultdict(lambda: defaultdict(int))
    tally = defaultdict(int)
    for r in results:
        by_cat[r["cat"]][r["outcome"]] += 1
        tally[r["outcome"]] += 1

    C = {"CORRECT": "\033[32m", "SAFE": "\033[36m", "WRONG": "\033[31m", "ERROR": "\033[35m"}
    print(f"\n{'category':<14}{'n':>3}  {'✓correct':>9} {'~safe':>7} {'✗wrong':>7} {'err':>4}")
    print("-" * 74)
    order = ["supersede","ambiguous","hypothetical","sarcasm","question","coexist",
             "dedup","refine","negation","out_of_order","entity","unrelated"]
    for cat in order:
        d = by_cat[cat]; n = sum(d.values())
        print(f"{cat:<14}{n:>3}  {d['CORRECT']:>9} {d['SAFE']:>7} "
              f"{C['WRONG'] if d['WRONG'] else ''}{d['WRONG']:>7}\033[0m {d['ERROR']:>4}")

    n = len(results)
    print("-" * 74)
    print(f"{'TOTAL':<14}{n:>3}  \033[32m{tally['CORRECT']:>9}\033[0m "
          f"\033[36m{tally['SAFE']:>7}\033[0m {C['WRONG']}{tally['WRONG']:>7}\033[0m {tally['ERROR']:>4}")

    sw = tally["WRONG"]; trustworthy = tally["CORRECT"] + tally["SAFE"]
    print(f"\n\033[1mHEADLINE\033[0m")
    print(f"  silently-wrong rate : \033[1m{sw}/{n} = {100*sw/n:.1f}%\033[0m  (confident + false — the trust-killer)")
    print(f"  trustworthy         : {trustworthy}/{n} = {100*trustworthy/n:.1f}%  (right answer OR honest flag/skip)")
    print(f"  resolved-correctly  : {tally['CORRECT']}/{n} = {100*tally['CORRECT']/n:.1f}%")

    wrong = [r for r in results if r["outcome"] == "WRONG"]
    if wrong:
        print(f"\n\033[31m\033[1mSILENTLY-WRONG cases (each is a trust failure — inspect):\033[0m")
        for r in wrong:
            print(f"  {r['id']} [{r['cat']}] last-action={r['act']}  active={r['active']} flagged={r['flagged']} retired={r['retired']}")
    else:
        print(f"\n\033[32mNo silently-wrong cases. Every miss degraded to a flag/skip.\033[0m")

    err = [r for r in results if r["outcome"] == "ERROR"]
    if err:
        print(f"\n\033[35mERRORS ({len(err)}): " + ", ".join(f"{r['id']}:{r['act']}" for r in err) + "\033[0m")

if __name__ == "__main__":
    main()
