from telethon import TelegramClient
from telethon.sessions import StringSession
import os
import time
import asyncio

# --- Configuration (Pulled from GitHub Secrets) ---
api_id = int(os.environ['API_ID'])
api_hash = os.environ['API_HASH']
session_string = os.environ['TELEGRAM_SESSION']

# Extracted from your links (t.me/c/3708183148/...)
# Telethon requires the -100 prefix for private channels
channel_id = -1003708183148

# Base storage path
base_save_path = '/content/drive/MyDrive/Telegram_Archive/GK_GS/'

# Hardcoded target lists mapping subjects to their specific message IDs
target_downloads = {
    "Physics":[1193,1197,1201,1204,1223,1228,1249,1249],
    "Chemistry":[1122,1123,1123],
    "Biology": [1035, 1038, 1043, 1048, 1051, 1053, 1059, 1063, 1066, 1070, 1076, 1080, 1082]
}

async def main():
    print("Starting connection to Telegram...")
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    
    await client.connect()
    
    if not await client.is_user_authorized():
        print("CRITICAL ERROR: Session string is invalid or expired.")
        return

    print(f"Connected! Target channel ID: {channel_id}")
    
    # Flatten our dictionary into a job list to process sequentially
    jobs = []
    for subject, msg_ids in target_downloads.items():
        for msg_id in msg_ids:
            jobs.append((subject, msg_id))

    total_jobs = len(jobs)
    print(f"Found {total_jobs} target files across Physics, Chemistry, and Biology to process.")

    for index, (subject, msg_id) in enumerate(jobs, start=1):
        print(f"[{index}/{total_jobs}] Fetching message ID {msg_id} for {subject}...")
        
        try:
            # Directly fetch the specific message by ID instead of looping through thousands
            message = await client.get_messages(channel_id, ids=msg_id)
            
            if not message or not message.media:
                print(f"-> Warning: Message ID {msg_id} has no media or doesn't exist. Skipping.")
                continue

            is_video = message.video is not None
            is_pdf = message.document and 'pdf' in message.document.mime_type

            if is_video or is_pdf:
                # Set up file name
                if message.file and message.file.name:
                    file_name = message.file.name
                else:
                    extension = message.file.ext if message.file.ext else ('.mp4' if is_video else '.pdf')
                    file_name = f"file_{message.id}{extension}"

                # Handle organized sub-folders
                current_save_path = os.path.join(base_save_path, subject)
                os.makedirs(current_save_path, exist_ok=True)
                full_path = os.path.join(current_save_path, file_name)

                # Skip file if already fully downloaded
                if os.path.exists(full_path):
                    print(f"-> {file_name} already exists in {subject}. Skipping.")
                    continue

                file_type = "Video" if is_video else "PDF"
                print(f"-> Downloading {file_type}: {file_name} into {subject}/ folder...")

                await client.download_media(message, file=full_path)
                print(f"-> Successfully saved {file_name}")
                
                # Dynamic flood/rate limits protection delay
                await asyncio.sleep(4) 
            else:
                print(f"-> Message ID {msg_id} is not a Video or PDF. Skipping.")

        except Exception as e:
            print(f"-> Error processing message ID {msg_id}: {e}")
            await asyncio.sleep(5)  # Wait a bit longer if an error occurs before moving on

    print("Finished! All specified links processed.")

if __name__ == "__main__":
    asyncio.run(main())
    
