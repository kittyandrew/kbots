import asyncio
import html
import tempfile
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from secrets import token_urlsafe
from typing import cast

import cv2
import imageio_ffmpeg
import sentry_sdk
import yt_dlp
from telethon import events, utils
from telethon.extensions import html as telegram_html
from telethon.tl.types import DocumentAttributeVideo, MessageEntityUrl, MessageMediaWebPage
from yt_dlp.utils import traverse_obj

MATCH_RULES: dict[str, tuple[list[str], Callable[[str], bool]]] = {
    "instagram": (["instagram.com", "facebook.com"], lambda p: "/reel/" in p),
    "facebook": (["facebook.com"], lambda p: "/share/v/" in p),
    "youtube": (["youtube.com"], lambda p: p.startswith("/shorts/")),
    "twitter": (["x.com", "fixupx.com", "fxtwitter.com"], lambda p: len(p.strip("/").split("/")) > 1),
    "tiktok": (["tiktok.com", "*.tiktok.com"], bool),
    "funnyjunk": (["funnyjunk.com", "*.funnyjunk.com"], lambda p: bool(p) and not p.lower().endswith(".gif")),
}


@dataclass(frozen=True)
class Video:
    path: Path
    caption: str
    short_caption: str
    attributes: list[DocumentAttributeVideo]


def validate_url(source: str):
    parsed = urllib.parse.urlparse(source if source.startswith("http") else f"http://{source}")
    assert parsed.hostname, f"Somehow url without .hostname: '{source}'"

    hostname = parsed.hostname.removeprefix("www.")
    for platform, (domains, matches_path) in MATCH_RULES.items():
        if any(fnmatchcase(hostname, domain) for domain in domains) and matches_path(parsed.path):
            return platform
    return False


def download_by_url(url: str, output_dir: str, platform: str, logger) -> Video:
    yt_dlp_config = {
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "outtmpl": str(path := Path(output_dir) / f"{token_urlsafe(16)}.mp4"),
        "format_sort": ["res", "vcodec:avc", "acodec:aac"],
        "playlist_items": "1",
    }
    with yt_dlp.YoutubeDL(yt_dlp_config) as ydl:
        post = None
        extractor = ydl.get_info_extractor("Twitter")
        graphql = extractor._call_graphql_api

        # yt-dlp omits long-form X text and flattens paragraphs; capture only the requested post, not linked posts.
        def capture_graphql(endpoint, media_id):
            nonlocal post
            response = graphql(endpoint, media_id)
            if extractor.suitable(url) and media_id == extractor._match_id(url):
                tweet = traverse_obj(response, ("tweetResult", "result"), expected_type=dict) or {}
                tweet = tweet.get("tweet") or tweet
                if isinstance(tweet, dict) and tweet.get("rest_id") == media_id:
                    post = tweet
                    quote = traverse_obj(post, ("quoted_status_result", "result")) or {}
                    if (quote_id := quote.get("rest_id")) and not ("legacy" in quote and "core" in quote):
                        try:
                            fallback = extractor._call_syndication_api(quote_id)  # GraphQL can return only the quote's ID.
                            if fallback.get("id_str") != quote_id:
                                raise yt_dlp.utils.ExtractorError("Syndication returned a different quoted post")
                        except yt_dlp.utils.ExtractorError:
                            logger.warning("Quoted X post unavailable: %s", quote_id, exc_info=True)
                        else:
                            quote.setdefault("legacy", {**fallback, "full_text": fallback.get("text", "")})
                            quote.setdefault("core", {"user_results": {"result": {"legacy": fallback.get("user", {})}}})
            return response

        extractor._call_graphql_api = capture_graphql

        info = ydl.extract_info(url, download=False, process=False)
        if ids := traverse_obj(post, ("quoted_status_result", "result", "legacy", "extended_entities", "media", ..., "id_str")):
            info.get("entries", []).sort(key=lambda entry: entry["id"] in ids)  # yt-dlp lists quoted media before outer cards.
        info = ydl.process_ie_result(info, download=True)
        while info.get("_type") in ("playlist", "multi_video"):
            info = info["entries"][0]
    assert info.get("resolution") != "audio only", "Audio-only posts are unsupported"
    if extractor.suitable(url) and post is None:
        logger.warning("X post text unavailable from yt-dlp extraction: %s", url)
    caption, short_caption = format_caption(url, platform, info, post)
    return Video(path, caption, short_caption, video_attributes(path, info, logger))


def twitter_text(post: dict) -> str:
    if note := traverse_obj(post, ("note_tweet", "note_tweet_results", "result", "text"), expected_type=str):
        return note.strip()

    text = traverse_obj(post, ("legacy", "full_text"), expected_type=str) or ""
    for media_url in traverse_obj(post, ("legacy", "entities", "media", ..., "url")):
        text = text.replace(media_url, "")
    return html.unescape(text).strip()


def format_caption(url: str, platform: str, info: dict, post: dict | None) -> tuple[str, str]:
    hostname = cast(str, urllib.parse.urlparse(url).hostname).removeprefix("www.")
    author = info.get("uploader") or hostname
    if post is not None:
        author = traverse_obj(post, ("core", "user_results", "result", "legacy", "name")) or author
    date = ""
    if (timestamp := info.get("timestamp")) and not info.get("direct"):
        date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime(" (%d %b %Y)")
    source = f'- <a href="{html.escape(url, quote=True)}">{html.escape(author)}</a>{date}'

    if info.get("direct"):
        return source, source
    if platform == "twitter" and hostname == "x.com":
        return format_twitter_caption(info, post, source) if post is not None else (source, source)
    if platform == "tiktok":
        video_id = info["display_id"]
        link = f"https://www.tiktok.com/@{urllib.parse.quote(author, safe='')}/video/{video_id}"
        caption = f'<a href="{link}">https://www.tiktok.com/@{html.escape(author)}/video/{video_id}</a>{date}'
        return caption, source
    if description := info.get("description") or info.get("title"):
        return f"<blockquote>{html.escape(description)}</blockquote>\n\n{source}", source
    return source, source


def format_twitter_caption(info: dict, post: dict, source: str) -> tuple[str, str]:
    quote = traverse_obj(post, ("quoted_status_result", "result")) or {}
    video_ids = traverse_obj(quote, ("legacy", "extended_entities", "media", ..., "id_str"))
    note = "<i>Video from the quoted post</i>" if str(info["id"]) in video_ids else ""
    parts = [note] if note else []

    quote_user = traverse_obj(quote, ("core", "user_results", "result", "legacy")) or {}
    if (quote_text := twitter_text(quote)) and (quote_id := quote.get("rest_id")) and quote_user.get("screen_name"):
        quote_url = f"https://x.com/{quote_user['screen_name']}/status/{quote_id}"
        quote_author = quote_user.get("name") or "Original post"
        quote_link = f'<a href="{html.escape(quote_url, quote=True)}">{html.escape(quote_author)}</a>'
        parts.append(f"Quoted {quote_link}:\n<blockquote>{html.escape(quote_text)}</blockquote>")

    if text := twitter_text(post):
        parts.append(html.escape(text) if quote_text else f"<blockquote>{html.escape(text)}</blockquote>")

    parts.append(source)
    short_caption = f"{note}\n\n{source}" if note else source
    return "\n\n".join(parts), short_caption


def video_attributes(path: Path, info: dict, logger) -> list[DocumentAttributeVideo]:
    duration, width, height = info.get("duration"), info.get("width"), info.get("height")
    if not (duration and width and height):  # yt-dlp can omit metadata for direct CDN downloads.
        sentry_sdk.add_breadcrumb(category="downloader", message="Falling back to OpenCV for video metadata")
        cap = cv2.VideoCapture()
        try:
            cap.open(str(path))
            width = width or int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = height or int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if fps := cap.get(cv2.CAP_PROP_FPS):
                duration = duration or cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
        except cv2.error:
            logger.exception("Video metadata probe failed for '%s'", path)
        finally:
            cap.release()
    return [DocumentAttributeVideo(duration, width, height, supports_streaming=True)] if duration and width and height else []


async def init(client, logger, config, **context):
    logger.info("Initiating shortform video downloader ...")

    @client.on(events.NewMessage(func=lambda e: e.text and e.entities and not (e.is_channel and not e.is_group)))
    async def shortform_video_downloader(event):
        preview = isinstance(event.media, MessageMediaWebPage)
        if event.media and not preview:  # Attachment links are attribution; bare links with previews still need downloading.
            logger.info("Skipping message %s: already has an attachment ('%s') ...", event.id, event.file and event.file.name)
            return

        for _, url in event.get_entities_text(MessageEntityUrl):  # Handles Telegram's UTF-16 entity offsets.
            if not (rule := validate_url(url)):
                continue

            if rule == "funnyjunk" and preview:  # FunnyJunk needs downloading only without a Telegram preview.
                continue

            logger.info("Processing url [detected shortform video]: '%s' ...", url)
            sentry_sdk.add_breadcrumb(category="downloader", message=f"Downloading {rule} video", data={"url": url})
            with tempfile.TemporaryDirectory() as out_dir:
                try:
                    video = await asyncio.to_thread(download_by_url, url, out_dir, rule, logger)
                    text, entities = telegram_html.parse(video.caption)
                    long_caption = len(utils.add_surrogate(text)) > 1024
                    caption = video.short_caption if long_caption else video.caption
                    reply = await event.reply(caption, file=video.path, attributes=video.attributes, parse_mode="html")
                    if long_caption:
                        # Telethon's default split can bisect an emoji's UTF-16 pair.
                        for part, part_entities in utils.split_text(text, entities, split_at=(r"\n", r"\s", r"[^\ud800-\udbff]")):
                            await reply.reply(part, formatting_entities=part_entities, link_preview=False)
                    logger.info("Uploaded video for shortform video url: '%s' ...", url)
                except (yt_dlp.utils.DownloadError, yt_dlp.utils.ExtractorError, AssertionError) as e:
                    if rule == "twitter" and urllib.parse.urlparse(url).hostname.removeprefix("www.") == "x.com":
                        logger.warning("[%s]: Failed downloading ('%s'), just replacing url ...", url, e)
                        await event.reply(url.replace("x.com/", "fxtwitter.com/"))
                    elif rule == "tiktok":
                        await event.reply("Failed to download, unprocessable tiktok link..", parse_mode="html")
                    else:
                        logger.error("[%s]: Failed downloading ('%s'), ignoring ...", url, e)
                except Exception:
                    logger.exception("Unexpected error processing video url: '%s'", url)
