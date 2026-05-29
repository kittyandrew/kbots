import asyncio
import html
import json
import os
from datetime import datetime
from random import randint
from time import time

import cachetools
import numpy as np
import pytz
from telethon import events


def load_data(fp: str):
    ids, dates = [], []
    with open(fp) as f:
        data = json.load(f)
        for item in data:
            ids.append(item["id"])
            dates.append(item["time"])
    return np.array(ids), np.array(dates)


async def delayed_kick_task(event):
    await asyncio.sleep(60 * 10)
    await event.client.kick_participant(event.chat, event.user)


async def init(client, logger, config, **context):
    owner = int(config.get("general", "owner"))
    timezone = pytz.timezone(config.get("general", "timezone"))
    target_id = config.getint("general", "target_id")
    target_text = config.get("general", "target_text")

    # @NOTE: Guesstimator estimates account creation date from Telegram user IDs using
    # polynomial interpolation on historical data. Without the data file, it's disabled.
    historical_data_fp = config.get("guesstimator", "historical", fallback="")
    guesstimator_enabled = False
    if not historical_data_fp:
        logger.warning("Guesstimator disabled: no historical data path configured in [guesstimator] historical.")
    elif not os.path.exists(historical_data_fp):
        logger.warning("Guesstimator disabled: historical data file '%s' not found.", historical_data_fp)
    else:
        guesstimator_enabled = True

    # Temporary storage that automatically cleans up references over time.
    kick_tasks: cachetools.TTLCache[int, asyncio.Task] = cachetools.TTLCache(maxsize=64, ttl=60 * 15)

    logger.info("Initiating gatekeeper ...")

    @client.on(events.ChatAction(chats=[target_id]))
    async def gatekeeper(event):
        if event.user_joined:
            await asyncio.sleep(2)

            donation = randint(50, 59)
            join_text = target_text.format(donation=donation)
            await event.reply(join_text, parse_mode="html", link_preview=False)

            assert event.user
            u = event.user

            kick_tasks[u.id] = asyncio.create_task(delayed_kick_task(event))

            # Profile photo processing bit (displaying when current pfp was set).
            photo_date = "-" * 10
            async for photo in client.iter_profile_photos(u, limit=50):
                if u.photo and u.photo.photo_id == photo.id:
                    photo_date = photo.date.astimezone(timezone).strftime("%Y-%m-%d %H:%M")

            guess_dt = "unknown"
            if guesstimator_enabled:
                x_data, y_data = load_data(historical_data_fp)
                fitted = np.polyfit(x_data, y_data, 3)
                interp = np.poly1d(fitted)

                guess, current = interp(u.id), time()
                if guess > current:
                    guess = current
                guess_dt = datetime.fromtimestamp(guess, tz=pytz.utc).astimezone(timezone).strftime("%m/%Y")

            safe_first = html.escape(u.first_name or "")
            safe_last = html.escape(u.last_name or "")
            safe_username = html.escape(u.username) if u.username else "-" * 10
            text = (
                "New user in the group\n\n"
                f"Fullname: <code>{safe_first} {safe_last}</code>\n"
                f"Username: <code>{'@' + safe_username if u.username else safe_username}</code>\n"
                f"Id: <code>{u.id}</code> (~{guess_dt})\n\n"
                f"Phone: <code>{u.phone or '-' * 10}</code>\n"
                f"Photo: <code>{photo_date}</code>\n"
            )
            await client.send_message(owner, text, parse_mode="html")

    @client.on(events.NewMessage(chats=[target_id], func=lambda e: e.sender_id in kick_tasks))
    async def kick_message_cancelator(event):
        logger.info("Cancelled kick task for user %s ...", event.sender_id)
        kick_tasks[event.sender_id].cancel()
        del kick_tasks[event.sender_id]
