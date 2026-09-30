
from datetime import datetime
from typing import Optional
import unicodedata

import cv2
import numpy as np

from app.core.model_pool import ModelPool
from app.models.config import AppConfig
from app.models.result import DetectionResult
from app.paths import get_model_ai_path


def _normalize_label(label) -> str:
    value = unicodedata.normalize("NFKD", str(label)).encode("ascii", "ignore").decode()
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _evaluate_detection(tape_count: int) -> bool:
    return tape_count == 1


def draw_result_badge(image: np.ndarray, result: str) -> np.ndarray:
    """Draw a high-contrast OK/NG badge in the image's top-left corner."""
    if image is None or image.size == 0:
        return image

    label = "OK" if str(result).strip().upper() in ("OK", "PASS") else "NG"
    color = (0, 170, 0) if label == "OK" else (0, 0, 220)
    image_height, image_width = image.shape[:2]
    font_scale = max(0.8, min(2.4, min(image_width, image_height) / 600.0))
    thickness = max(2, int(round(font_scale * 2)))
    padding = max(6, int(round(font_scale * 9)))
    (text_width, text_height), baseline = cv2.getTextSize(
        label,
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        thickness,
    )
    badge_width = min(image_width, text_width + padding * 2)
    badge_height = min(image_height, text_height + baseline + padding * 2)

    cv2.rectangle(
        image,
        (0, 0),
        (max(0, badge_width - 1), max(0, badge_height - 1)),
        color,
        cv2.FILLED,
    )
    cv2.putText(
        image,
        label,
        (padding, min(image_height - 1, padding + text_height)),
        cv2.FONT_HERSHEY_SIMPLEX,
        font_scale,
        (255, 255, 255),
        thickness,
        cv2.LINE_AA,
    )
    return image


def _draw_detection_boxes(
    image: np.ndarray,
    xyxy: np.ndarray,
    labels: np.ndarray,
    confidences: np.ndarray,
) -> None:
    """Draw model boxes, labels, and confidence values in-place."""
    image_height, image_width = image.shape[:2]
    thickness = max(2, int(round(min(image_width, image_height) / 500.0)))
    font_scale = max(0.5, min(0.8, min(image_width, image_height) / 900.0))

    for coordinates, label, confidence in zip(xyxy, labels, confidences):
        x1, y1, x2, y2 = (int(round(float(value))) for value in coordinates)
        x1 = min(max(x1, 0), image_width - 1)
        y1 = min(max(y1, 0), image_height - 1)
        x2 = min(max(x2, 0), image_width - 1)
        y2 = min(max(y2, 0), image_height - 1)
        if x2 <= x1 or y2 <= y1:
            continue

        normalized_label = _normalize_label(label)
        color = (0, 200, 0) if normalized_label == "tape" else (0, 140, 255)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)

        caption = f"{label} {float(confidence):.2f}"
        (text_width, text_height), baseline = cv2.getTextSize(
            caption, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        padding = 4
        if y1 >= text_height + baseline + padding * 2:
            background_top = y1 - text_height - baseline - padding * 2
            background_bottom = y1
            text_y = y1 - baseline - padding
        else:
            background_top = y1
            background_bottom = min(
                image_height - 1, y1 + text_height + baseline + padding * 2
            )
            text_y = min(image_height - baseline - 1, y1 + text_height + padding)
        background_right = min(image_width - 1, x1 + text_width + padding * 2)
        cv2.rectangle(
            image,
            (x1, background_top),
            (background_right, background_bottom),
            color,
            cv2.FILLED,
        )
        cv2.putText(
            image,
            caption,
            (x1 + padding, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA,
        )

def detect_tape(
    image: np.ndarray,
    config: AppConfig,
    model_pool: ModelPool,
) -> DetectionResult:
    started_at = datetime.now()
    dst = image.copy() if image is not None and image.size else image

    try:
        if image is None or image.size == 0:
            raise ValueError("Detection requires a non-empty image.")

        image_height, image_width = image.shape[:2]
        shapes = config.shapes or {}
        if len(shapes) > 1:
            raise ValueError("Detection supports at most one ROI in config.shapes.")

        has_roi = bool(shapes)
        if has_roi:
            roi_name, roi = next(iter(shapes.items()))
            roi_x, roi_y, roi_width, roi_height = map(int, roi)
            if roi_width <= 0 or roi_height <= 0:
                raise ValueError(f"ROI for '{roi_name}' must have positive width and height.")

            roi_x = max(0, roi_x)
            roi_y = max(0, roi_y)
            roi_x2 = min(roi_x + roi_width, image_width)
            roi_y2 = min(roi_y + roi_height, image_height)
            if roi_x2 - roi_x <= 0 or roi_y2 - roi_y <= 0:
                raise ValueError(f"ROI for '{roi_name}' is completely outside the image.")
        else:
            roi_name = "Full Image"
            roi_x, roi_y = 0, 0
            roi_x2, roi_y2 = image_width, image_height

        roi_image = np.ascontiguousarray(image[roi_y:roi_y2, roi_x:roi_x2])

        model_path = get_model_ai_path(config.model_ai.model_name)
        model = model_pool.get(model_path)
        prediction = model.predict(
            source=roi_image,
            conf=config.model_ai.conf,
            iou=config.model_ai.iou,
            max_det=config.model_ai.max_det,
            verbose=False,
        )

        dst = image.copy()
        if has_roi:
            cv2.rectangle(dst, (roi_x, roi_y), (roi_x2, roi_y2), (0, 180, 0), 2)
            cv2.putText(dst, f"ROI: {roi_name}", (roi_x, max(20, roi_y - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 180, 0), 2, cv2.LINE_AA)

        result = prediction[0]
        boxes = result.boxes
        if boxes is None:
            # Ultralytics OBB results expose axis-aligned bounds through
            # ``result.obb.xyxy`` instead of ``result.boxes``.
            boxes = getattr(result, "obb", None)
        xyxy = np.empty((0, 4), dtype=np.float32)
        classes = np.empty((0,), dtype=np.int32)
        confidences = np.empty((0,), dtype=np.float32)
        labels = np.empty((0,), dtype=object)

        if boxes is not None and len(boxes):
            xyxy = boxes.xyxy.cpu().numpy()
            xyxy += np.array((roi_x, roi_y, roi_x, roi_y), dtype=xyxy.dtype)
            classes = boxes.cls.cpu().numpy().astype(np.int32)
            confidences = boxes.conf.cpu().numpy()

            # Ultralytics normally applies max_det after NMS. Keep the same
            # guarantee in our post-processing so only the N highest-confidence
            # results are counted and drawn, regardless of the model backend.
            max_det = config.model_ai.max_det
            if len(confidences) > max_det:
                keep_indices = np.argsort(-confidences, kind="stable")[:max_det]
                xyxy = xyxy[keep_indices]
                classes = classes[keep_indices]
                confidences = confidences[keep_indices]

            names = getattr(result, "names", getattr(model, "names", {}))
            labels = np.asarray(
                [str(names.get(int(c), c) if isinstance(names, dict) else names[int(c)]).lower()
                for c in classes]
            )

            _draw_detection_boxes(dst, xyxy, labels, confidences)

        normalized_labels = np.asarray([_normalize_label(label) for label in labels])

        tape_indices = np.flatnonzero(normalized_labels == "tape")
        tape_count = len(tape_indices)
        is_ok = _evaluate_detection(tape_count)
        draw_result_badge(dst, "OK" if is_ok else "NG")

        label_counts = f"Tape: {tape_count}"
        duration_ms = (datetime.now() - started_at).total_seconds() * 1000.0

        return DetectionResult(
            model_name=config.model_ai.model_name,
            src=image,
            dst=dst,
            ret="PASS" if is_ok else "FAIL",
            label_counts=label_counts,
            timecheck=round(duration_ms, 2),
        )

    except Exception as e:
        duration_ms = (datetime.now() - started_at).total_seconds() * 1000.0
        draw_result_badge(dst, "NG")
        return DetectionResult(
            model_name=config.model_ai.model_name,
            src=image,
            dst=dst,
            ret="FAIL",
            label_counts="NG",
            timecheck=round(duration_ms, 2),
            error=str(e),
        )
