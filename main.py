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

# Local staging directory on the GitHub Action VM runner
# Updated to match the underscore configuration you verified working
save_path = './downloads/Telegram_Archive/Maths/vidya_gram/'
os.makedirs(save_path, exist_ok=True)

# 🎯 MATCHED EXACTLY: Matches your successful 'rclone lsf' command path parameters
remote_drive_name = "gdrive1" 
remote_drive_path = f"{remote_drive_name}:Telegram_Archive/Matha/vidya_gram/"

def get_already_downloaded_files():
    """Queries Google Drive via Rclone safely using your exact working remote path."""
    print(f"Scanning Google Drive remote folder: [{remote_drive_path}]")
    existing_files = set()

    try:
        result = subprocess.run(
            ['rclone', 'lsf', remote_drive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # If exit code is not 0, it means the folder is empty or new.
        # We treat it as an empty set instead of throwing a fatal script crash.
        if result.returncode != 0:
            print(f"ℹ️ Rclone Info: Remote folder appears new or empty. (Logs: {result.stderr.strip()})")
            return existing_files

        for line in result.stdout.splitlines():
            if line.strip():
                existing_files.add(line.strip())
        print(f"Index successfully populated: {len(existing_files)} files tracked on Google Drive.")
    except Exception as e:
        print(f"⚠️ Unexpected Rclone parsing error: {e}")
        
    return existing_files

async def main():
    # Phase 1: Pull live remote folder contents safely
    drive_files = get_already_downloaded_files()

    print("Initializing Telethon connection link...")
    # Setting an extended 120-second connection timeout threshold on the client initialization
    client = TelegramClient(StringSession(session_string), api_id, api_hash, timeout=120)
    await client.connect()
    
    if not await client.is_user_authorized():
        print("CRITICAL ERROR: Telegram String Session key is invalid or expired.")
        return

    print(f"Authorized! Parsing target feed ID: {channel_id}...")
    file_count = 0

    def progress_callback(received_bytes, total_bytes):
        if total_bytes:
            percentage = (received_bytes / total_bytes) * 100
            if int(percentage) % 25 == 0:
                print(f" -> Download Progress: {percentage:.1f}%")

    async for message in client.iter_messages(channel_id):
        is_video = message.video is not None
        is_pdf = message.document and 'pdf' in message.document.mime_type

        if is_video or is_pdf:
            file_count += 1
            extension = message.file.ext if message.file.ext else ('.mp4' if is_video else '.pdf')

            if message.file and message.file.name:
                base_name, _ = os.path.splitext(message.file.name)
            else:
                base_name = "file"

            # Clean and sanitize the string to remove illegal character blocks
            base_name = "".join([c for c in base_name if c.isalpha() or c.isdigit() or c in ' _-']).strip()
            
            # Unique message ID suffix ensures true 1:1 match across remote verification checks
            file_name = f"{base_name}_msg_{message.id}{extension}"
            full_path = os.path.join(save_path, file_name)

            # --- TRUE DUPLICATION CHECK ---
            if os.path.exists(full_path) or file_name in drive_files:
                print(f"[{file_count}] Skipping: {file_name} already exists on Google Drive.")
                continue

            file_type = "Video" if is_video else "PDF"
            print(f"[{file_count}] Initiating Download [{file_type}]: {file_name}...")

            # --- TIMEOUT RESISTANT BACKOFF RETRY SYSTEM ---
            max_retries = 5
            attempt = 0
            download_success = False

            while attempt < max_retries and not download_success:
                try:
                    if not client.is_connected():
                        print("Restoring dropped connection link...")
                        await client.connect()

                    # Force download using custom 1MB data chunks to prevent structural timeouts on large inputs
                    await client.download_media(
                        message, 
                        file=full_path, 
                        progress_callback=progress_callback,
                        request_size=1024 * 1024  
                    )
                    
                    print(f"Successfully written: {file_name}")
                    download_success = True
                    await asyncio.sleep(2)

                except (ConnectionError, asyncio.TimeoutError, Exception) as ce:
                    attempt += 1
                    wait_time = attempt * 20
                    print(f"⚠️ Telegram pipeline error ({type(ce).__name__}: {ce}). Retrying attempt {attempt}/{max_retries} in {wait_time}s...")
                    
                    # Wipe partial corrupted file fragments immediately before trying again
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        
                    await asyncio.sleep(wait_time)

            if not download_success:
                print(f"💥 Permanent drop: Skipping {file_name} after {max_retries} failed connections.")

    print(f"Job sequence completed. Total target elements parsed: {file_count}")

if __name__ == "__main__":
    asyncio.run(main())
    
