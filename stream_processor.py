import os
import sys
import subprocess
import logging
import time
import shutil

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("CookieFreeLiveArchiver")

# Configuration parameters
CHUNK_DURATION = 120  # 2 minutes per segment (in seconds)
LOCAL_TEMP_DIR = '/tmp/stream_chunks/'
base_gdrive_destination = os.environ.get("GDRIVE_DESTINATION", "mydrive:Telegram_Archive/Live_Streams/")

os.makedirs(LOCAL_TEMP_DIR, exist_ok=True)

def merge_and_upload():
    logger.info("Stream session closed or went private. Beginning immediate rescue merge...")
    
    # Filter and sort files to keep chronological timeline sequence perfect
    all_chunks = sorted([f for f in os.listdir(LOCAL_TEMP_DIR) if f.endswith('.mp4')])
    
    if not all_chunks:
        logger.critical("CRITICAL ERROR: No video chunks found. The stream may have gone private before the script started.")
        sys.exit(1)
        
    logger.info(f"Discovered {len(all_chunks)} video chunks saved locally. Building merge map...")
    
    # Create the text file mapping required by FFmpeg's concat demuxer
    concat_list_path = os.path.join(LOCAL_TEMP_DIR, "inputs.txt")
    with open(concat_list_path, "w") as f:
        for chunk in all_chunks:
            safe_chunk_path = chunk.replace("'", "'\\''")
            f.write(f"file '{safe_chunk_path}'\n")
            
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    final_merged_file = f"/tmp/rescued_stream_{timestamp}.mp4"
    
    logger.info("[MERGE START] Combining all saved 2-minute parts locally...")
    
    # Concatenate all local 2-minute chunks together without touching YouTube servers
    concat_cmd = f'ffmpeg -f concat -safe 0 -i "{concat_list_path}" -c copy "{final_merged_file}"'
    
    try:
        subprocess.run(concat_cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        file_size_mb = os.path.getsize(final_merged_file) / (1024 * 1024)
        logger.info(f"[MERGE SUCCESS] Created single file: rescued_stream_{timestamp}.mp4 ({file_size_mb:.2f} MB)")
    except subprocess.CalledProcessError as e:
        logger.critical(f"CRITICAL ERROR: Merge engine failed: {e.stderr.decode().strip()}")
        sys.exit(1)
        
    # Upload the single merged file to Google Drive
    logger.info(f"[UPLOAD START] Moving merged video safely to Google Drive...")
    upload_cmd = f'rclone move "{final_merged_file}" "{base_gdrive_destination}" --drive-chunk-size 64M -P'
    
    try:
        subprocess.run(upload_cmd, shell=True, check=True)
        logger.info("[SUCCESS] Video successfully archived and synced to your Google Drive path!")
    except subprocess.CalledProcessError:
        logger.error("CRITICAL ERROR: Rclone failed to upload the merged video file.")
        sys.exit(1)

def main():
    stream_url = os.environ.get("STREAM_LINK")
    
    if not stream_url:
        logger.critical("CRITICAL ERROR: No Stream URL provided.")
        sys.exit(1)

    # Target naming layout for chunks inside our temporary working directory
    segment_template = os.path.join(LOCAL_TEMP_DIR, "chunk_%04d.mp4")

    logger.info("[RECORDING ACTIVE] Initiating anonymous live capture with automated fragment extraction...")
    
    # The -movflags argument forces FFmpeg to keep writing readable MP4 files continuously.
    # If YouTube boots the script out the millisecond the stream ends, the downloaded parts are not corrupted.
    ytdlp_cmd = (
        f'yt-dlp --live-from-start "{stream_url}" '
        f'--downloader ffmpeg '
        f'--downloader-args "ffmpeg:-f segment -segment_time {CHUNK_DURATION} -reset_timestamps 1 -c copy -movflags +faststart+frag_keyframe+empty_moov" '
        f'-o "{segment_template}"'
    )

    try:
        # Launch tracking process in real-time mode so logs show clearly inside GitHub Actions console
        process = subprocess.Popen(ytdlp_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        
        for line in process.stdout:
            print(line, end="")
            
        process.wait()
        
    except Exception as e:
        logger.error(f"Network stream connection dropped: {e}")
    
    # Regardless of why the download stops (natural end or sudden private lockout),
    # immediately trigger the local merge process to rescue whatever was saved.
    merge_and_upload()

    # Clean up runner workspace storage environment
    try:
        shutil.rmtree(LOCAL_TEMP_DIR)
    except Exception:
        pass

if __name__ == "__main__":
    main()
  
