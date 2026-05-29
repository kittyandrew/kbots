import asyncio
from datetime import datetime, timedelta, timezone

import sentry_sdk
from telethon import events, types
from telethon.errors.rpcerrorlist import FloodWaitError

INACTIVE_LIMIT = timedelta(days=30)
JOINED_LIMIT = timedelta(days=1)


def _is_admin(user):
    """Check if the user's participant status is admin or channel creator.

    @NOTE: Relies on user.participant being populated, which is the default behavior
    of iter_participants (it fetches ChannelParticipant info for each user).
    """
    return isinstance(user.participant, (types.ChannelParticipantAdmin, types.ChannelParticipantCreator))


async def init(client, logger, config, **context):
    admin_chat = int(config.get("general", "admin_chat"))
    me = await client.get_me()
    purge_lock = asyncio.Lock()

    @client.on(events.NewMessage(outgoing=True, chats=[admin_chat], pattern=r"^/purge$"))
    async def handle_purge_command(event):
        await event.delete()

        if purge_lock.locked():
            await event.respond("Purge already in progress.")
            return

        async with purge_lock:
            participants = await event.client.get_participants(event.chat)
            status_msg = await event.respond(f"Scanning {len(participants)} participants...")

            to_kick = []
            for user in participants:
                if user.id == me.id or user.bot or user.deleted or _is_admin(user):
                    continue

                try:
                    result = await event.client.get_messages(event.chat, from_user=user, limit=1)
                except FloodWaitError as e:
                    logger.warning("Flood-wait during scan, sleeping %ds...", e.seconds)
                    await asyncio.sleep(e.seconds)
                    try:
                        result = await event.client.get_messages(event.chat, from_user=user, limit=1)
                    except Exception as e2:
                        logger.error("Failed to scan user %s after flood-wait retry: %s", user.first_name, e2, exc_info=True)
                        continue
                # @NOTE: Small delay between get_messages calls to avoid Telegram flood-wait bans.
                # In a 500-member chat this is 500 sequential API calls, ~2min total with this delay.
                await asyncio.sleep(0.25)

                if not result:
                    logger.info("Preparing to kick [never talked]: %s %s", user.first_name, user.last_name or "")
                    to_kick.append(user)
                    continue

                if (datetime.now(timezone.utc) - result[0].date) > INACTIVE_LIMIT:
                    logger.info("Preparing to kick [inactive 30+ days]: %s %s", user.first_name, user.last_name or "")
                    to_kick.append(user)
                    continue

                if isinstance(result[0], types.MessageService) and (datetime.now(timezone.utc) - result[0].date) > JOINED_LIMIT:
                    logger.info("Preparing to kick [joined but never talked]: %s %s", user.first_name, user.last_name or "")
                    to_kick.append(user)
                    continue

            logger.info("Kicking %d users...", len(to_kick))
            kicked, failed = 0, 0
            for kuser in to_kick:
                try:
                    await event.client.kick_participant(event.chat, kuser)
                    kicked += 1
                except FloodWaitError as e:
                    logger.warning("Flood-wait during kick, sleeping %ds...", e.seconds)
                    await asyncio.sleep(e.seconds)
                    try:
                        await event.client.kick_participant(event.chat, kuser)
                        kicked += 1
                    except Exception as e2:
                        logger.error("Failed to kick user after flood-wait retry: %s", e2, exc_info=True)
                        failed += 1
                except Exception as e:
                    logger.error("Failed to kick user: %s", e, exc_info=True)
                    failed += 1
                # @NOTE: Small delay between kicks to avoid hitting Telegram flood-wait limits.
                await asyncio.sleep(0.25)

            sentry_sdk.add_breadcrumb(category="admin", message=f"Purge complete: kicked {kicked}, failed {failed}", level="info")
            await status_msg.edit(f"Purge complete. Kicked {kicked} users." + (f" Failed: {failed}." if failed else ""))
            logger.info("Purge complete. Kicked: %d, Failed: %d", kicked, failed)
