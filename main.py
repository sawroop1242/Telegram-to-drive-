from telethon import TelegramClient
from telethon.sessions import StringSession
import os
import asyncio
import subprocess
import re

# --- Configuration (Pulled from GitHub Secrets) ---
api_id = int(os.environ['API_ID'])
api_hash = os.environ['API_HASH']
session_string = os.environ['TELEGRAM_SESSION']

# Target channel ID
channel_id = -1003708183148

# Local staging directory on the GitHub Action VM runner
save_path = './downloads/Telegram_Archive/GK-GS/parmar_ssc/'
os.makedirs(save_path, exist_ok=True)

# Google Drive rclone remote/path
remote_drive_name = "gdrive1"
remote_drive_path = f"{remote_drive_name}:Telegram_Archive/GK-GS/parmar_ssc/"

# -----------------------------------------------------------------------------
# SUBJECT FILTER
# Only Physics, Chemistry, Biology and Static GK material is downloaded.
# The filter checks the Telegram filename + caption/message text.
# -----------------------------------------------------------------------------

SUBJECT_KEYWORDS = {
    "physics": [
        "physics", "phys", "भौतिक", "भौतिकी", "गति", "motion", "force", "बल",
        "work", "कार्य", "energy", "ऊर्जा", "power", "शक्ति", "gravitation",
        "gravity", "गुरुत्व", "heat", "ऊष्मा", "temperature", "तापमान", "wave",
        "तरंग", "sound", "ध्वनि", "light", "प्रकाश", "optics", "optics", "lens",
        "दर्पण", "mirror", "electricity", "विद्युत", "current", "धारा", "magnet",
        "चुंबक", "magnetic", "electromagnetism", "resistance", "प्रतिरोध", "voltage",
        "वोल्टेज", "semiconductor", "nuclear", "परमाणु", "radioactivity", "इलेक्ट्रॉन"
    ],
    "chemistry": [
        "chemistry", "chem", "रसायन", "रसायन विज्ञान", "atom", "परमाणु", "molecule",
        "अणु", "periodic", "आवर्त सारणी", "element", "तत्व", "compound", "यौगिक",
        "chemical", "रासायनिक", "acid", "अम्ल", "base", "क्षार", "salt", "लवण",
        "reaction", "अभिक्रिया", "oxidation", "अपचयन", "reduction", "redox", "carbon",
        "कार्बन", "organic", "कार्बनिक", "inorganic", "अकार्बनिक", "metallurgy",
        "धातु", "non metal", "अधातु", "catalyst", "उत्प्रेरक", "solution", "विलयन"
    ],
    "biology": [
        "biology", "bio", "जीव विज्ञान", "जीवविज्ञान", "cell", "कोशिका", "tissue",
        "ऊतक", "plant", "पादप", "वनस्पति", "animal", "प्राणी", "human body", "मानव शरीर",
        "human", "मानव", "blood", "रक्त", "heart", "हृदय", "brain", "मस्तिष्क", "digest",
        "पाचन", "respiration", "श्वसन", "reproduction", "प्रजनन", "genetics", "आनुवंशिकी",
        "dna", "rna", "chromosome", "गुणसूत्र", "hormone", "हार्मोन", "enzyme", "एंजाइम",
        "disease", "रोग", "vitamin", "विटामिन", "nutrition", "पोषण", "ecology", "पारिस्थितिकी",
        "evolution", "विकासवाद", "botany", "zoology", "microbiology", "bacteria", "virus"
    ],
    "static_gk": [
        "static gk", "static-gk", "staticgk", "static general knowledge", "स्थिर सामान्य ज्ञान",
        "general knowledge", "general knowledge", "सामान्य ज्ञान", "gk", "जीके",
        "indian polity", "polity", "राजव्यवस्था", "संविधान", "constitution", "fundamental rights",
        "मौलिक अधिकार", "parliament", "संसद", "president", "राष्ट्रपति", "prime minister",
        "प्रधानमंत्री", "supreme court", "सर्वोच्च न्यायालय", "history", "इतिहास", "ancient history",
        "प्राचीन इतिहास", "medieval history", "मध्यकालीन इतिहास", "modern history", "आधुनिक इतिहास",
        "geography", "भूगोल", "river", "नदी", "dam", "बांध", "lake", "झील", "mountain",
        "पर्वत", "plateau", "पठार", "desert", "मरुस्थल", "national park", "राष्ट्रीय उद्यान",
        "wildlife sanctuary", "अभयारण्य", "biosphere reserve", "biosphere", "राज्य", "capital",
        "राजधानी", "currency", "मुद्रा", "country", "देश", "world", "विश्व", "continent",
        "महाद्वीप", "economics", "अर्थशास्त्र", "banking", "बैंकिंग", "rbi", "भारतीय रिजर्व बैंक",
        "award", "पुरस्कार", "book", "पुस्तक", "author", "लेखक", "important days", "महत्वपूर्ण दिवस",
        "national symbols", "राष्ट्रीय प्रतीक", "first in india", "भारत में प्रथम", "first in world",
        "भारत में प्रथम", "world records", "sport", "खेल", "olympics", "olympic", "static facts"
    ]
}

# Strong subject labels are checked first so words such as "atom" do not
# accidentally classify an unrelated GK message as Chemistry.
STRONG_SUBJECT_LABELS = [
    "physics", "physics class", "chemistry", "chemistry class", "biology", "biology class",
    "भौतिक विज्ञान", "भौतिकी", "रसायन विज्ञान", "जीव विज्ञान", "जीवविज्ञान",
    "static gk", "static-gk", "staticgk", "static general knowledge", "static facts",
    "स्थिर सामान्य ज्ञान"
]


def normalize_text(text):
    """Normalize Telegram titles/captions for reliable keyword matching."""
    if not text:
        return ""
    text = text.lower().replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def classify_message(message):
    """Return Physics/Chemistry/Biology/Static GK or None."""
    parts = []

    if message.file and message.file.name:
        parts.append(message.file.name)

    if message.raw_text:
        parts.append(message.raw_text)

    text = normalize_text(" ".join(parts))
    if not text:
        return None

    # Explicit subject labels get priority.
    for label in STRONG_SUBJECT_LABELS:
        if label in text:
            label_lower = label.lower()
            if "physics" in label_lower or "भौतिक" in label_lower:
                return "Physics"
            if "chemistry" in label_lower or "रसायन" in label_lower:
                return "Chemistry"
            if "biology" in label_lower or "जीव" in label_lower:
                return "Biology"
            return "Static GK"

    # Otherwise score keyword matches. Require at least one meaningful match.
    scores = {}
    for subject, keywords in SUBJECT_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            keyword = normalize_text(keyword)
            if keyword and keyword in text:
                score += 1
        scores[subject] = score

    best_subject, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score == 0:
        return None

    return {
        "physics": "Physics",
        "chemistry": "Chemistry",
        "biology": "Biology",
        "static_gk": "Static GK"
    }[best_subject]


def get_already_downloaded_files():
    """Query Google Drive through rclone and return existing filenames."""
    print(f"Scanning Google Drive remote folder: [{remote_drive_path}]")
    existing_files = set()

    try:
        result = subprocess.run(
            ['rclone', 'lsf', remote_drive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            print(f"⚠️ Rclone listing failed: {result.stderr.strip()}")
            return existing_files

        for line in result.stdout.splitlines():
            if line.strip():
                existing_files.add(line.strip())

        print(f"Index successfully populated: {len(existing_files)} files tracked on Google Drive.")
    except Exception as e:
        print(f"⚠️ Unexpected Rclone error: {e}")

    return existing_files


async def main():
    drive_files = get_already_downloaded_files()

    print("Initializing Telethon connection...")
    client = TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        timeout=120
    )
    await client.connect()

    if not await client.is_user_authorized():
        print("CRITICAL ERROR: Telegram String Session key is invalid or expired.")
        return

    print(f"Authorized! Parsing target feed ID: {channel_id}...")
    print("FILTER: Physics + Chemistry + Biology + Static GK only")

    scanned = 0
    selected = 0
    skipped_subject = 0

    def progress_callback(received_bytes, total_bytes):
        if total_bytes:
            percentage = (received_bytes / total_bytes) * 100
            # Avoid printing the same integer percentage repeatedly.
            if int(percentage) % 25 == 0:
                print(f" -> Download Progress: {percentage:.1f}%")

    async for message in client.iter_messages(channel_id):
        scanned += 1

        is_video = message.video is not None
        is_pdf = bool(
            message.document
            and message.document.mime_type
            and message.document.mime_type.lower() == 'application/pdf'
        )

        # Ignore everything except videos and PDFs.
        if not (is_video or is_pdf):
            continue

        subject = classify_message(message)
        if subject is None:
            skipped_subject += 1
            continue

        selected += 1

        extension = message.file.ext if message.file and message.file.ext else (
            '.mp4' if is_video else '.pdf'
        )

        if message.file and message.file.name:
            base_name, _ = os.path.splitext(message.file.name)
        else:
            base_name = "file"

        # Clean filename while preserving useful words from the original title.
        base_name = "".join(
            c for c in base_name
            if c.isalpha() or c.isdigit() or c in ' _-'
        ).strip()

        if not base_name:
            base_name = subject

        file_name = f"{base_name}_msg_{message.id}{extension}"
        full_path = os.path.join(save_path, file_name)

        if os.path.exists(full_path) or file_name in drive_files:
            print(f"[{selected}] Skipping [{subject}]: {file_name} already exists.")
            continue

        file_type = "Video" if is_video else "PDF"
        print(f"[{selected}] Downloading [{subject} / {file_type}]: {file_name}")

        max_retries = 5
        attempt = 0
        download_success = False

        while attempt < max_retries and not download_success:
            try:
                if not client.is_connected():
                    print("Restoring dropped Telegram connection...")
                    await client.connect()

                await client.download_media(
                    message,
                    file=full_path,
                    progress_callback=progress_callback,
                    request_size=1024 * 1024
                )

                print(f"Successfully downloaded: {file_name}")
                download_success = True
                await asyncio.sleep(2)

            except Exception as ce:
                attempt += 1
                wait_time = attempt * 20
                print(
                    f"⚠️ Telegram error ({type(ce).__name__}: {ce}). "
                    f"Retrying {attempt}/{max_retries} in {wait_time}s..."
                )

                if os.path.exists(full_path):
                    os.remove(full_path)

                await asyncio.sleep(wait_time)

        if not download_success:
            print(f"💥 Permanent drop: {file_name} after {max_retries} failed attempts.")

    print("\n========== JOB SUMMARY ==========")
    print(f"Messages scanned:       {scanned}")
    print(f"Matching files found:  {selected}")
    print(f"Non-matching skipped:   {skipped_subject}")
    print("Allowed subjects:       Physics, Chemistry, Biology, Static GK")
    print("=================================")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
