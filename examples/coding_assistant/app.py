"""A coding assistant that remembers your project's decisions — and forgets the ones you changed.

The project's stack is stated in plain sentences as it evolves. With the `coding` preset, Vayl folds
different names for the same thing ("state_management", "state_library") onto one slot, so a switch
retires the old choice. Ask what the project uses and you get the current answer, not "Redux, Zustand".

    python app.py          # offline: no model, no key — the reconciler runs on pre-extracted facts
    python app.py --live   # extract from the raw sentences with your LLM (a local Ollama model works)
"""
import os
import sys

os.environ.setdefault("VAYL_SLOT_SCHEMA", "preset:coding")   # read at import, so set it first

from vayl.memory.llm_memory import LLMMemory  # noqa: E402

# How the project evolved, as said in chat, with the fact a model would extract from each line.
# The subjects deliberately vary — the preset's aliases fold them onto one canonical slot. The pnpm
# switch is extracted as a plain ADD (a weak model's typical mislabel); the preset still retires npm.
SESSION = [
    ("Let's use Redux for state.",     "ADD",       "state_management", "Redux"),
    ("We're on npm for packages.",     "ADD",       "pkg_manager",      "npm"),
    ("Tests run on Jest.",             "ADD",       "test_framework",   "Jest"),
    ("We moved off Redux to Zustand.", "SUPERSEDE", "state_library",    "Zustand"),
    ("Switched the repo to pnpm.",     "ADD",       "package_manager",  "pnpm"),
    ("We dropped Jest for Vitest.",    "SUPERSEDE", "test_runner",      "Vitest"),
]

QUESTIONS = {
    "state_library": "What do we use for state?",
    "package_manager": "Which package manager?",
    "test_runner": "What runs our tests?",
}


def main(live=False):
    m = LLMMemory()
    for sentence, action, subject, value in SESSION:
        print("you:", sentence)
        if live:
            m.add(sentence)
        else:
            m._apply({"action": action, "subject": subject, "value": value,
                      "scope": "global", "confidence": 0.95}, sentence)

    current = {s.subject: s.value for s in m.active()}
    print("\nWhat the agent gets back now:")
    for slot, question in QUESTIONS.items():
        print(f"  {question:<28} {current.get(slot, '(not known)')}")

    print("\nHistory (kept for audit, never returned as current):")
    for slot in QUESTIONS:
        print(f"  {slot}: " + " -> ".join(f"{s.value} [{s.status.name}]" for s in m.history(slot)))

    if not live:  # the stale choices are retired, not handed back alongside the new ones
        assert current == {"state_library": "Zustand", "package_manager": "pnpm",
                           "test_runner": "Vitest"}, current


if __name__ == "__main__":
    main(live="--live" in sys.argv)
