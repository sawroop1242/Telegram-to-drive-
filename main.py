import asyncio
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError


# ============================================================
# CONFIGURATION
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TELEGRAM_SESSION = os.environ["TELEGRAM_SESSION"]

CHANNEL_ID = -1003708183148

ARCHIVE_ROOT = Path("./downloads/Telegram_Archive/GK-GS")
RCLONE_REMOTE_ROOT = os.environ.get("RCLONE_REMOTE_ROOT", "gdrive1:Telegram_Archive/GK-GS")

MAX_RETRIES = 5
RETRY_DELAY = 5
DOWNLOAD_DELAY = 0
UPLOAD_RETRIES = 3

# 0 = unlimited
MAX_FILES = 0

# IMPORTANT:
# Only files matching one of SUBJECT_MAP are downloaded.
ONLY_MATCHED_FILES = True


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("telegram_archive")


# ============================================================
# SUBJECTS
# ============================================================

SUBJECT_MAP = {
    "Ancient_History": [
        "ancient history", "ancient_history", "ancienthistory", "प्राचीन इतिहास",
    ],
    "Medieval_History": [
        "medieval history", "medieval_history", "medievalhistory", "मध्यकालीन इतिहास",
    ],
    "Modern_History": [
        "modern history", "modern_history", "modernhistory", "आधुनिक इतिहास",
    ],
    "Polity": [
        "polity", "indian polity", "constitution", "constitutional", "राजव्यवस्था", "संविधान",
    ],
    "Geography": [
        "geography", "geo", "भूगोल",
    ],
    "Economics": [
        "economics", "economy", "indian economy", "अर्थशास्त्र", "अर्थव्यवस्था",
    ],
    "Physics": [
        "physics", "भौतिक विज्ञान",
    ],
    "Chemistry": [
        "chemistry", "रसायन विज्ञान",
    ],
    "Biology": [
        "biology", "zoology", "botany", "जीव विज्ञान", "वनस्पति विज्ञान", "प्राणी विज्ञान",
    ],
    "Static_GK": [
        "static gk", "static_gk", "staticgk", "static general knowledge", "स्टेटिक जीके", "स्थैतिक सामान्य ज्ञान",
    ],
    "Computer": [
        "computer", "computers", "computer awareness", "कंप्यूटर",
    ],
}


# ============================================================
# MANIFEST
# ============================================================
# This is ONLY a local-run manifest.
# It does NOT check Google Drive.

MANIFEST_PATH = ARCHIVE_ROOT / ".download_manifest.json"


def load_manifest():
    if not MANIFEST_PATH.exists():
        return {}
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Could not load manifest: %s", exc)
        return {}


def save_manifest(manifest):
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    temp_path = MANIFEST_PATH.with_suffix(".tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        temp_path.replace(MANIFEST_PATH)
    except Exception as exc:
        logger.warning("Could not save manifest: %s", exc)


# ============================================================
# TEXT UTILITIES
# ============================================================

def normalize_text(text):
    if not text:
        return ""
    text = str(text).lower()
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sanitize_filename(filename):
    if not filename:
        filename = "unnamed_file"
    filename = str(filename)
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip()
    filename = filename.rstrip(". ")
    return filename or "unnamed_file"


def get_message_text(message):
    parts = []
    if getattr(message, "text", None):
        parts.append(message.text)
    if getattr(message, "message", None):
        parts.append(message.message)
    file_obj = getattr(message, "file", None)
    if file_obj:
        name = getattr(file_obj, "name", None)
        if name:
            parts.append(name)
    return " ".join(p for p in parts if p)


# ============================================================
# SUBJECT CLASSIFICATION
# ============================================================

def classify_subject(message):
    """Return an allowed subject, or None when unmatched."""
    text = normalize_text(get_message_text(message))
    if not text:
        return None

    subjects = sorted(
        SUBJECT_MAP.items(),
        key=lambda item: max(len(normalize_text(alias)) for alias in item[1]),
        reverse=True,
    )

    for subject, aliases in subjects:
        for alias in aliases:
            if normalize_text(alias) in text:
                return subject
    return None


# ============================================================
# FILE TYPE
# ============================================================

def detect_file_type(message):
    file_obj = getattr(message, "file", None)
    if not file_obj:
        return None

    mime_type = getattr(file_obj, "mime_type", None)
    name = getattr(file_obj, "name", None) or ""
    name_lower = name.lower()

    if mime_type == "application/pdf" or name_lower.endswith(".pdf"):
        return "PDF"

    if (
        getattr(message, "video", None)
        or (mime_type and mime_type.startswith("video/"))
        or name_lower.endswith((".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".3gp"))
    ):
        return "VIDEO"

    return None


# ============================================================
# FILE NAME
# ============================================================

def get_original_filename(message):
    file_obj = getattr(message, "file", None)
    if file_obj:
        name = getattr(file_obj, "name", None)
        if name:
            return sanitize_filename(name)

    mime_type = getattr(file_obj, "mime_type", None) if file_obj else None
    extension = mimetypes.guess_extension(mime_type or "") or ""
    if extension:
        return f"telegram_file{extension}"
    return "telegram_file"


def build_filename(message):
    original = get_original_filename(message)
    return sanitize_filename(f"{original}_msg_{message.id}")


# ============================================================
# DESTINATION
# ============================================================

def get_destination(subject, file_type):
    if file_type == "VIDEO":
        return ARCHIVE_ROOT / subject / "Videos"
    if file_type == "PDF":
        return ARCHIVE_ROOT / subject / "PDFs"
    return None


def get_remote_destination(subject, file_type):
    if file_type == "VIDEO":
        return f"{RCLONE_REMOTE_ROOT}/{subject}/Videos"
    if file_type == "PDF":
        return f"{RCLONE_REMOTE_ROOT}/{subject}/PDFs"
    return None


# ============================================================
# PROGRESS
# ============================================================

class DownloadProgress:
    def __init__(self, filename):
        self.filename = filename
        self.last_percent = -1

    def __call__(self, current, total):
        if not total:
            return
        percent = int(current * 100 / total)
        rounded = (percent // 10) * 10
        if rounded != self.last_percent:
            self.last_percent = rounded
            logger.info(
                "Downloading %-70s %3d%% (%.1f/%.1f MB)",
                self.filename,
                percent,
                current / (1024 * 1024),
                total / (1024 * 1024),
            )


# ============================================================
# DOWNLOAD
# ============================================================

async def download_file(client, message, output_path):
    filename = output_path.name
    temp_path = output_path.with_name(output_path.name + ".part")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if temp_path.exists():
                temp_path.unlink()

            logger.info("Download attempt %d/%d: %s", attempt, MAX_RETRIES, filename)
            await client.download_media(
                message,
                file=str(temp_path),
                progress_callback=DownloadProgress(filename),
            )

            if not temp_path.exists():
                raise RuntimeError("Download completed but temporary file does not exist.")

            size = temp_path.stat().st_size
            if size <= 0:
                raise RuntimeError("Downloaded file is empty.")

            # Atomic rename: the uploader only sees the final filename after
            # the Telegram download has completely finished.
            temp_path.replace(output_path)

            logger.info("SUCCESS: %s (%.2f MB)", filename, size / 1024 / 1024)
            return True

        except FloodWaitError as exc:
            wait_time = int(exc.seconds) + 2
            logger.warning("Telegram FloodWait: waiting %d seconds", wait_time)
            await asyncio.sleep(wait_time)

        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError, RPCError) as exc:
            logger.warning("Download error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

        except TypeError as exc:
            logger.error("Fatal download API error: %s", exc)
            return False

        except Exception as exc:
            logger.exception("Unexpected download error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY)

        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass

    logger.error("FAILED after %d attempts: %s", MAX_RETRIES, filename)
    return False


# ============================================================
# IMMEDIATE GOOGLE DRIVE UPLOAD
# ============================================================

async def upload_to_drive(output_path, subject, file_type):
    """Upload one completed file immediately after its Telegram download."""
    remote_destination = get_remote_destination(subject, file_type)
    if not remote_destination:
        logger.error("No Google Drive destination for %s / %s", subject, file_type)
        return False

    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            logger.info(
                "Uploading immediately to Google Drive: %s -> %s",
                output_path.name,
                remote_destination,
            )

            command = [
                "rclone",
                "copyto",
                str(output_path),
                f"{remote_destination}/{output_path.name}",
                "--retries",
                "3",
                "--low-level-retries",
                "10",
                "--drive-chunk-size",
                "64M",
            ]

            result = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                logger.info("DRIVE UPLOAD SUCCESS: %s", output_path.name)
                return True

            logger.warning(
                "Google Drive upload failed (attempt %d/%d): %s",
                attempt,
                UPLOAD_RETRIES,
                result.stderr.strip() or result.stdout.strip() or "rclone failed",
            )

        except Exception as exc:
            logger.warning(
                "Google Drive upload error (attempt %d/%d): %s",
                attempt,
                UPLOAD_RETRIES,
                exc,
            )

        if attempt < UPLOAD_RETRIES:
            await asyncio.sleep(RETRY_DELAY)

    logger.error("DRIVE UPLOAD FAILED: %s", output_path.name)
    return False


# ============================================================
# PROCESS MESSAGE
# ============================================================

async def process_message(client, message, manifest):
    file_type = detect_file_type(message)

    if file_type is None:
        return "SKIP_OTHER"

    subject = classify_subject(message)
    if subject is None:
        original = get_original_filename(message)
        logger.info("[%s] SKIP [NO ALLOWED SUBJECT] [%s] %s", message.id, file_type, original)
        return "SKIP_SUBJECT"

    destination = get_destination(subject, file_type)
    if destination is None:
        return "SKIP_OTHER"

    destination.mkdir(parents=True, exist_ok=True)
    filename = build_filename(message)
    output_path = destination / filename
    message_key = str(message.id)

    if message_key in manifest:
        logger.info("[%s] SKIP [LOCAL MANIFEST] [%s] %s", message.id, file_type, filename)
        return "SKIP_MANIFEST"

    if output_path.exists():
        size = output_path.stat().st_size
        if size > 0:
            logger.info("[%s] SKIP [LOCAL FILE EXISTS] [%s] %s", message.id, file_type, filename)
            manifest[message_key] = {
                "filename": filename,
                "subject": subject,
                "file_type": file_type,
                "status": "downloaded",
            }
            save_manifest(manifest)
            return "SKIP_LOCAL"

    logger.info("[%s] [%s] [%s] %s", message.id, subject, file_type, filename)

    success = await download_file(client, message, output_path)
    if not success:
        return "FAILED"

    # IMPORTANT: upload immediately after the Telegram download completes.
    # main.py never checks Google Drive for file existence.
    upload_success = await upload_to_drive(output_path, subject, file_type)
    if not upload_success:
        return "FAILED_UPLOAD"

    manifest[message_key] = {
        "filename": filename,
        "subject": subject,
        "file_type": file_type,
        "status": "downloaded_and_uploaded",
    }
    save_manifest(manifest)

    await asyncio.sleep(DOWNLOAD_DELAY)
    return "DOWNLOADED_UPLOADED"


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info("=" * 70)
    logger.info("Telegram Archive Downloader")
    logger.info("=" * 70)
    logger.info("Channel ID : %s", CHANNEL_ID)
    logger.info("Archive    : %s", ARCHIVE_ROOT.resolve())
    logger.info("Drive root : %s", RCLONE_REMOTE_ROOT)
    logger.info("Max retry  : %s", MAX_RETRIES)
    logger.info("Only match : %s", ONLY_MATCHED_FILES)
    logger.info("Subjects   : %d", len(SUBJECT_MAP))
    logger.info("Drive check: DISABLED")
    logger.info("Upload mode: IMMEDIATE - each file uploads after download")

    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    client = TelegramClient(
        StringSession(TELEGRAM_SESSION),
        API_ID,
        API_HASH,
    )

    try:
        logger.info("Connecting to Telegram...")
        await client.start()
        logger.info("Telegram authorization successful.")

        entity = await client.get_entity(CHANNEL_ID)
        logger.info("Target resolved: %s", getattr(entity, "title", str(entity)))
        logger.info("Scanning messages...")

        counters = {
            "scanned": 0,
            "downloaded_uploaded": 0,
            "skipped_subject": 0,
            "skipped_manifest": 0,
            "skipped_local": 0,
            "skipped_other": 0,
            "failed": 0,
            "failed_upload": 0,
        }

        processed = 0

        async for message in client.iter_messages(entity, reverse=True):
            counters["scanned"] += 1

            if MAX_FILES > 0 and processed >= MAX_FILES:
                logger.info("MAX_FILES reached: %d", MAX_FILES)
                break

            result = await process_message(client, message, manifest)

            if result == "DOWNLOADED_UPLOADED":
                counters["downloaded_uploaded"] += 1
                processed += 1
            elif result == "SKIP_SUBJECT":
                counters["skipped_subject"] += 1
            elif result == "SKIP_MANIFEST":
                counters["skipped_manifest"] += 1
            elif result == "SKIP_LOCAL":
                counters["skipped_local"] += 1
            elif result == "SKIP_OTHER":
                counters["skipped_other"] += 1
            elif result == "FAILED_UPLOAD":
                counters["failed_upload"] += 1
                counters["failed"] += 1
            elif result == "FAILED":
                counters["failed"] += 1

        logger.info("=" * 70)
        logger.info("SCAN COMPLETE")
        logger.info("Messages scanned : %d", counters["scanned"])
        logger.info("Downloaded+Drive : %d", counters["downloaded_uploaded"])
        logger.info("No subject       : %d", counters["skipped_subject"])
        logger.info("Manifest skipped : %d", counters["skipped_manifest"])
        logger.info("Local skipped    : %d", counters["skipped_local"])
        logger.info("Other skipped    : %d", counters["skipped_other"])
        logger.info("Failed           : %d", counters["failed"])
        logger.info("Upload failures  : %d", counters["failed_upload"])
        logger.info("Google Drive     : uploaded per file immediately")
        logger.info("=" * 70)

    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
