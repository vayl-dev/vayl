/**
 * Mastra adapter — give an agent Vayl's *reconciling* long-term memory as tools.
 *
 *   import { Agent } from "@mastra/core/agent";
 *   import { Vayl } from "vayl";
 *   import { vaylTools } from "vayl/mastra";
 *
 *   const m = await Vayl.connect({ userId: "proj_7" });
 *   const agent = new Agent({
 *     id: "assistant",
 *     name: "Assistant",
 *     instructions: "Call recall before answering; call remember when the user states or changes a fact.",
 *     model: "openai/gpt-4o",              // Mastra v1 model-router string (no @ai-sdk/* import)
 *     tools: vaylTools(m),                 // remember / recall / history / forget / list_memories
 *   });
 *
 * Why tools, not Mastra's Memory class: Mastra's `Memory` owns persistence in its own store and injects
 * context automatically. Vayl is an external, reconciling store the agent should *deliberately* read and
 * write, so each op is a `createTool`. Scope (userId / agentId / runId) is bound on the client, never a
 * tool argument the model can set. Targets Mastra v1 — `execute(input, context)` with input first.
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";
import type { Vayl } from "../index.js";
import { memoryOps, selected, TOOL_DESCRIPTIONS, type VaylToolsOptions } from "./_common.js";

/** Build Mastra tools over a connected Vayl client, keyed by tool name. */
export function vaylTools(m: Vayl, opts: VaylToolsOptions = {}) {
  const ops = memoryOps(m);
  const s = z.string();
  const all = {
    remember: createTool({
      id: "remember",
      description: TOOL_DESCRIPTIONS.remember,
      inputSchema: z.object({ text: z.string().describe("The statement to store.") }),
      outputSchema: s,
      execute: async ({ text }) => ops.remember(text),
    }),
    recall: createTool({
      id: "recall",
      description: TOOL_DESCRIPTIONS.recall,
      inputSchema: z.object({ question: z.string().describe("The question to answer from memory.") }),
      outputSchema: s,
      execute: async ({ question }) => ops.recall(question),
    }),
    history: createTool({
      id: "history",
      description: TOOL_DESCRIPTIONS.history,
      inputSchema: z.object({ subject: z.string().describe("The subject whose change-log to show.") }),
      outputSchema: s,
      execute: async ({ subject }) => ops.history(subject),
    }),
    forget: createTool({
      id: "forget",
      description: TOOL_DESCRIPTIONS.forget,
      inputSchema: z.object({ text: z.string().describe("The fact to retract.") }),
      outputSchema: s,
      execute: async ({ text }) => ops.forget(text),
    }),
    list_memories: createTool({
      id: "list_memories",
      description: TOOL_DESCRIPTIONS.list_memories,
      inputSchema: z.object({}),
      outputSchema: s,
      execute: async () => ops.list_memories(),
    }),
  };
  const out: Record<string, (typeof all)[keyof typeof all]> = {};
  for (const n of selected(opts.include, opts.exclude)) out[n] = all[n];
  return out;
}
