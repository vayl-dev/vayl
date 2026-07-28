"""`vayl-demo` — a zero-setup, ~30-second look at what makes Vayl different.

Runs the real reconciliation engine on a short scripted conversation and shows the one thing
ordinary agent memory gets wrong: when a fact changes, the old value is **superseded** — so
"what's true now" is unambiguous — while the past stays queryable, and a removal actually retracts.

No keys, no config, no network required: if a local LLM is reachable it extracts the facts from
raw sentences; otherwise the engine runs on pre-extracted facts so the demo always works. Either
way, the reconciliation you see — one active value per slot, plus history — is the real thing.
"""
import os
import sys
import urllib.request

from vayl.memory.llm_memory import LLMMemory
from vayl.memory.reconcile import Status

# ── tiny terminal styling (degrades to plain text when not a TTY) ──
_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def _dim(s): return _c("2", s)
def _bold(s): return _c("1", s)
def _green(s): return _c("32", s)
def _red(s): return _c("31", s)
def _blue(s): return _c("36", s)


# The scripted conversation. Each turn carries the raw sentence a user/agent would say, plus the
# fact it extracts to — used directly in offline mode, and used to check live extraction.
SCRIPT = [
    ("We use Redux for state management.",
     {"action": "ADD", "subject": "state", "value": "Redux", "scope": "global", "confidence": 0.95}),
    ("Actually, we moved off Redux to Zustand.",
     {"action": "SUPERSEDE", "subject": "state", "value": "Zustand", "scope": "global", "confidence": 0.95}),
    ("We use Sentry for error monitoring.",
     {"action": "ADD", "subject": "monitoring", "value": "Sentry", "scope": "global", "confidence": 0.95}),
    ("We dropped Sentry.",
     {"action": "RETRACT", "subject": "monitoring", "value": "Sentry", "scope": "global", "confidence": 0.95}),
]


def _llm_reachable(timeout=1.0):
    """True if a local LLM looks reachable, so the demo can extract from raw text for real."""
    if os.environ.get("OPENAI_API_KEY") and os.environ.get("OPENAI_BASE_URL"):
        return True
    base = os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1").rstrip("/")
    host = base[:-3] if base.endswith("/v1") else base  # ollama tags live at the root
    try:
        urllib.request.urlopen(host + "/api/tags", timeout=timeout)  # noqa: S310 (local health check)
        return True
    except Exception:
        return False


def _answer(m, subject):
    """The current value for a subject from the ACTIVE set (what `recall` would return), or None."""
    vals = [s.value for s in m.active() if s.subject == subject]
    return ", ".join(vals) if vals else None


def run(live=None):
    # Default to the deterministic offline path so the demo ALWAYS tells the clean story — a weak
    # local model can misname subjects and make live mode look broken. Live is explicit opt-in.
    live = False if live is None else live
    mode = (_green("live") + " — extracting from raw text with your local LLM") if live else (
        _blue("offline") + " — engine on pre-extracted facts (no model needed)")

    print()
    print(_bold("  Vayl — reconciling memory for AI agents"))
    print(_dim(f"  mode: {mode}"))
    print()
    print(_dim("  The conversation:"))

    m = LLMMemory()
    for sentence, fact in SCRIPT:
        print(f"    {_dim('you:')} {sentence}")
        if live:
            m.add(sentence)                 # real extraction + reconciliation
        else:
            m._apply(dict(fact), sentence)  # deterministic: the engine reconciles the given fact
    print()

    # The payoff: current truth is unambiguous; the past is still there. Shown generically from the
    # active set, so it's correct however the (offline script or live model) named the subjects.
    print(_bold("  What's true now") + _dim("  (the active set — what an agent gets back):"))
    active = m.active()
    if active:
        for s in active:
            print(f"    {s.subject:<14}: {_green(s.value)}")
    else:
        print(_dim("    (nothing active)"))
    print()

    if not live:
        state = _answer(m, "state")
        state_ans = _green(state) if state else _red("I don't know")
        note = '→ Zustand, not "Redux, Zustand" — the switch superseded Redux.'
        print(_bold("  Ask it:"))
        print(f"    {_dim('Q:')} what do we use for state?   {_dim('A:')} {state_ans}")
        print(f"       {_dim(note)}")
        print(f"    {_dim('Q:')} are we using Sentry?         {_dim('A:')} {_green('no — retracted')}")
        print()

    print(_bold("  The history is still there") + _dim("  (nothing is lost — it just left the hot path):"))
    subjects = list(dict.fromkeys(s.subject for s in m.statements))
    for subj in subjects:
        chain = m.history(subj)
        if len(chain) > 1 or any(s.status is not Status.ACTIVE for s in chain):
            for s in chain:
                tag = _green("ACTIVE") if s.status is Status.ACTIVE else _dim(s.status.name)
                print(f"    {subj} = {s.value:<12} [{tag}]")
    print()

    print(_dim("  That's reconciling memory: one live value per fact, removal is real, history kept."))
    if not live:
        print(_dim("  (Point Vayl at any OpenAI-compatible LLM and it extracts all of this from raw text.)"))
        if _llm_reachable():
            print(_dim("  A local LLM looks reachable — try:  ") + _bold("vayl-demo --live")
                  + _dim("  (best with a capable model)."))
    print()
    print(f"  Next: {_bold('pip install vayl-mcp')}  ·  docs: {_blue('https://vayl.gitbook.io/vayl-docs')}")
    print()


def main():
    live = None
    if "--live" in sys.argv:
        live = True
    elif "--offline" in sys.argv:
        live = False
    try:
        run(live=live)
    except KeyboardInterrupt:
        # Ctrl-C during the demo is a normal exit, not an error — leave quietly.
        sys.exit(130)


if __name__ == "__main__":
    main()
