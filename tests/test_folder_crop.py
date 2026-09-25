import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.core.folder_crop import CropRegion, crop_folder, list_images, read_image


class FolderCropTests(unittest.TestCase):
    @staticmethod
    def _write_image(path: Path, width: int, height: int):
        image = np.arange(height * width * 3, dtype=np.uint8).reshape(height, width, 3)
        ok, encoded = cv2.imencode(path.suffix, image)
        assert ok
        encoded.tofile(str(path))

    def test_each_roi_is_cropped_from_each_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            self._write_image(folder / "one.jpg", 100, 80)
            self._write_image(folder / "two.png", 100, 80)
            (folder / "ignore.txt").write_text("not an image", encoding="utf-8")

            result = crop_folder(
                folder,
                [
                    CropRegion("top", 10, 5, 20, 15),
                    CropRegion("bottom", 40, 30, 25, 20),
                ],
                (100, 80),
            )

            self.assertEqual(result.source_images, 2)
            self.assertEqual(result.saved_images, 4)
            self.assertEqual(result.failed_images, 0)
            outputs = list_images(result.output_folder)
            self.assertEqual(len(outputs), 4)
            self.assertEqual(read_image(outputs[0]).shape[:2], (20, 25))
            self.assertEqual(read_image(outputs[1]).shape[:2], (15, 20))

    def test_roi_scales_for_images_with_different_resolution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            self._write_image(folder / "large.png", 200, 100)

            result = crop_folder(
                folder,
                [CropRegion("ROI 1", 10, 5, 20, 10)],
                (100, 50),
            )

            output = list_images(result.output_folder)[0]
            self.assertEqual(read_image(output).shape[:2], (20, 40))

    def test_recursive_image_listing_is_stable_and_optional(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            folder = Path(temp_dir)
            nested = folder / "nested"
            nested.mkdir()
            self._write_image(folder / "root.png", 10, 10)
            self._write_image(nested / "child.jpg", 10, 10)
            (nested / "ignore.txt").write_text("not an image", encoding="utf-8")

            self.assertEqual([path.name for path in list_images(folder)], ["root.png"])
            self.assertEqual(
                [str(path.relative_to(folder)) for path in list_images(folder, recursive=True)],
                [str(Path("nested") / "child.jpg"), "root.png"],
            )


if __name__ == "__main__":
    unittest.main()
