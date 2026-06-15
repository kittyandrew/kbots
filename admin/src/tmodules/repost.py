import asyncio
import pickle
from pathlib import Path

import cachetools
import sentry_sdk
from telethon import events, utils
from telethon.errors.rpcerrorlist import FloodWaitError, MessageNotModifiedError
from telethon.events import album

CACHE_MAXSIZE = 65536
CACHE_TTL = 24 * 60 * 60 * 31  # 31 days in seconds


def load_cache(fp: Path, logger):
    if not fp.exists():
        return cachetools.TTLCache(maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL)

    try:
        with open(fp, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        logger.warning("Cache file '%s' is corrupt, starting fresh: %s", fp, e)
        return cachetools.TTLCache(maxsize=CACHE_MAXSIZE, ttl=CACHE_TTL)


async def save_cache(fp: Path, lock, cache):
    async with lock:
        tmp = fp.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            pickle.dump(cache, f)
        tmp.rename(fp)


def channel_link(entity, channel_id):
    """Build a t.me link prefix for a channel or user entity."""
    real_id, _ = utils.resolve_id(channel_id)
    slug = entity.username if entity.username else f"c/{real_id}"
    return f"https://t.me/{slug}/"


# @NOTE: Telethon's album event handler waits _HACK_DELAY seconds to collect all messages in an album
# before firing the event. The default is too short for large albums or slow connections, causing
# albums to be split into multiple events. Adding 4.5s makes it more reliable.
# Tested with Telethon 1.35.0 — re-verify after upgrades.
album._HACK_DELAY += 4.5
DEFAULTS = {"link_preview": False}


async def init(client, logger, config, **context):
    source_id = int(config.get("repost", "source_id"))
    target_id = int(config.get("repost", "target_id"))

    source = await client.get_entity(source_id)
    source_addr = channel_link(source, source_id)
    target = await client.get_entity(target_id)
    target_addr = channel_link(target, target_id)

    stitle = source.title if hasattr(source, "title") else source.first_name
    ttitle = target.title if hasattr(target, "title") else target.first_name
    logger.info("Forwarding from '%s' ('%s') to '%s' ('%s')!", stitle, source_addr, ttitle, target_addr)

    cache_lock = asyncio.Lock()
    cache_fp = Path(config.get("repost", "cache_fp"))
    ttl_cache = load_cache(cache_fp, logger)

    double_event_bug_cache: cachetools.TTLCache[int, bool] = cachetools.TTLCache(maxsize=128, ttl=24 * 60 * 60)
    album_dedup_lock = asyncio.Lock()
    single_dedup_lock = asyncio.Lock()

    # @NOTE: Bounded TTL cache of locks instead of unbounded defaultdict — prevents slow memory leak
    # from accumulating one Lock per unique edited message ID over the process lifetime.
    event_edit_locks: cachetools.TTLCache[int, asyncio.Lock] = cachetools.TTLCache(maxsize=8192, ttl=24 * 60 * 60)

    # @NOTE: Serializes all outbound API calls (send, edit, delete) to the target channel.
    # Without this, concurrent handlers (e.g. 3 albums arriving within seconds) issue overlapping
    # send_message calls, causing Telegram to break album grouping into individual images.
    send_lock = asyncio.Lock()

    # @TODO: Add button link instead of the text link when they finally add the bot.
    # @TODO: Enable catch_up w/ events feature of telethon for the bot when ready.
    @client.on(events.Album(chats=[source_id]))
    async def auto_forward_album(event):
        try:
            async with album_dedup_lock:
                if event.messages[0].id in double_event_bug_cache:
                    return
                double_event_bug_cache[event.messages[0].id] = True

            files = [e.media for e in event.messages]
            # @NOTE: Only the album's caption-bearing message gets the source link; text-less images must
            # stay caption-less. Telegram shows at most one album caption, so forcing a caption onto every
            # image (which happens when Telegram splits the album into per-message events) makes it hide
            # them all. Source from .message (raw text) so existing entity offsets stay valid. The caption
            # text is recovered from whichever message carries it; the backlink anchors to messages[0].id
            # (the album's deep-link target) regardless.
            caption_msg = next((m for m in event.messages if m.message), None)
            text = entities = None
            if caption_msg is not None:
                text = f"{caption_msg.message}\n\n{source_addr}{event.messages[0].id}"
                entities = caption_msg.entities or None
            async with send_lock:
                results = await client.send_message(
                    target_id,
                    text,
                    file=files,
                    formatting_entities=entities,
                    **DEFAULTS,
                )
            if not isinstance(results, list):
                results = [results]
            if len(results) != len(event.messages):
                logger.warning("Album length mismatch: sent %d, got %d results", len(event.messages), len(results))
            for e, message in zip(event.messages, results):
                ttl_cache[e.id] = message.id

            sentry_sdk.add_breadcrumb(category="repost", message=f"Album forwarded ({len(results)} messages)", level="info")
            await save_cache(cache_fp, cache_lock, ttl_cache)
        except FloodWaitError as e:
            logger.warning("Flood-wait in album forward, sleeping %ds...", e.seconds)
            await asyncio.sleep(e.seconds)
            # @NOTE: Clear dedup entry so the event can be reprocessed if it naturally re-fires.
            async with album_dedup_lock:
                double_event_bug_cache.pop(event.messages[0].id, None)
        except Exception:
            logger.exception("Unhandled error in album forward")

    @client.on(events.NewMessage(chats=[source_id], func=lambda e: e.grouped_id is None))
    async def auto_forward_single(event):
        try:
            async with single_dedup_lock:
                if event.id in double_event_bug_cache:
                    return
                double_event_bug_cache[event.id] = True

            # @NOTE: Only append the link when the message actually has text; a caption-less media message
            # must stay caption-less. Mutate .message (raw text) directly so entity offsets stay valid and
            # Telethon does not re-parse markdown.
            if event.message.message:
                event.message.message += f"\n\n{source_addr}{event.id}"
            async with send_lock:
                result = await client.send_message(target_id, event.message, **DEFAULTS)
            ttl_cache[event.id] = result.id

            sentry_sdk.add_breadcrumb(category="repost", message="Single message forwarded", level="info")
            await save_cache(cache_fp, cache_lock, ttl_cache)
        except FloodWaitError as e:
            logger.warning("Flood-wait in single forward, sleeping %ds...", e.seconds)
            await asyncio.sleep(e.seconds)
            # @NOTE: Clear dedup entry so the event can be reprocessed if it naturally re-fires.
            async with single_dedup_lock:
                double_event_bug_cache.pop(event.id, None)
        except Exception:
            logger.exception("Unhandled error in single forward")

    @client.on(events.MessageEdited(chats=[source_id]))
    async def auto_edit_any(event):
        try:
            # @NOTE: Lazy-init the lock so the TTLCache only stores locks for messages that are actually edited.
            lock = event_edit_locks.get(event.id)
            if lock is None:
                lock = asyncio.Lock()
                event_edit_locks[event.id] = lock

            async with lock:
                # @NOTE: If an edit arrives before the album handler has populated ttl_cache (race between
                # Album event's _HACK_DELAY collection window and the edit event), sleep slightly longer
                # than the album delay to let the cache populate first.
                if event.id not in ttl_cache:
                    await asyncio.sleep(album._HACK_DELAY * 1.25)

                if event.id in ttl_cache:
                    # @NOTE: Only append the link when the edited message has text, matching the forward
                    # handlers — a text-less edit must not gain a caption. Use raw .message so entity
                    # offsets stay valid instead of the markdown-rendered .text.
                    text = f"{event.message.message}\n\n{source_addr}{event.id}" if event.message.message else ""
                    entities = event.message.entities or None

                    try:
                        async with send_lock:
                            await client.edit_message(
                                target_id,
                                ttl_cache[event.id],
                                text,
                                file=event.media,
                                formatting_entities=entities,
                                **DEFAULTS,
                            )
                        sentry_sdk.add_breadcrumb(category="repost", message="Message edited", level="info")
                        logger.info(
                            "Successful edit [%s]: %s", source_addr + str(event.id), target_addr + str(ttl_cache[event.id])
                        )
                        await save_cache(cache_fp, cache_lock, ttl_cache)
                    except MessageNotModifiedError as e:
                        logger.warning(
                            "Edit failed [%s]: %s (%s)",
                            source_addr + str(event.id),
                            str(e),
                            target_addr + str(ttl_cache[event.id]),
                        )
                else:
                    logger.debug("Edit event for unknown message %d — not in cache", event.id)
        except FloodWaitError as e:
            logger.warning("Flood-wait in edit handler, sleeping %ds...", e.seconds)
            await asyncio.sleep(e.seconds)
        except Exception:
            logger.exception("Unhandled error in edit handler")

    # @TODO: Listen to these events from userbot if possible, since it's the only reliable way to have
    #        latest delete events arrive from channel immediately and not in batch with other updates.
    @client.on(events.MessageDeleted(chats=[source_id]))
    async def auto_delete_any(event):
        try:
            to_delete = {}
            for delete_id in event.deleted_ids:
                if delete_id in ttl_cache:
                    to_delete[delete_id] = ttl_cache[delete_id]

            if not to_delete:
                return

            async with send_lock:
                await client.delete_messages(target_id, list(to_delete.values()))

            sentry_sdk.add_breadcrumb(category="repost", message=f"Deleted {len(to_delete)} mirrored message(s)", level="info")
            # @NOTE: Cache entries removed after successful API call — if delete_messages fails,
            # the mapping is preserved for potential manual cleanup.
            for delete_id in to_delete:
                if delete_id in ttl_cache:
                    del ttl_cache[delete_id]

            await save_cache(cache_fp, cache_lock, ttl_cache)
        except FloodWaitError as e:
            logger.warning("Flood-wait in delete handler, sleeping %ds...", e.seconds)
            await asyncio.sleep(e.seconds)
        except Exception:
            logger.exception("Unhandled error in delete handler")

    # @NOTE: Reverse handler only cleans up the cache — does NOT propagate deletes back to the source
    # channel. This is intentional: target-side moderation (e.g. removing spam) should not cascade
    # upstream and delete original posts.
    # @NOTE: Linear scan over ttl_cache to find reverse mappings (target→source). A reverse-lookup dict
    # would be O(1) but adds sync complexity with TTL eviction. Fine at current cache sizes.
    @client.on(events.MessageDeleted(chats=[target_id]))
    async def auto_delete_any_reverse(event):
        try:
            to_delete = []
            for message_oid, message_did in list(ttl_cache.items()):
                if message_did in event.deleted_ids:
                    to_delete.append(message_oid)

            if not to_delete:
                return

            for delete_id in to_delete:
                if delete_id in ttl_cache:
                    del ttl_cache[delete_id]

            await save_cache(cache_fp, cache_lock, ttl_cache)
        except Exception:
            logger.exception("Unhandled error in reverse delete handler")
