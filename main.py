import asyncio
import json
import logging
import mimetypes
import os
import re
import subprocess
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.sessions import StringSession


# ============================================================
# CONFIGURATION
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TELEGRAM_SESSION = os.environ["TELEGRAM_SESSION"]

CHANNEL_ID = -1003708183148

ARCHIVE_ROOT = Path("./downloads/Telegram_Archive/GK-GS")
RCLONE_REMOTE_ROOT = os.environ.get(
    "RCLONE_REMOTE_ROOT",
    "gdrive:Telegram_Archive/GK-GS",
)

# Telegram download retry settings.
MAX_RETRIES = 6
RETRY_DELAY = 5
MAX_RETRY_DELAY = 60

# Google Drive upload retry settings.
UPLOAD_RETRIES = 4
UPLOAD_RETRY_DELAY = 5

# Keep at 0 for unlimited messages.
MAX_FILES = 0

# Upload each file immediately after its Telegram download completes.
IMMEDIATE_UPLOAD = True

# Only matched subjects are downloaded.
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
    "Medieval_History": [
        "medieval history",
        "medieval_history",
        "medievalhistory",
        "middle age",
        "middle ages",
        "मध्यकालीन इतिहास",
        "मध्यकाल इतिहास",
    ],
    "Modern_History": [
        "modern history",
        "modern_history",
        "modernhistory",
        "आधुनिक इतिहास",
    ],
    "Physics": [
        "physics",
        "physics lecture",
        "physics class",
        "phy",
        "भौतिक विज्ञान",
        "भौतिकी",
    ],
    "Chemistry": [
        "chemistry",
        "chemistry lecture",
        "chemistry class",
        "chem",
        "रसायन विज्ञान",
        "रसायन",
    ],
    "Biology": [
        "biology",
        "biology lecture",
        "biology class",
        "bio",
        "biol",
        "zoology",
        "botany",
        "cell biology",
        "genetics",
        "heredity",
        "evolution",
        "ecology",
        "human biology",
        "human physiology",
        "plant physiology",
        "anatomy",
        "physiology",
        "reproduction",
        "human reproduction",
        "microbiology",
        "biotechnology",
        "biodiversity",
        "photosynthesis",
        "respiration",
        "digestion",
        "blood circulation",
        "nervous system",
        "endocrine system",
        "hormone",
        "जीव विज्ञान",
        "जीवविज्ञान",
        "जीव शास्त्र",
        "जीवशास्त्र",
        "वनस्पति विज्ञान",
        "वनस्पति",
        "प्राणी विज्ञान",
        "जंतु विज्ञान",
        "जंतु",
        "आनुवंशिकी",
        "कोशिका",
        "पारिस्थितिकी",
        "प्रजनन",
        "पाचन",
        "रक्त",
        "उत्सर्जन",
    ],
    "Static_GK": [
        "static gk",
        "static_gk",
        "staticgk",
        "static general knowledge",
        "static general awareness",
        "static ga",
        "स्टेटिक जीके",
        "स्टैटिक जीके",
        "स्थैतिक सामान्य ज्ञान",
    ],
    "Computer": [
        "computer",
        "computers",
        "computer awareness",
        "computer knowledge",
        "कंप्यूटर",
    ],
}


# ============================================================
# MANIFEST
# ============================================================
# Local state only. Google Drive is intentionally NOT queried.

MANIFEST_PATH = ARCHIVE_ROOT / ".download_manifest.json"


def load_manifest():
    if not MANIFEST_PATH.exists():
        return {}

    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.warning("Could not load manifest: %s", exc)
        return {}


def save_manifest(manifest):
    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    temp_path = MANIFEST_PATH.with_name(MANIFEST_PATH.name + ".tmp")

    try:
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(manifest, file, ensure_ascii=False, indent=2)
        temp_path.replace(MANIFEST_PATH)
    except Exception as exc:
        logger.warning("Could not save manifest: %s", exc)
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


# ============================================================
# TEXT UTILITIES
# ============================================================

def normalize_text(text):
    if not text:
        return ""

    text = str(text).lower()
    text = text.replace("_", " ")
    text = text.replace("-", " ")
    text = text.replace("–", " ")
    text = text.replace("—", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sanitize_filename(filename):
    if not filename:
        filename = "telegram_file"

    filename = str(filename)
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    filename = re.sub(r"\s+", " ", filename).strip()
    filename = filename.rstrip(". ")

    return filename or "telegram_file"


def phrase_matches(text, phrase):
    """Match English aliases as words and Hindi aliases as Unicode text."""
    text = normalize_text(text)
    phrase = normalize_text(phrase)

    if not text or not phrase:
        return False

    # Hindi/Devanagari: substring matching is appropriate.
    if re.search(r"[\u0900-\u097F]", phrase):
        return phrase in text

    # English: avoid matching 'chem' inside unrelated words.
    pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def get_message_text(message):
    parts = []

    message_text = getattr(message, "message", None)
    if message_text:
        parts.append(str(message_text))

    raw_text = getattr(message, "raw_text", None)
    if raw_text and str(raw_text) not in parts:
        parts.append(str(raw_text))

    file_obj = getattr(message, "file", None)
    if file_obj:
        name = getattr(file_obj, "name", None)
        if name:
            parts.append(str(name))

    return " ".join(parts)


# ============================================================
# SUBJECT CLASSIFICATION
# ============================================================

def classify_subject(message):
    """
    Classify using both Telegram caption/message text and filename.

    Returns:
        (subject, confidence, reasons)
    or:
        (None, 0, [])
    """
    text = normalize_text(get_message_text(message))
    if not text:
        return None, 0, []

    scores = {}
    reasons = {}

    for subject, aliases in SUBJECT_MAP.items():
        score = 0
        subject_reasons = []

        for alias in aliases:
            alias_normalized = normalize_text(alias)
            if not alias_normalized:
                continue

            if not phrase_matches(text, alias_normalized):
                continue

            # Longer/more specific phrases get more weight.
            words = alias_normalized.split()
            if len(words) >= 3:
                points = 10
            elif len(words) == 2:
                points = 7
            else:
                points = 5

            # A filename hit is already part of text, so don't double-count
            # it here. The full caption + filename combination is considered
            # together to avoid brittle filename-only matching.
            score += points
            subject_reasons.append(alias)

        if score:
            scores[subject] = score
            reasons[subject] = subject_reasons

    if not scores:
        return None, 0, []

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_subject, best_score = ranked[0]

    # Avoid silently classifying genuinely ambiguous messages.
    if len(ranked) > 1:
        second_score = ranked[1][1]
        if best_score == second_score:
            logger.warning(
                "[%s] Ambiguous subject: %s",
                getattr(message, "id", "?"),
                ranked,
            )
            return None, 0, []

    return best_subject, best_score, reasons.get(best_subject, [])


# ============================================================
# FILE TYPE
# ============================================================

def detect_file_type(message):
    file_obj = getattr(message, "file", None)
    if not file_obj:
        return None

    mime_type = (getattr(file_obj, "mime_type", None) or "").lower()
    name = getattr(file_obj, "name", None) or ""
    name_lower = name.lower()

    if mime_type == "application/pdf" or name_lower.endswith(".pdf"):
        return "PDF"

    video_extensions = (
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".m4v",
        ".3gp",
        ".mpeg",
        ".mpg",
        ".ts",
    )

    if (
        getattr(message, "video", None) is not None
        or mime_type.startswith("video/")
        or name_lower.endswith(video_extensions)
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
    """Keep the real extension at the end of the filename."""
    original = get_original_filename(message)
    path = Path(original)

    if path.suffix:
        stem = path.stem
        suffix = path.suffix
        return sanitize_filename(f"{stem}_msg_{message.id}{suffix}")

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
# TELEGRAM CONNECTION RECOVERY
# ============================================================

async def ensure_connected(client):
    """Reconnect when Telegram closed the network connection."""
    try:
        if client.is_connected():
            return True
    except Exception:
        pass

    logger.warning("Telegram connection is not active. Reconnecting...")

    try:
        await client.connect()
        if client.is_connected():
            logger.info("Telegram reconnected successfully.")
            return True
    except Exception as exc:
        logger.warning("Telegram reconnect failed: %s", exc)

    return False


async def recover_telegram_connection(client, delay):
    await asyncio.sleep(delay)

    for reconnect_attempt in range(1, 4):
        try:
            if await ensure_connected(client):
                return True
        except Exception as exc:
            logger.warning(
                "Reconnect attempt %d/3 failed: %s",
                reconnect_attempt,
                exc,
            )

        await asyncio.sleep(min(delay * reconnect_attempt, 15))

    return False


# ============================================================
# DOWNLOAD PROGRESS
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
                "Downloading %-55s %3d%% (%.1f/%.1f MB)",
                self.filename,
                percent,
                current / (1024 * 1024),
                total / (1024 * 1024),
            )


# ============================================================
# DOWNLOAD
# ============================================================

async def download_file(client, message, output_path):
    """
    Download a Telegram file safely.

    Important:
    - Downloads into .part first.
    - A final file appears only after a successful download.
    - Connection-close errors are retried.
    - Telegram is reconnected between retries.
    """
    filename = output_path.name
    temp_path = output_path.with_name(output_path.name + ".part")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if not await ensure_connected(client):
                raise ConnectionError("Telegram is not connected")

            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

            logger.info(
                "Download attempt %d/%d: %s",
                attempt,
                MAX_RETRIES,
                filename,
            )

            result = await client.download_media(
                message,
                file=str(temp_path),
                progress_callback=DownloadProgress(filename),
            )

            if not result:
                raise RuntimeError("Telethon returned no download path")

            if not temp_path.exists():
                raise RuntimeError("Download returned successfully but .part file is missing")

            size = temp_path.stat().st_size
            if size <= 0:
                raise RuntimeError("Downloaded file is empty")

            # Atomic finalization. The uploader never sees a partial file.
            temp_path.replace(output_path)

            logger.info(
                "DOWNLOAD SUCCESS: %s (%.2f MB)",
                filename,
                size / (1024 * 1024),
            )
            return True

        except FloodWaitError as exc:
            wait_time = int(exc.seconds) + 2
            logger.warning(
                "Telegram FloodWait: waiting %d seconds",
                wait_time,
            )
            await asyncio.sleep(wait_time)

        except (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError, RPCError) as exc:
            error_text = str(exc) or exc.__class__.__name__

            logger.warning(
                "Telegram download error (attempt %d/%d): %s",
                attempt,
                MAX_RETRIES,
                error_text,
            )

            if attempt < MAX_RETRIES:
                delay = min(RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                await recover_telegram_connection(client, delay)

        except Exception as exc:
            error_text = str(exc) or exc.__class__.__name__

            # Telethon/network layers can surface a connection close as a
            # generic exception. Explicitly recognize the observed error.
            connection_error = any(
                phrase in error_text.lower()
                for phrase in (
                    "server closed the connection",
                    "0 bytes read",
                    "connection reset",
                    "connection aborted",
                    "connection closed",
                    "broken pipe",
                    "incomplete read",
                )
            )

            if connection_error:
                logger.warning(
                    "Telegram connection interruption (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    error_text,
                )

                if attempt < MAX_RETRIES:
                    delay = min(RETRY_DELAY * (2 ** (attempt - 1)), MAX_RETRY_DELAY)
                    await recover_telegram_connection(client, delay)
            else:
                logger.exception(
                    "Unexpected download error (attempt %d/%d): %s",
                    attempt,
                    MAX_RETRIES,
                    error_text,
                )

                if attempt < MAX_RETRIES:
                    await asyncio.sleep(RETRY_DELAY)

        finally:
            if temp_path.exists() and output_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    logger.error(
        "DOWNLOAD FAILED after %d attempts: %s",
        MAX_RETRIES,
        filename,
    )
    return False


# ============================================================
# GOOGLE DRIVE UPLOAD
# ============================================================

async def upload_to_drive(output_path, subject, file_type):
    """Upload one completed file immediately after Telegram download."""
    remote_destination = get_remote_destination(subject, file_type)

    if not remote_destination:
        logger.error(
            "No Google Drive destination for subject=%s type=%s",
            subject,
            file_type,
        )
        return False

    remote_path = f"{remote_destination}/{output_path.name}"

    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            logger.info(
                "DRIVE UPLOAD attempt %d/%d: %s -> %s",
                attempt,
                UPLOAD_RETRIES,
                output_path.name,
                remote_destination,
            )

            command = [
                "rclone",
                "copyto",
                str(output_path),
                remote_path,
                "--retries",
                "3",
                "--low-level-retries",
                "10",
                "--drive-chunk-size",
                "64M",
                "--stats",
                "30s",
            ]

            result = await asyncio.to_thread(
                subprocess.run,
                command,
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                logger.info(
                    "DRIVE UPLOAD SUCCESS: %s",
                    output_path.name,
                )
                return True

            error_output = (
                result.stderr.strip()
                or result.stdout.strip()
                or "rclone returned a non-zero exit code"
            )

            logger.warning(
                "Drive upload failed (attempt %d/%d): %s",
                attempt,
                UPLOAD_RETRIES,
                error_output,
            )

        except FileNotFoundError:
            logger.error("rclone command not found. Install/configure rclone first.")
            return False

        except Exception as exc:
            logger.warning(
                "Drive upload error (attempt %d/%d): %s",
                attempt,
                UPLOAD_RETRIES,
                exc,
            )

        if attempt < UPLOAD_RETRIES:
            delay = min(
                UPLOAD_RETRY_DELAY * (2 ** (attempt - 1)),
                60,
            )
            await asyncio.sleep(delay)

    logger.error(
        "DRIVE UPLOAD FAILED after %d attempts: %s",
        UPLOAD_RETRIES,
        output_path.name,
    )
    return False


# ============================================================
# PROCESS MESSAGE
# ============================================================

async def process_message(client, message, manifest):
    file_type = detect_file_type(message)

    if file_type is None:
        return "SKIP_OTHER"

    subject, confidence, reasons = classify_subject(message)

    if subject is None:
        original = get_original_filename(message)
        logger.info(
            "[%s] SKIP [NO/AMBIGUOUS SUBJECT] [%s] %s",
            message.id,
            file_type,
            original,
        )
        return "SKIP_SUBJECT"

    destination = get_destination(subject, file_type)
    if destination is None:
        return "SKIP_OTHER"

    destination.mkdir(parents=True, exist_ok=True)

    filename = build_filename(message)
    output_path = destination / filename
    message_key = str(message.id)

    logger.info(
        "[%s] %s | %s | confidence=%d | matched=%s | %s",
        message.id,
        subject,
        file_type,
        confidence,
        ", ".join(reasons[:5]),
        filename,
    )

    # Manifest means this Telegram message was already handled locally.
    if message_key in manifest:
        entry = manifest[message_key]
        status = entry.get("status", "unknown") if isinstance(entry, dict) else "unknown"

        logger.info(
            "[%s] SKIP [LOCAL MANIFEST] status=%s %s",
            message.id,
            status,
            filename,
        )
        return "SKIP_MANIFEST"

    # Local file check avoids downloading an already-complete file.
    if output_path.exists():
        try:
            size = output_path.stat().st_size
        except OSError:
            size = 0

        if size > 0:
            logger.info(
                "[%s] SKIP [LOCAL FILE EXISTS] %s",
                message.id,
                filename,
            )

            manifest[message_key] = {
                "filename": filename,
                "subject": subject,
                "file_type": file_type,
                "confidence": confidence,
                "status": "local_file_exists",
            }
            save_manifest(manifest)
            return "SKIP_LOCAL"

    # Remove stale empty final files.
    if output_path.exists():
        try:
            output_path.unlink()
        except OSError:
            pass

    # --------------------------------------------------------
    # 1. Telegram download
    # --------------------------------------------------------
    downloaded = await download_file(client, message, output_path)

    if not downloaded:
        return "FAILED"

    # --------------------------------------------------------
    # 2. Immediate Google Drive upload
    # --------------------------------------------------------
    if IMMEDIATE_UPLOAD:
        uploaded = await upload_to_drive(
            output_path,
            subject,
            file_type,
        )

        if not uploaded:
            # Keep the local file. This is intentional: the next run can
            # retry the upload without downloading the Telegram file again.
            manifest[message_key] = {
                "filename": filename,
                "subject": subject,
                "file_type": file_type,
                "confidence": confidence,
                "status": "downloaded_upload_failed",
            }
            save_manifest(manifest)
            return "FAILED_UPLOAD"

    # --------------------------------------------------------
    # 3. Record success only after upload succeeds
    # --------------------------------------------------------
    manifest[message_key] = {
        "filename": filename,
        "subject": subject,
        "file_type": file_type,
        "confidence": confidence,
        "status": "downloaded_and_uploaded",
    }
    save_manifest(manifest)

    return "DOWNLOADED_UPLOADED"


# ============================================================
# MAIN
# ============================================================

async def main():
    logger.info("=" * 72)
    logger.info("Telegram -> Google Drive Archive Downloader")
    logger.info("=" * 72)
    logger.info("Channel ID       : %s", CHANNEL_ID)
    logger.info("Local archive    : %s", ARCHIVE_ROOT.resolve())
    logger.info("Google Drive root: %s", RCLONE_REMOTE_ROOT)
    logger.info("Telegram retries : %d", MAX_RETRIES)
    logger.info("Upload retries   : %d", UPLOAD_RETRIES)
    logger.info("Only matched     : %s", ONLY_MATCHED_FILES)
    logger.info("Immediate upload : %s", IMMEDIATE_UPLOAD)
    logger.info("Subjects         : %s", ", ".join(SUBJECT_MAP.keys()))
    logger.info("Google Drive scan: DISABLED")

    ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()

    client = TelegramClient(
        StringSession(TELEGRAM_SESSION),
        API_ID,
        API_HASH,
        connection_retries=10,
        retry_delay=5,
        auto_reconnect=True,
    )

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

    try:
        logger.info("Connecting to Telegram...")
        await client.start()
        logger.info("Telegram authorization successful.")

        entity = await client.get_entity(CHANNEL_ID)
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(entity)
        logger.info("Target resolved: %s", title)
        logger.info("Scanning messages from oldest to newest...")

        processed = 0

        async for message in client.iter_messages(entity, reverse=True):
            counters["scanned"] += 1

            if MAX_FILES > 0 and processed >= MAX_FILES:
                logger.info("MAX_FILES reached: %d", MAX_FILES)
                break

            try:
                result = await process_message(
                    client,
                    message,
                    manifest,
                )

                if result in counters:
                    counters[result] += 1

                if result in (
                    "DOWNLOADED_UPLOADED",
                    "FAILED",
                    "FAILED_UPLOAD",
                ):
                    processed += 1

            except FloodWaitError as exc:
                wait_time = int(exc.seconds) + 2
                logger.warning(
                    "FloodWait while processing message %s: %d seconds",
                    message.id,
                    wait_time,
                )
                await asyncio.sleep(wait_time)

            except Exception as exc:
                counters["failed"] += 1
                logger.exception(
                    "Unhandled error while processing message %s: %s",
                    message.id,
                    exc,
                )

        logger.info("=" * 72)
        logger.info("SCAN COMPLETE")
        logger.info("Scanned              : %d", counters["scanned"])
        logger.info("Downloaded + uploaded: %d", counters["downloaded_uploaded"])
        logger.info("Skipped subject      : %d", counters["skipped_subject"])
        logger.info("Skipped manifest     : %d", counters["skipped_manifest"])
        logger.info("Skipped local        : %d", counters["skipped_local"])
        logger.info("Skipped other        : %d", counters["skipped_other"])
        logger.info("Download failures    : %d", counters["failed"])
        logger.info("Upload failures      : %d", counters["failed_upload"])
        logger.info("=" * 72)

    except FloodWaitError as exc:
        wait_time = int(exc.seconds) + 2
        logger.error(
            "Telegram requested a FloodWait of %d seconds during startup/scan.",
            wait_time,
        )
        raise

    except Exception as exc:
        logger.exception("Fatal Telegram archive error: %s", exc)
        raise

    finally:
        try:
            await client.disconnect()
            logger.info("Telegram connection closed.")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
