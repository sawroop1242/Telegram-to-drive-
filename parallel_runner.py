import asyncio
import sys

import main as archive


# Bounded concurrency keeps Telegram and Google Drive stable while allowing
# downloads and uploads to overlap.
PARALLEL_WORKERS = 3


async def process_parallel(client, message, manifest, manifest_lock, semaphore):
    async with semaphore:
        file_type = archive.detect_file_type(message)
        if file_type is None:
            return "SKIP_OTHER"

        subject = archive.classify_subject(message)
        if subject is None:
            original = archive.get_original_filename(message)
            archive.logger.info(
                "[%s] SKIP [NO ALLOWED SUBJECT] [%s] %s",
                message.id,
                file_type,
                original,
            )
            return "SKIP_SUBJECT"

        destination = archive.get_destination(subject, file_type)
        if destination is None:
            return "SKIP_OTHER"

        destination.mkdir(parents=True, exist_ok=True)
        filename = archive.build_filename(message)
        output_path = destination / filename
        message_key = str(message.id)

        async with manifest_lock:
            if message_key in manifest:
                archive.logger.info(
                    "[%s] SKIP [LOCAL MANIFEST] [%s] %s",
                    message.id,
                    file_type,
                    filename,
                )
                return "SKIP_MANIFEST"

        if output_path.exists():
            size = output_path.stat().st_size
            if size > 0:
                archive.logger.info(
                    "[%s] SKIP [LOCAL FILE EXISTS] [%s] %s",
                    message.id,
                    file_type,
                    filename,
                )
                async with manifest_lock:
                    manifest[message_key] = {
                        "filename": filename,
                        "subject": subject,
                        "file_type": file_type,
                        "status": "downloaded",
                    }
                    archive.save_manifest(manifest)
                return "SKIP_LOCAL"

        archive.logger.info(
            "[%s] [%s] [%s] %s | parallel worker",
            message.id,
            subject,
            file_type,
            filename,
        )

        # Download completes atomically before upload starts for THIS file.
        # Other workers may be downloading/uploading at the same time.
        success = await archive.download_file(client, message, output_path)
        if not success:
            return "FAILED"

        # Upload immediately after this file finishes downloading.
        upload_success = await archive.upload_to_drive(
            output_path,
            subject,
            file_type,
        )
        if not upload_success:
            return "FAILED_UPLOAD"

        async with manifest_lock:
            manifest[message_key] = {
                "filename": filename,
                "subject": subject,
                "file_type": file_type,
                "status": "downloaded_and_uploaded",
            }
            archive.save_manifest(manifest)

        return "DOWNLOADED_UPLOADED"


async def main():
    archive.logger.info("=" * 70)
    archive.logger.info("Parallel Telegram Archive Downloader")
    archive.logger.info("Parallel workers: %d", PARALLEL_WORKERS)
    archive.logger.info("Flow: parallel download -> immediate per-file upload")
    archive.logger.info("Google Drive check: DISABLED")
    archive.logger.info("=" * 70)

    archive.ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
    manifest = archive.load_manifest()
    manifest_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(PARALLEL_WORKERS)

    client = archive.TelegramClient(
        archive.StringSession(archive.TELEGRAM_SESSION),
        archive.API_ID,
        archive.API_HASH,
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

    tasks = set()

    try:
        archive.logger.info("Connecting to Telegram...")
        await client.start()
        archive.logger.info("Telegram authorization successful.")

        entity = await client.get_entity(archive.CHANNEL_ID)
        archive.logger.info("Target resolved: %s", getattr(entity, "title", str(entity)))
        archive.logger.info("Scanning messages with %d parallel workers...", PARALLEL_WORKERS)

        async for message in client.iter_messages(entity, reverse=True):
            counters["scanned"] += 1

            if archive.MAX_FILES > 0 and counters["downloaded_uploaded"] >= archive.MAX_FILES:
                archive.logger.info("MAX_FILES reached: %d", archive.MAX_FILES)
                break

            task = asyncio.create_task(
                process_parallel(
                    client,
                    message,
                    manifest,
                    manifest_lock,
                    semaphore,
                )
            )
            tasks.add(task)

            # Keep the task set bounded so a large Telegram channel does not
            # create thousands of pending asyncio tasks.
            if len(tasks) >= PARALLEL_WORKERS * 2:
                done, tasks = await asyncio.wait(
                    tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for completed in done:
                    result = await completed
                    if result == "DOWNLOADED_UPLOADED":
                        counters["downloaded_uploaded"] += 1
                    elif result == "SKIP_SUBJECT":
                        counters["skipped_subject"] += 1
                    elif result == "SKIP_MANIFEST":
                        counters["skipped_manifest"] += 1
                    elif result == "SKIP_LOCAL":
                        counters["skipped_local"] += 1
                    elif result == "SKIP_OTHER":
                        counters["skipped_other"] += 1
                    elif result == "FAILED_UPLOAD":
                        counters["failed_upload"] += 1
                        counters["failed"] += 1
                    elif result == "FAILED":
                        counters["failed"] += 1

        if tasks:
            done, _ = await asyncio.wait(tasks)
            for completed in done:
                result = await completed
                if result == "DOWNLOADED_UPLOADED":
                    counters["downloaded_uploaded"] += 1
                elif result == "SKIP_SUBJECT":
                    counters["skipped_subject"] += 1
                elif result == "SKIP_MANIFEST":
                    counters["skipped_manifest"] += 1
                elif result == "SKIP_LOCAL":
                    counters["skipped_local"] += 1
                elif result == "SKIP_OTHER":
                    counters["skipped_other"] += 1
                elif result == "FAILED_UPLOAD":
                    counters["failed_upload"] += 1
                    counters["failed"] += 1
                elif result == "FAILED":
                    counters["failed"] += 1

        archive.logger.info("=" * 70)
        archive.logger.info("PARALLEL SCAN COMPLETE")
        archive.logger.info("Messages scanned : %d", counters["scanned"])
        archive.logger.info("Downloaded+Drive : %d", counters["downloaded_uploaded"])
        archive.logger.info("No subject       : %d", counters["skipped_subject"])
        archive.logger.info("Manifest skipped : %d", counters["skipped_manifest"])
        archive.logger.info("Local skipped    : %d", counters["skipped_local"])
        archive.logger.info("Other skipped    : %d", counters["skipped_other"])
        archive.logger.info("Failed           : %d", counters["failed"])
        archive.logger.info("Upload failures  : %d", counters["failed_upload"])
        archive.logger.info("Google Drive     : parallel per-file immediate uploads")
        archive.logger.info("=" * 70)

    finally:
        if tasks:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
