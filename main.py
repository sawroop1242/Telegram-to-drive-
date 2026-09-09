from telethon import TelegramClient
from telethon.sessions import StringSession
import os
import time
import asyncio

# --- Configuration (Pulled from GitHub Secrets) ---
api_id = int(os.environ['API_ID'])
api_hash = os.environ['API_HASH']
session_string = os.environ['TELEGRAM_SESSION']

channel_id = -1003708183148

# Base storage path
base_save_path = '/content/drive/MyDrive/Telegram_Archive/parmar_ssc/new/'

# Target subjects to check for in the filename
subjects = ["physics", "chemistry", "ancient history", "midelve history", "medieval history"]

async def main():
    print("Starting connection to Telegram...")
    # Use StringSession to bypass the OTP prompt completely
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    
    await client.connect()
    
    if not await client.is_user_authorized():
        print("CRITICAL ERROR: Session string is invalid or expired.")
        return

    print(f"Connected! Scraping videos and PDFs from ID: {channel_id}...")
    file_count = 0

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

            # --- Subject Filter Logic ---
            matched_subject = None
            filename_lower = file_name.lower() # Normalize case for mapping matches

            for subject in subjects:
                if subject in filename_lower:
                    matched_subject = subject
                    break  # Break loop on first discovered match

            # Determine storage route based on whether a subject matches
            if matched_subject:
                # Sanitize the folder name string (replacing spaces with underscores)
                folder_name = matched_subject.replace(" ", "_")
                current_save_path = os.path.join(base_save_path, folder_name)
            else:
                current_save_path = base_save_path

            # Create path directories dynamically if they do not yet exist
            os.makedirs(current_save_path, exist_ok=True)
            full_path = os.path.join(current_save_path, file_name)

            if os.path.exists(full_path):
                print(f"[{file_count}] {file_name} already exists. Skipping.")
                continue

            file_type = "Video" if is_video else "PDF"
            if matched_subject:
                print(f"[{file_count}] Found {matched_subject.upper()} asset! Downloading {file_type}: {file_name}...")
            else:
                print(f"[{file_count}] Downloading {file_type}: {file_name}...")

            try:
                await client.download_media(message, file=full_path)
                print(f"Successfully saved {file_name}")
                time.sleep(3)
            except Exception as e:
                print(f"Error downloading {file_name}: {e}")

    print(f"Finished! Processed {file_count} files.")

# Execute standard Python async
if __name__ == "__main__":
    asyncio.run(main())
    
