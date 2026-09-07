import asyncio
import json
import logging
import os
import re
import signal
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    RPCError,
    ServerError,
    TimedOutError,
)


# ============================================================
# CONFIGURATION
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TELEGRAM_SESSION = os.environ["TELEGRAM_SESSION"]

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003708183148"))

ARCHIVE_ROOT = Path(
    os.getenv(
        "ARCHIVE_ROOT",
        "./downloads/Telegram_Archive/GK-GS",
    )
)

MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
DOWNLOAD_DELAY = float(os.getenv("DOWNLOAD_DELAY", "1"))
RETRY_DELAY = float(os.getenv("RETRY_DELAY", "5"))

# 0 = unlimited
MAX_FILES = int(os.getenv("MAX_FILES", "0"))

# If true, only files matching the subject list are downloaded.
ONLY_MATCHED_FILES = True

# Manifest used to avoid downloading the same Telegram message again.
MANIFEST_FILE = ARCHIVE_ROOT / ".download_manifest.json"


# ============================================================
# ALLOWED SUBJECTS
# ============================================================

SUBJECT_MAP = {
    

    "MEDIEVAL HISTORY": "Medieval_History",
    "MEDIEVAL": "Medieval_History",

    "MODERN HISTORY": "Modern_History",
    "MODERN": "Modern_History",



    
    "PHYSICS": "Physics",

    "CHEMISTRY": "Chemistry",

    "BIOLOGY": "Biology",

    "STATIC GK": "Static_GK",
    "STATIC GENERAL KNOWLEDGE": "Static_GK",

    "CURRENT AFFAIRS": "Current_Affairs",
    "CURRENT AFFAIR": "Current_Affairs",

    "GENERAL SCIENCE": "General_Science",
    "GENERAL SCIENCE": "General_Science",

    "ENVIRONMENT": "Environment",
    "ENVIRONMENT & ECOLOGY": "Environment",
    "ENVIRONMENT AND ECOLOGY": "Environment",

    "ART AND CULTURE": "Art_and_Culture",
    "ART & CULTURE": "Art_and_Culture",
    "ART CULTURE": "Art_and_Culture",

    "COMPUTER": "Computer",
    "COMPUTER SCIENCE": "Computer",
    "COMPUTER AWARENESS": "Computer",
}


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("telegram_downloader")


# ============================================================
# GLOBAL STATE
# ============================================================

shutdown_requested = False


# ============================================================
# SIGNAL HANDLING
# ============================================================

def handle_shutdown(signum, frame):
    global shutdown_requested

    shutdown_requested = True

    logger.warning(
        "Shutdown requested. Current operation will finish safely..."
    )


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


# ============================================================
# MANIFEST
# ============================================================

def load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        return {}

    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        return data

    except Exception as exc:
        logger.warning(
            "Could not read manifest: %s",
            exc,
        )

        return {}


def save_manifest(manifest: dict):
    ARCHIVE_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp_file = MANIFEST_FILE.with_suffix(".tmp")

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            manifest,
            f,
            indent=2,
            ensure_ascii=False,
        )

    temp_file.replace(MANIFEST_FILE)


# ============================================================
# TEXT / FILENAME HELPERS
# ============================================================

def sanitize_filename(filename: str) -> str:
    """
    Make Telegram filenames safe for Linux/Windows/Drive.
    """

    filename = filename.strip()

    filename = re.sub(
        r'[<>:"/\\|?*\x00-\x1F]',
        "_",
        filename,
    )

    filename = re.sub(
        r"\s+",
        " ",
        filename,
    )

    filename = filename.strip(" .")

    if not filename:
        filename = "telegram_file"

    # Avoid extremely long filesystem names.
    if len(filename) > 180:
        suffix = Path(filename).suffix

        filename = (
            filename[:180 - len(suffix)]
            + suffix
        )

    return filename


def normalize_text(text: str) -> str:
    """
    Normalize separators so that:

    COMPUTER
    COMPUTER_
    COMPUTER-
    COMPUTER / SCIENCE

    can be classified more reliably.
    """

    text = text.upper()

    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = text.replace("/", " ")
    text = text.replace("\\", " ")

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def get_message_text(message) -> str:
    parts = []

    if getattr(message, "message", None):
        parts.append(message.message)

    if getattr(message, "raw_text", None):
        parts.append(message.raw_text)

    if getattr(message, "file", None):
        if getattr(message.file, "name", None):
            parts.append(message.file.name)

    return " ".join(parts)


# ============================================================
# SUBJECT CLASSIFICATION
# ============================================================

def classify_subject(message) -> Optional[str]:
    """
    Return the allowed subject folder.

    Return None when no allowed subject is detected.

    IMPORTANT:
    None means the file must NOT be downloaded.
    """

    text = normalize_text(
        get_message_text(message)
    )

    if not text:
        return None

    # Longer / more specific phrases first.
    subjects = sorted(
        SUBJECT_MAP.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    for keyword, folder in subjects:

        keyword_normalized = normalize_text(
            keyword
        )

        if keyword_normalized in text:
            return folder

    return None


# ============================================================
# FILE TYPE DETECTION
# ============================================================

def detect_file_type(message) -> Optional[str]:
    """
    Returns:

    PDF
    VIDEO
    None
    """

    if getattr(message, "video", None):
        return "VIDEO"

    document = getattr(message, "document", None)

    if document:
        mime_type = (
            getattr(
                document,
                "mime_type",
                "",
            )
            or ""
        ).lower()

        if mime_type == "application/pdf":
            return "PDF"

        # Some Telegram files may have an incorrect MIME type.
        filename = ""

        if getattr(message, "file", None):
            filename = (
                getattr(
                    message.file,
                    "name",
                    "",
                )
                or ""
            )

        if filename.lower().endswith(".pdf"):
            return "PDF"

        if mime_type.startswith("video/"):
            return "VIDEO"

        extension = Path(filename).suffix.lower()

        if extension in {
            ".mp4",
            ".mkv",
            ".avi",
            ".mov",
            ".webm",
            ".m4v",
        }:
            return "VIDEO"

    return None


# ============================================================
# FILE EXTENSION
# ============================================================

def get_extension(message, file_type: str) -> str:

    if getattr(message, "file", None):
        filename = (
            getattr(
                message.file,
                "name",
                "",
            )
            or ""
        )

        extension = Path(filename).suffix

        if extension:
            return extension.lower()

    if file_type == "PDF":
        return ".pdf"

    if file_type == "VIDEO":
        return ".mp4"

    return ""


# ============================================================
# FILENAME
# ============================================================

def build_filename(
    message,
    file_type: str,
) -> str:

    original_name = ""

    if getattr(message, "file", None):
        original_name = (
            getattr(
                message.file,
                "name",
                "",
            )
            or ""
        )

    if not original_name:

        if file_type == "PDF":
            original_name = "document.pdf"

        elif file_type == "VIDEO":
            original_name = "video.mp4"

        else:
            original_name = "telegram_file"

    original_name = sanitize_filename(
        original_name
    )

    extension = get_extension(
        message,
        file_type,
    )

    # Make sure extension exists.
    if not Path(original_name).suffix:
        original_name += extension

    message_id = getattr(
        message,
        "id",
        "unknown",
    )

    stem = Path(original_name).stem
    suffix = Path(original_name).suffix

    filename = (
        f"{stem}_msg_{message_id}{suffix}"
    )

    return sanitize_filename(filename)


# ============================================================
# DESTINATION
# ============================================================

def get_destination(
    subject: str,
    file_type: str,
) -> Path:

    if file_type == "VIDEO":
        type_folder = "Videos"

    else:
        type_folder = "PDFs"

    destination = (
        ARCHIVE_ROOT
        / subject
        / type_folder
    )

    destination.mkdir(
        parents=True,
        exist_ok=True,
    )

    return destination


# ============================================================
# PROGRESS CALLBACK
# ============================================================

class DownloadProgress:

    def __init__(self, filename: str):
        self.filename = filename
        self.last_percent = -1

    def __call__(
        self,
        current: int,
        total: int,
    ):

        if total <= 0:
            return

        percent = int(
            current * 100 / total
        )

        # Log every 10%.
        bucket = (
            percent // 10
        ) * 10

        if bucket > self.last_percent:

            self.last_percent = bucket

            current_mb = current / (
                1024 * 1024
            )

            total_mb = total / (
                1024 * 1024
            )

            logger.info(
                "Downloading %-55s %3d%% "
                "(%.1f/%.1f MB)",
                self.filename[:55],
                percent,
                current_mb,
                total_mb,
            )


# ============================================================
# PARTIAL FILE CLEANUP
# ============================================================

def remove_partial_file(path: Path):

    try:
        if path.exists():
            path.unlink()

            logger.warning(
                "Removed partial file: %s",
                path.name,
            )

    except Exception as exc:

        logger.warning(
            "Could not remove partial file %s: %s",
            path,
            exc,
        )


# ============================================================
# DOWNLOAD
# ============================================================

async def download_file(
    client: TelegramClient,
    message,
    destination: Path,
    filename: str,
) -> bool:

    output_path = destination / filename

    # Already downloaded.
    if output_path.exists():

        logger.info(
            "ALREADY EXISTS: %s",
            filename,
        )

        return True

    progress = DownloadProgress(
        filename
    )

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        if shutdown_requested:
            return False

        logger.info(
            "Download attempt %d/%d: %s",
            attempt,
            MAX_RETRIES,
            filename,
        )

        try:

            # IMPORTANT:
            # Do NOT use request_size here.
            #
            # Telethon's high-level download_media()
            # does not accept request_size in the installed
            # version.

            downloaded_path = (
                await client.download_media(
                    message,
                    file=str(output_path),
                    progress_callback=progress,
                )
            )

            if downloaded_path:

                actual_path = Path(
                    downloaded_path
                )

                if actual_path.exists():

                    size_mb = (
                        actual_path.stat().st_size
                        / (1024 * 1024)
                    )

                    logger.info(
                        "SUCCESS: %s (%.2f MB)",
                        filename,
                        size_mb,
                    )

                    return True

            logger.warning(
                "Download returned no file: %s",
                filename,
            )

        # ----------------------------------------------------
        # FLOOD WAIT
        # ----------------------------------------------------

        except FloodWaitError as exc:

            wait_seconds = (
                int(exc.seconds) + 2
            )

            logger.warning(
                "Telegram FloodWait: "
                "waiting %d seconds",
                wait_seconds,
            )

            await asyncio.sleep(
                wait_seconds
            )

            continue

        # ----------------------------------------------------
        # TELETHON TIMEOUT
        # ----------------------------------------------------

        except (
            TimedOutError,
            asyncio.TimeoutError,
        ) as exc:

            logger.warning(
                "Timeout downloading %s: %s",
                filename,
                exc,
            )

        # ----------------------------------------------------
        # CONNECTION / SERVER ERRORS
        # ----------------------------------------------------

        except (
            ServerError,
            ConnectionError,
            OSError,
        ) as exc:

            logger.warning(
                "Temporary connection error "
                "downloading %s: %s",
                filename,
                exc,
            )

        # ----------------------------------------------------
        # RPC ERRORS
        # ----------------------------------------------------

        except RPCError as exc:

            logger.warning(
                "Telegram RPC error "
                "downloading %s: %s",
                filename,
                exc,
            )

        # ----------------------------------------------------
        # API / PROGRAMMING ERROR
        # ----------------------------------------------------

        except TypeError as exc:

            logger.error(
                "Non-retryable API error "
                "downloading %s: %s",
                filename,
                exc,
            )

            return False

        # ----------------------------------------------------
        # UNEXPECTED ERROR
        # ----------------------------------------------------

        except Exception as exc:

            logger.exception(
                "Unexpected error downloading %s: %s",
                filename,
                exc,
            )

            # Do not endlessly retry programming errors.
            return False

        # ----------------------------------------------------
        # CLEAN PARTIAL DOWNLOAD
        # ----------------------------------------------------

        if attempt < MAX_RETRIES:

            remove_partial_file(
                output_path
            )

            wait = (
                RETRY_DELAY * attempt
            )

            logger.info(
                "Retrying in %.1f seconds...",
                wait,
            )

            await asyncio.sleep(
                wait,
            )

    logger.error(
        "FAILED after %d attempts: %s",
        MAX_RETRIES,
        filename,
    )

    remove_partial_file(
        output_path
    )

    return False


# ============================================================
# PROCESS MESSAGE
# ============================================================

async def process_message(
    client: TelegramClient,
    message,
    manifest: dict,
) -> str:

    message_id = str(
        getattr(message, "id", "")
    )

    # --------------------------------------------------------
    # Detect file type
    # --------------------------------------------------------

    file_type = detect_file_type(
        message
    )

    if file_type is None:

        logger.info(
            "SKIP [message %s]: "
            "Not a PDF or video",
            message_id,
        )

        return "skipped"

    # --------------------------------------------------------
    # Classify SUBJECT BEFORE DOWNLOAD
    # --------------------------------------------------------

    subject = classify_subject(
        message
    )

    # This is the critical rule.
    # No matching subject = NEVER DOWNLOAD.
    if subject is None:

        filename = ""

        if getattr(message, "file", None):
            filename = (
                getattr(
                    message.file,
                    "name",
                    "",
                )
                or ""
            )

        if not filename:
            filename = (
                f"message_{message_id}"
            )

        logger.info(
            "SKIP [NO ALLOWED SUBJECT] "
            "[%s] %s",
            file_type,
            filename,
        )

        return "skipped"

    # --------------------------------------------------------
    # Build destination
    # --------------------------------------------------------

    destination = get_destination(
        subject,
        file_type,
    )

    filename = build_filename(
        message,
        file_type,
    )

    output_path = (
        destination / filename
    )

    # --------------------------------------------------------
    # Manifest check
    # --------------------------------------------------------

    manifest_key = (
        f"{CHANNEL_ID}:{message_id}"
    )

    existing_record = manifest.get(
        manifest_key
    )

    if existing_record:

        recorded_path = Path(
            existing_record.get(
                "path",
                "",
            )
        )

        if recorded_path.exists():

            logger.info(
                "MANIFEST SKIP: %s",
                filename,
            )

            return "already_done"

    # --------------------------------------------------------
    # Existing physical file
    # --------------------------------------------------------

    if output_path.exists():

        logger.info(
            "FILE EXISTS: %s",
            output_path,
        )

        manifest[
            manifest_key
        ] = {
            "message_id": int(message_id),
         
