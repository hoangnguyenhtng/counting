import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import numpy as np

from app.core.image_storage import save_captured_image
from data.logger import Logger
from app.metadata import APP_DISPLAY_NAME, APP_VERSION
from app.security import teaching_password_is_valid


class StorageTests(unittest.TestCase):
    def test_project_metadata_and_default_teaching_password(self):
        self.assertEqual(APP_VERSION, "1.1.0")
        self.assertIn("Tray Counter", APP_DISPLAY_NAME)
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(teaching_password_is_valid("1"))
            self.assertFalse(teaching_password_is_valid("2"))

    def test_capture_is_saved_below_daily_image_capture_folder(self):
        image = np.zeros((8, 8, 3), dtype=np.uint8)
        captured_at = datetime(2026, 8, 24, 15, 16, 17, 123456)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = save_captured_image(image, captured_at, temp_dir)

            self.assertEqual(
                path,
                os.path.join(
                    temp_dir,
                    "2026_08_24",
                    "Capture_15_16_17_123456.jpg",
                ),
            )
            self.assertTrue(os.path.isfile(path))

    def test_vision_log_is_valid_daily_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("data.logger.get_abs_path", side_effect=lambda path: os.path.join(temp_dir, path)):
                logger = Logger("StorageTestJson")
                logger.info("kiểm tra JSON")
                logger.warning("bản ghi thứ hai")

            log_path = os.path.join(
                temp_dir,
                "Log_Vision",
                datetime.now().strftime("%Y_%m_%d.json"),
            )
            with open(log_path, "r", encoding="utf-8") as source:
                records = json.load(source)

            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["logger"], "StorageTestJson")
            self.assertEqual(records[0]["level"], "INFO")
            self.assertEqual(records[0]["message"], "kiểm tra JSON")
            self.assertEqual(records[1]["level"], "WARNING")

    def test_auto_result_image_is_grouped_by_day(self):
        image = np.zeros((8, 8, 3), dtype=np.uint8)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("data.logger.get_abs_path", side_effect=lambda path: path if os.path.isabs(path) else os.path.join(temp_dir, path)):
                logger = Logger("StorageTestImage")
                saved_path = logger.log_image(
                    "model",
                    image,
                    os.path.join(temp_dir, "Log_Vision", "LogImage"),
                    ret="OK",
                )

            expected_folder = os.path.join(
                temp_dir,
                "Log_Vision",
                "LogImage",
                datetime.now().strftime("%Y_%m_%d"),
                "OK",
            )
            self.assertEqual(os.path.dirname(saved_path), expected_folder.replace("\\", "/"))
            self.assertTrue(os.path.isfile(saved_path))

    def test_auto_result_images_are_separated_into_ok_and_ng_folders(self):
        image = np.zeros((8, 8, 3), dtype=np.uint8)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("data.logger.get_abs_path", side_effect=lambda path: path if os.path.isabs(path) else os.path.join(temp_dir, path)):
                logger = Logger("StorageTestResultFolders")
                image_root = os.path.join(temp_dir, "Log_Vision", "LogImage")
                ok_path = logger.log_image("model", image, image_root, ret="OK")
                ng_path = logger.log_image("model", image, image_root, ret="NG")

            day_folder = datetime.now().strftime("%Y_%m_%d")
            self.assertEqual(os.path.basename(os.path.dirname(ok_path)), "OK")
            self.assertEqual(os.path.basename(os.path.dirname(ng_path)), "NG")
            self.assertEqual(os.path.basename(os.path.dirname(os.path.dirname(ok_path))), day_folder)
            self.assertTrue(os.path.isfile(ok_path))
            self.assertTrue(os.path.isfile(ng_path))

    def test_hard_limit_skips_image_write_without_failing_inspection_logger(self):
        class SuspendedRetention:
            saving_suspended = True

            @staticmethod
            def manages(folder):
                return True

            @staticmethod
            def can_save_image(folder):
                return False

        image = np.zeros((8, 8, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("data.logger.get_abs_path", side_effect=lambda path: path if os.path.isabs(path) else os.path.join(temp_dir, path)):
                logger = Logger("StorageTestHardLimit")
                logger.set_image_retention(SuspendedRetention())
                image_root = os.path.join(temp_dir, "Log_Vision", "LogImage")
                saved_path = logger.log_image("model", image, image_root, ret="OK")

            self.assertEqual(saved_path, "")
            self.assertFalse(os.path.exists(image_root))


if __name__ == "__main__":
    unittest.main()
