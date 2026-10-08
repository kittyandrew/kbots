# Fact-check

Tagging the PES bot in a configured chat, usually in reply to a message, asks an opencode agent to fact-check it. The agent answers with a short verdict and 1 to 3 points carrying superscript source links, or warns and later ignores members who abuse it. Code: `pes/src/tmodules/factcheck.py`; agent: `pes/opencode/`; sidecar: `.#pes-opencode` in `flake.nix`.

## Design

- **The model decides, kbots enforces.** kbots forwards every tag; the agent acts on each with exactly one of two plugin tools that call back into the bot: reply or warn. kbots owns the Telegram token and checks every call: the reply shape, which trigger it answers, and at most one action per trigger, so parallel or re-run calls cannot double-post. A rejection goes back to the model as the tool result so it can retry.
- **The chat comes from opencode's session id, never from model input**, and `ignore_user` can only hit the sender of a pending trigger. A prompt injection cannot redirect a reply to another chat or punish an arbitrary member; at worst it acts on another tag still pending in the same chat.
- **The model picks which message an answer replies to; warnings go to the tagger.** It may pick only the tag or a message that trigger showed it, never the bot's own, so an injection cannot aim replies elsewhere. Misuse (nothing to check, off-topic requests) counts as abuse: a warning counts for 30 days from the member's last strike, and a strike within that window mutes them for 24 hours. Both are visible: a warning reply, a mute notice, and a 🙉 reaction on a muted member's tags.
- **One persistent session per chat.** Follow-ups see earlier answers; triggers queue and run one at a time, which is the only throttle.
- **The model is set on the session every trigger** (`MODEL`). opencode's config default drops the effort variant and silently falls back to any available model, even a free one; a session model fails loudly instead.
- **Context:** the replied message with the messages around it, plus older messages they reply to (one hop), and images of the replied message, its album and the tag itself. Everything else stays a placeholder.
- **Search:** Exa (keyless) via `websearch` lags days behind the news, so the prompt teaches Google News RSS for anything recent and Bing News RSS to reach the article. No evidence means "unverified", never "false".
- **Sources:** hostile-state media are presumed unreliable but allowed for Russia's own figures, marked as such. This is a prompt rule, not a domain block, by choice.

## ChatGPT login

The model runs on a ChatGPT plan. The sidecar logs in by device code over its own HTTP API, so it works headless and an agent can drive it; the credential persists in the sidecar's state directory, which must survive restarts.

```bash
U=http://127.0.0.1:4096 A="opencode:$OPENCODE_SERVER_PASSWORD"
curl -su "$A" -X POST "$U/api/integration/openai/connect/oauth" -H 'content-type: application/json' \
  -d '{"methodID": "chatgpt-headless"}' | jq .data   # -> attemptID, url, "Enter code: ..."
curl -su "$A" "$U/api/integration/openai/connect/oauth/<attemptID>" | jq .data.status   # until "complete"
```

## Security

The sidecar reads text any chat member writes, so assume injection sometimes succeeds and limit the blast radius:

- Permissions allow only `websearch`, `webfetch` and the two kbots tools; that also shuts out skills, subagents and codemode's unchecked `fetch`.
- **Deployment must block private address ranges.** `webfetch` fetches any URL, `127.0.0.1` included. The sidecar may reach the PES bot and the public internet, nothing else; on tustan that includes the factory opencode at `10.0.0.2:4096`.
- Run it like the bot images: non-root, read-only root, only the state volume writable.

## Evals

`pes/evals/factcheck.py` runs the real sidecar and kbots validation against a fake Telegram, on the ChatGPT login in `data/opencode`. It costs plan usage and is nondeterministic, so it is not a CI gate; run it after changing the prompt, plugin or model. Each eval failure so far traced to a prompt rule, not code.

## Known limits

- The sidecar runs `opencode serve` in default mode, which does not resume turns a restart interrupted; that trigger keeps its 👀 and gets no answer. Pending triggers live in PES memory, so turns still queued across a PES restart run and their answers are rejected. Tags sent while PES is down are never seen: Telethon's catch-up would need `catch_up=True` plus handlers registered before connecting, and would replay up to a minute of already-handled updates into every module.
- Queued triggers can stall until the next tag in that chat: after a sidecar restart, or after two consecutive failed turns.
- Fetched pages, attached images and any injected text in them stay in the chat's session until compaction.
- Context is fetched by message id, which spans minutes in a busy group but can reach back months in a quiet DM. In a forum chat, a bare tag's reply target is its topic's first message.
- Turn failures surface only in the sidecar's stderr, not in kbots or Sentry.
- A personal ChatGPT subscription behind a public bot may conflict with OpenAI's terms.
