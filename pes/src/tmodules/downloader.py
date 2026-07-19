import asyncio
import html
import tempfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_urlsafe
from typing import Optional

import aiofiles
import aiohttp
import imageio_ffmpeg
import sentry_sdk
import yt_dlp
from telethon import events
from telethon.tl.types import DocumentAttributeVideo, MessageEntityUrl, MessageMediaWebPage

MATCH_RULES = {
    "instagram": {"domains": ["instagram.com", "facebook.com"], "path": lambda p: "/reel/" in str(p)},
    "facebook": {"domains": ["facebook.com"], "path": lambda p: "/share/v/" in str(p)},
    "youtube": {"domains": ["youtube.com"], "path": lambda p: str(p).startswith("/shorts/")},
    "twitter": {"domains": ["x.com", "fixupx.com", "fxtwitter.com"], "path": lambda p: len(str(p).strip("/").split("/")) > 1},
    "tiktok": {"domains": ["tiktok.com"], "path": lambda p: bool(p), "match_subdomains": True},
    "funnyjunk": {
        "domains": ["funnyjunk.com"],
        "path": lambda p: bool(p) and not p.lower().endswith(".gif"),
        "match_subdomains": True,
        "skip_if_previewed": True,
    },
}


def validate_url(source: str, rules=MATCH_RULES):
    parsed = urllib.parse.urlparse(source if source.startswith("http") else f"http://{source}")
    assert parsed.hostname, f"Somehow url without .hostname: '{source}'"

    hostname = parsed.hostname.removeprefix("www.")
    for rname, config in rules.items():
        if config.get("match_subdomains"):
            matched = any(hostname == d or hostname.endswith("." + d) for d in config["domains"])
        else:
            matched = hostname in config["domains"]
        if matched and config["path"](parsed.path):
            return rname
    return False


def download_by_url(url: str, output_dir: str):
    yt_dlp_config = {
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "outtmpl": str(path := Path(output_dir) / f"{token_urlsafe(16)}.mp4"),
        "progress_hooks": [lambda _: None],
        "format_sort": ["res", "vcodec:avc", "acodec:aac"],
    }
    with yt_dlp.YoutubeDL(yt_dlp_config) as ydl:
        return path, ydl.extract_info(url, download=True)


def probe_video(path: Path, logger) -> dict:
    try:
        import cv2 as cv2_untyped

        cap = cv2_untyped.VideoCapture(str(path))
        if not cap.isOpened():
            return {}
        fps = cap.get(cv2_untyped.CAP_PROP_FPS)
        frame_count = cap.get(cv2_untyped.CAP_PROP_FRAME_COUNT)
        out = {
            "width": int(cap.get(cv2_untyped.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2_untyped.CAP_PROP_FRAME_HEIGHT)),
        }
        if fps and frame_count:
            out["duration"] = round(frame_count / fps)
        return out
    except Exception:
        logger.exception("probe_video failed for '%s'", path)
        return {}


async def download_thumb(info, output_dir: str, logger) -> Optional[Path]:
    path = Path(output_dir) / "video_thumbnail.jpeg"

    # @TODO: We can just convert any thumbnail to jpeg.
    for thumbnail in info.get("thumbnails", []):
        thumb_url = thumbnail.get("url", "")
        if any(thumb_url.split("?")[0].endswith(ext) for ext in (".jpeg", ".jpg")):
            async with aiohttp.ClientSession() as session:
                async with session.get(thumb_url) as resp, aiofiles.open(path, "wb") as f:
                    await f.write(await resp.read())
                    logger.info("Attempted to prepare thumbnail for the video: '%s' ...", path)
                    return path
    return None


async def init(client, logger, config, **context):
    logger.info("Initiating shortform video downloader ...")

    @client.on(events.NewMessage(func=lambda e: e.text and e.entities and not (e.is_channel and not e.is_group)))
    async def shortform_video_downloader(event):
        # @NOTE: A link in the caption of a real attachment (video/photo) is attribution for content
        # already posted — don't re-download it. Telegram's own link previews (MessageMediaWebPage)
        # don't count: bare-link messages with a preview still need processing.
        if event.media and not isinstance(event.media, MessageMediaWebPage):
            logger.info("Skipping message %s: already has an attachment ('%s') ...", event.id, event.file and event.file.name)
            return

        for item in event.entities:
            if not isinstance(item, MessageEntityUrl):
                continue

            if not (rule := validate_url(url := event.raw_text[item.offset : item.offset + item.length])):
                continue

            # @NOTE: Some platforms (e.g. FunnyJunk direct links) only need downloading when
            # Telegram can't generate its own preview. skip_if_previewed enables this per-rule.
            if MATCH_RULES[rule].get("skip_if_previewed") and isinstance(event.media, MessageMediaWebPage):
                continue

            logger.info("Processing url [detected shortform video]: '%s' ...", url)
            sentry_sdk.add_breadcrumb(category="downloader", message=f"Downloading {rule} video", data={"url": url})
            with tempfile.TemporaryDirectory() as out_dir:
                try:
                    fp, info = await asyncio.to_thread(download_by_url, url, out_dir)
                    assert info is not None, f"Something real broken (info={info})!"
                    # @NOTE: Direct file downloads (e.g. FunnyJunk CDN) lack duration/resolution
                    # and uploader metadata from yt-dlp, so we relax validation and use fallbacks.
                    is_direct = bool(info.get("direct"))
                    if not is_direct:
                        assert info.get("duration") is not None, "GIF or livestream... maybe support GIFs?"
                        assert str(info.get("resolution")) != "audio only", "Audio-only (e.g. tiktok presentation), can't process!"

                    user = info.get("uploader") or urllib.parse.urlparse(url).hostname.removeprefix("www.")
                    video_id = info.get("display_id", "")
                    if info.get("timestamp") and not is_direct:
                        upload_date = datetime.fromtimestamp(info["timestamp"], tz=timezone.utc).strftime("%d %b %Y")
                    else:
                        upload_date = None
                    safe_user = html.escape(user)
                    safe_url = html.escape(url, quote=True)
                    date_str = f" ({upload_date})" if upload_date else ""

                    if is_direct:
                        message = f'- <a href="{safe_url}">{safe_user}</a>'
                    elif rule == "tiktok":
                        tt_url = f"https://www.tiktok.com/@{urllib.parse.quote(user, safe='')}/video/{video_id}"
                        message = f'<a href="{tt_url}">https://www.tiktok.com/@{safe_user}/video/{video_id}</a>{date_str}'
                    elif description := info.get("description"):
                        # @TODO: We currently skip original description if it's a sub-tweet,
                        #  instead this should be a double quote or sub-quote somehow.
                        if rule == "twitter":
                            if (words := description.split(" "))[-1].startswith("https://t.co/"):
                                description = " ".join(words[:-1]).replace("  ", "\n\n")
                        message = f'<blockquote>{html.escape(description)}</blockquote>\n\n- <a href="{safe_url}">{safe_user}</a>{date_str}'
                    elif title := info.get("title"):
                        message = (
                            f'<blockquote>{html.escape(title)}</blockquote>\n\n- <a href="{safe_url}">{safe_user}</a>{date_str}'
                        )
                    else:
                        message = f'- <a href="{safe_url}">{safe_user}</a>{date_str}'

                    thumb, tg_file = await asyncio.gather(download_thumb(info, out_dir, logger), event.client.upload_file(fp))

                    duration, width, height = info.get("duration"), info.get("width"), info.get("height")
                    if not (duration and width and height):
                        sentry_sdk.add_breadcrumb(category="downloader", message="Falling back to OpenCV for video metadata")
                        probe = await asyncio.to_thread(probe_video, fp, logger)
                        duration = duration or probe.get("duration")
                        width = width or probe.get("width")
                        height = height or probe.get("height")
                    if duration and width and height:
                        attrs = [DocumentAttributeVideo(duration, width, height, supports_streaming=True)]
                    else:
                        attrs = []
                    await event.reply(message, file=tg_file, attributes=attrs, thumb=thumb, parse_mode="html")
                    logger.info("Uploaded video for shortform video url: '%s' ...", url)
                except (yt_dlp.utils.DownloadError, AssertionError) as e:
                    if rule == "twitter" and urllib.parse.urlparse(url).hostname.removeprefix("www.") == "x.com":
                        logger.warning("[%s]: Failed downloading ('%s'), just replacing url ...", url, e)
                        await event.reply(url.replace("x.com/", "fxtwitter.com/"))
                    elif rule == "tiktok":
                        await event.reply("Failed to download, unprocessable tiktok link..", parse_mode="html")
                    else:
                        logger.error("[%s]: Failed downloading ('%s'), ignoring ...", url, e)
                except Exception:
                    logger.exception("Unexpected error processing video url: '%s'", url)
