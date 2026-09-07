import asyncio
import logging
import mimetypes
import os
import re
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TELEGRAM_SESSION = os.environ["TELEGRAM_SESSION"]

CHANNEL_ID = -1003708183148
ARCHIVE_ROOT = Path("./downloads/Telegram_Archive/GK-GS/parmar_ssc")
MAX_RETRIES = 5
RETRY_DELAY = 5
DOWNLOAD_DELAY = 1
MAX_FILES = 0

SUBJECT_MAP = {
    "Physics": ["physics", "physics class", "भौतिक विज्ञान", "भौतिकी"],
    "Chemistry": ["chemistry", "chemistry class", "रसायन विज्ञान"],
    "Biology": ["biology", "biology class", "जीव विज्ञान", "जीवविज्ञान"],
    "Static_GK": [
        "static gk", "static-gk", "staticgk", "static general knowledge",
        "static facts", "स्थिर सामान्य ज्ञान", "general knowledge", "सामान्य ज्ञान",
        "gk", "जीके", "polity", "राजव्यवस्था", "constitution", "संविधान",
        "history", "इतिहास", "geography", "भूगोल", "economics", "अर्थशास्त्र",
        "banking", "बैंकिंग", "award", "पुरस्कार", "important days", "महत्वपूर्ण दिवस",
        "national symbols", "राष्ट्रीय प्रतीक", "sport", "खेल", "olympic", "olympics"
    ],
}

STRONG_LABELS = {
    "Physics": ["physics", "physics class", "भौतिक विज्ञान", "भौतिकी"],
    "Chemistry": ["chemistry", "chemistry class", "रसायन विज्ञान"],
    "Biology": ["biology", "biology class", "जीव विज्ञान", "जीवविज्ञान"],
    "Static_GK": [
        "static gk", "static-gk", "staticgk", "static general knowledge",
        "static facts", "स्थिर सामान्य ज्ञान"
    ],
}

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".3gp")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("telegram_archive")


def normalize_text(text):
    if not text:
        return ""
    text = str(text).lower().replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename or "file")
    filename = re.sub(r"\s+", " ", filename).strip().rstrip(". ")
    return filename or "file"


def message_text(message):
    parts = []
    if getattr(message, "file", None) and getattr(message.file, "name", None):
        parts.append(message.file.name)
    if getattr(message, "raw_text", None):
        parts.append(message.raw_text)
    return " ".join(parts)


def classify_subject(message):
    text = normalize_text(message_text(message))
    if not text:
        return None

    for subject, labels in STRONG_LABELS.items():
        if any(normalize_text(label) in text for label in labels):
            return subject

    scores = {}
    for subject, keywords in SUBJECT_MAP.items():
        scores[subject] = sum(1 for keyword in keywords if normalize_text(keyword) in text)

    subject, score = max(scores.items(), key=lambda item: item[1])
    return subject if score > 0 else None


def detect_file_type(message):
    file_obj = getattr(message, "file", None)
    if not file_obj:
        return None

    mime = (getattr(file_obj, "mime_type", None) or "").lower()
    name = (getattr(file_obj, "name", None) or "").lower()

    if mime == "application/pdf" or name.endswith(".pdf"):
        return "PDF"

    if getattr(message, "video", None) or mime.startswith("video/") or name.endswith(VIDEO_EXTENSIONS):
        return "VIDEO"

    return None


def original_filename(message):
    name = getattr(getattr(message, "file", None), "name", None)
    if name:
        return sanitize_filename(name)
    mime = getattr(getattr(message, "file", None), "mime_type", None) or ""
    return "telegram_file" + (mimetypes.guess_extension(mime) or "")


def build_filename(message):
    original = original_filename(message)
    stem, ext = os.path.splitext(original)
    if not ext:
        ext = ".mp4" if detect_file_type(message) == "VIDEO" else ".pdf"
    return sanitize_filename(f"{stem}_msg_{message.id}{ext}")


def destination(subject, file_type):
    folder = "Videos" if file_type == "VIDEO" else "PDFs"
    return ARCHIVE_ROOT / subject / folder


class Progress:
    def __init__(self, filename):
        self.filename = filename
        self.last_percent = -1

    def __call__(self, current, total):
        if not total:
            return
        percent = int(current * 100 / total)
        if percent == 100 or percent - self.last_percent >= 10:
            self.last_percent = percent
            logger.info("Downloading %s %d%% (%.1f/%.1f MB)", self.filename, percent, current / 1048576, total / 1048576)


async def download_file(client, message, output_path):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info("Download attempt %d/%d: %s", attempt, MAX_RETRIES, output_path.name)
            await client.download_media(
                message,
                file=str(output_path),
                progress_callback=Progress(output_path.name),
            )
            if not output_path.exists() or output_path.stat().st_size <= 0:
                raise RuntimeError("Downloaded file is missing or empty")
            logger.info("SUCCESS: %s (%.2f MB)", output_path.name, output_path.stat().st_size / 1048576)
            return True
        except FloodWaitError as exc:
            wait = int(exc.seconds) + 2
            logger.warning("Telegram FloodWait: waiting %d seconds", wait)
            await asyncio.sleep(wait)
        except (RPCError, asyncio.TimeoutError, TimeoutError, ConnectionError, OSError) as exc:
            logger.warning("Download error on attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
        except Exception as exc:
            logger.exception("Unexpected download error on attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

        if output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass

    logger.error("FAILED after %d attempts: %s", MAX_RETRIES, output_path.name)
    return False


async def main():
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("Telegram Archive Downloader")
    logger.info("=" * 70)
    logger.info("Channel ID : %s", CHANNEL_ID)
    logger.info("Archive    : %s", ARCHIVE_ROOT.resolve())
    logger.info("Subjects   : %s", ", ".join(SUBJECT_MAP))
    logger.info("Drive check: DISABLED")
    logger.info("Drive upload is handled by rclone in GitHub Actions")

    client = TelegramClient(StringSession(TELEGRAM_SESSION), API_ID, API_HASH, timeout=120)

    try:
        logger.info("Connecting to Telegram...")
        await client.start()
        entity = await client.get_entity(CHANNEL_ID)
        logger.info("Target resolved: %s", getattr(entity, "title", str(entity)))
        logger.info("Scanning messages...")

        scanned = downloaded = skipped_subject = skipped_other = failed = 0

        async for message in client.iter_messages(entity, reverse=True):
            scanned += 1

            if MAX_FILES and downloaded >= MAX_FILES:
                break

            file_type = detect_file_type(message)
            if file_type is None:
                skipped_other += 1
                continue

            # IMPORTANT: classify before downloading.
            subject = classify_subject(message)
            if subject is None:
                skipped_subject += 1
                logger.info("[%s] SKIP [NO ALLOWED SUBJECT] [%s] %s", message.id, file_type, original_filename(message))
                continue

            target_dir = destination(subject, file_type)
            target_dir.mkdir(parents=True, exist_ok=True)
            filename = build_filename(message)
            output_path = target_dir / filename

            logger.info("[%s] [%s] [%s] %s", message.id, subject, file_type, filename)

            # This is only a local filesystem check. There is NO Drive check.
            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info("[%s] SKIP [LOCAL FILE EXISTS] %s", message.id, filename)
                continue

            if await download_file(client, message, output_path):
                downloaded += 1
            else:
                failed += 1

            await asyncio.sleep(DOWNLOAD_DELAY)

        logger.info("=" * 70)
        logger.info("SCAN COMPLETE")
        logger.info("Messages scanned : %d", scanned)
        logger.info("Downloaded       : %d", downloaded)
        logger.info("No subject       : %d", skipped_subject)
        logger.info("Other skipped    : %d", skipped_other)
        logger.info("Failed           : %d", failed)
        logger.info("Google Drive     : NOT CHECKED")
        logger.info("=" * 70)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
