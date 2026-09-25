import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import app.workers.developer_worker as developer_worker_module
from app.models.result import DetectionResult
from app.workers.developer_worker import DeveloperWorker


class DeveloperWorkerTests(unittest.TestCase):
    @staticmethod
    def _write_image(path: Path, value: int):
        image = np.full((12, 16, 3), value, dtype=np.uint8)
        ok, encoded = cv2.imencode(path.suffix, image)
        assert ok
        encoded.tofile(str(path))

    def test_batch_processes_images_in_given_order_and_reports_summary(self):
        original_detect = developer_worker_module.detect_tape
        try:
            developer_worker_module.detect_tape = lambda image, config, pool: DetectionResult(
                model_name="test",
                src=image,
                dst=image,
                ret="PASS" if int(image[0, 0, 0]) < 100 else "FAIL",
                label_counts="Tape: 1",
                timecheck=1.0,
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                folder = Path(temp_dir)
                first = folder / "01.png"
                second = folder / "02.png"
                self._write_image(first, 10)
                self._write_image(second, 200)

                results = []
                summaries = []
                worker = DeveloperWorker([first, second], object(), object(), delay_ms=0)
                worker.result_ready.connect(
                    lambda path, result, index, total: results.append(
                        (Path(path).name, result.ret, index, total)
                    )
                )
                worker.batch_completed.connect(lambda *args: summaries.append(args))

                # The loop itself is independent of QThread scheduling and can be
                # exercised synchronously for a deterministic unit test.
                worker.run()

                self.assertEqual(
                    results,
                    [("01.png", "PASS", 1, 2), ("02.png", "FAIL", 2, 2)],
                )
                self.assertEqual(summaries, [(2, 1, 1, 0, False)])
        finally:
            developer_worker_module.detect_tape = original_detect


if __name__ == "__main__":
    unittest.main()
