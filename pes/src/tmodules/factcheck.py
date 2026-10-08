import base64
import hmac
import html
import json
import os
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiohttp
import cachetools
import pytz
import sentry_sdk
from aiohttp import web
from telethon import events, functions, types, utils

# Set on the session every trigger: a config default model loses its #variant and, when unavailable (no ChatGPT
# login yet), silently falls back to any available model, even a free one. A session model fails the turn instead.
MODEL = {"providerID": "openai", "id": "gpt-6-luna-fast", "variant": "medium"}
CONTEXT_BEFORE, CONTEXT_AFTER = 15, 5  # message IDs around the replied (or trigger) message sent as chat context
MAX_POINTS, MAX_CHARS, MAX_SOURCES, MAX_VERDICT = 3, 250, 4, 40
IGNORE_SECONDS = 24 * 60 * 60
SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
IMAGE_MIMES, MAX_IMAGES, MAX_IMAGE_BYTES = ("image/jpeg", "image/png", "image/webp"), 4, 10 * 1024 * 1024
URL = re.compile(r"https?://[^\s/?#\[\]]+\S*")  # urlparse raises on some model-written URLs ("https://[x")

Send = Callable[[int, str, int], Awaitable[None]]  # (chat_id, Telegram HTML, reply_to message_id)


class Rejected(Exception):
    """A tool call kbots refuses; the message goes back to the model as the tool result."""


@dataclass(frozen=True)
class ChatMessage:
    id: int
    date: datetime
    author: str
    text: str
    reply_to: int | None = None
    forwarded_from: str | None = None  # "" when the origin is hidden or not resolvable
    media: str | None = None

    @classmethod
    def from_telethon(cls, msg) -> "ChatMessage":
        fwd = msg.forward
        forwarded = None if fwd is None else (fwd.from_name or utils.get_display_name(fwd.chat or fwd.sender))
        media = type(msg.media).__name__.removeprefix("MessageMedia") if msg.media else None
        return cls(msg.id, msg.date, describe(msg.sender), msg.message or "", msg.reply_to_msg_id, forwarded, media)

    def render(self, tz) -> str:
        head = f"[{self.id}] {self.date.astimezone(tz):%d.%m %H:%M} {self.author}"
        if self.reply_to:
            head += f" (reply to [{self.reply_to}])"
        if self.forwarded_from is not None:
            head += f" (forwarded from {self.forwarded_from})" if self.forwarded_from else " (forwarded)"
        media = f"[{self.media}] " if self.media else ""
        return quote(f"{head}:\n{media}{self.text}")


async def attach_images(msgs, logger) -> list[dict]:
    """The messages' photos and image files as opencode prompt attachments, in order, at most MAX_IMAGES."""
    files: list[dict] = []
    for m in msgs:
        if m.file is None or m.sticker or m.file.mime_type not in IMAGE_MIMES or (m.file.size or 0) > MAX_IMAGE_BYTES:
            continue
        try:
            data = base64.b64encode(await m.download_media(bytes)).decode()
        except Exception:  # an expired file reference must not cost the whole trigger
            logger.warning("Fact-check: could not download image from %s", m.id, exc_info=True)
            continue
        name, uri = f"message-{m.id}{m.file.ext}", f"data:{m.file.mime_type};base64,{data}"
        files.append({"uri": uri, "name": name, "description": f"Image from message [{m.id}]"})
        if len(files) == MAX_IMAGES:
            break
    return files


def session_id(chat_id: int) -> str:
    return f"ses_tg_{chat_id}"  # opencode requires the "ses" prefix; create() with a known id returns that session


def describe(user) -> str:
    if user is None:
        return "unknown"
    username = f"@{user.username}, " if getattr(user, "username", None) else ""
    return f"{utils.get_display_name(user)} ({username}id {user.id})"


def quote(text: str) -> str:
    return html.escape(text, quote=False)  # chat text must not close or forge the <trigger>/<conversation> blocks


def build_prompt(
    *,
    now: datetime,
    chat: str,
    sender: str,
    message_id: int,
    replied_id: int | None,
    focus: str,
    window: list[ChatMessage],
    attachments: Sequence[str] = (),
) -> str:
    conversation = "\n\n".join(m.render(now.tzinfo) for m in window)
    return (
        "<trigger>\n"
        f"time: {now:%Y-%m-%d %H:%M %Z}\n"
        f"chat: {quote(chat)}\n"
        f"from: {quote(sender)}\n"
        f"message_id: {message_id}\n"
        f"replied_message_id: {replied_id or 'none'}\n"
        f"focus: {quote(' '.join(focus.split())) or 'none'}\n"
        f"attachments: {', '.join(attachments) or 'none'}\n"
        "</trigger>\n"
        f"<conversation>\n{conversation}\n</conversation>"
    )


def render_reply(verdict: str, points: list[dict]) -> str:
    """Telegram HTML: an optional "Verdict:" line, then one "- point" line each; a lone point without a verdict
    stays a plain line. Every point is followed by superscript links, numbered by first appearance."""
    numbers: dict[str, int] = {}
    lines = []
    for point in points:
        refs = []
        for url in dict.fromkeys(point["sources"]):
            number = numbers.setdefault(url, len(numbers) + 1)
            refs.append(f'<a href="{html.escape(url)}">{str(number).translate(SUPERSCRIPT)}</a>')
        lines.append(quote(point["text"]) + "\u2009".join(refs))  # thin space, else ¹² reads as 12
    if not verdict and len(lines) == 1:
        return lines[0]
    return (f"{quote(verdict)}:\n" if verdict else "") + "\n".join(f"- {line}" for line in lines)


def validate_reply(payload: dict) -> tuple[str, list[dict]]:
    """Limits only: the plugin's input schema already guarantees the types."""
    verdict = " ".join(payload.get("verdict", "").split()).rstrip(":")
    if len(verdict) > MAX_VERDICT:
        raise Rejected(f"`verdict` has {len(verdict)} characters; keep it a short label of at most {MAX_VERDICT}.")
    if not 1 <= len(payload["points"]) <= MAX_POINTS:
        raise Rejected(f"`points` must hold 1 to {MAX_POINTS} items.")
    points = []
    for i, item in enumerate(payload["points"], 1):
        text, sources = item["text"].strip(), item["sources"]  # keep the model's line breaks and spacing
        if not text or len(text) > MAX_CHARS:
            raise Rejected(f"Point {i} has {len(text)} characters; it must have 1 to {MAX_CHARS}. Shorten it.")
        if "http://" in text or "https://" in text:
            raise Rejected(f"Point {i} contains a URL. Move it to `sources`.")
        if len(sources) > MAX_SOURCES:
            raise Rejected(f"Point {i} must have at most {MAX_SOURCES} sources.")
        if bad := [url for url in sources if not URL.fullmatch(url)]:
            raise Rejected(f"Point {i} source {bad[0]!r} is not an http(s) URL.")
        points.append({"text": text, "sources": sources})
    return verdict, points


class Actions:
    """Pending triggers, per-user strikes, and the tool calls the opencode plugin forwards (pes/opencode/plugin/kbots)."""

    def __init__(self, strikes_fp: Path, send: Send, logger):
        self.strikes_fp = strikes_fp
        self.send = send
        self.logger = logger
        # (session, message) -> (chat, sender, message the answer replies to). A tool call pops its trigger before it
        # awaits anything, so even parallel calls answer it once, and the chat comes from opencode's session id, never
        # from model input. A trigger queued longer than the TTL can no longer be answered, nor one from before a restart.
        self.triggers: cachetools.TTLCache[tuple[str, int], tuple[int, int, int]] = cachetools.TTLCache(1024, ttl=6 * 3600)
        # str(user_id) -> unix time they are ignored until. Present, even at 0, means they were warned once.
        self.strikes: dict[str, float] = json.loads(strikes_fp.read_text()) if strikes_fp.exists() else {}

    def is_ignored(self, user_id: int) -> bool:
        return self.strikes.get(str(user_id), 0) > time.time()

    def claim(self, sid: str, message_id: int) -> tuple[int, int, int]:
        """Take the trigger this call answers; return its (chat_id, sender_id, answer_to)."""
        if (trigger := self.triggers.pop((sid, message_id), None)) is None:
            raise Rejected(f"No pending trigger {message_id}: it was already answered, or is not a message_id from a <trigger>.")
        if self.is_ignored(trigger[1]):
            raise Rejected(f"The sender of trigger {message_id} is ignored. Take no action on it.")
        return trigger

    async def reply(self, sid: str, payload: dict) -> str:
        text = render_reply(*validate_reply(payload))  # before claim(): a rejected call may retry
        chat_id, _, answer_to = self.claim(sid, payload["trigger_message_id"])
        await self.send(chat_id, text, answer_to)
        return "sent"

    async def ignore_user(self, sid: str, payload: dict) -> str:
        text = payload["text"].strip()  # keep line breaks: a playful warning may carry a tiny ASCII drawing
        if not text or len(text) > MAX_CHARS:
            raise Rejected(f"`text` has {len(text)} characters; it must have 1 to {MAX_CHARS}.")
        chat_id, sender_id, _ = self.claim(sid, payload["trigger_message_id"])
        warned = str(sender_id) in self.strikes
        self.strikes[str(sender_id)] = time.time() + IGNORE_SECONDS if warned else 0
        tmp = self.strikes_fp.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.strikes, indent=2))
        os.replace(tmp, self.strikes_fp)
        if warned:
            result = "The sender already had a warning: kbots now ignores them for 24 hours and posted nothing."
        else:
            await self.send(chat_id, html.escape(text), payload["trigger_message_id"])  # the warning goes to the tagger
            result = "Posted your text as a warning reply. The next ignore_user call for this sender silences them."
        self.logger.info("Fact-check: ignore_user on %s in %s: %s", sender_id, chat_id, result)
        return result

    def app(self, token: str) -> web.Application:
        handlers = {"reply": self.reply, "ignore_user": self.ignore_user}

        async def callback(request: web.Request) -> web.Response:
            if not hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()):
                return web.Response(status=401, text="unauthorized")
            body = await request.json()
            action = request.match_info["action"]
            sentry_sdk.add_breadcrumb(category="factcheck", message=f"Tool call {action} in {body['session_id']}")
            try:
                return web.Response(text=await handlers[action](body["session_id"], body["input"]))
            except Rejected as error:
                return web.Response(status=422, text=str(error))

        app = web.Application()
        app.router.add_post("/opencode/{action:reply|ignore_user}", callback)
        return app


async def queue_prompt(opencode: aiohttp.ClientSession, sid: str, title: str, text: str, files: Sequence[dict] = ()):
    async with opencode.post("/api/session", json={"id": sid, "title": title}):  # a titled session skips a title model call
        pass
    async with opencode.post(f"/api/session/{sid}/model", json={"model": MODEL}):  # no-op when unchanged
        pass
    # "queue" runs each trigger as its own turn, in order, after the current one finishes.
    async with opencode.post(f"/api/session/{sid}/prompt", json={"text": text, "files": list(files), "delivery": "queue"}):
        pass


async def init(client, logger, config, **context):
    chat_ids = [int(c) for c in config.get("factcheck", "chat_ids", fallback="").replace(",", " ").split()]
    if not chat_ids:
        logger.info("Fact-check disabled: no [factcheck] chat_ids configured.")
        return

    tz = pytz.timezone(config.get("general", "timezone"))
    callback_token = config.get("factcheck", "callback_token").strip()
    if len(callback_token) < 32:
        raise ValueError("factcheck.callback_token must be at least 32 characters")

    async def send(chat_id: int, text: str, reply_to: int):
        await client.send_message(chat_id, text, reply_to=reply_to, parse_mode="html", link_preview=False)

    # Callbacks first: if the port is taken, init fails before any tag can be forwarded.
    actions = Actions(Path(config.get("factcheck", "state_fp")), send, logger)
    runner = web.AppRunner(actions.app(callback_token))
    await runner.setup()
    host, port = config.get("factcheck", "callback_host"), config.getint("factcheck", "callback_port")
    await web.TCPSite(runner, host, port).start()

    auth = {"Authorization": aiohttp.encode_basic_auth("opencode", config.get("factcheck", "opencode_password"))}
    opencode = aiohttp.ClientSession(base_url=config.get("factcheck", "opencode_url"), headers=auth, raise_for_status=True)
    me = await client.get_me()

    def my_mentions(msg) -> list[str]:
        return [
            text
            for entity, text in msg.get_entities_text()
            if (isinstance(entity, types.MessageEntityMention) and text.lower() == f"@{me.username}".lower())
            or (isinstance(entity, types.MessageEntityMentionName) and entity.user_id == me.id)
        ]

    async def fetch(chat_id: int, ids) -> list:
        return [m for m in await client.get_messages(chat_id, ids=list(ids)) if m is not None]

    @client.on(events.NewMessage(chats=chat_ids, func=lambda e: my_mentions(e.message)))
    async def on_tag(event):
        msg = event.message
        if actions.is_ignored(event.sender_id):
            logger.info("Fact-check: ignoring tag from %s", event.sender_id)
            return

        anchor = msg.reply_to_msg_id or msg.id
        ids = (i for i in range(max(1, anchor - CONTEXT_BEFORE), anchor + CONTEXT_AFTER + 1) if i != msg.id)
        window = await fetch(event.chat_id, ids)
        # One hop out: a message the window replies to is fetched even when it is older than the window.
        if parents := {m.reply_to_msg_id for m in window if m.reply_to_msg_id} - {m.id for m in window} - {msg.id}:
            window = sorted(window + await fetch(event.chat_id, parents), key=lambda m: m.id)
        # Images of the checked message and its album, then the tagger's own screenshot; the rest stay placeholders.
        replied = next((m for m in window if m.id == msg.reply_to_msg_id), None)
        album = [
            m for m in window if replied and (m.id == replied.id or (replied.grouped_id and m.grouped_id == replied.grouped_id))
        ]
        files = await attach_images([*album, msg], logger)
        focus = msg.message
        for text in my_mentions(msg):  # only the bot's own: "@bot is @borys right?" keeps @borys
            focus = focus.replace(text, "")
        chat = utils.get_display_name(await event.get_chat())
        prompt = build_prompt(
            now=datetime.now(tz),
            chat=f"{chat} (id {event.chat_id})",
            sender=describe(await event.get_sender()),
            message_id=msg.id,
            replied_id=msg.reply_to_msg_id,
            focus=focus,
            window=[ChatMessage.from_telethon(m) for m in window],
            attachments=[f["name"] for f in files],
        )

        sid = session_id(event.chat_id)
        sentry_sdk.add_breadcrumb(category="factcheck", message=f"Forwarding trigger {msg.id} to {sid}")
        # Before the prompt, which may run at once. The answer replies to the checked claim, not to the tag.
        actions.triggers[(sid, msg.id)] = (event.chat_id, event.sender_id, msg.reply_to_msg_id or msg.id)
        await queue_prompt(opencode, sid, f"Telegram: {chat}", prompt, files)

        try:
            reaction = [types.ReactionEmoji(emoticon="👀")]
            await client(functions.messages.SendReactionRequest(peer=event.chat_id, msg_id=msg.id, reaction=reaction))
        except Exception:  # best effort: reactions can be disabled in the chat
            logger.warning("Fact-check: could not react to %s", msg.id, exc_info=True)

    logger.info("Fact-check enabled for %s; callbacks on %s:%s", chat_ids, host, port)
