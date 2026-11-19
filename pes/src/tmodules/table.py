import asyncio
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from itertools import chain, zip_longest
from pathlib import Path
from secrets import token_hex

import imgkit
import jinja2
import pytz
import sentry_sdk
from aiocache import Cache, cached
from markupsafe import Markup
from telethon import Button, TelegramClient, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.types import ChannelParticipantsAdmins, InputMessagesFilterPinned

from ..gsheets import get_gsheet_prompt, get_vehicle_types
from ..llm import Item, parse_messages
from ..template import template as table_html_template


def init_counter():
    return defaultdict(lambda: {"count": 0, "old": 0, "damaged": 0, "destroyed": 0, "captured": 0})


def get_time_range(tz):
    now = datetime.now(tz)
    # Check if we passed 6 am of the current day already.
    if (now.hour * 60 + now.minute) >= (6 * 60):
        # If so, range of interest concludes today.
        end = tz.normalize(now.replace(hour=6, minute=0, second=0, microsecond=0))
        return end - timedelta(days=1), end
    else:
        end = tz.normalize(now.replace(hour=6, minute=0, second=0, microsecond=0)) - timedelta(days=1)
        return end - timedelta(days=1), end


def is_relevant_post(message) -> bool:
    """Filter out posts that should not be included in equipment tracking.
    Skips archived posts and posts with undetermined affiliation."""
    if not message.text:
        return False
    msg_headers = set(word.lower().strip(".,!?/()") for word in message.text.split()[:5])
    if "архив" in msg_headers:
        return False
    if "принадлежность не определена" in message.text.lower():
        return False
    return True


def cache_exists(cache_fp: Path, date: str) -> bool:
    if cache_fp.exists():
        cache = json.loads(cache_fp.read_text())
        return date in cache
    return False


def get_cache(cache_fp: Path, date: str) -> list[Item]:
    if cache_fp.exists():
        values = json.loads(cache_fp.read_text()).get(date, [])
        return [Item.model_validate(r) for r in values]
    return []


def store_cache(cache_fp: Path, date: str, values: list[Item]):
    cache = {}
    if cache_fp.exists():
        cache = json.loads(cache_fp.read_text())

    cache[date] = [r.model_dump() for r in values]
    # @NOTE: Write to temp file then rename for atomic writes on POSIX.
    # Prevents cache corruption if the process is interrupted mid-write.
    tmp_fp = cache_fp.with_suffix(".tmp")
    with open(tmp_fp, "w") as f:
        json.dump(cache, f, indent=4)
    tmp_fp.rename(cache_fp)


def convert_counter_into_lines(counter: dict, vehicle_types):
    items = list(sorted(counter.values(), key=lambda o: o["name"], reverse=True))

    for item in items:
        item["type"] = vehicle_types.UNKNOWN
        for vtype in vehicle_types:
            if vtype.name != "UNKNOWN" and vtype.name.upper() in item["name"]:
                item["type"] = vtype
                break

    items = list(sorted(items, key=lambda o: o["type"]))

    lines, total, damaged, destroyed, captured, olds = [], 0, 0, 0, 0, 0
    for value in items:
        statuses, old = [], ""
        total += value["count"]
        if value["destroyed"]:
            statuses.append(f"{value['destroyed']}x DESTROYED")
            destroyed += value["destroyed"]
        if value["damaged"]:
            statuses.append(f"{value['damaged']}x DAMAGED")
            damaged += value["damaged"]
        if value["captured"]:
            statuses.append(f"{value['captured']}x CAPTURED")
            captured += value["captured"]
        if value["old"]:
            old = f"({value['old']}x OLD)"
            olds += value["old"]
        lines.append(f"{value['count']}x {value['name']} ({', '.join(statuses)}) {old}")

    statuses, old = [], ""
    if destroyed:
        statuses.append(f"{destroyed}x DESTROYED")
    if damaged:
        statuses.append(f"{damaged}x DAMAGED")
    if captured:
        statuses.append(f"{captured}x CAPTURED")
    if olds:
        old = f"({olds}x OLD)"

    return lines, f"{total}x ({', '.join(statuses)}) {old}" if total else ""


def render_table(date: str, ru_losses: list[str], ru_total: str, ua_losses: list[str], ua_total: str):
    losses = list(zip_longest(ru_losses, ua_losses, fillvalue=Markup("&nbsp;")))
    # @NOTE: autoescape=True prevents LLM-generated vehicle names from injecting HTML
    # into the template rendered by wkhtmltoimage (which executes JS).
    env = jinja2.Environment(autoescape=True)
    template = env.from_string(table_html_template)
    img = imgkit.from_string(template.render(date=date, losses=losses, ru_total=ru_total, ua_total=ua_total), False)
    return img


async def generate_cache_for_date(
    user, source_id: int, cache_fp: Path, target_date: datetime, gsheet_id: str, gsheet_key: str, logger
) -> list[Item]:
    """Generate and cache data for a specific historical date if cache entry is missing."""
    date_str = target_date.date().strftime("%d.%m.%Y")

    # @NOTE: We re-query even when a cache entry exists but is empty. An empty cache entry could
    # be from a failed run or a day with no posts. Re-querying is cheap when there are genuinely
    # no posts (no LLM calls), so we prefer to verify rather than trust an empty result.
    if cache_exists(cache_fp, date_str):
        cached = get_cache(cache_fp, date_str)
        if cached:
            return cached

    # Calculate the time range for the target date (6am to 6am window).
    # @NOTE: No tz.normalize() needed here — target_date is already 6am-aligned from
    # get_time_range() which normalizes, so replace(hour=6) is a no-op on the time component.
    start = target_date.replace(hour=6, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    logger.info(f"Generating missing cache for {date_str} (range: {start} - {end})...")

    relevant_posts = []
    async for message in user.iter_messages(source_id, offset_date=end):
        if start > message.date:
            break
        if (start <= message.date < end) and is_relevant_post(message):
            relevant_posts.append(message)

    if not relevant_posts:
        logger.warning(f"No relevant posts for {date_str}, storing empty cache entry.")
        store_cache(cache_fp, date_str, [])
        return []

    logger.info(f"Processing {len(relevant_posts)} posts for {date_str}...")
    extra_prompt = await get_gsheet_prompt(gsheet_id, gsheet_key)

    page_size, sem, tasks = 3, asyncio.Semaphore(8), []
    for page in range(len(relevant_posts[::page_size])):
        page_posts = relevant_posts[page * page_size : (page + 1) * page_size]
        tasks.append(parse_messages([m.raw_text for m in page_posts], extra_prompt, sem))

    results = list(chain(*(await asyncio.gather(*tasks))))
    store_cache(cache_fp, date_str, results)
    return results


async def generate_table(user, client: TelegramClient, config, logger, force_new=False, **context):
    source_id = config.getint("repost", "source_id")
    target_id = context.get("send_to") or config.getint("general", "target_id")
    tz = pytz.timezone(config.get("general", "timezone"))

    gsheet_id = config.get("general", "gsheet_id")
    gsheet_key = config.get("general", "gsheet_key")

    cache_fp = Path(config.get("general", "table_cache_fp"))

    start, end = get_time_range(tz)
    date = start.date().strftime("%d.%m.%Y")
    results: list[Item] = get_cache(cache_fp, date)
    if force_new or not results:
        relevant_posts = []
        async for message in user.iter_messages(source_id, offset_date=end):
            if start > message.date:
                break
            if (start <= message.date < end) and is_relevant_post(message):
                relevant_posts.append(message)

        if not len(relevant_posts):
            # @NOTE: We intentionally still generate an empty table here rather than returning early.
            # An empty table can be regenerated later via the inline button when posts arrive.
            logger.warning("No relevant posts to process..")

        logger.info("Grabbed %s relevant posts and processing..", len(relevant_posts))
        sentry_sdk.add_breadcrumb(category="table", message=f"Processing {len(relevant_posts)} posts for {date}")
        extra_prompt = await get_gsheet_prompt(gsheet_id, gsheet_key)

        page_size, sem, tasks = 3, asyncio.Semaphore(8), []
        for page in range(len(relevant_posts[::page_size])):
            page_posts = relevant_posts[page * page_size : (page + 1) * page_size]
            tasks.append(parse_messages([m.raw_text for m in page_posts], extra_prompt, sem))

        results = list(chain(*(await asyncio.gather(*tasks))))
        store_cache(cache_fp, date, results)

    def process_item(counter: dict, item: Item, now: datetime):
        counter = counter[item.name.upper()]
        counter["name"] = item.name.upper().replace(" OBR. ", " obr. ")
        counter["count"] += 1
        counter[item.status] += 1

        if not item.post_date:
            return

        try:
            post_date = datetime.strptime(item.post_date, "%d.%m.%Y").date()
        except (ValueError, TypeError):
            try:
                post_date = datetime.strptime(item.post_date, "%d.%m.%y").date()
            except (ValueError, TypeError):
                return
        counter["old"] += (now.date() - timedelta(days=14)) > post_date

    ru_counter, ua_counter = init_counter(), init_counter()
    for item in results:
        if not item.ownership:
            continue
        process_item(ru_counter if item.ownership == "ru" else ua_counter, item, start)

    vehicle_types = await get_vehicle_types(gsheet_id, gsheet_key, logger)
    ru_losses, ru_total = convert_counter_into_lines(ru_counter, vehicle_types)
    ua_losses, ua_total = convert_counter_into_lines(ua_counter, vehicle_types)

    table_img = render_table(date, ru_losses, ru_total, ua_losses, ua_total)
    daily_file = await client.upload_file(table_img, file_name=f"{date}-{token_hex(10)}.jpg")
    # @NOTE: tg_files order is [daily] or [daily, weekly]. Callers rely on this order
    #  for album posting and per-message editing on regeneration callbacks.
    tg_files = [daily_file]
    caption = f"Згенерована таблиця за {date}\n#table #таблиця"

    # Weekly summary table: generated on Mondays alongside the daily table.
    # Covers the full 7-day window (end-7 through end), which includes the same
    # date range as today's daily table. This is intentional — the weekly summary
    # is a standalone aggregate, not a complement to the daily.
    if datetime.now(tz).weekday() == 0:
        total_ru_counter, total_ua_counter = init_counter(), init_counter()

        days = 6  # 0..6 inclusive = 7 iterations
        week_start = end - timedelta(days=days + 1)
        dates = [week_start + timedelta(days=d) for d in range(days + 1)]
        all_items = await asyncio.gather(
            *[generate_cache_for_date(user, source_id, cache_fp, d, gsheet_id, gsheet_key, logger) for d in dates]
        )
        for current_date, items in zip(dates, all_items):
            for item in items:
                if not item.ownership:
                    continue
                process_item(total_ru_counter if item.ownership == "ru" else total_ua_counter, item, current_date)
        week_end = week_start + timedelta(days=days)

        t_ru_losses, t_ru_total = convert_counter_into_lines(total_ru_counter, vehicle_types)
        t_ua_losses, t_ua_total = convert_counter_into_lines(total_ua_counter, vehicle_types)
        summary_title = f"{week_start.strftime('%d.%m.%Y')} - {week_end.strftime('%d.%m.%Y')}"

        weekly_img = render_table(summary_title, t_ru_losses, t_ru_total, t_ua_losses, t_ua_total)
        weekly_file = await client.upload_file(weekly_img, file_name=f"weekly-summary-{date}-{token_hex(8)}.jpg")
        tg_files.append(weekly_file)

    return tg_files, caption, target_id, date


def get_next_run_at(tz, hour: int, minute: int):
    now = datetime.now(tz)
    if (now.hour * 60 + now.minute) < (hour * 60 + minute):
        return tz.normalize(now.replace(hour=hour, minute=minute, second=0, microsecond=0))
    return tz.normalize(now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=1))


async def scheduled_table(config, client, user, logger, storage, **context):
    target_id = config.getint("general", "target_id")
    while True:
        tz = pytz.timezone(config.get("general", "timezone"))
        d = datetime.strptime(config.get("general", "table_schedule_at"), "%H:%M")

        delta_time = get_next_run_at(tz, d.hour, d.minute) - datetime.now(tz)
        logger.info(f"Scheduling table generation for {delta_time} ...")
        sleep_until_next = max(1, round(delta_time.total_seconds()))
        logger.info(f"Sleeping until table generation ({sleep_until_next} seconds) ...")
        await asyncio.sleep(sleep_until_next)

        async for message in user.iter_messages(target_id, filter=InputMessagesFilterPinned):
            if message.text and "#table #таблиця" in message.text:
                try:
                    await message.unpin()
                except Exception:
                    logger.exception("Failed to unpin old table message")
                    break

        try:
            sentry_sdk.add_breadcrumb(category="schedule", message="Starting scheduled table generation")
            tg_files, caption, _, date = await generate_table(force_new=True, **storage)
            is_album = len(tg_files) > 1

            # @WARNING: Telegram limits callback data to 64 bytes. Current format uses ~44 bytes
            #  with typical IDs (7-digit msg IDs, 14-digit channel IDs). If message IDs ever
            #  exceed 8 digits this could get tight — monitor if the channel grows past 10M messages.
            buttons = [
                [Button.url("Таблиця-словник бота", config.get("general", "table_ref_url"))],
            ]

            if is_album:
                # @NOTE: Telegram albums cannot have inline buttons attached directly, so on
                #  Mondays we post the album (daily + weekly) without caption, then a separate
                #  text message with caption + buttons. We pin the caption message because it has
                #  the #table tag needed for the unpin lookup on the next scheduled run.
                result = await client.send_file(target_id, tg_files)
                messages = result if isinstance(result, list) else [result]
                msg_ids = ",".join(str(m.id) for m in messages)
                buttons.insert(0, [Button.inline("Згенерувати повторно", f"v0|{target_id}|{date}|{msg_ids}")])
                caption_msg = await client.send_message(target_id, caption, buttons=buttons)
                await caption_msg.pin()
            else:
                # Single image: attach caption + buttons directly to the image and pin it.
                # @NOTE: We can't encode the message ID in the button before sending (chicken-and-egg),
                #  so single-image buttons use the old format without msg_ids. The callback handler
                #  falls back to editing the button's own message via event.get_message() in this case.
                buttons.insert(0, [Button.inline("Згенерувати повторно", f"v0|{target_id}|{date}")])
                message = await client.send_file(target_id, tg_files[0], caption=caption, buttons=buttons)
                await message.pin()
        except Exception:
            logger.exception("Failed to generate or post scheduled table")
            await asyncio.sleep(30)


async def init(client: TelegramClient, logger, storage, **context):
    logger.info("Initiating table generator ...")
    callback_lock, callback_cache = asyncio.Lock(), Cache(ttl=60)

    @cached(ttl=60 * 5)
    async def get_channel_admins(channel_id: int):
        admins = await client.get_participants(channel_id, filter=ChannelParticipantsAdmins)
        return set(admin.id for admin in admins)

    # @TODO: Add a button to confirm/uncofirm table automatically post it at
    #   the certain other time to the main channel. Also add limited LLM
    #   chatbot functionality with certain tools to edit table data from chat (?)
    # @NOTE: Must store the task reference in storage — Python 3.12+ event loops only keep weak
    # references to tasks, so a local variable in init() could be GC'd after init() returns.
    storage["_scheduled_task"] = asyncio.create_task(scheduled_table(**storage))

    def _on_task_done(t):
        if not t.cancelled() and t.exception():
            logger.error("Scheduled table task died unexpectedly: %s", t.exception())

    storage["_scheduled_task"].add_done_callback(_on_task_done)

    @client.on(events.NewMessage(pattern="^/table ?(new)?$", func=lambda e: not (e.is_channel and not e.is_group)))
    async def table_generation_handler(event):
        await event.delete()
        force_new = bool(event.pattern_match.group(1))
        logger.info(f"Processing /table command (force_new={force_new}) ...")
        tg_files, caption, target_id, _ = await generate_table(force_new=force_new, send_to=event.chat_id, **storage)
        # @NOTE: Follows the same schedule rules as the scheduled post — on Mondays tg_files
        #  will contain [daily, weekly], otherwise just [daily].
        if len(tg_files) > 1:
            await client.send_file(target_id, tg_files)
            await client.send_message(target_id, caption)
        else:
            await client.send_file(target_id, tg_files[0], caption=caption)

    @client.on(events.CallbackQuery(data=re.compile(rb"^v\d+\|")))
    async def callback_query_handler(event):
        parts = event.data.decode().split("|")
        # @NOTE: Callback data format is "v0|channel_id|date|msg_id1,msg_id2".
        #  The msg_ids field was added to support editing individual album images
        #  (daily + weekly on Mondays). Older buttons without msg_ids will still
        #  work — the handler falls back to editing the button's own message.
        version, channel_id, date = parts[0], int(parts[1]), parts[2]
        if version != "v0":
            await event.answer("Unsupported button version!", alert=True)
            return
        album_msg_ids = [int(x) for x in parts[3].split(",")] if len(parts) > 3 else []

        # @NOTE: Pre-checks run outside the lock to avoid blocking on Telegram's 30s
        #  callback acknowledgment timeout during concurrent regenerations.
        post_date, now = datetime.strptime(date, "%d.%m.%Y"), datetime.now()
        # @NOTE: The button date is the DATA date (yesterday's date at 6am boundary), not the
        # posting date. A table posted at 9am March 7 has date "06.03.2026" (March 6 midnight).
        # timedelta(days=2) gives a ~39h window from posting, expiring around midnight the day after.
        if (now - post_date) > timedelta(days=2):
            await event.answer("Пост занадто старий для редагування!", alert=True)
            return

        admins = await get_channel_admins(channel_id)
        if event.sender_id not in admins:
            await event.answer("You are not an admin, bucko!", alert=True)
            return

        # @NOTE: Known limitation — callback_cache TTL is 60s but generate_table runs outside the
        # lock. If regeneration takes >60s, a second admin could trigger a concurrent regen. In
        # practice this is extremely unlikely: most days are cached, and weekly parallelization
        # below keeps even Monday regeneration fast.
        async with callback_lock:
            recent = await callback_cache.get("callback_query")
            if recent:
                await event.answer("Почекайте хвилину перед тим як генерувати знову!", alert=True)
                return
            await callback_cache.set("callback_query", True)

        await event.answer("Почекайте хвилину поки генерується таблиця!", alert=True)
        tg_files, _, _, _ = await generate_table(force_new=True, send_to=channel_id, **storage)

        try:
            if album_msg_ids:
                # Edit each album image individually. tg_files and album_msg_ids are
                # in the same order: [daily, weekly]. zip() safely handles the case where
                # the regenerated set has fewer images than the original (e.g. regen on a
                # non-Monday for a post that was originally made on Monday).
                for msg_id, tg_file in zip(album_msg_ids, tg_files):
                    await client.edit_message(channel_id, msg_id, file=tg_file)
            else:
                # Fallback for old-format buttons without encoded message IDs.
                original_message = await event.get_message()
                await original_message.edit(file=tg_files[0])
        except MessageNotModifiedError as e:
            logger.info(f"Did not modify the message (identical file): {e}!")
