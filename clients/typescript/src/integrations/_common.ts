/**
 * Shared pieces for the TypeScript framework integrations (Vercel AI SDK, Mastra).
 *
 * Every adapter exposes the SAME curated memory surface — the same tool names and the same
 * model-facing descriptions — so an agent behaves identically no matter which framework wraps Vayl.
 * The caller's scope (userId / agentId / runId) lives on the client and is NEVER a tool argument, so
 * the model can neither see nor choose whose memory it touches.
 */
import type { Vayl } from "../index.js";

export const DEFAULT_TOOLS = ["remember", "recall", "history", "forget", "list_memories"] as const;
export type ToolName = (typeof DEFAULT_TOOLS)[number];

/** name → the model-facing description. Written FOR the model: when to reach for each tool. */
export const TOOL_DESCRIPTIONS: Record<ToolName, string> = {
  remember:
    "Save fact(s) from a statement to long-term memory. Vayl RECONCILES: a new value supersedes the " +
    "old one for the same thing, and 'we dropped / stopped using X' retracts X — so memory stays " +
    "current instead of accumulating contradictions. Call this whenever the user states a durable " +
    "fact, decision, or preference, or changes one.",
  recall:
    'Answer a question from long-term memory. Returns the CURRENT value and says "I don\'t know" ' +
    "rather than guessing. Call this before answering anything that depends on what was said, chosen, " +
    "or preferred earlier.",
  history:
    "Show the full change-log for a subject — every value it has held, oldest to newest, with status. " +
    "Use for 'what did we use before / what changed' questions.",
  forget:
    "Retract a fact: it leaves the active set but stays in history. Use when the user says something " +
    "is no longer true and gives no replacement value.",
  list_memories: "List the current active facts in memory (with #ids). Use to review what is known.",
};

/** A single-string-argument (or nullary) call into a connected Vayl client, per curated tool. */
export interface MemoryOps {
  remember: (text: string) => Promise<string>;
  recall: (question: string) => Promise<string>;
  history: (subject: string) => Promise<string>;
  forget: (text: string) => Promise<string>;
  list_memories: () => Promise<string>;
}

/** Bind the curated operations to a connected client. Scope stays on the client. */
export function memoryOps(m: Vayl): MemoryOps {
  return {
    remember: (text) => m.remember(text),
    recall: (question) => m.recall(question),
    history: (subject) => m.call("history", { subject }),
    forget: (text) => m.forget(text),
    list_memories: () => m.call("list_memories"),
  };
}

/** The tool names to build, honoring include/exclude (defaults to the full curated set). */
export function selected(include?: ToolName[], exclude?: ToolName[]): ToolName[] {
  const ex = new Set(exclude ?? []);
  return (include ?? [...DEFAULT_TOOLS]).filter((n) => !ex.has(n));
}

export interface VaylToolsOptions {
  include?: ToolName[];
  exclude?: ToolName[];
}
