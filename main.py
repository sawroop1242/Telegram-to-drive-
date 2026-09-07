from telethon import TelegramClient
from telethon.sessions import StringSession
import os
import asyncio
import subprocess

# --- Configuration (Pulled from GitHub Secrets) ---
api_id = int(os.environ['API_ID'])
api_hash = os.environ['API_HASH']
session_string = os.environ['TELEGRAM_SESSION']

# Target channel ID
channel_id = 'aditya_ranjan_maths_vidya_gramm'

# Folder inside the runner to hold files temporarily before Rclone pushes them
save_path = './downloads/Telegram_Archive/Maths/vidya_gram/'
os.makedirs(save_path, exist_ok=True)

# Google Drive path checked via Rclone (Must match your .yml path)
# Format: "remote_name:folder/path"
remote_drive_path = "gdrive1:Telegram_Archive/Maths/vidya gram/"

def get_already_downloaded_files():
    """Queries Google Drive using Rclone to get a list of all existing files."""
    print("Checking Google Drive for existing files...")
    existing_files = set()
    try:
        # Runs 'rclone lsf' to cleanly list only the file names in that folder
        result = subprocess.run(
            ['rclone', 'lsf', remote_drive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        # Parse output into a set for O(1) ultra-fast lookup times
        for line in result.stdout.splitlines():
            if line.strip():
                existing_files.add(line.strip())
        print(f"Found {len(existing_files)} files already archived on Google Drive.")
    except Exception as e:
        print(f"⚠️ Could not read remote Google Drive directory (it may be empty or new): {e}")
    return existing_files

async def main():
    # 1. Fetch the list of files already stored safely on your Drive
    drive_files = get_already_downloaded_files()

    print("Starting connection to Telegram...")
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("CRITICAL ERROR: Session string is invalid or expired.")
        return

    print(f"Connected! Scraping videos and PDFs from ID: {channel_id}...")
    file_count = 0

    def progress_callback(received_bytes, total_bytes):
        if total_bytes:
            percentage = (received_bytes / total_bytes) * 100
            if int(percentage) % 25 == 0:
                print(f" -> Progress: {percentage:.1f}%")

    async for message in client.iter_messages(channel_id):
        is_video = message.video is not None
        is_pdf = message.document and 'pdf' in message.document.mime_type

        if is_video or is_pdf:
            file_count += 1

            if message.file and message.file.name:
                file_name = message.file.name
            else:
                extension = message.file.ext if message.file.ext else ('.mp4' if is_video else '.pdf')
                file_name = f"file_{message.id}{extension}"

            # Sanitize filename string to match saved formats
            file_name = "".join([c for c in file_name if c.isalpha() or c.isdigit() or c in ' ._-']).strip()
            full_path = os.path.join(save_path, file_name)

            # --- DOUBLE DUPLICATION CHECK ---
            # Check 1: Does it exist locally on the runner?
            # Check 2: Does it already exist in your live Google Drive folder?
            if os.path.exists(full_path) or file_name in drive_files:
                print(f"[{file_count}] {file_name} already exists on Google Drive. Skipping download.")
                continue

            file_type = "Video" if is_video else "PDF"
            print(f"[{file_count}] Downloading {file_type}: {file_name}...")

            # --- ROBUST RETRY SYSTEM TO FIX DISCONNECTIONS ---
            max_retries = 5
            attempt = 0
            download_success = False

            while attempt < max_retries and not download_success:
                try:
                    if not client.is_connected():
                        print("Reconnecting to Telegram servers...")
                        await client.connect()

                    await client.download_media(message, file=full_path, progress_callback=progress_callback)
                    print(f"Successfully saved {file_name}")
                    download_success = True
                    await asyncio.sleep(3) 

                except (ConnectionError, asyncio.TimeoutError) as ce:
                    attempt += 1
                    wait_time = attempt * 15
                    print(f"⚠️ Telegram disconnected ({ce}). Retrying attempt {attempt}/{max_retries} in {wait_time}s...")
                    await asyncio.sleep(wait_time)

                except Exception as e:
                    print(f"❌ Unrecoverable error with {file_name}: {e}")
                    break

            if not download_success:
                print(f"💥 Skipping {file_name} after {max_retries} failed connection attempts.")

    print(f"Finished! Processed {file_count} files.")

if __name__ == "__main__":
    asyncio.run(main())
    from telethon import TelegramClient
from telethon.sessions import StringSession
import os
import asyncio
import subprocess

# --- Configuration (Pulled from GitHub Secrets) ---
api_id = int(os.environ['API_ID'])
api_hash = os.environ['API_HASH']
session_string = os.environ['TELEGRAM_SESSION']

# Target channel ID
channel_id = 'aditya_ranjan_maths_vidyagramm'

# Folder inside the runner to hold files temporarily before Rclone pushes them
save_path = './downloads/Telegram_Archive/maths/vidya gram/'
os.makedirs(save_path, exist_ok=True)

# Google Drive path checked via Rclone (Must match your .yml path)
# Format: "remote_name:folder/path"
remote_drive_path = "mydrive:Telegram_Archive/GK-GS/parmar_ssc/"

def get_already_downloaded_files():
    """Queries Google Drive using Rclone to get a list of all existing files."""
    print("Checking Google Drive for existing files...")
    existing_files = set()
    try:
        # Runs 'rclone lsf' to cleanly list only the file names in that folder
        result = subprocess.run(
            ['rclone', 'lsf', remote_drive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        # Parse output into a set for O(1) ultra-fast lookup times
        for line in result.stdout.splitlines():
            if line.strip():
                existing_files.add(line.strip())
        print(f"Found {len(existing_files)} files already archived on Google Drive.")
    except Exception as e:
        print(f"⚠️ Could not read remote Google Drive directory (it may be empty or new): {e}")
    return existing_files

async def main():
    # 1. Fetch the list of files already stored safely on your Drive
    drive_files = get_already_downloaded_files()

    print("Starting connection to Telegram...")
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("CRITICAL ERROR: Session string is invalid or expired.")
        return

    print(f"Connected! Scraping videos and PDFs from ID: {channel_id}...")
    file_count = 0

    def progress_callback(received_bytes, total_bytes):
        if total_bytes:
            percentage = (received_bytes / total_bytes) * 100
            if int(percentage) % 25 == 0:
                print(f" -> Progress: {percentage:.1f}%")

    async for message in client.iter_messages(channel_id):
        is_video = message.video is not None
        is_pdf = message.document and 'pdf' in message.document.mime_type

        if is_video or is_pdf:
            file_count += 1

            if message.file and message.file.name:
                file_name = message.file.name
            else:
                extension = message.file.ext if message.file.ext else ('.mp4' if is_video else '.pdf')
                file_name = f"file_{message.id}{extension}"

            # Sanitize filename string to match saved formats
            file_name = "".join([c for c in file_name if c.isalpha() or c.isdigit() or c in ' ._-']).strip()
            full_path = os.path.join(save_path, file_name)

            # --- DOUBLE DUPLICATION CHECK ---
            # Check 1: Does it exist locally on the runner?
            # Check 2: Does it already exist in your live Google Drive folder?
            if os.path.exists(full_path) or file_name in drive_files:
                print(f"[{file_count}] {file_name} already exists on Google Drive. Skipping download.")
                continue

            file_type = "Video" if is_video else "PDF"
            print(f"[{file_count}] Downloading {file_type}: {file_name}...")

            # --- ROBUST RETRY SYSTEM TO FIX DISCONNECTIONS ---
            max_retries = 5
            attempt = 0
            download_success = False

            while attempt < max_retries and not download_success:
                try:
                    if not client.is_connected():
                        print("Reconnecting to Telegram servers...")
                        await client.connect()

                    await client.download_media(message, file=full_path, progress_callback=progress_callback)
                    print(f"Successfully saved {file_name}")
                    download_success = True
                    await asyncio.sleep(3) 

                except (ConnectionError, asyncio.TimeoutError) as ce:
                    attempt += 1
                    wait_time = attempt * 15
                    print(f"⚠️ Telegram disconnected ({ce}). Retrying attempt {attempt}/{max_retries} in {wait_time}s...")
                    await asyncio.sleep(wait_time)

                except Exception as e:
                    print(f"❌ Unrecoverable error with {file_name}: {e}")
                    break

            if not download_success:
                print(f"💥 Skipping {file_name} after {max_retries} failed connection attempts.")

    print(f"Finished! Processed {file_count} files.")

if __name__ == "__main__":
    asyncio.run(main())
    
