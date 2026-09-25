"""Throttled background retention for date-partitioned inspection images."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from uuid import uuid4

from app.models.config import ImageRetentionConfig


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
GIB = 1024 ** 3


@dataclass(frozen=True)
class RetentionPolicy:
    enabled: bool
    max_size_bytes: int
    cleanup_start_bytes: int
    active_cleanup_start_bytes: int
    cleanup_target_bytes: int
    resume_saving_below_bytes: int
    idle_after_seconds: float
    scan_interval_seconds: float
    running_batch_files: int
    running_work_seconds: float
    running_sleep_seconds: float
    idle_batch_files: int
    idle_work_seconds: float
    idle_sleep_seconds: float
    protect_current_day: bool

    @classmethod
    def from_config(cls, config: ImageRetentionConfig) -> "RetentionPolicy":
        return cls(
            enabled=config.enabled,
            max_size_bytes=int(config.max_size_gb * GIB),
            cleanup_start_bytes=int(config.cleanup_start_gb * GIB),
            active_cleanup_start_bytes=int(config.active_cleanup_start_gb * GIB),
            cleanup_target_bytes=int(config.cleanup_target_gb * GIB),
            resume_saving_below_bytes=int(config.resume_saving_below_gb * GIB),
            idle_after_seconds=config.idle_after_minutes * 60.0,
            scan_interval_seconds=config.scan_interval_seconds,
            running_batch_files=config.running_batch_files,
            running_work_seconds=config.running_work_time_ms / 1000.0,
            running_sleep_seconds=config.running_sleep_ms / 1000.0,
            idle_batch_files=config.idle_batch_files,
            idle_work_seconds=config.idle_work_time_ms / 1000.0,
            idle_sleep_seconds=config.idle_sleep_ms / 1000.0,
            protect_current_day=config.protect_current_day,
        )


@dataclass(frozen=True)
class CleanupBatchResult:
    deleted_files: int = 0
    freed_bytes: int = 0
    folder_removed: bool = False
    errors: int = 0


class ImageRetentionService:
    """Delete old daily folders incrementally without blocking image writes.

    Folder scans and deletions happen only on this service's daemon thread. A
    valid inspection trigger resets the idle timer. Cleanup is aggressive after
    the configured idle period and deliberately throttled while production is
    active.
    """

    def __init__(
        self,
        root_dir: str,
        policy: RetentionPolicy,
        log_callback: Optional[Callable[[str, str], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._lock = threading.RLock()
        self._wake_event = threading.Event()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._clock = clock
        self._log_callback = log_callback

        self._root = Path(root_dir).resolve()
        self._policy = policy
        self._last_trigger_at = self._clock()
        self._last_scan_at = 0.0
        self._known_size_bytes: Optional[int] = None
        self._saving_suspended = False
        self._idle_cleanup_pending = False
        self._active_cleanup_pending = False
        self._last_reported_state: Optional[str] = None

    @property
    def root_dir(self) -> str:
        with self._lock:
            return str(self._root)

    @property
    def known_size_bytes(self) -> Optional[int]:
        with self._lock:
            return self._known_size_bytes

    @property
    def saving_suspended(self) -> bool:
        with self._lock:
            return self._saving_suspended

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="ImageRetentionWorker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 3.0) -> bool:
        self._stop_event.set()
        self._wake_event.set()
        with self._lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def configure(self, root_dir: str, policy: RetentionPolicy) -> None:
        """Apply a new root/policy and force a background rescan."""
        with self._lock:
            self._root = Path(root_dir).resolve()
            self._policy = policy
            self._known_size_bytes = None
            self._last_scan_at = 0.0
            self._saving_suspended = False
            self._idle_cleanup_pending = False
            self._active_cleanup_pending = False
            self._last_trigger_at = self._clock()
            self._last_reported_state = None
        self._wake_event.set()

    def notify_trigger(self) -> None:
        """Record an accepted inspection trigger and leave idle cleanup mode."""
        with self._lock:
            self._last_trigger_at = self._clock()
        self._wake_event.set()

    def manages(self, folder: str) -> bool:
        try:
            return Path(folder).resolve() == Path(self.root_dir)
        except OSError:
            return False

    def can_save_image(self, folder: str) -> bool:
        if not self.manages(folder):
            return True
        with self._lock:
            return not self._policy.enabled or not self._saving_suspended

    def record_saved_image(self, image_path: str) -> None:
        """Update the cached size after a successful write and wake near limits."""
        try:
            file_size = os.path.getsize(image_path)
        except OSError:
            self._wake_event.set()
            return

        should_wake = False
        became_suspended = False
        archive_size_bytes = None
        with self._lock:
            if not self._is_within_root(Path(image_path), self._root):
                return
            if self._known_size_bytes is not None:
                self._known_size_bytes += file_size
                archive_size_bytes = self._known_size_bytes
                if self._known_size_bytes >= self._policy.max_size_bytes:
                    became_suspended = not self._saving_suspended
                    self._saving_suspended = True
                    self._active_cleanup_pending = True
                    should_wake = True
                elif self._known_size_bytes >= self._policy.cleanup_start_bytes:
                    should_wake = True
            else:
                should_wake = True
        if should_wake:
            self._wake_event.set()
        if became_suspended:
            self._log(
                f"Inspection image archive reached {archive_size_bytes / GIB:.2f} GB. "
                "New inspection images are temporarily disabled; inspection continues.",
                "ERR",
            )

    def run_maintenance_cycle(self) -> bool:
        """Run at most one deletion batch; public to allow deterministic tests."""
        now = self._clock()
        with self._lock:
            root = self._root
            policy = self._policy
            known_size = self._known_size_bytes
            last_scan_at = self._last_scan_at
            last_trigger_at = self._last_trigger_at

        if not policy.enabled:
            return False

        if known_size is None or now - last_scan_at >= policy.scan_interval_seconds:
            known_size = self._scan_image_size(root)
            with self._lock:
                self._known_size_bytes = known_size
                self._last_scan_at = now

        idle = now - last_trigger_at >= policy.idle_after_seconds
        self._update_suspension(known_size, policy)
        with self._lock:
            saving_suspended = self._saving_suspended
            if known_size <= policy.cleanup_target_bytes:
                self._idle_cleanup_pending = False
            if known_size <= policy.resume_saving_below_bytes:
                self._active_cleanup_pending = False
            if idle and known_size >= policy.cleanup_start_bytes:
                self._idle_cleanup_pending = True
            if known_size >= policy.active_cleanup_start_bytes or saving_suspended:
                self._active_cleanup_pending = True
            idle_cleanup_pending = self._idle_cleanup_pending
            active_cleanup_pending = self._active_cleanup_pending
        pending_dir = self._oldest_pending_directory(root)
        finish_pending_while_idle = pending_dir is not None and idle

        if idle and (idle_cleanup_pending or finish_pending_while_idle):
            # A folder already moved to .delete_pending is committed for
            # deletion. Finish it during idle time even if an earlier batch
            # has already brought the archive below the target.
            mode = "idle"
            target_bytes = policy.cleanup_target_bytes if idle_cleanup_pending else 0
        elif saving_suspended:
            # Once the hard limit pauses writes, cleanup must continue even if
            # production is active and the size has fallen below the normal
            # active-cleanup threshold.
            mode = "running"
            target_bytes = policy.resume_saving_below_bytes
        elif active_cleanup_pending:
            mode = "running"
            target_bytes = policy.resume_saving_below_bytes
        else:
            self._report_state("waiting", known_size, idle)
            return False

        if known_size <= target_bytes and not finish_pending_while_idle:
            self._update_suspension(known_size, policy)
            return False

        if pending_dir is None:
            pending_dir = self._stage_oldest_daily_directory(root, policy)
            if pending_dir is None:
                self._report_state("no_candidate", known_size, idle)
                return False

        result = self._delete_batch(pending_dir, mode, policy)
        if result.deleted_files or result.folder_removed:
            with self._lock:
                current_size = self._known_size_bytes
                if current_size is not None:
                    self._known_size_bytes = max(0, current_size - result.freed_bytes)
                    known_size = self._known_size_bytes
            self._update_suspension(known_size, policy)
            if result.folder_removed:
                self._log(
                    f"Image retention removed old folder '{pending_dir.name}'. "
                    f"Archive size is now approximately {known_size / GIB:.2f} GB.",
                    "INF",
                )
            return True
        return False

    def _run(self) -> None:
        self._log("Image retention worker started.", "INF")
        while not self._stop_event.is_set():
            did_work = False
            mode = "running"
            try:
                did_work = self.run_maintenance_cycle()
                with self._lock:
                    size = self._known_size_bytes or 0
                    policy = self._policy
                    idle = self._clock() - self._last_trigger_at >= policy.idle_after_seconds
                    mode = "idle" if idle and size >= policy.cleanup_start_bytes else "running"
            except Exception as exc:
                self._log(f"Image retention worker error: {exc}", "ERR")

            if did_work:
                delay = (
                    policy.idle_sleep_seconds
                    if mode == "idle"
                    else policy.running_sleep_seconds
                )
            else:
                with self._lock:
                    until_idle = max(
                        0.0,
                        self._policy.idle_after_seconds
                        - (self._clock() - self._last_trigger_at),
                    )
                    delay = min(
                        self._policy.scan_interval_seconds,
                        until_idle if until_idle > 0 else self._policy.scan_interval_seconds,
                    )

            self._wake_event.wait(delay)
            self._wake_event.clear()
        self._log("Image retention worker stopped.", "INF")

    def _update_suspension(self, size_bytes: int, policy: RetentionPolicy) -> None:
        with self._lock:
            was_suspended = self._saving_suspended
            if size_bytes >= policy.max_size_bytes:
                self._saving_suspended = True
            elif size_bytes <= policy.resume_saving_below_bytes:
                self._saving_suspended = False
            is_suspended = self._saving_suspended

        if is_suspended and not was_suspended:
            self._log(
                f"Inspection image archive reached {size_bytes / GIB:.2f} GB. "
                "New inspection images are temporarily disabled; inspection continues.",
                "ERR",
            )
        elif was_suspended and not is_suspended:
            self._log(
                f"Inspection image archive is below {size_bytes / GIB:.2f} GB. "
                "Image saving resumed.",
                "INF",
            )

    def _scan_image_size(self, root: Path) -> int:
        total = 0
        if not root.exists():
            return 0
        for current_root, dir_names, file_names in os.walk(root, followlinks=False):
            dir_names[:] = [
                name
                for name in dir_names
                if not (Path(current_root) / name).is_symlink()
            ]
            for file_name in file_names:
                path = Path(current_root) / file_name
                if path.suffix.lower() not in IMAGE_EXTENSIONS or path.is_symlink():
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def _oldest_pending_directory(self, root: Path) -> Optional[Path]:
        pending_root = root / ".delete_pending"
        try:
            candidates = [
                path
                for path in pending_root.iterdir()
                if path.is_dir() and not path.is_symlink()
            ]
        except OSError:
            return None
        return min(candidates, key=self._directory_sort_key, default=None)

    def _stage_oldest_daily_directory(
        self,
        root: Path,
        policy: RetentionPolicy,
    ) -> Optional[Path]:
        try:
            children = list(root.iterdir())
        except OSError:
            return None

        today = datetime.now().date()
        candidates = []
        for path in children:
            if not path.is_dir() or path.is_symlink() or path.name == ".delete_pending":
                continue
            folder_date = self._parse_folder_date(path.name)
            if folder_date is None:
                continue
            if policy.protect_current_day and folder_date >= today:
                continue
            candidates.append((folder_date, path))

        if not candidates:
            return None

        _, source = min(candidates, key=lambda item: (item[0], item[1].name))
        pending_root = root / ".delete_pending"
        pending_root.mkdir(parents=True, exist_ok=True)
        destination = pending_root / source.name
        if destination.exists():
            destination = pending_root / f"{source.name}_{uuid4().hex[:8]}"
        try:
            source.rename(destination)
        except OSError as exc:
            self._log(f"Cannot stage old image folder '{source}': {exc}", "WRN")
            return None
        self._log(f"Image retention queued old folder '{source.name}' for paced deletion.", "INF")
        return destination

    def _delete_batch(
        self,
        pending_dir: Path,
        mode: str,
        policy: RetentionPolicy,
    ) -> CleanupBatchResult:
        if not self._is_within_root(pending_dir, self._root / ".delete_pending"):
            self._log(f"Refusing to delete outside retention root: {pending_dir}", "ERR")
            return CleanupBatchResult(errors=1)

        max_files = policy.idle_batch_files if mode == "idle" else policy.running_batch_files
        max_seconds = policy.idle_work_seconds if mode == "idle" else policy.running_work_seconds
        started_at = self._clock()
        deleted_files = 0
        freed_bytes = 0
        errors = 0

        try:
            walker = os.walk(pending_dir, topdown=False, followlinks=False)
            for current_root, dir_names, file_names in walker:
                for file_name in sorted(file_names):
                    path = Path(current_root) / file_name
                    if path.is_symlink() or path.suffix.lower() not in IMAGE_EXTENSIONS:
                        continue
                    try:
                        file_size = path.stat().st_size
                        path.unlink()
                        deleted_files += 1
                        freed_bytes += file_size
                    except OSError as exc:
                        errors += 1
                        self._log(f"Cannot delete old image '{path}': {exc}", "WRN")

                    if deleted_files >= max_files or self._clock() - started_at >= max_seconds:
                        return CleanupBatchResult(deleted_files, freed_bytes, False, errors)

                for dir_name in dir_names:
                    child = Path(current_root) / dir_name
                    if child.is_symlink():
                        continue
                    try:
                        child.rmdir()
                    except OSError:
                        pass
        except OSError as exc:
            self._log(f"Cannot enumerate old image folder '{pending_dir}': {exc}", "WRN")
            return CleanupBatchResult(deleted_files, freed_bytes, False, errors + 1)

        folder_removed = False
        try:
            pending_dir.rmdir()
            folder_removed = True
        except OSError:
            # A locked or non-image file remains. It will be retried without
            # blocking the production path.
            pass
        return CleanupBatchResult(deleted_files, freed_bytes, folder_removed, errors)

    def _report_state(self, state: str, size_bytes: int, idle: bool) -> None:
        with self._lock:
            if state == self._last_reported_state:
                return
            self._last_reported_state = state
        if state == "no_candidate":
            self._log(
                f"Image retention cannot reclaim {size_bytes / GIB:.2f} GB because no "
                "eligible old daily folder exists.",
                "WRN",
            )

    def _log(self, message: str, level: str) -> None:
        if self._log_callback is None:
            return
        try:
            self._log_callback(message, level)
        except Exception:
            pass

    @staticmethod
    def _parse_folder_date(name: str):
        try:
            return datetime.strptime(name[:10], "%Y_%m_%d").date()
        except (TypeError, ValueError):
            return None

    @classmethod
    def _directory_sort_key(cls, path: Path):
        folder_date = cls._parse_folder_date(path.name)
        return (folder_date or datetime.max.date(), path.name)

    @staticmethod
    def _is_within_root(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            return False
