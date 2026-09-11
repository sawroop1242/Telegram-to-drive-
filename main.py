from telethon import TelegramClient
from telethon.sessions import StringSession
import os
import shutil
import asyncio
import logging

# --- Logging System Setup ---
# Formats logs nicely with Timestamp, Log Level, and Message
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("TelegramArchiver")

logging.getLogger('telethon').setLevel(logging.WARNING)


# --- Configuration (Pulled from GitHub Secrets) ---
api_id = int(os.environ['API_ID'])
api_hash = os.environ['API_HASH']
session_string = os.environ['TELEGRAM_SESSION']

channel_id = -1004303944698 # Your private channel ID
base_save_path = '/content/drive/MyDrive/Telegram_Archive/Maths/bhutesh_sir/'
LOCAL_TEMP_DIR = '/content/telegram_tmp/'
os.makedirs(LOCAL_TEMP_DIR, exist_ok=True)

# Massive range array targeting 946 continuous index links
target_downloads = {
    "Maths": [ i for i in range(5265,5362)]
}

async def main():
    logger.info("Starting connection to Telegram...")
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    await client.connect()
    
    if not await client.is_user_authorized():
        logger.critical("Session string is invalid or expired.")
        return

    logger.info(f"Connected! Processing large range target for channel: {channel_id}")

    for subject, msg_ids in target_downloads.items():
        current_save_path = os.path.join(base_save_path, subject)
        os.makedirs(current_save_path, exist_ok=True)
        
        total_ids = len(msg_ids)
        logger.info(f"Starting {subject} Block: Total {total_ids} messages to evaluate.")
        
        # Batch IDs into blocks of 100 to optimize performance and prevent flood bans
        BATCH_SIZE = 100
        for b_idx in range(0, total_ids, BATCH_SIZE):
            batch_slice = msg_ids[b_idx : b_idx + BATCH_SIZE]
            logger.info(f"Requesting batch bundle ({b_idx + 1} to {min(b_idx + BATCH_SIZE, total_ids)}) from Telegram...")
            
            try:
                # Pulls up to 100 items in ONE network frame
                messages = await client.get_messages(channel_id, ids=batch_slice)
                
                for message in messages:
                    if not message or not message.media:
                        continue
                    
                    is_video = message.video is not None
                    is_pdf = message.document and 'pdf' in message.document.mime_type

                    if is_video or is_pdf:
                        # Naming assignment logic
                        if message.file and message.file.name:
                            file_name = message.file.name
                        else:
                            extension = message.file.ext if message.file.ext else ('.mp4' if is_video else '.pdf')
                            file_name = f"file_{message.id}{extension}"

                        full_drive_path = os.path.join(current_save_path, file_name)
                        
                        # --- Logging Skip Status ---
                        if os.path.exists(full_drive_path):
                            logger.info(f"[SKIP] [ID: {message.id}] File already exists: {file_name}")
                            continue

                        local_path = os.path.join(LOCAL_TEMP_DIR, file_name)
                        file_type = "Video" if is_video else "PDF"
                        
                        logger.info(f"[DOWNLOAD] [ID: {message.id}] Starting {file_type} download: {file_name}")
                        
                        await client.download_media(message, file=local_path)
                        
                        if os.path.exists(local_path):
                            shutil.move(local_path, full_drive_path)
                            logger.info(f"[SYNC SUCCESS] [ID: {message.id}] Moved to Google Drive: {file_name}")
                        
                        # Brief safety delay between media transfers 
                        await asyncio.sleep(2)
                        
            except Exception as e:
                logger.error(f"Error handling batch block starting at index {b_idx}: {e}")
                await asyncio.sleep(10)  # Cool-down wait period on network errors

    # Final Workspace Garbage Collection Cleanup
    try:
        shutil.rmtree(LOCAL_TEMP_DIR)
    except Exception:
        pass
    logger.info("Execution complete. Large data arrays completely handled.")

if __name__ == "__main__":
    asyncio.run(main())
                        
