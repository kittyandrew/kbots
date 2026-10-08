// Forwards the agent's Telegram actions to the PES bot (tmodules/factcheck.py). kbots owns the bot token,
// validates every call and decides the effect; a rejection comes back as text the model can act on.
import type { Plugin } from "@opencode/plugin/promise/plugin"

const { KBOTS_CALLBACK_URL: callbackUrl, KBOTS_CALLBACK_TOKEN: callbackToken } = process.env // required by flake.nix pes-opencode

// A thrown error (kbots down, timeout) reaches the model as a tool error and the turn continues. After a timeout
// kbots may still have posted; the one-answer-per-trigger rule makes a retry harmless.
const forward = async (action: string, sessionID: string, input: unknown, signal: AbortSignal) => {
  const response = await fetch(new URL(`/opencode/${action}`, callbackUrl), {
    method: "POST",
    headers: { Authorization: `Bearer ${callbackToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionID, input }),
    signal: AbortSignal.any([signal, AbortSignal.timeout(30_000)]),
    redirect: "error" // a misconfigured URL must not carry the token elsewhere
  })
  const body = (await response.text()).slice(0, 2048)
  return response.ok ? body : `kbots rejected the call (HTTP ${response.status}): ${body}`
}

// Types only: opencode validates input against these schemas before execute. kbots enforces the limits the
// descriptions state. Never add a `pattern` keyword - it makes opencode skip validation of the whole schema.
export default {
  id: "kbots.telegram",
  setup: async (ctx) => {
    await ctx.tool.transform((editor) => {
      editor.add({
        name: "kbots_reply",
        options: { codemode: false }, // a codemode tool lives inside `execute`, which the permission lockdown denies
        description:
          "Reply in Telegram to one trigger message. Each trigger accepts one reply or one ignore_user call, ever. " +
          "kbots posts an optional \"Verdict:\" line, then one \"- point\" line per point; a lone point without a verdict is plain text. " +
          "1 to 3 points, each at most 250 characters. Put each point's sources in its `sources`; kbots renders them as " +
          "numbered superscript links after the point, so never write URLs or citation markers like [1] in `text`. " +
          "Returns `sent` on success, otherwise the reason it was rejected - fix the input and call again.",
        input: {
          type: "object",
          additionalProperties: false,
          required: ["trigger_message_id", "points"],
          properties: {
            trigger_message_id: { type: "integer", description: "message_id from the <trigger> block you answer." },
            verdict: {
              type: "string",
              description: "Short verdict label in the reply language, at most 40 characters, e.g. \"Partly true\" or \"Частково правда\". Omit for a reply that checks no claim."
            },
            points: {
              type: "array",
              items: {
                type: "object",
                additionalProperties: false,
                required: ["text", "sources"],
                properties: {
                  text: { type: "string", description: "One plain-text point: a sentence or two." },
                  sources: {
                    type: "array",
                    items: { type: "string" },
                    description: "Up to 4 http(s) URLs you opened that support this point. Empty only for a point with no factual claim."
                  }
                }
              }
            }
          }
        },
        execute: async (input, { sessionID, signal }) => ({ content: await forward("reply", sessionID, input, signal) })
      })
      editor.add({
        name: "kbots_ignore_user",
        options: { codemode: false },
        description:
          "Act on a trigger whose sender abuses or misuses the bot, as your instructions define. " +
          "Always targets the sender of that trigger, nobody else. kbots posts `text` as a warning reply to them; if they were " +
          "warned in the past 30 days, it adds a notice that they are ignored for 24 hours. Returns what kbots did.",
        input: {
          type: "object",
          additionalProperties: false,
          required: ["trigger_message_id", "text"],
          properties: {
            trigger_message_id: { type: "integer", description: "message_id from the <trigger> block of the abusive request." },
            text: { type: "string", description: "Short warning in the chat's language, at most 250 characters." }
          }
        },
        execute: async (input, { sessionID, signal }) => ({ content: await forward("ignore_user", sessionID, input, signal) })
      })
    })
  }
} satisfies Plugin
