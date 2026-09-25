import unittest

import numpy as np

from app.core.detection import detect_tape
from app.models.config import AppConfig, ModelAIConfig


class _FakeTensor:
    def __init__(self, values):
        self.values = np.asarray(values)

    def cpu(self):
        return self

    def numpy(self):
        return self.values.copy()


class _FakeBoxes:
    def __init__(self):
        self.xyxy = _FakeTensor([[10, 15, 40, 45], [50, 20, 70, 50]])
        self.cls = _FakeTensor([0, 1])
        self.conf = _FakeTensor([0.91, 0.72])

    def __len__(self):
        return 2


class _FakeResult:
    boxes = _FakeBoxes()
    names = {0: "Tape", 1: "scratch"}


class _FakeModel:
    def predict(self, **kwargs):
        return [_FakeResult()]


class _FakeModelPool:
    def get(self, path):
        return _FakeModel()


class DetectionDrawingTests(unittest.TestCase):
    def test_detected_objects_are_drawn_at_full_image_coordinates(self):
        image = np.zeros((120, 140, 3), dtype=np.uint8)
        config = AppConfig(
            shapes={"ROI": [20, 25, 100, 80]},
            model_ai=ModelAIConfig(model_name="fake.pt", conf=0.25, iou=0.45),
        )

        result = detect_tape(image, config, _FakeModelPool())

        self.assertEqual(result.ret, "PASS")
        self.assertEqual(result.label_counts, "Tape: 1")
        # Local Tape box [10, 15, 40, 45] is translated by ROI origin [20, 25].
        self.assertGreater(int(result.dst[70, 45, 1]), 100)
        # The non-Tape class receives the orange box color.
        self.assertGreater(int(result.dst[75, 80, 2]), 150)
        self.assertFalse(np.array_equal(result.dst, image))


if __name__ == "__main__":
    unittest.main()
