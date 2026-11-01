"""Full-frame ONNX detection with per-code or reference-embedding verification."""
import ast
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from time import perf_counter

import cv2
import numpy as np

from .models import Result
from .storage import read_image


class CodeVerifier(ABC):
    @abstractmethod
    def load(self, code):
        """Load and warm up off the GUI thread."""

    @abstractmethod
    def count_and_verify(self, img, code) -> Result:
        pass


def open_session(path, providers=None):
    import onnxruntime as ort
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    requested = providers or ["CPUExecutionProvider"]
    missing = set(requested) - set(ort.get_available_providers())
    if missing:
        raise ValueError(f"ONNX Runtime thiếu providers: {sorted(missing)}")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    return ort.InferenceSession(str(path), sess_options=options, providers=requested)


def input_spec(session, fallback_size):
    inputs = session.get_inputs()
    if len(inputs) != 1 or len(inputs[0].shape) != 4 or inputs[0].type != "tensor(float)":
        raise ValueError("Model phải có một input float32 NCHW")
    batch, channels, height, width = inputs[0].shape
    if isinstance(batch, int) and batch != 1 or isinstance(channels, int) and channels != 3:
        raise ValueError("Model phải nhận batch=1 và 3 kênh RGB")
    h = height if isinstance(height, int) and height > 0 else fallback_size
    w = width if isinstance(width, int) and width > 0 else fallback_size
    return inputs[0].name, h, w


def nms(rows, threshold):
    """Class-wise NMS retains evidence for foreign classes."""
    keep = []
    for label in np.unique(rows[:, 5]):
        indices = np.where(rows[:, 5] == label)[0]
        indices = indices[np.argsort(-rows[indices, 4])]
        while len(indices):
            selected = indices[0]
            keep.append(int(selected))
            others = indices[1:]
            if not len(others):
                break
            a, b = rows[selected, :4], rows[others, :4]
            intersection = np.maximum(0, np.minimum(a[2:], b[:, 2:]) - np.maximum(a[:2], b[:, :2])).prod(axis=1)
            union = (a[2] - a[0]) * (a[3] - a[1]) + (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]) - intersection
            indices = others[intersection / np.maximum(union, 1e-9) <= threshold]
    return rows[keep]


class Detector:
    def __init__(self, cfg, session_factory=open_session):
        self.cfg = cfg
        self.session = session_factory(cfg.model)
        self.name, self.h, self.w = input_spec(self.session, cfg.input_size)
        metadata = getattr(self.session, "get_modelmeta", lambda: None)()
        names = getattr(metadata, "custom_metadata_map", {}).get("names")
        if names:
            names = ast.literal_eval(names)
            names = [names[k] for k in sorted(names, key=int)] if isinstance(names, dict) else list(names)
            if names != list(cfg.classes):
                raise ValueError(f"Classes của model {names} khác registry {list(cfg.classes)}")
        outputs = self.session.run(None, {self.name: np.zeros((1, 3, self.h, self.w), np.float32)})
        self._decode(outputs)  # Validate the export format before accepting triggers.

    def _decode(self, outputs):
        if len(outputs) != 1:
            raise ValueError("Detector phải có một output; không hỗ trợ segmentation/OBB")
        output = np.asarray(outputs[0])
        if output.ndim != 3 or output.shape[0] != 1 or not np.isfinite(output).all():
            raise ValueError("Output detector không hợp lệ")
        if self.cfg.output_format == "yolo_raw":
            if output.shape[1] != 4 + len(self.cfg.classes):
                raise ValueError("yolo_raw yêu cầu output [1, 4+nc, N] (xywh và class scores, không objectness)")
            pred = output[0].T
            if np.any((pred[:, 4:] < 0) | (pred[:, 4:] > 1)):
                raise ValueError("Class scores phải nằm trong [0,1]")
            labels = pred[:, 4:].argmax(axis=1)
            scores = pred[np.arange(len(pred)), labels + 4]
            xyxy = np.column_stack((pred[:, :2] - pred[:, 2:4] / 2, pred[:, :2] + pred[:, 2:4] / 2))
            rows = np.column_stack((xyxy, scores, labels))
        else:
            if output.shape[2] != 6:
                raise ValueError("xyxy yêu cầu output [1, N, 6]: x1,y1,x2,y2,score,class_id")
            rows = output[0].copy()
        rows = rows[rows[:, 4] >= self.cfg.conf_low]
        if len(rows) > 10000:
            raise ValueError("Quá nhiều detections; kiểm tra model/ngưỡng")
        if len(rows) and (np.any(rows[:, 4] > 1) or np.any(rows[:, 5] != rows[:, 5].astype(int)) or
                          np.any(rows[:, 5] < 0) or np.any(rows[:, 5] >= len(self.cfg.classes)) or
                          np.any(rows[:, 2:4] <= rows[:, :2])):
            raise ValueError("Box, độ tin cậy hoặc class_id không hợp lệ")
        return nms(rows, self.cfg.iou) if len(rows) else np.empty((0, 6), np.float32)

    def detect(self, image):
        if image is None or image.ndim != 3 or image.shape[2] != 3 or not image.size:
            raise ValueError("Cần ảnh gốc BGR 3 kênh")
        height, width = image.shape[:2]
        scale = min(self.w / width, self.h / height)
        nw, nh = max(1, round(width * scale)), max(1, round(height * scale))
        left, top = (self.w - nw) // 2, (self.h - nh) // 2
        padded = np.full((self.h, self.w, 3), 114, np.uint8)
        padded[top:top + nh, left:left + nw] = cv2.resize(image, (nw, nh))
        tensor = np.ascontiguousarray(padded[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
        rows = self._decode(self.session.run(None, {self.name: tensor}))
        rows[:, [0, 2]] = np.clip((rows[:, [0, 2]] - left) / scale, 0, width)
        rows[:, [1, 3]] = np.clip((rows[:, [1, 3]] - top) / scale, 0, height)
        return rows[(rows[:, 2] > rows[:, 0]) & (rows[:, 3] > rows[:, 1])]


def tray_region(image, rows, cfg):
    height, width = image.shape[:2]
    if cfg.tray_mode == "class":
        tray_index = cfg.classes.index("tray")
        trays = rows[(rows[:, 5] == tray_index) & (rows[:, 4] >= cfg.conf)]
        if len(trays) != 1:
            raise ValueError("Phải tìm thấy đúng một tray đủ độ tin cậy trong toàn ảnh")
        x1, y1, x2, y2 = trays[0, :4]
        return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float32)
    gray = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    candidates = []
    for mode in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
        _, mask = cv2.threshold(gray, 0, 255, mode | cv2.THRESH_OTSU)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if not cfg.tray_min_area * height * width <= area < 0.98 * height * width:
                continue
            # A fully visible, approximately rectangular contour; never fall back to full frame.
            if np.any(contour[:, 0, 0] <= 1) or np.any(contour[:, 0, 0] >= width - 2) or np.any(contour[:, 0, 1] <= 1) or np.any(contour[:, 0, 1] >= height - 2):
                continue
            approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                candidates.append((area, approx.reshape(-1, 2).astype(np.float32)))
    if not candidates:
        raise ValueError("Không tìm được vùng tray; cần kiểm tra ảnh hoặc dùng lớp tray")
    return max(candidates, key=lambda c: c[0])[1]


def product_rows(image, rows, cfg):
    region = tray_region(image, rows, cfg)
    for row in rows:
        label = cfg.classes[int(row[5])]
        if label == "tray":
            continue
        center = ((float(row[0]) + float(row[2])) / 2, (float(row[1]) + float(row[3])) / 2)
        if cv2.pointPolygonTest(region, center, False) >= 0:
            yield row


class PerCodeVerifier:
    def __init__(self, cfg, session_factory=open_session):
        self.cfg, self.detector = cfg, Detector(cfg, session_factory)

    def count_and_verify(self, image):
        started = perf_counter()
        count = foreign = low = 0
        boxes = []
        for x1, y1, x2, y2, score, class_id in product_rows(image, self.detector.detect(image), self.cfg):
            name = self.cfg.classes[int(class_id)]
            if score < self.cfg.conf:
                label, low = "lowconf", low + 1
            elif name == "target":
                label, count = "target", count + 1
            else:
                label, foreign = "foreign", foreign + 1
            boxes.append((float(x1), float(y1), float(x2), float(y2), label, float(score)))
        return Result(count, foreign, low, boxes, self.cfg.version, (perf_counter() - started) * 1000)


class SharedVerifier:
    def __init__(self, cfg, session_factory=open_session):
        self.cfg, self.detector = cfg, Detector(cfg, session_factory)
        self.embedding = session_factory(cfg.embedding_model)
        self.name, self.h, self.w = input_spec(self.embedding, cfg.embedding_size)
        self.embed(np.zeros((self.h, self.w, 3), np.uint8))  # Warm up and validate.
        refs = sorted(p for p in Path(cfg.ref_dir).iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"})
        if not refs:
            raise ValueError(f"Không có ảnh sản phẩm mẫu trong {cfg.ref_dir}")
        self.references = np.stack([self.embed(read_image(p)) for p in refs])

    def embed(self, image):
        if not image.size:
            raise ValueError("Crop sản phẩm rỗng")
        rgb = cv2.resize(image, (self.w, self.h))[:, :, ::-1].astype(np.float32) / 255.0
        rgb = (rgb - np.asarray(self.cfg.embedding_mean, np.float32)) / np.asarray(self.cfg.embedding_std, np.float32)
        outputs = self.embedding.run(None, {self.name: np.ascontiguousarray(rgb.transpose(2, 0, 1)[None])})
        if len(outputs) != 1:
            raise ValueError("Embedding model phải có một output vector")
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(vector)
        if vector.size < 2 or not np.isfinite(vector).all() or norm < 1e-9:
            raise ValueError("Embedding không hợp lệ")
        return vector / norm

    def count_and_verify(self, image):
        started = perf_counter()
        count = foreign = low = 0
        boxes = []
        for x1, y1, x2, y2, score, _ in product_rows(image, self.detector.detect(image), self.cfg):
            similarity = 0.0
            if score < self.cfg.conf:
                label, low = "lowconf", low + 1
            else:
                crop = image[int(y1):max(int(y1) + 1, int(y2)), int(x1):max(int(x1) + 1, int(x2))]
                similarity = float(np.max(self.references @ self.embed(crop)))
                if similarity >= self.cfg.sim_thr + self.cfg.sim_margin:
                    label, count = "target", count + 1
                elif similarity < self.cfg.sim_thr - self.cfg.sim_margin:
                    label, foreign = "foreign", foreign + 1
                else:
                    label, low = "lowconf", low + 1
            boxes.append((float(x1), float(y1), float(x2), float(y2), label, similarity))
        return Result(count, foreign, low, boxes, self.cfg.version, (perf_counter() - started) * 1000)


class RegistryVerifier(CodeVerifier):
    """Two warmed code models, including next-code prefetch, with LRU eviction."""
    def __init__(self, registry, providers=None, session_factory=None):
        self.registry, self.cache, self.lock = registry, OrderedDict(), RLock()
        self.active = None
        self.session_factory = session_factory or (lambda path: open_session(path, providers))

    def _load(self, code, activate):
        with self.lock:
            cached = self.cache.get(code)
        if cached is None:
            cfg = self.registry.get(code)
            verifier = PerCodeVerifier if cfg.mode == "per_code" else SharedVerifier
            cached = verifier(cfg, self.session_factory)
        with self.lock:
            self.cache[code] = cached
            if activate:
                self.active = code
            self.cache.move_to_end(code)
            while len(self.cache) > 2:
                victim = next(key for key in self.cache if key != self.active)
                del self.cache[victim]

    def load(self, code):
        self._load(code, True)

    def prefetch(self, code):
        self._load(code, False)

    def count_and_verify(self, img, code):
        with self.lock:
            if code not in self.cache:
                raise ValueError(f"Model {code} chưa nạp và warm-up")
            verifier = self.cache[code]
            self.cache.move_to_end(code)
        return verifier.count_and_verify(img)
