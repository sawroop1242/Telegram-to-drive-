import asyncio
import os
import re
import unicodedata
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession


# ============================================================
# CONFIGURATION
# ============================================================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["TELEGRAM_SESSION"]

CHANNEL_ID = -1003708183148

BASE_SAVE_PATH = Path(
    "/content/drive/MyDrive/Telegram_Archive/GK_GS/parmar_ssc/NEW/"
)

# Minimum score required to classify a file.
# Increase this if you want stricter classification.
MIN_CONFIDENCE = 5

# If the top two subjects are too close, treat the file as unknown.
AMBIGUITY_MARGIN = 2

# Delay between downloads.
DOWNLOAD_DELAY = 1


# ============================================================
# SUBJECT KEYWORDS
# ============================================================
#
# Score meanings:
#
#   10 = very strong / exact subject phrase
#    7 = strong keyword
#    5 = useful abbreviation
#    3 = weak/general keyword
#
# The classifier checks BOTH:
#
#   filename
#   caption
#
# Filename matches receive a higher multiplier.
# ============================================================

SUBJECT_KEYWORDS = {
    "physics": {
        "exact": [
            "physics",
            "physics lecture",
            "physics class",
        ],
        "strong": [
            "phys",
            "phy",
            "bhoutik vigyan",
            "bhautik vigyan",
            "भौतिक विज्ञान",
            "भौतिकी",
            "भौतिक",
        ],
        "weak": [
            "mechanics",
            "motion",
            "force",
            "work energy",
            "thermodynamics",
            "optics",
            "electricity",
            "magnetism",
            "current electricity",
            "kinematics",
        ],
    },

    "chemistry": {
        "exact": [
            "chemistry",
            "chemistry lecture",
            "chemistry class",
        ],
        "strong": [
            "chem",
            "che",
            "chemical",
            "rasayan vigyan",
            "rasaayan vigyan",
            "रसायन विज्ञान",
            "रसायन",
        ],
        "weak": [
            "organic",
            "inorganic",
            "physical chemistry",
            "mole concept",
            "periodic table",
            "thermochemistry",
            "electrochemistry",
            "chemical bonding",
            "atom",
            "molecule",
        ],
    },

    "ancient_history": {
        "exact": [
            "ancient history",
            "ancient indian history",
            "ancient india",
            "ancient_hist",
            "ancienthist",
        ],
        "strong": [
            "ancient",
            "pracheen itihas",
            "pracheen bharat",
            "pracheen",
            "प्राचीन इतिहास",
            "प्राचीन भारत",
            "प्राचीन",
        ],
        "weak": [
            "indus valley",
            "harappa",
            "harappan",
            "vedic",
            "maurya",
            "gupta",
            "buddhism",
            "jainism",
            "ashoka",
            "rigveda",
            "upanishad",
            "chalcolithic",
            "paleolithic",
            "mesolithic",
            "neolithic",
        ],
    },

    "biology": {
    "exact": [
        "biology",
        "biology lecture",
        "biology class",
        "biology notes",
    ],
    "strong": [
        "bio",
        "biol",
        "biology",
        "जीव विज्ञान",
        "जीवविज्ञान",
        "जीव शास्त्र",
        "जीवशास्त्र",
        "jiv vigyan",
        "jeev vigyan",
        "jiv vigyan",
    ],
    "weak": [
        "cell",
        "cell biology",
        "genetics",
        "heredity",
        "evolution",
        "ecology",
        "botany",
        "zoology",
        "human biology",
        "human physiology",
        "plant physiology",
        "anatomy",
        "physiology",
        "reproduction",
        "human reproduction",
        "plant",
        "animal",
        "microbiology",
        "biotechnology",
        "biodiversity",
        "photosynthesis",
        "respiration",
        "digestion",
        "blood",
        "circulation",
        "excretion",
        "nervous system",
        "endocrine",
        "hormone",
        "पर्यावरण",
        "आनुवंशिकी",
        "कोशिका",
        "पारिस्थितिकी",
        "वनस्पति",
        "जंतु",
        "प्रजनन",
        "पाचन",
        "रक्त",
        "उत्सर्जन",
    ],
},

    "medieval_history": {
        "exact": [
            "medieval history",
            "medieval indian history",
            "medieval india",
            "medieval_hist",
            "medievalhist",
            "mideval history",
            "midelve history",
            "midevl history",
        ],
        "strong": [
            "medieval",
            "mideval",
            "midelve",
            "midevl",
            "madhyakalin itihas",
            "madhyakalin bharat",
            "madhyakalin",
            "मध्यकालीन इतिहास",
            "मध्यकालीन भारत",
            "मध्यकालीन",
        ],
        "weak": [
            "delhi sultanate",
            "sultanate",
            "mughal",
            "mughals",
            "akbar",
            "babur",
            "humayun",
            "aurangzeb",
            "tughlaq",
            "khilji",
            "lodhi",
            "vijayanagara",
            "maratha",
            "bhakti",
            "sufi",
        ],
    },
}


# ============================================================
# FOLDER NAMES
# ============================================================

SUBJECT_FOLDERS = {
    "physics": "physics",
    "chemistry": "chemistry",
    "biology": "biology",
    "ancient_history": "ancient_history",
    "medieval_history": "medieval_history",
}

# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text: str) -> str:
    """
    Normalize text so that variations such as:

        Physics_Lecture-01
        physics.lecture 01
        PHYSICS lecture

    are easier to match.
    """

    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)

    text = text.lower()

    # Replace common separators with spaces.
    text = re.sub(r"[_\-.]+", " ", text)

    # Keep English/Hindi letters/numbers/spaces.
    text = re.sub(r"[^\w\s\u0900-\u097F]", " ", text)

    # Collapse multiple spaces.
    text = re.sub(r"\s+", " ", text)

    return text.strip()


# ============================================================
# TOKEN / PHRASE MATCHING
# ============================================================

def phrase_matches(text: str, phrase: str) -> bool:
    """
    Safer matching than:

        phrase in text

    This prevents accidental matches inside unrelated words.

    For example:

        'phy' should match 'phy lecture'
        but shouldn't match an arbitrary word containing 'phy'.
    """

    phrase = normalize_text(phrase)

    if not phrase:
        return False

    # Hindi doesn't need the same English word-boundary handling,
    # and normalized phrase matching works well for it.
    if re.search(r"[\u0900-\u097F]", phrase):
        return phrase in text

    pattern = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"

    return re.search(pattern, text, flags=re.IGNORECASE) is not None


# ============================================================
# SUBJECT CLASSIFICATION
# ============================================================

def classify_subject(filename: str, caption: str = ""):
    """
    Return:

        subject
        confidence
        details

    Example:

        ('physics', 12, ['filename: physics (+10)', ...])

    If classification is uncertain:

        (None, 0, [...])
    """

    normalized_filename = normalize_text(filename)
    normalized_caption = normalize_text(caption)

    scores = {
        subject: 0
        for subject in SUBJECT_KEYWORDS
    }

    details = {
        subject: []
        for subject in SUBJECT_KEYWORDS
    }

    for subject, groups in SUBJECT_KEYWORDS.items():

        # ----------------------------------------------------
        # EXACT MATCHES
        # ----------------------------------------------------

        for keyword in groups["exact"]:
            keyword_normalized = normalize_text(keyword)

            if phrase_matches(normalized_filename, keyword_normalized):
                scores[subject] += 10
                details[subject].append(
                    f"filename exact: '{keyword}' (+10)"
                )

            if phrase_matches(normalized_caption, keyword_normalized):
                scores[subject] += 7
                details[subject].append(
                    f"caption exact: '{keyword}' (+7)"
                )

        # ----------------------------------------------------
        # STRONG MATCHES
        # ----------------------------------------------------

        for keyword in groups["strong"]:
            keyword_normalized = normalize_text(keyword)

            if phrase_matches(normalized_filename, keyword_normalized):
                scores[subject] += 7
                details[subject].append(
                    f"filename strong: '{keyword}' (+7)"
                )

            if phrase_matches(normalized_caption, keyword_normalized):
                scores[subject] += 5
                details[subject].append(
                    f"caption strong: '{keyword}' (+5)"
                )

        # ----------------------------------------------------
        # WEAK / TOPIC MATCHES
        # ----------------------------------------------------

        for keyword in groups["weak"]:
            keyword_normalized = normalize_text(keyword)

            if phrase_matches(normalized_filename, keyword_normalized):
                scores[subject] += 4
                details[subject].append(
                    f"filename topic: '{keyword}' (+4)"
                )

            if phrase_matches(normalized_caption, keyword_normalized):
                scores[subject] += 2
                details[subject].append(
                    f"caption topic: '{keyword}' (+2)"
                )

    # --------------------------------------------------------
    # SORT SUBJECTS BY SCORE
    # --------------------------------------------------------

    ranked = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    best_subject, best_score = ranked[0]

    second_score = ranked[1][1] if len(ranked) > 1 else 0

    # --------------------------------------------------------
    # NO GOOD MATCH
    # --------------------------------------------------------

    if best_score < MIN_CONFIDENCE:
        return None, best_score, [
            "No sufficiently strong subject match."
        ]

    # --------------------------------------------------------
    # AMBIGUOUS MATCH
    # --------------------------------------------------------

    if (
        second_score > 0
        and best_score - second_score < AMBIGUITY_MARGIN
    ):
        return None, best_score, [
            f"Ambiguous classification: "
            f"{best_subject}={best_score}, "
            f"{ranked[1][0]}={second_score}"
        ]

    return (
        best_subject,
        best_score,
        details[best_subject],
    )


# ============================================================
# SAFE FILE NAME
# ============================================================

def safe_filename(filename: str, message_id: int, extension: str):
    """
    Prevent invalid filesystem names.
    """

    if not filename:
        filename = f"file_{message_id}{extension}"

    filename = os.path.basename(filename)

    # Remove dangerous path characters.
    filename = filename.replace("/", "_")
    filename = filename.replace("\\", "_")

    # Remove control characters.
    filename = re.sub(r"[\x00-\x1f\x7f]", "_", filename)

    # Avoid empty names.
    if not filename.strip():
        filename = f"file_{message_id}{extension}"

    return filename


# ============================================================
# UNIQUE PATH
# ============================================================

def get_unique_path(path: Path) -> Path:
    """
    If:

        lecture.mp4

    already exists, return:

        lecture_1.mp4
        lecture_2.mp4
        ...
    """

    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix

    counter = 1

    while True:
        candidate = path.parent / f"{stem}_{counter}{suffix}"

        if not candidate.exists():
            return candidate

        counter += 1


# ============================================================
# GET MESSAGE CAPTION
# ============================================================

def get_caption(message) -> str:
    """
    Telegram captions are stored in message.text/message.raw_text.
    """

    try:
        return message.raw_text or ""
    except Exception:
        return ""


# ============================================================
# GET FILE NAME
# ============================================================

def get_file_name(message, is_video: bool, is_pdf: bool) -> str:
    """
    Extract the Telegram filename safely.
    """

    if message.file and message.file.name:
        return message.file.name

    if message.file and message.file.ext:
        extension = message.file.ext
    elif is_video:
        extension = ".mp4"
    elif is_pdf:
        extension = ".pdf"
    else:
        extension = ".bin"

    return f"file_{message.id}{extension}"


# ============================================================
# MAIN
# ============================================================

async def main():

    print("=" * 70)
    print("Telegram → Google Drive Downloader")
    print("Robust Subject Identification System")
    print("=" * 70)

    print("\nStarting connection to Telegram...")

    client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
    )

    await client.connect()

    # --------------------------------------------------------
    # AUTHENTICATION CHECK
    # --------------------------------------------------------

    if not await client.is_user_authorized():
        print(
            "\nCRITICAL ERROR:"
            "\nTelegram session is invalid or expired."
        )
        await client.disconnect()
        return

    print("Connected successfully!")

    print(f"\nChannel ID: {CHANNEL_ID}")
    print(f"Base path: {BASE_SAVE_PATH}")

    print("\nSubject detection:")
    print("  • Physics")
    print("  • Chemistry")
    print("  • Ancient History")
    print("  • Medieval History")

    print("\nScanning Telegram messages...\n")

    BASE_SAVE_PATH.mkdir(
        parents=True,
        exist_ok=True,
    )

    processed_files = 0
    downloaded_files = 0
    skipped_files = 0
    unknown_files = 0
    error_files = 0

    # ========================================================
    # MESSAGE LOOP
    # ========================================================

    async for message in client.iter_messages(CHANNEL_ID):

        # ----------------------------------------------------
        # FILE TYPE DETECTION
        # ----------------------------------------------------

        is_video = message.video is not None

        is_pdf = (
            message.document is not None
            and (
                message.document.mime_type == "application/pdf"
                or (
                    message.document.mime_type
                    and "pdf" in message.document.mime_type.lower()
                )
            )
        )

        # Ignore everything except videos and PDFs.
        if not (is_video or is_pdf):
            continue

        processed_files += 1

        # ----------------------------------------------------
        # FILE NAME
        # ----------------------------------------------------

        original_file_name = get_file_name(
            message,
            is_video,
            is_pdf,
        )

        extension = (
            ".mp4"
            if is_video
            else ".pdf"
        )

        file_name = safe_filename(
            original_file_name,
            message.id,
            extension,
        )

        # ----------------------------------------------------
        # CAPTION
        # ----------------------------------------------------

        caption = get_caption(message)

        # ----------------------------------------------------
        # SUBJECT CLASSIFICATION
        # ----------------------------------------------------

        subject, confidence, reasons = classify_subject(
            file_name,
            caption,
        )

        # ----------------------------------------------------
        # DISPLAY IDENTIFICATION
        # ----------------------------------------------------

        print("-" * 70)

        print(f"[{processed_files}] {file_name}")

        if caption:
            display_caption = caption.replace("\n", " ")

            if len(display_caption) > 150:
                display_caption = display_caption[:150] + "..."

            print(f"Caption: {display_caption}")

        if subject:
            print(
                f"IDENTIFIED: {subject.upper()} "
                f"(confidence={confidence})"
            )

            for reason in reasons:
                print(f"  └─ {reason}")

        else:
            print(
                f"IDENTIFIED: UNKNOWN "
                f"(best score={confidence})"
            )

            for reason in reasons:
                print(f"  └─ {reason}")

        # ----------------------------------------------------
        # DETERMINE SAVE DIRECTORY
        # ----------------------------------------------------

        if subject:
            folder_name = SUBJECT_FOLDERS[subject]

            save_directory = (
                BASE_SAVE_PATH / folder_name
            )

        else:
            # Unknown files stay in the base directory.
            save_directory = BASE_SAVE_PATH

            unknown_files += 1

        save_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        # ----------------------------------------------------
        # TARGET FILE
        # ----------------------------------------------------

        target_path = (
            save_directory / file_name
        )

        # ----------------------------------------------------
        # EXISTING FILE CHECK
        # ----------------------------------------------------

        if target_path.exists():

            print(
                f"SKIP: Already exists → "
                f"{target_path}"
            )

            skipped_files += 1

            continue

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        file_type = (
            "VIDEO"
            if is_video
            else "PDF"
        )

        print(
            f"Downloading {file_type}..."
        )

        print(
            f"Destination: {target_path}"
        )

        try:

            downloaded_path = await client.download_media(
                message,
                file=str(target_path),
            )

            if downloaded_path:

                print(
                    f"SUCCESS: {downloaded_path}"
                )

                downloaded_files += 1

            else:

                print(
                    "WARNING: Telegram returned "
                    "no download path."
                )

                error_files += 1

        except Exception as error:

            print(
                f"ERROR downloading "
                f"{file_name}: {error}"
            )

            error_files += 1

        # ----------------------------------------------------
        # NON-BLOCKING DELAY
        # ----------------------------------------------------

        if DOWNLOAD_DELAY > 0:
            await asyncio.sleep(DOWNLOAD_DELAY)

    # ========================================================
    # FINAL REPORT
    # ========================================================

    print("\n")
    print("=" * 70)
    print("DOWNLOAD COMPLETE")
    print("=" * 70)

    print(
        f"Processed files : {processed_files}"
    )

    print(
        f"Downloaded      : {downloaded_files}"
    )

    print(
        f"Already existed : {skipped_files}"
    )

    print(
        f"Unknown subject : {unknown_files}"
    )

    print(
        f"Errors          : {error_files}"
    )

    print("=" * 70)

    await client.disconnect()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
