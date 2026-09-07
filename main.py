import asyncio
import json
import logging
import os
import re
import signal
import time
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    RPCError,
    ServerError,
    TimedOutError,
)
from telethon.sessions import StringSession


# ============================================================
# CONFIGURATION
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["TELEGRAM_SESSION"]

# Telegram channel / group ID
CHANNEL_ID = -1003708183148

# Local archive root
BASE_PATH = Path("./downloads/Telegram_Archive/GK-GS")

# Maximum number of retry attempts per file
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))

# Delay between successful downloads
DOWNLOAD_DELAY = float(os.getenv("DOWNLOAD_DELAY", "1.5"))

# Telegram request timeout
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))

# Download chunk size
REQUEST_SIZE = 1024 * 1024  # 1 MB

# Set to true if files that don't match a subject should be skipped
ONLY_MATCHED_FILES = os.getenv("ONLY_MATCHED_FILES", "false").lower() == "true"

# Optional maximum number of files to process.
# 0 = unlimited
MAX_FILES = int(os.getenv("MAX_FILES", "0"))

# Manifest used for resumability
MANIFEST_FILE = BASE_PATH / ".download_manifest.json"

# ============================================================
# SUBJECT CLASSIFICATION
# ============================================================

SUBJECT_FILTERS = {
    "MEDIEVAL HISTORY": "Medieval_History",
    "MODERN HISTORY": "Modern_History",
    "PHYSICS": "Physics",
    "CHEMISTRY": "Chemistry",
    "BIOLOGY": "Biology",
    "STATIC GK": "Static_GK",
}

DEFAULT_FOLDER = "Other_Stray_Files"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("telegram-downloader")


# ============================================================
# GLOBAL SHUTDOWN FLAG
# ============================================================

shutdown_requested = False


def request_shutdown(*_args):
    global shutdown_requested

    if not shutdown_requested:
        shutdown_requested = True
        logger.warning("Shutdown requested. Finishing current operation...")


# ============================================================
# MANIFEST
# ============================================================

def load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        return {}

    try:
        with MANIFEST_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            return data

    except Exception as exc:
        logger.warning("Could not read manifest: %s", exc)

    return {}


def save_manifest(manifest: dict):
    BASE_PATH.mkdir(parents=True, exist_ok=True)

    temporary_file = MANIFEST_FILE.with_suffix(".tmp")

    try:
        with temporary_file.open("w", encoding="utf-8") as f:
            json.dump(
                manifest,
                f,
                indent=2,
                ensure_ascii=False,
            )

        temporary_file.replace(MANIFEST_FILE)

    except Exception as exc:
        logger.warning("Could not save manifest: %s", exc)


# ============================================================
# FILENAME UTILITIES
# ============================================================

def sanitize_filename(name: str) -> str:
    """
    Remove characters that are unsafe for filenames while
    preserving spaces, dots, underscores and hyphens.
    """

    name = name.strip()

    # Remove control characters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)

    # Replace filesystem-dangerous characters
    name = re.sub(r'[<>:"/\\|?*]', "_", name)

    # Collapse repeated spaces
    name = re.sub(r"\s+", " ", name)

    # Avoid filenames ending in dots/spaces
    name = name.rstrip(". ")

    if not name:
        name = "file"

    return name[:180]


def get_extension(message, is_video: bool, is_pdf: bool) -> str:
    """
    Determine the most reliable file extension.
    """

    if message.file:
        ext = message.file.ext

        if ext:
            return ext.lower()

    if is_video:
        return ".mp4"

    if is_pdf:
        return ".pdf"

    return ".bin"


def build_filename(message, is_video: bool, is_pdf: bool) -> str:
    extension = get_extension(
        message,
        is_video,
        is_pdf,
    )

    original_name = None

    if message.file and message.file.name:
        original_name = message.file.name

    if original_name:
        base_name = Path(original_name).stem
    else:
        base_name = "file"

    base_name = sanitize_filename(base_name)

    # Message ID guarantees deterministic uniqueness.
    return f"{base_name}_msg_{message.id}{extension}"


# ============================================================
# SUBJECT DETECTION
# ============================================================

def get_message_text(message) -> str:
    parts = []

    if message.text:
        parts.append(message.text)

    if message.file and message.file.name:
        parts.append(message.file.name)

    return " ".join(parts).upper()


def classify_subject(message) -> tuple[str, Optional[str]]:
    """
    Return:
        (folder_name, matched_keyword)
    """

    text = get_message_text(message)

    for keyword, folder_name in SUBJECT_FILTERS.items():

        # Match the complete phrase instead of accidental
        # partial matches.
        pattern = rf"\b{re.escape(keyword)}\b"

        if re.search(pattern, text):
            return folder_name, keyword

    return DEFAULT_FOLDER, None


# ============================================================
# FILE TYPE DETECTION
# ============================================================

def detect_file_type(message) -> tuple[bool, bool]:
    is_video = bool(message.video)

    is_pdf = bool(
        message.document
        and message.document.mime_type
        and message.document.mime_type.lower() == "application/pdf"
    )

    return is_video, is_pdf


# ============================================================
# DOWNLOAD PROGRESS
# ============================================================

class DownloadProgress:
    """
    Prevent GitHub Actions logs from being flooded with
    thousands of progress messages.
    """

    def __init__(self, filename: str):
        self.filename = filename
        self.last_percentage = -1
        self.last_time = 0.0

    def __call__(self, received: int, total: int):

        if not total:
            return

        percentage = int((received / total) * 100)
        now = time.monotonic()

        # Print every 10% or at least every 5 seconds
        if (
            percentage >= self.last_percentage + 10
            or now - self.last_time >= 5
            or percentage == 100
        ):
            self.last_percentage = percentage
            self.last_time = now

            received_mb = received / (1024 * 1024)
            total_mb = total / (1024 * 1024)

            logger.info(
                "Downloading %-45s %3d%% (%0.1f/%0.1f MB)",
                self.filename[:45],
                percentage,
                received_mb,
                total_mb,
            )


# ============================================================
# PARTIAL FILE CLEANUP
# ============================================================

def remove_partial_file(path: Path):
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.warning(
            "Could not remove partial file %s: %s",
            path,
            exc,
        )


# ============================================================
# DOWNLOAD ONE FILE
# ============================================================

async def download_file(
    client: TelegramClient,
    message,
    destination: Path,
) -> bool:

    for attempt in range(1, MAX_RETRIES + 1):

        if shutdown_requested:
            return False

        try:

            if not client.is_connected():
                logger.warning("Telegram connection lost. Reconnecting...")
                await client.connect()

            if not await client.is_user_authorized():
                logger.error("Telegram session is no longer authorized.")
                return False

            logger.info(
                "Download attempt %d/%d: %s",
                attempt,
                MAX_RETRIES,
                destination.name,
            )

            progress = DownloadProgress(destination.name)

            downloaded_path = await client.download_media(
                message,
                file=str(destination),
                progress_callback=progress,
                request_size=REQUEST_SIZE,
            )

            if downloaded_path and destination.exists():

                # Basic integrity check
                file_size = destination.stat().st_size

                if file_size == 0:
                    raise IOError("Downloaded file is empty.")

                logger.info(
                    "SUCCESS: %s (%0.2f MB)",
                    destination.name,
                    file_size / (1024 * 1024),
                )

                return True

            raise IOError("Telegram returned no valid downloaded file.")

        except FloodWaitError as exc:

            # Telegram explicitly tells us how long to wait.
            wait_seconds = exc.seconds + 5

            logger.warning(
                "Telegram FloodWait: waiting %d seconds.",
                wait_seconds,
            )

            await asyncio.sleep(wait_seconds)

        except (TimedOutError, asyncio.TimeoutError, ServerError) as exc:

            wait_seconds = min(60, attempt * 10)

            logger.warning(
                "Temporary Telegram error: %s | retrying in %ds",
                exc,
                wait_seconds,
            )

            remove_partial_file(destination)
            await asyncio.sleep(wait_seconds)

        except (ConnectionError, OSError) as exc:

            wait_seconds = min(60, attempt * 10)

            logger.warning(
                "Connection/file error: %s | retrying in %ds",
                exc,
                wait_seconds,
            )

            remove_partial_file(destination)
            await asyncio.sleep(wait_seconds)

        except RPCError as exc:

            # RPC errors aren't always retryable, but transient
            # server errors may recover.
            if attempt < MAX_RETRIES:

                wait_seconds = min(60, attempt * 10)

                logger.warning(
                    "Telegram RPC error: %s | retrying in %ds",
                    exc,
                    wait_seconds,
                )

                remove_partial_file(destination)
                await asyncio.sleep(wait_seconds)

            else:
                logger.error(
                    "Permanent/unknown Telegram RPC failure: %s",
                    exc,
                )

        except Exception as exc:

            # Unexpected errors are logged and retried a limited
            # number of times instead of being silently swallowed.
            logger.exception(
                "Unexpected error downloading %s: %s",
                destination.name,
                exc,
            )

            remove_partial_file(destination)

            if attempt < MAX_RETRIES:
                wait_seconds = min(60, attempt * 10)
                await asyncio.sleep(wait_seconds)

    logger.error(
        "FAILED after %d attempts: %s",
        MAX_RETRIES,
        destination.name,
    )

    return False


# ============================================================
# MAIN
# ============================================================

async def main():

    global shutdown_requested

    # Create archive root
    BASE_PATH.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest = load_manifest()

    logger.info("=" * 70)
    logger.info("Telegram Archive Downloader")
    logger.info("=" * 70)

    logger.info("Channel ID : %s", CHANNEL_ID)
    logger.info("Archive    : %s", BASE_PATH.resolve())
    logger.info("Max retry  : %d", MAX_RETRIES)
    logger.info("Only match : %s", ONLY_MATCHED_FILES)

    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
        timeout=REQUEST_TIMEOUT,
        connection_retries=5,
        retry_delay=5,
        auto_reconnect=True,
    )

    try:

        logger.info("Connecting to Telegram...")

        await client.connect()

        if not await client.is_user_authorized():
            logger.error(
                "CRITICAL: Telegram StringSession is invalid or expired."
            )
            return

        logger.info("Telegram authorization successful.")

        # Verify target entity before processing the entire channel.
        try:
            entity = await client.get_entity(CHANNEL_ID)

            logger.info(
                "Target resolved: %s",
                getattr(entity, "title", CHANNEL_ID),
            )

        except Exception as exc:
            logger.error(
                "Could not resolve Telegram channel %s: %s",
                CHANNEL_ID,
                exc,
            )
            return

        processed = 0
        downloaded = 0
        skipped = 0
        failed = 0
        unmatched = 0

        logger.info("Scanning messages...")

        async for message in client.iter_messages(entity):

            if shutdown_requested:
                logger.warning("Stopping message scan.")
                break

            if MAX_FILES > 0 and processed >= MAX_FILES:
                logger.info(
                    "MAX_FILES=%d reached.",
                    MAX_FILES,
                )
                break

            is_video, is_pdf = detect_file_type(message)

            # Only process videos and PDFs.
            if not (is_video or is_pdf):
                continue

            processed += 1

            folder_name, matched_keyword = classify_subject(message)

            if matched_keyword is None:
                unmatched += 1

                if ONLY_MATCHED_FILES:
                    logger.info(
                        "[%d] Skipping unmatched file: message %s",
                        processed,
                        message.id,
                    )
                    skipped += 1
                    continue

            target_directory = BASE_PATH / folder_name
            target_directory.mkdir(
                parents=True,
                exist_ok=True,
            )

            filename = build_filename(
                message,
                is_video,
                is_pdf,
            )

            destination = target_directory / filename

            # ----------------------------------------------------
            # Fast local existence check
            # ----------------------------------------------------

            if destination.exists():

                logger.info(
                    "[%d] SKIP existing: %s/%s",
                    processed,
                    folder_name,
                    filename,
                )

                skipped += 1

                manifest[str(message.id)] = {
                    "status": "downloaded",
                    "path": str(destination),
                    "folder": folder_name,
                    "filename": filename,
                    "type": "video" if is_video else "pdf",
                    "updated_at": int(time.time()),
                }

                save_manifest(manifest)

                continue

            # ----------------------------------------------------
            # Manifest check
            # ----------------------------------------------------

            record = manifest.get(str(message.id))

            if record and record.get("status") == "downloaded":

                recorded_path = Path(
                    record.get("path", "")
                )

                if recorded_path.exists():

                    logger.info(
                        "[%d] SKIP manifest: message %s",
                        processed,
                        message.id,
                    )

                    skipped += 1
                    continue

            file_type = "Video" if is_video else "PDF"

            logger.info(
                "[%d] [%s] [%s] %s",
                processed,
                folder_name,
                file_type,
                filename,
            )

            if matched_keyword:
                logger.info(
                    "Matched subject: %s",
                    matched_keyword,
                )

            success = await download_file(
                client,
                message,
                destination,
            )

            if success:

                downloaded += 1

                manifest[str(message.id)] = {
                    "status": "downloaded",
                    "path": str(destination),
                    "folder": folder_name,
                    "filename": filename,
                    "type": "video" if is_video else "pdf",
                    "message_id": message.id,
                    "updated_at": int(time.time()),
                }

                save_manifest(manifest)

                await asyncio.sleep(DOWNLOAD_DELAY)

            else:

                failed += 1

                manifest[str(message.id)] = {
                    "status": "failed",
                    "path": str(destination),
                    "folder": folder_name,
                    "filename": filename,
                    "type": "video" if is_video else "pdf",
                    "message_id": message.id,
                    "updated_at": int(time.time()),
                }

                save_manifest(manifest)

        # ========================================================
        # FINAL REPORT
        # ========================================================

        logger.info("")
        logger.info("=" * 70)
        logger.info("DOWNLOAD JOB COMPLETED")
        logger.info("=" * 70)

        logger.info("Files processed : %d", processed)
        logger.info("Downloaded      : %d", downloaded)
        logger.info("Skipped         : %d", skipped)
        logger.info("Failed          : %d", failed)
        logger.info("Unmatched       : %d", unmatched)

        logger.info("Archive path    : %s", BASE_PATH.resolve())

        logger.info("=" * 70)

    finally:

        if client.is_connected():
            logger.info("Disconnecting from Telegram...")
            await client.disconnect()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    # Graceful shutdown for GitHub Actions / Ctrl+C
    signal.signal(
        signal.SIGINT,
        request_shutdown,
    )

    signal.signal(
        signal.SIGTERM,
        request_shutdown,
    )

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.warning("Interrupted by user.")

    except Exception as exc:
        logger.exception(
            "Fatal application error: %s",
            exc,
        )
        raise
