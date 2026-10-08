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
          "Reply in Telegram to one trigger message: an answer, or a warning. kbots posts an optional \"Verdict:\" line, then " +
          "one \"- point\" line per point followed by its `sources` as numbered superscript links. Returns `sent` or what kbots " +
          "did, or why kbots rejected the call.",
        input: {
          type: "object",
          additionalProperties: false,
          required: ["trigger_message_id", "reply_to", "warning", "points"],
          properties: {
            trigger_message_id: { type: "integer", description: "message_id from the <trigger> block you answer." },
            reply_to: {
              type: "integer",
              description: "message_id to post the answer under: the tag or a message this trigger showed you, never the bot's own."
            },
            warning: {
              type: "boolean",
              description:
                "true when you decline instead of answering - every refusal is a warning (see your instructions). kbots then posts " +
                "your one point, without verdict or sources, as a warning under the tag; a second warning within 24 hours mutes the tagger for 24 hours."
            },
            verdict: {
              type: "string",
              description:
                "True, false, misleading, partly true or unverified, in the reply language (\"Неправда\"). " +
                "Omit when there is no claim to judge, such as an opinion or an open question."
            },
            points: {
              type: "array",
              description: "1 to 3 points.",
              items: {
                type: "object",
                additionalProperties: false,
                required: ["text", "sources"],
                properties: {
                  text: {
                    type: "string",
                    description:
                      "One point, at most 500 characters, plain text (markdown shows literally). No verdict, bullet, URL or citation " +
                      "mark ([1], ¹) - kbots adds those. A warning: a sentence or two in the tagger's language (that of their focus " +
                      "text, else of their recent messages, else Ukrainian), naming no consequence - kbots adds the mute notice."
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
