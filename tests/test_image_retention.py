import os
import tempfile
import unittest
from pathlib import Path

from app.core.image_retention import ImageRetentionService, RetentionPolicy
from app.models.config import ImageRetentionConfig


class _Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


def _policy(**overrides):
    values = {
        "enabled": True,
        "max_size_bytes": 100,
        "cleanup_start_bytes": 30,
        "active_cleanup_start_bytes": 80,
        "cleanup_target_bytes": 10,
        "resume_saving_below_bytes": 30,
        "idle_after_seconds": 1800.0,
        "scan_interval_seconds": 60.0,
        "running_batch_files": 1,
        "running_work_seconds": 5.0,
        "running_sleep_seconds": 0.0,
        "idle_batch_files": 2,
        "idle_work_seconds": 5.0,
        "idle_sleep_seconds": 0.0,
        "protect_current_day": True,
    }
    values.update(overrides)
    return RetentionPolicy(**values)


def _write_image_bytes(root, day, name, size):
    folder = Path(root) / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x" * size)
    return path


class ImageRetentionTests(unittest.TestCase):
    def test_default_policy_matches_approved_limits(self):
        config = ImageRetentionConfig()
        self.assertEqual(config.max_size_gb, 100.0)
        self.assertEqual(config.cleanup_start_gb, 90.0)
        self.assertEqual(config.active_cleanup_start_gb, 97.0)
        self.assertEqual(config.cleanup_target_gb, 80.0)
        self.assertEqual(config.idle_after_minutes, 30.0)

    def test_cleanup_waits_until_thirty_minutes_without_trigger(self):
        clock = _Clock(0.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            old_image = _write_image_bytes(temp_dir, "2020_01_01", "old.jpg", 40)
            service = ImageRetentionService(temp_dir, _policy(), clock=clock)

            clock.value = 1799.0
            self.assertFalse(service.run_maintenance_cycle())
            self.assertTrue(old_image.exists())

            clock.value = 1800.0
            self.assertTrue(service.run_maintenance_cycle())
            self.assertFalse((Path(temp_dir) / "2020_01_01").exists())

    def test_valid_trigger_resets_idle_timer(self):
        clock = _Clock(1800.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            old_image = _write_image_bytes(temp_dir, "2020_01_01", "old.jpg", 40)
            service = ImageRetentionService(temp_dir, _policy(), clock=clock)
            service.notify_trigger()

            clock.value = 3599.0
            self.assertFalse(service.run_maintenance_cycle())
            self.assertTrue(old_image.exists())

            clock.value = 3600.0
            self.assertTrue(service.run_maintenance_cycle())
            self.assertFalse(old_image.exists())

    def test_oldest_folder_is_staged_and_deleted_in_bounded_batches(self):
        clock = _Clock(0.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            _write_image_bytes(temp_dir, "2020_01_01", "a.jpg", 20)
            _write_image_bytes(temp_dir, "2020_01_01", "b.jpg", 20)
            newer = _write_image_bytes(temp_dir, "2020_01_02", "c.jpg", 20)
            policy = _policy(idle_batch_files=1)
            service = ImageRetentionService(temp_dir, policy, clock=clock)
            clock.value = 2000.0

            self.assertTrue(service.run_maintenance_cycle())
            pending = Path(temp_dir) / ".delete_pending" / "2020_01_01"
            self.assertTrue(pending.exists())
            self.assertEqual(len(list(pending.glob("*.jpg"))), 1)
            self.assertTrue(newer.exists())

            self.assertTrue(service.run_maintenance_cycle())
            self.assertTrue(service.run_maintenance_cycle())
            self.assertFalse(pending.exists())
            self.assertTrue(newer.exists())

    def test_active_cleanup_and_hard_limit_suspend_then_resume_saving(self):
        clock = _Clock(0.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            for index in range(6):
                _write_image_bytes(temp_dir, "2020_01_01", f"{index}.jpg", 10)
            policy = _policy(
                max_size_bytes=50,
                cleanup_start_bytes=30,
                active_cleanup_start_bytes=40,
                cleanup_target_bytes=10,
                resume_saving_below_bytes=30,
                running_batch_files=1,
            )
            service = ImageRetentionService(temp_dir, policy, clock=clock)

            self.assertTrue(service.run_maintenance_cycle())
            self.assertTrue(service.saving_suspended)
            self.assertFalse(service.can_save_image(temp_dir))

            while service.saving_suspended:
                self.assertTrue(service.run_maintenance_cycle())

            self.assertFalse(service.saving_suspended)
            self.assertTrue(service.can_save_image(temp_dir))

    def test_unmanaged_folder_is_never_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            managed = os.path.join(temp_dir, "managed")
            other = os.path.join(temp_dir, "other")
            service = ImageRetentionService(managed, _policy())
            service._saving_suspended = True
            self.assertTrue(service.can_save_image(other))


if __name__ == "__main__":
    unittest.main()
