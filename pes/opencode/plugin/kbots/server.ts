// Forwards the agent's Telegram replies to the PES bot (tmodules/factcheck.py). kbots owns the bot token,
// validates every call and decides the effect; a rejection comes back as text the model can act on.
import type { Plugin } from "@opencode/plugin/promise/plugin"

const { KBOTS_CALLBACK_URL: callbackUrl, KBOTS_CALLBACK_TOKEN: callbackToken } = process.env // required by flake.nix pes-opencode

// A thrown error (kbots down, timeout) reaches the model as a tool error and the turn continues. After a timeout
// kbots may still have posted; the one-answer-per-trigger rule makes a retry harmless.
const forward = async (sessionID: string, input: unknown, signal: AbortSignal) => {
  const response = await fetch(new URL("/opencode/reply", callbackUrl), {
    method: "POST",
    headers: { Authorization: `Bearer ${callbackToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionID, input }),
    signal: AbortSignal.any([signal, AbortSignal.timeout(30_000)]),
    redirect: "error" // a misconfigured URL must not carry the token elsewhere
  })
  const body = (await response.text()).slice(0, 2048)
  return response.ok ? body : `kbots rejected the call (HTTP ${response.status}): ${body}`
}

// opencode's webfetch refuses PDFs, and many sites answer its datacenter IP with 403 (a browser user agent gets the
// same). The keyless Jina reader (20 requests a minute) returns either as markdown; its error warnings mean it failed too.
const readThroughJina = async (url: string, signal: AbortSignal) => {
  const response = await fetch(`https://r.jina.ai/${url}`, { signal: AbortSignal.any([signal, AbortSignal.timeout(60_000)]) })
  const text = await response.text()
  const head = text.split("Markdown Content:", 1)[0]
  return response.ok && !/^Warning: Target URL returned error|CAPTCHA|^Title: Just a moment/m.test(head) ? text : undefined
}

// For `execute` programs, which get no fetch of their own: http(s) only, since Bun's fetch also reads file:// paths.
const download = async (url: string, signal: AbortSignal) => {
  if (!/^https?:$/.test(new URL(url).protocol)) throw new Error("only http(s) URLs")
  const response = await fetch(url, { signal: AbortSignal.any([signal, AbortSignal.timeout(60_000)]) })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return await response.text()
}

// Types only: opencode validates input against these schemas before execute. kbots enforces the limits the
// descriptions state. Never add a `pattern` keyword - it makes opencode skip validation of the whole schema.
export default {
  id: "kbots.telegram",
  setup: async (ctx) => {
    // Keyless tiers rate-limit per IP (Parallel locks it out for minutes, Tavily has a daily cap), so searches rotate
    // over the providers not resting; each failure in a row doubles a provider's rest, 10 minutes up to 6 hours. When
    // all rest, the one due back first is probed. Firecrawl has no keyless access; an env key (PARALLEL_API_KEY,
    // EXA_API_KEY, ...) raises a provider's limits, and opencode reads it.
    const providers = ["parallel", "exa", "tavily", "tinyfish", ...(process.env.FIRECRAWL_API_KEY ? ["firecrawl"] : [])]
    const resting = new Map<string, { failures: number; until: number }>()
    let searches = 0
    await ctx.websearch.transform((editor) => {
      editor.add({
        id: "kbots",
        name: "Keyless providers in rotation",
        execute: async ({ query }) => {
          const rotated = providers.map((_, i) => providers[(searches + i) % providers.length])
          searches++
          const ready = rotated.filter((id) => (resting.get(id)?.until ?? 0) <= Date.now())
          const dueFirst = rotated.reduce((a, b) => ((resting.get(a)?.until ?? 0) <= (resting.get(b)?.until ?? 0) ? a : b))
          let failure: unknown
          for (const providerID of ready.length ? ready : [dueFirst]) {
            try {
              const { results } = (await ctx.websearch.query({ query, providerID })).data
              resting.delete(providerID)
              return results
            } catch (error) {
              failure = error
              const failures = (resting.get(providerID)?.failures ?? 0) + 1
              resting.set(providerID, { failures, until: Date.now() + Math.min(600_000 * 2 ** (failures - 1), 21_600_000) })
            }
          }
          throw failure
        }
      })
    })
    // Without forcing, the model can stop after its research without replying. Any kbots_reply call ends the forcing, a
    // rejected one too: kbots rejects every call for a trigger it no longer holds.
    await ctx.session.hook("context", (event) => {
      const turn = event.messages.slice(event.messages.findLastIndex((m) => m.role === "user") + 1) // after the trigger
      if (turn.some((m) => m.content.some((part) => part.type === "tool-call" && part.name === "kbots_reply"))) return
      event.options.allowedTools = { toolNames: Object.keys(event.tools), mode: "required" } // OpenAI tool_choice "required"
    })
    // OpenAI's hosted web_search comes with the ChatGPT plan and opens pages that answer this server 403. opencode parses
    // its calls but cannot request the tool, so it joins each agent step's websocket request. A session that fell back to
    // HTTP goes without it until the sidecar restarts, and the `websearch` above stays for that.
    await ctx.session.hook(
      "experimental.ws.send",
      (event) => {
        if (event.kind !== "primary") return // skips compaction, the only other request on the socket
        const frame = JSON.parse(event.frame)
        frame.tools = [...(frame.tools ?? []), { type: "web_search" }]
        if (frame.tool_choice?.type === "allowed_tools") frame.tool_choice.tools.push({ type: "web_search" }) // else the forcing locks it out
        event.frame = JSON.stringify(frame)
      },
      { providerID: "openai" } // the only provider that knows this tool
    )
    // execute's built-in fetch is Bun's, which also reads file:// paths (/proc/self/environ holds this process's secrets).
    // A program-level const shadows it, so programs reach the network only through `download`.
    await ctx.tool.hook("execute.before", (event) => {
      const input = event.input as { code?: unknown } | undefined
      if (event.tool !== "execute" || typeof input?.code !== "string") return
      event.input = { ...input, code: `const fetch = () => { throw new Error("fetch is unavailable; use tools.download") }; ${input.code}` }
    })
    await ctx.tool.transform((editor) => {
      editor.update("webfetch", (tool) => {
        const direct = tool.execute
        tool.execute = async (input, context) => {
          try {
            return await direct(input, context)
          } catch (error) {
            const { url, format } = input as { url: string; format: string }
            // Returned, not thrown: a throw here reached the model as some other parallel call's failure.
            const read = await readThroughJina(url, context.signal).catch(() => undefined)
            const text = read ?? `${url} did not open (${String(error).slice(0, 200)}).`
            const output = { url, contentType: "text/markdown", format, output: text }
            return { output, content: text, metadata: { contentType: output.contentType } }
          }
        }
      })
      editor.add({
        name: "download",
        options: { codemode: true }, // only inside `execute`, so a program returns what it filtered, not the whole file
        description: "GET an http(s) URL and return its body as text, to parse and filter in code.",
        input: { type: "object", additionalProperties: false, required: ["url"], properties: { url: { type: "string" } } },
        execute: async (input, { signal }) => ({ content: await download((input as { url: string }).url, signal) })
      })
      editor.add({
        name: "kbots_reply",
        options: { codemode: false }, // a direct tool: a program inside `execute` must not be able to post
        description:
          "Reply in Telegram to one trigger message. kbots posts an optional \"Verdict:\" line, then one \"- point\" line " +
          "per point followed by its `sources` as numbered superscript links. Returns `sent`, or why kbots rejected the call.",
        input: {
          type: "object",
          additionalProperties: false,
          required: ["trigger_message_id", "reply_to", "points"],
          properties: {
            trigger_message_id: { type: "integer", description: "message_id from the <trigger> block you answer." },
            reply_to: {
              type: "integer",
              description: "message_id to post the answer under: the tag or a message this trigger showed you, never the bot's own."
            },
            verdict: {
              type: "string",
              description:
                "True, false, misleading, partly true, baseless (nothing backs it after scrutiny) or unverified (too new or obscure to check), " +
                "in the reply language (\"Неправда\"). " +
                "Omit when there is no claim to judge, such as an opinion or an open question."
            },
            points: {
              type: "array",
              description: "1 to 3 points; add one only for a new fact.",
              items: {
                type: "object",
                additionalProperties: false,
                required: ["text", "sources"],
                properties: {
                  text: {
                    type: "string",
                    description:
                      "One point, at most 500 characters, usually far fewer, in Telegram HTML: <b>, <i>, <u>, <s>, <code>, <pre> for monospace such as " +
                      "ASCII art, <blockquote>; write < and & in text as &lt; and &amp;. Never markdown or ```: Telegram shows " +
                      "them literally. Plain keyboard punctuation: - for dashes, ' and \" for apostrophes and quotes, never \u2014, \u2013, \u2019, \u201c\u201d " +
                      "or \u00ab\u00bb. No verdict, bullet, URL or citation mark ([1], ¹) - kbots adds those."
                  },
                  sources: {
                    type: "array",
                    items: { type: "string" },
                    description: "Up to 4 URLs you opened that say what this point claims. Empty only for a point with no factual claim."
                  }
                }
              }
            }
          }
        },
        execute: async (input, { sessionID, signal }) => ({ content: await forward(sessionID, input, signal) })
      })
    })
  }
} satisfies Plugin
