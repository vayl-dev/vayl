/**
 * Vercel AI SDK adapter — give an agent Vayl's *reconciling* long-term memory as tools.
 *
 *   import { generateText, stepCountIs } from "ai";
 *   import { openai } from "@ai-sdk/openai";
 *   import { Vayl } from "vayl";
 *   import { vaylTools } from "vayl/vercel";
 *
 *   const m = await Vayl.connect({ userId: "proj_7" });
 *   const { text } = await generateText({
 *     model: openai("gpt-4o"),
 *     tools: vaylTools(m),                 // remember / recall / history / forget / list_memories
 *     stopWhen: stepCountIs(5),            // let the model take tool steps
 *     prompt: "We moved off Redux to Zustand. What do we use now?",
 *   });
 *   await m.close();
 *
 * The agent's memory *reconciles* — a plain vector store would hand back both "Redux" and "Zustand".
 * Scope (userId / agentId / runId) is bound on the client, never a tool argument the model can set.
 *
 * Targets the current AI SDK (v7): tools use `inputSchema` (not the pre-v5 `parameters`).
 */
import { tool, type ToolSet } from "ai";
import { z } from "zod";
import type { Vayl } from "../index.js";
import { memoryOps, selected, TOOL_DESCRIPTIONS, type VaylToolsOptions } from "./_common.js";

/** Build a Vercel AI SDK tool set over a connected Vayl client, keyed by tool name. */
export function vaylTools(m: Vayl, opts: VaylToolsOptions = {}): ToolSet {
  const ops = memoryOps(m);
  const all = {
    remember: tool({
      description: TOOL_DESCRIPTIONS.remember,
      inputSchema: z.object({ text: z.string().describe("The statement to store.") }),
      execute: async ({ text }) => ops.remember(text),
    }),
    recall: tool({
      description: TOOL_DESCRIPTIONS.recall,
      inputSchema: z.object({ question: z.string().describe("The question to answer from memory.") }),
      execute: async ({ question }) => ops.recall(question),
    }),
    history: tool({
      description: TOOL_DESCRIPTIONS.history,
      inputSchema: z.object({ subject: z.string().describe("The subject whose change-log to show.") }),
      execute: async ({ subject }) => ops.history(subject),
    }),
    forget: tool({
      description: TOOL_DESCRIPTIONS.forget,
      inputSchema: z.object({ text: z.string().describe("The fact to retract.") }),
      execute: async ({ text }) => ops.forget(text),
    }),
    list_memories: tool({
      description: TOOL_DESCRIPTIONS.list_memories,
      inputSchema: z.object({}),
      execute: async () => ops.list_memories(),
    }),
  } satisfies ToolSet;
  const out: ToolSet = {};
  for (const n of selected(opts.include, opts.exclude)) out[n] = all[n];
  return out;
}
