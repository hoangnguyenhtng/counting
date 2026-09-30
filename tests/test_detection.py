import unittest

import numpy as np

from app.core.detection import detect_tape, draw_result_badge
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
    def __init__(self):
        self.source_shape = None
        self.predict_kwargs = None

    def predict(self, **kwargs):
        self.predict_kwargs = kwargs
        self.source_shape = kwargs["source"].shape
        return [_FakeResult()]


class _FakeModelPool:
    def __init__(self):
        self.model = _FakeModel()

    def get(self, path):
        return self.model


class DetectionDrawingTests(unittest.TestCase):
    def test_result_badge_uses_green_for_ok_and_red_for_ng(self):
        ok_image = draw_result_badge(np.zeros((120, 140, 3), dtype=np.uint8), "OK")
        ng_image = draw_result_badge(np.zeros((120, 140, 3), dtype=np.uint8), "NG")

        self.assertGreater(int(ok_image[2, 2, 1]), 100)
        self.assertEqual(int(ok_image[2, 2, 2]), 0)
        self.assertGreater(int(ng_image[2, 2, 2]), 100)
        self.assertEqual(int(ng_image[2, 2, 1]), 0)

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

    def test_missing_roi_runs_detection_on_the_full_image(self):
        image = np.zeros((120, 140, 3), dtype=np.uint8)
        config = AppConfig(
            shapes={},
            model_ai=ModelAIConfig(model_name="fake.pt", conf=0.25, iou=0.45),
        )
        model_pool = _FakeModelPool()

        result = detect_tape(image, config, model_pool)

        self.assertEqual(result.ret, "PASS")
        self.assertFalse(result.error)
        self.assertEqual(model_pool.model.source_shape, image.shape)
        # With no ROI offset, the Tape box remains at its model coordinates.
        self.assertGreater(int(result.dst[45, 25, 1]), 100)

    def test_max_det_keeps_only_the_highest_confidence_detection(self):
        image = np.zeros((120, 140, 3), dtype=np.uint8)
        config = AppConfig(
            shapes={},
            model_ai=ModelAIConfig(
                model_name="fake.pt",
                conf=0.25,
                iou=0.45,
                max_det=1,
            ),
        )
        model_pool = _FakeModelPool()
        # Make both returned detections Tape. The fake backend deliberately
        # ignores max_det so this also verifies our post-processing guarantee.
        model_pool.model.predict = lambda **kwargs: (
            setattr(model_pool.model, "predict_kwargs", kwargs)
            or [type("Result", (), {"boxes": _FakeBoxes(), "names": {0: "Tape", 1: "Tape"}})()]
        )

        result = detect_tape(image, config, model_pool)

        self.assertEqual(model_pool.model.predict_kwargs["max_det"], 1)
        self.assertEqual(result.label_counts, "Tape: 1")
        self.assertEqual(result.ret, "PASS")


if __name__ == "__main__":
    unittest.main()
