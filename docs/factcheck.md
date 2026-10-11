# Fact-check

Tagging the PES bot in a configured chat, usually in reply to a message, asks an opencode agent to fact-check it. The agent answers with a short verdict and 1 to 3 points carrying superscript source links, or claps back at trolls and off-topic requests. Code: `pes/src/tmodules/factcheck.py`; agent: `pes/opencode/`; sidecar: `.#pes-opencode` in `flake.nix`.

## Design

- **The model decides, kbots enforces.** kbots forwards every tag; the agent acts on each with exactly one call of a plugin tool that calls back into the bot. kbots owns the Telegram token and checks every call: the reply shape, which trigger it answers, and at most one action per trigger, so parallel or re-run calls cannot double-post. A rejection goes back to the model as the tool result so it can retry.
- **The chat comes from opencode's session id, never from model input.** A prompt injection cannot redirect a reply to another chat; at worst it acts on another tag still pending in the same chat.
- **The model picks which message an answer replies to.** It may pick only the tag or a message that trigger showed it, never the bot's own, so an injection cannot aim replies elsewhere.
- **Two modes, no punishment.** A sincere request gets a researched answer; trolling and off-topic requests get a sarcastic clap back under the tag. Warnings and timed ignores were dropped: one misread follow-up (a member asking about his slow answer, read as another member's troll request) cost him an 8-hour ignore.
- **One persistent session per chat.** Follow-ups see earlier answers; triggers queue and run one at a time, which is the only throttle.
- **Until a turn calls `kbots_reply`, every step must call a tool.** The model sometimes stopped right after its research without replying: 1 of 21 production turns on GPT-6.1 Sol, 17 of 84 in parallel local replays. Until that call, the plugin sets OpenAI's `tool_choice` to required. Unforced, the model already called a tool at every other step, so forcing changed nothing else: 0 of 15 forced replays went silent, with the same steps and tool calls per turn. The hosted `web_search` reopens a gap: it also counts as a tool call, and opencode continues a turn only after a tool it runs itself, so a step that only searches and then writes text could end the turn. None of 48 replays and three eval runs with it went silent.
- **Bottom line first.** An audit of 158 production answers graded 66 milquetoast and 15 sharp: the research usually held the decisive fact or the source's own conclusion, and the answer dropped it for hedges ("залежить від", "залишається невизначеним") or ended on an open question. The prompt asks for the direct answer first, a judgment where the evidence leans, the closest sourced figure for a number question, and no asides about what something is not, what the outcome depends on or what stays unknown; banning only "X, not Y" moved the hedging into other words. Two guards followed: every figure comes from a source, since "commit to a judgment" alone produced the model's own odds ("85-90%"), and answers skip background the chat already has, since US answers kept re-explaining veto overrides. Judged blind on 12 production questions replayed twice, each revision beat the one before 18 to 6, 21 to 3, and 17 to 5 with 2 ties for the guards, which cut unsourced numbers from 7 to 3 and repeated background from 8 to 0. About 60 spot-checked claims showed small slips and no serious truth errors.
- **The model is set on the session every trigger** (`MODEL`). opencode's config default drops the effort variant and silently falls back to any available model, even a free one; a session model fails loudly instead.
- **Context:** the replied message with the messages around it, plus older messages they reply to (one hop), images of the replied message, its album and the tag itself, and each message's links hidden behind text or in its preview, since a news forward's source usually sits there. The bot's own answers appear without their superscript links (their URLs stay listed): shown them, the model types its own markers into new answers. Everything else stays a placeholder.
- **Search:** the model gets OpenAI's hosted `web_search`, which comes with the ChatGPT plan and opens pages that answer the sidecar 403 (an AP article `webfetch` could not read). opencode parses its calls and replays them but has no setting to request the tool, so the plugin adds it to each agent step's websocket request. A session that falls back to HTTP (a failed connect, five stream drops in a row, an oversized request) goes without it until the sidecar restarts. The plugin's `websearch` stays as the fallback and rotates over the keyless Parallel, Exa, Tavily and TinyFish tiers. Each caps the IP: Parallel locks it out for minutes, Tavily for the day, and one day of evals and replays from one machine exhausted all but TinyFish (91 of 93 searches failed with a fixed order, 1 of 152 with the rotation). A failing provider rests, 10 minutes doubling up to 6 hours; a provider's env key (`PARALLEL_API_KEY`, `EXA_API_KEY`, `TAVILY_API_KEY`, `TINYFISH_API_KEY`, `FIRECRAWL_API_KEY`) raises its limit, and Firecrawl joins only with one. The model must read the relevant pages in full and follow their citations, and kbots rejects Google News links as sources so every citation is a page it read. Google News RSS stays as a way to find news minutes old. Many sites answer the sidecar 403, and opencode refuses PDFs; the plugin retries any failed `webfetch` through the keyless Jina reader, because the model ignored a prompt hint to do so. A browser user agent does not help: tested from tustan, the blocks key on the IP, and sites behind a JavaScript challenge stay closed either way. No evidence means "unverified", never "false"; an accusation that years of scrutiny never backed is "baseless".
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

- Permissions allow only `websearch`, `webfetch`, `kbots_reply`, and `execute` with the plugin's `download`, which shuts out files, shell, skills and subagents. The hosted `web_search` runs on OpenAI's servers, outside these permissions, and cannot reach the sidecar's files or local network. `execute` lets the model process data too big to read whole (a 28k-row sheet) in code; its built-in `fetch` also reads `file://` paths, this process's environment and the opencode database included, so the plugin shadows it in every program and `download` takes http(s) only.
- **Deployment must block private address ranges.** `webfetch` and `download` fetch any URL, `127.0.0.1` included. The sidecar may reach the PES bot and the public internet, nothing else; on tustan that includes the factory opencode at `10.0.0.2:4096`.
- Run it like the bot images: non-root, read-only root, only the state volume writable.

## Evals

`pes/evals/factcheck.py` runs the real sidecar and kbots validation against a fake Telegram, on the ChatGPT login in `data/opencode`. It costs plan usage and is nondeterministic, so it is not a CI gate; run it after changing the prompt, plugin or model. Each eval failure so far traced to a prompt rule, not code.

## Known limits

- The sidecar runs `opencode serve` in default mode, which does not resume turns a restart interrupted; that trigger keeps its 👀 and gets no answer. Pending triggers live in PES memory, so turns still queued across a PES restart run and their answers are rejected. Tags sent while PES is down are never seen: Telethon's catch-up would need `catch_up=True` plus handlers registered before connecting, and would replay up to a minute of already-handled updates into every module.
- The agent has no step limit, so a turn that never calls `kbots_reply` keeps calling tools and blocks its chat's queue until the sidecar restarts. In 1315 eval turns the longest took 11 steps.
- Queued triggers can stall until the next tag in that chat: after a sidecar restart, or after two consecutive failed turns.
- Fetched pages, attached images and any injected text in them stay in the chat's session until compaction.
- Compaction rewrites the session as a summary whose "Requirements" (opencode's fixed template) restate the prompt's rules in the model's words and keep members' requests as standing requirements. Rare wrong-language answers in production followed compactions; local replays did not reproduce them.
- Context is fetched by message id, which spans minutes in a busy group but can reach back months in a quiet DM. In a forum chat, a bare tag's reply target is its topic's first message.
- If OpenAI stops accepting the hosted `web_search` (a model or plan change), every turn fails, and the `websearch` fallback never runs; the evals catch it.
- Turn failures surface only in the sidecar's stderr, not in kbots or Sentry.
- A personal ChatGPT subscription behind a public bot may conflict with OpenAI's terms.
