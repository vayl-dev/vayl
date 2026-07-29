/**
 * Vayl — reconciling memory for AI agents.
 *
 * A small TypeScript client so you call the MCP tools as methods instead of hand-writing
 * `tools/call` JSON:
 *
 *   import { Vayl } from "vayl";
 *
 *   const m = await Vayl.connect({ userId: "proj_7" });   // local: spawns `vayl-mcp` over stdio
 *   await m.remember("We moved off Redux to Zustand");
 *   console.log(await m.recall("what do we use for state?"));   // -> "Zustand"
 *   await m.close();
 *
 *   // a shared team server (authenticated streamable-HTTP):
 *   const m = await Vayl.connect({ url: "https://memory.acme.com/mcp", apiKey: "vayl_sk_…" });
 *
 * `remember` / `recall` / `forget` are named methods; any other tool is reachable via
 * `call(tool, args)` (e.g. `m.call("check_before_act", { subject: "…" })`). A default
 * `userId` / `agentId` / `runId` is sent on every call and can be overridden per call.
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

export class VaylError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "VaylError";
  }
}

export interface VaylOptions {
  /** Team-server URL (streamable-HTTP). Omit for local stdio. */
  url?: string;
  /** Bearer API key for the team server. */
  apiKey?: string;
  /** stdio command to spawn (default `vayl-mcp`). */
  command?: string;
  /** Extra args for the stdio command. */
  args?: string[];
  /** Environment for the spawned server (default: the current process env). */
  env?: Record<string, string>;
  /** Default memory-space keys sent on every call; overridable per call. */
  userId?: string;
  agentId?: string;
  runId?: string;
}

export type ToolArgs = Record<string, unknown>;

function clean(o: Record<string, unknown>): ToolArgs {
  const out: ToolArgs = {};
  for (const [k, v] of Object.entries(o)) {
    if (v !== undefined && v !== null && v !== "") out[k] = v;
  }
  return out;
}

function stringEnv(env: NodeJS.ProcessEnv): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) if (v !== undefined) out[k] = v;
  return out;
}

export class Vayl {
  private client: Client;
  private transport: StdioClientTransport | StreamableHTTPClientTransport | null = null;
  private scope: ToolArgs;
  private connected = false;
  private toolAccepts = new Map<string, Set<string>>();

  constructor(private opts: VaylOptions = {}) {
    this.scope = clean({ user_id: opts.userId, agent_id: opts.agentId, run_id: opts.runId });
    this.client = new Client({ name: "vayl-ts", version: "0.1.0" }, { capabilities: {} });
  }

  /** Construct and connect in one step. */
  static async connect(opts: VaylOptions = {}): Promise<Vayl> {
    const m = new Vayl(opts);
    await m.connect();
    return m;
  }

  async connect(): Promise<void> {
    if (this.connected) return;
    if (this.opts.url) {
      const headers = this.opts.apiKey ? { Authorization: `Bearer ${this.opts.apiKey}` } : undefined;
      this.transport = new StreamableHTTPClientTransport(new URL(this.opts.url), {
        requestInit: headers ? { headers } : undefined,
      });
    } else {
      this.transport = new StdioClientTransport({
        command: this.opts.command ?? "vayl-mcp",
        args: this.opts.args ?? [],
        env: this.opts.env ?? stringEnv(process.env),
      });
    }
    await this.client.connect(this.transport);
    this.connected = true;
    // Learn each tool's parameters so `call()` only sends scope keys a tool accepts — FastMCP
    // validates arguments strictly and rejects unknown ones (e.g. a default userId to health()).
    try {
      const { tools } = await this.client.listTools();
      for (const t of tools) {
        const props = ((t.inputSchema as { properties?: Record<string, unknown> })?.properties) ?? {};
        this.toolAccepts.set(t.name, new Set(Object.keys(props)));
      }
    } catch {
      // best-effort: fall back to sending the full scope
    }
  }

  private scopeFor(tool: string): ToolArgs {
    const accepts = this.toolAccepts.get(tool);
    const out: ToolArgs = {};
    for (const [k, v] of Object.entries(this.scope)) {
      if (!accepts || accepts.has(k)) out[k] = v;
    }
    return out;
  }

  /** Call any tool by name; returns its text result. Prefer the named methods where they exist. */
  async call(tool: string, args: ToolArgs = {}): Promise<string> {
    if (!this.connected) await this.connect();
    const res = await this.client.callTool({
      name: tool,
      arguments: { ...this.scopeFor(tool), ...clean(args) },
    });
    // callTool's result content is loosely typed by the SDK; narrow to the text blocks we return.
    const content = (res.content ?? []) as unknown as Array<{ type: string; text?: string }>;
    const parts: string[] = [];
    for (const c of content) {
      if (c.type === "text" && typeof c.text === "string") parts.push(c.text);
    }
    const text = parts.join("\n");
    if (res.isError) throw new VaylError(text || "tool returned an error");
    return text;
  }

  /** Store fact(s) from a natural-language statement (extracted and reconciled). */
  remember(text: string, extra: ToolArgs = {}): Promise<string> {
    return this.call("remember", { text, ...extra });
  }

  /** Answer a question from the active memory, or "I don't know". */
  recall(question: string, extra: ToolArgs = {}): Promise<string> {
    return this.call("recall", { question, ...extra });
  }

  /** Retract a fact — retired from the active set but kept in history. */
  forget(text: string, extra: ToolArgs = {}): Promise<string> {
    return this.call("forget", { text, ...extra });
  }

  async close(): Promise<void> {
    if (!this.connected) return;
    this.connected = false;
    await this.client.close();
  }
}

/** Scoped usage: connects, runs `fn`, and always closes. */
export async function withVayl<T>(opts: VaylOptions, fn: (m: Vayl) => Promise<T>): Promise<T> {
  const m = await Vayl.connect(opts);
  try {
    return await fn(m);
  } finally {
    await m.close();
  }
}
