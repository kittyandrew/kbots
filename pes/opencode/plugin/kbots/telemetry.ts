// Counts what the sidecar does in each session (one per chat) and keeps its latest problems. The PES bot polls the
// snapshot over the plugin RPC, serves it to Prometheus and logs the problems (pes/src/telemetry.py), so nothing on
// the monitoring side has to reach this isolated container. No chat or answer text is kept: only error messages.
import type { Context } from "@opencode/plugin/promise/plugin"

const MAX_PROBLEMS = 200 // the bot polls every 15 s; only more problems than this in one interval lose the oldest
const PLAN_EVERY = 5 * 60_000
const USAGE_URL = "https://chatgpt.com/backend-api/wham/usage" // the ChatGPT plan's windows, as the Codex CLI reads them

export const Telemetry = {
  id: "kbots.telemetry",
  methods: { snapshot: { input: { type: "object", additionalProperties: false, properties: {} }, output: { type: "object" } } },
  events: {}
} as const

type Counts = Record<string, number>
interface Chat {
  turns: Counts // started, succeeded, failed, interrupted
  steps: Counts // by how each ended: tool-calls, stop, length, failed, ...
  tokens: Counts
  tools: Record<string, Counts> // tool name -> ok, failed
  retries: number
  compactions: number
}
interface Problem {
  seq: number
  time: number
  session: string // "" for the sidecar as a whole
  kind: string
  message: string
}
interface Window {
  seconds: number
  used: number // percent
  resets_at: number // unix seconds
}
interface StructuredError {
  type: string
  message: string
  status?: number
}

const describe = (error: StructuredError) => `${error.type}${error.status ? ` ${error.status}` : ""}: ${error.message}`
const add = (counts: Counts, key: string, n = 1) => (counts[key] = (counts[key] ?? 0) + n)
const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value)

export function telemetry(ctx: Context) {
  const started = Date.now()
  const chats = new Map<string, Chat>()
  const search: Record<string, Counts> = {} // keyless search provider -> ok, failed
  const jina: Counts = {}
  const problems: Problem[] = []
  const toolNames = new Map<string, string>() // tool call id -> name: success and failure events carry only the id
  const plan: { windows: Window[]; updated: number; error: string | null } = { windows: [], updated: 0, error: null }
  let seq = 0

  const chat = (session: string) => {
    let entry = chats.get(session)
    if (!entry) chats.set(session, (entry = { turns: {}, steps: {}, tokens: {}, tools: {}, retries: 0, compactions: 0 }))
    return entry
  }
  const problem = (session: string, kind: string, message: string) => {
    problems.push({ seq: ++seq, time: Date.now(), session, kind, message: message.slice(0, 300) })
    if (problems.length > MAX_PROBLEMS) problems.shift()
  }
  const tokens = (entry: Chat, used: { input: number; output: number; reasoning: number; cache: { read: number; write: number } }) => {
    add(entry.tokens, "input", used.input)
    add(entry.tokens, "output", used.output)
    add(entry.tokens, "reasoning", used.reasoning)
    add(entry.tokens, "cache_read", used.cache.read)
    add(entry.tokens, "cache_write", used.cache.write)
  }
  const toolEnded = (session: string, id: string, outcome: "ok" | "failed") => {
    const name = toolNames.get(id) ?? "unknown"
    toolNames.delete(id)
    add((chat(session).tools[name] ??= {}), outcome)
    return name
  }

  const handle = (event: { type: string; data: any }) => { // any opencode event; each case reads its own type's fields
    const data = event.data
    const session: string = data?.sessionID ?? ""
    switch (event.type) {
      case "session.execution.started":
        add(chat(session).turns, "started")
        break
      case "session.execution.succeeded":
        add(chat(session).turns, "succeeded")
        break
      case "session.execution.failed":
        add(chat(session).turns, "failed")
        problem(session, "turn_failed", describe(data.error))
        break
      case "session.execution.interrupted":
        add(chat(session).turns, "interrupted")
        if (data.reason !== "user") problem(session, "turn_interrupted", data.reason)
        break
      case "session.step.ended":
        add(chat(session).steps, data.finish)
        tokens(chat(session), data.tokens)
        break
      case "session.step.failed":
        add(chat(session).steps, "failed")
        if (data.tokens) tokens(chat(session), data.tokens)
        problem(session, data.finish === "content-filter" ? "content_filter" : "step_failed", describe(data.error))
        break
      case "session.retry.scheduled":
        chat(session).retries++
        problem(session, "retry", `attempt ${data.attempt}, ${describe(data.error)}`)
        break
      case "session.compaction.started":
        chat(session).compactions++
        break
      case "session.tool.input.started":
        toolNames.set(data.id, data.name)
        if (toolNames.size > 1000) toolNames.delete(toolNames.keys().next().value!) // bounds the ids of calls that never settle
        break
      case "session.tool.success":
        toolEnded(session, data.id, "ok")
        break
      case "session.tool.failed":
        problem(session, "tool_failed", `${toolEnded(session, data.id, "failed")}, ${describe(data.error)}`)
        break
    }
  }

  const stop = new AbortController()
  const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
  void (async () => {
    while (!stop.signal.aborted) {
      try {
        for await (const event of ctx.event.subscribe({ signal: stop.signal })) handle(event)
      } catch (error) {
        if (!stop.signal.aborted) problem("", "telemetry", `event stream ended, ${String(error)}`)
      }
      await sleep(5_000) // counts miss what happens until the stream is back, nothing worse
    }
  })()

  const readPlan = async () => {
    try {
      const connection = await ctx.integration.connection.active("openai")
      const credential = connection && (await ctx.integration.connection.resolve(connection))
      const account = credential?.type === "oauth" ? credential.metadata?.accountID : undefined
      if (credential?.type !== "oauth" || !credential.access || typeof account !== "string") throw new Error("no ChatGPT login")
      const response = await fetch(USAGE_URL, {
        headers: { Authorization: `Bearer ${credential.access}`, "ChatGPT-Account-Id": account, Accept: "application/json" },
        signal: AbortSignal.any([stop.signal, AbortSignal.timeout(15_000)]),
        redirect: "error"
      })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const limits = (await response.json())?.rate_limit
      const windows = [limits?.primary_window, limits?.secondary_window]
        .filter((w) => w != null)
        .map((w) => ({ seconds: w.limit_window_seconds, used: w.used_percent, resets_at: w.reset_at }))
      if (!windows.length || !windows.every((w) => isNumber(w.seconds) && isNumber(w.used) && isNumber(w.resets_at)))
        throw new Error("usage response changed shape") // a guess here would show a wrong plan gauge
      Object.assign(plan, { windows, updated: Date.now(), error: null })
    } catch (error) {
      const message = String(error).slice(0, 200)
      if (!stop.signal.aborted && message !== plan.error) problem("", "plan_unavailable", message) // once per new error
      plan.error = message
    }
  }
  void readPlan()
  const planTimer = setInterval(readPlan, PLAN_EVERY)

  return {
    searched: (provider: string, outcome: "ok" | "failed") => add((search[provider] ??= {}), outcome),
    readThroughJina: (outcome: "ok" | "failed") => add(jina, outcome),
    snapshot: async () => ({ started, chats: Object.fromEntries(chats), search, jina, plan, problems }),
    stop: () => {
      stop.abort()
      clearInterval(planTimer)
    }
  }
}
