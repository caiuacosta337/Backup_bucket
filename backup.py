from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import smtplib
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any
from zipfile import ZIP_DEFLATED, ZipFile

import boto3
import yaml
from botocore.exceptions import BotoCoreError

if TYPE_CHECKING:
    from watchdog.events import FileSystemEvent

try:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except ImportError:
    FileSystemEventHandler = object
    Observer = None


EXIT_SUCCESS = 0
EXIT_PARTIAL_FAILURE = 1
EXIT_FATAL = 2


@dataclass
class BackupConfig:
    source_path: Path
    bucket: str
    region: str
    retention_days: int
    compress: bool
    s3_prefix: str
    log_file: Path
    state_file: Path
    endpoint_url: str | None
    watch_mode: str
    watch_interval_seconds: int
    watch_debounce_seconds: int
    email: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backup changed files to S3")
    parser.add_argument("--config", default="config.yml", help="Path to YAML configuration file")
    parser.add_argument(
        "--watch-interval",
        type=int,
        default=None,
        help="Override config and rerun backup every N seconds",
    )
    parser.add_argument(
        "--watch-mode",
        choices=("poll", "events"),
        default=None,
        help="Override config watch mode: poll or events",
    )
    return parser.parse_args()


def load_config(config_path: Path, watch_interval_override: int | None, watch_mode_override: str | None) -> BackupConfig:
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    watch_interval_seconds = raw.get("watch_interval_seconds", 0)
    if watch_interval_override is not None:
        watch_interval_seconds = watch_interval_override
    watch_mode = str(raw.get("watch_mode", "poll")).lower()
    if watch_mode_override is not None:
        watch_mode = watch_mode_override

    config = BackupConfig(
        source_path=Path(raw["source_path"]),
        bucket=str(raw["bucket"]),
        region=str(raw.get("region") or os.getenv("AWS_DEFAULT_REGION") or "sa-east-1"),
        retention_days=int(raw.get("retention_days", 30)),
        compress=bool(raw.get("compress", False)),
        s3_prefix=str(raw.get("s3_prefix", "")).strip("/"),
        log_file=Path(raw.get("log_file", "backup.log")),
        state_file=Path(raw.get("state_file", "backup_state.json")),
        endpoint_url=raw.get("endpoint_url"),
        watch_mode=watch_mode,
        watch_interval_seconds=int(watch_interval_seconds),
        watch_debounce_seconds=int(raw.get("watch_debounce_seconds", 10)),
        email=raw.get("email") or {},
    )

    if config.watch_mode not in {"poll", "events"}:
        raise ValueError(f"Unsupported watch_mode: {config.watch_mode}")

    if not config.source_path.exists():
        raise FileNotFoundError(f"Source path not found: {config.source_path}")
    if not config.source_path.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {config.source_path}")

    config.log_file.parent.mkdir(parents=True, exist_ok=True)
    config.state_file.parent.mkdir(parents=True, exist_ok=True)

    return config


class BackupEventHandler(FileSystemEventHandler):
    def __init__(self, trigger: Event, logger: logging.Logger) -> None:
        self.trigger = trigger
        self.logger = logger

    def on_any_event(self, event: Any) -> None:
        if getattr(event, "is_directory", False):
            return
        self.logger.info("Detected filesystem change: %s %s", getattr(event, "event_type", "unknown"), getattr(event, "src_path", ""))
        self.trigger.set()


def setup_logging(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("backup")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


def load_state(state_file: Path) -> dict[str, str]:
    if not state_file.exists():
        return {}

    with state_file.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_state(state_file: Path, state: dict[str, str]) -> None:
    temp_file = state_file.with_suffix(state_file.suffix + ".tmp")
    with temp_file.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
    temp_file.replace(state_file)


def build_s3_client(config: BackupConfig):
    session = boto3.session.Session(region_name=config.region)
    return session.client("s3", endpoint_url=config.endpoint_url)


def compute_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_file_map(source_path: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in source_path.rglob("*"):
        if path.is_file():
            files[str(path.relative_to(source_path)).replace("\\", "/")] = path
    return files


def changed_files(source_path: Path, state: dict[str, str], logger: logging.Logger) -> tuple[list[tuple[str, Path, str]], list[str]]:
    changed: list[tuple[str, Path, str]] = []
    failures: list[str] = []

    for relative_path, full_path in sorted(relative_file_map(source_path).items()):
        try:
            file_hash = compute_sha256(full_path)
        except OSError as exc:
            message = f"Failed to hash {relative_path}: {exc}"
            logger.error(message)
            failures.append(message)
            continue

        if state.get(relative_path) != file_hash:
            changed.append((relative_path, full_path, file_hash))

    return changed, failures


def prefixed_key(prefix: str, key: str) -> str:
    return f"{prefix}/{key}" if prefix else key


def upload_individual_files(
    s3_client,
    config: BackupConfig,
    changed: list[tuple[str, Path, str]],
    logger: logging.Logger,
) -> tuple[int, list[str], dict[str, str]]:
    date_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    uploaded = 0
    failures: list[str] = []
    updated_state: dict[str, str] = {}

    for relative_path, full_path, file_hash in changed:
        key = prefixed_key(config.s3_prefix, f"{date_prefix}/{relative_path}")
        try:
            s3_client.upload_file(str(full_path), config.bucket, key)
            logger.info("Uploaded %s to s3://%s/%s", relative_path, config.bucket, key)
            uploaded += 1
            updated_state[relative_path] = file_hash
        except (OSError, BotoCoreError) as exc:
            message = f"Failed to upload {relative_path}: {exc}"
            logger.error(message)
            failures.append(message)

    return uploaded, failures, updated_state


def upload_zip_archive(
    s3_client,
    config: BackupConfig,
    changed: list[tuple[str, Path, str]],
    logger: logging.Logger,
) -> tuple[int, list[str], dict[str, str]]:
    timestamp = datetime.now(timezone.utc)
    date_prefix = timestamp.strftime("%Y-%m-%d")
    archive_name = f"backup-{timestamp.strftime('%Y%m%d-%H%M%S')}.zip"
    key = prefixed_key(config.s3_prefix, f"{date_prefix}/{archive_name}")
    failures: list[str] = []
    updated_state: dict[str, str] = {}

    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as temp_handle:
            temp_path = Path(temp_handle.name)

        with ZipFile(temp_path, "w", compression=ZIP_DEFLATED) as archive:
            for relative_path, full_path, file_hash in changed:
                try:
                    archive.write(full_path, arcname=relative_path)
                    updated_state[relative_path] = file_hash
                except OSError as exc:
                    message = f"Failed to archive {relative_path}: {exc}"
                    logger.error(message)
                    failures.append(message)

        if not updated_state:
            temp_path.unlink(missing_ok=True)
            return 0, failures, {}

        s3_client.upload_file(str(temp_path), config.bucket, key)
        logger.info("Uploaded archive to s3://%s/%s", config.bucket, key)
        temp_path.unlink(missing_ok=True)
        return len(updated_state), failures, updated_state
    except (OSError, BotoCoreError) as exc:
        message = f"Failed to upload ZIP archive: {exc}"
        logger.error(message)
        failures.append(message)
        return 0, failures, {}


def cleanup_retention(s3_client, config: BackupConfig, logger: logging.Logger) -> list[str]:
    failures: list[str] = []
    threshold_date = (datetime.now(timezone.utc) - timedelta(days=config.retention_days)).date()
    list_prefix = f"{config.s3_prefix}/" if config.s3_prefix else ""

    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config.bucket, Prefix=list_prefix, Delimiter="/"):
            for common_prefix in page.get("CommonPrefixes", []):
                prefix = common_prefix["Prefix"]
                relative_prefix = prefix[len(list_prefix):].strip("/") if list_prefix else prefix.strip("/")
                top_folder = relative_prefix.split("/", 1)[0]

                try:
                    folder_date = datetime.strptime(top_folder, "%Y-%m-%d").date()
                except ValueError:
                    continue

                if folder_date >= threshold_date:
                    continue

                logger.info("Deleting expired backup prefix %s", prefix)
                delete_prefix_contents(s3_client, config.bucket, prefix)
    except BotoCoreError as exc:
        message = f"Failed to enforce retention: {exc}"
        logger.error(message)
        failures.append(message)

    return failures


def delete_prefix_contents(s3_client, bucket: str, prefix: str) -> None:
    paginator = s3_client.get_paginator("list_objects_v2")
    keys_to_delete: list[dict[str, str]] = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for entry in page.get("Contents", []):
            keys_to_delete.append({"Key": entry["Key"]})
            if len(keys_to_delete) == 1000:
                s3_client.delete_objects(Bucket=bucket, Delete={"Objects": keys_to_delete})
                keys_to_delete.clear()

    if keys_to_delete:
        s3_client.delete_objects(Bucket=bucket, Delete={"Objects": keys_to_delete})


def send_email_notification(config: BackupConfig, subject: str, body: str, logger: logging.Logger) -> None:
    email_config = config.email
    if not email_config.get("enabled"):
        return

    recipients = email_config.get("to") or []
    if not recipients:
        logger.warning("Email notification enabled but no recipients configured")
        return

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_config["from"]
    message["To"] = ", ".join(recipients)
    message.set_content(body)

    try:
        with smtplib.SMTP(email_config["host"], int(email_config.get("port", 25))) as smtp:
            if email_config.get("use_tls", True):
                smtp.starttls()
            username = email_config.get("username")
            password = email_config.get("password")
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    except OSError:
        logger.exception("Failed to send email notification")


def verify_bucket_access(s3_client, config: BackupConfig) -> None:
    s3_client.head_bucket(Bucket=config.bucket)


def run_backup_cycle(config: BackupConfig, logger: logging.Logger) -> int:
    start_time = time.monotonic()
    logger.info("Starting backup cycle")

    try:
        s3_client = build_s3_client(config)
        verify_bucket_access(s3_client, config)
    except BotoCoreError:
        logger.exception("Cannot reach bucket %s", config.bucket)
        return EXIT_FATAL

    state = load_state(config.state_file)
    changed, failures = changed_files(config.source_path, state, logger)

    if not changed:
        logger.info("No new or modified files detected")
    elif config.compress:
        uploaded, upload_failures, updated_state = upload_zip_archive(s3_client, config, changed, logger)
        failures.extend(upload_failures)
        state.update(updated_state)
        logger.info("Uploaded %s changed files as archive", uploaded)
    else:
        uploaded, upload_failures, updated_state = upload_individual_files(s3_client, config, changed, logger)
        failures.extend(upload_failures)
        state.update(updated_state)
        logger.info("Uploaded %s changed files", uploaded)

    retention_failures = cleanup_retention(s3_client, config, logger)
    failures.extend(retention_failures)
    save_state(config.state_file, state)

    duration = round(time.monotonic() - start_time, 2)
    if failures:
        logger.warning("Backup completed with %s failure(s) in %ss", len(failures), duration)
        send_email_notification(
            config,
            "Backup Status: Partial Failure",
            f"Backup completed with failures.\nFailures: {len(failures)}\nDuration: {duration}s\nSee log: {config.log_file}",
            logger,
        )
        return EXIT_PARTIAL_FAILURE

    logger.info("Backup completed successfully in %ss", duration)
    send_email_notification(
        config,
        "Backup Status: Success",
        f"Backup completed successfully.\nUploaded files: {len(changed)}\nFailures: 0\nDuration: {duration}s",
        logger,
    )
    return EXIT_SUCCESS


def run_poll_watch_loop(config: BackupConfig, logger: logging.Logger) -> int:
    logger.info("Poll watch mode enabled with interval %s seconds", config.watch_interval_seconds)
    final_exit_code = EXIT_SUCCESS
    while True:
        cycle_exit_code = run_backup_cycle(config, logger)
        if cycle_exit_code > final_exit_code:
            final_exit_code = cycle_exit_code
        time.sleep(config.watch_interval_seconds)


def wait_for_quiet_period(trigger: Event, debounce_seconds: int) -> None:
    while True:
        trigger.clear()
        time.sleep(debounce_seconds)
        if not trigger.is_set():
            return


def run_event_watch_loop(config: BackupConfig, logger: logging.Logger) -> int:
    if Observer is None:
        logger.error("watchdog is not installed. Install requirements before using watch_mode=events")
        return EXIT_FATAL

    trigger = Event()
    observer = Observer()
    observer.schedule(BackupEventHandler(trigger, logger), str(config.source_path), recursive=True)
    observer.start()

    logger.info("Event watch mode enabled with debounce %s seconds", config.watch_debounce_seconds)
    final_exit_code = run_backup_cycle(config, logger)

    try:
        while True:
            trigger.wait()
            wait_for_quiet_period(trigger, config.watch_debounce_seconds)
            cycle_exit_code = run_backup_cycle(config, logger)
            if cycle_exit_code > final_exit_code:
                final_exit_code = cycle_exit_code
    except KeyboardInterrupt:
        logger.info("Stopping event watch mode")
    finally:
        observer.stop()
        observer.join()

    return final_exit_code


def main() -> int:
    args = parse_args()

    try:
        config = load_config(Path(args.config), args.watch_interval, args.watch_mode)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f"Failed to load configuration: {exc}", file=sys.stderr)
        return EXIT_FATAL

    logger = setup_logging(config.log_file)

    if config.watch_interval_seconds <= 0:
        return run_backup_cycle(config, logger)

    if config.watch_mode == "events":
        return run_event_watch_loop(config, logger)

    return run_poll_watch_loop(config, logger)


if __name__ == "__main__":
    raise SystemExit(main())