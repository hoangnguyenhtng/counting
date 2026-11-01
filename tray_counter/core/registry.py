import json
import math
from dataclasses import fields
from pathlib import Path

from .models import CodeConfig


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"registry.json có khóa trùng: {key}")
        result[key] = value
    return result


class Registry:
    def __init__(self, path, root=None):
        self.path = Path(path).resolve()
        self.root = Path(root).resolve() if root else self.path.parent.parent
        raw = json.loads(self.path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_keys)
        if not isinstance(raw, dict):
            raise ValueError("registry.json phải là ánh xạ code → cấu hình")
        self.configs = {}
        allowed = {f.name for f in fields(CodeConfig)} - {"code"}
        for code, values in raw.items():
            if not isinstance(code, str) or not code.strip() or code != code.strip():
                raise ValueError("Code trong registry không được rỗng hoặc có khoảng trắng ở hai đầu")
            if not isinstance(values, dict) or set(values) - allowed:
                raise ValueError(f"{code}: cấu hình hoặc tên trường không hợp lệ")
            values = dict(values)
            if values.get("mode") == "shared":
                values.setdefault("classes", ["product"])
            for key in ("model", "embedding_model", "ref_dir"):
                if values.get(key):
                    values[key] = str((self.root / values[key]).resolve())
            for key in ("classes", "tray_types", "embedding_mean", "embedding_std"):
                if key in values:
                    values[key] = tuple(values[key])
            cfg = CodeConfig(code=code, **values)
            self._validate(cfg)
            self.configs[code] = cfg

    @staticmethod
    def _validate(c):
        if c.mode not in ("per_code", "shared"):
            raise ValueError(f"{c.code}: mode phải là per_code hoặc shared")
        for key in ("conf", "conf_low", "sim_thr", "sim_margin", "iou", "tray_min_area"):
            v = getattr(c, key)
            if not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
                raise ValueError(f"{c.code}: {key} phải nằm trong [0, 1]")
        if not 0 < c.conf_low < c.conf <= 1:
            raise ValueError(f"{c.code}: cần 0 < conf_low < conf <= 1")
        if c.output_format not in ("yolo_raw", "xyxy") or c.tray_mode not in ("contour", "class"):
            raise ValueError(f"{c.code}: output_format/tray_mode không hợp lệ")
        required = {"target", "other"} if c.mode == "per_code" else {"product"}
        if c.tray_mode == "class":
            required.add("tray")
        if set(c.classes) != required or len(c.classes) != len(required):
            raise ValueError(f"{c.code}: classes phải có đúng {sorted(required)}")
        if not c.model or (c.mode == "shared" and (not c.embedding_model or not c.ref_dir)):
            raise ValueError(f"{c.code}: thiếu đường dẫn model/embedding_model/ref_dir")
        if not isinstance(c.input_size, int) or not isinstance(c.embedding_size, int) or c.input_size < 32 or c.embedding_size < 8:
            raise ValueError(f"{c.code}: kích thước đầu vào quá nhỏ")
        if len(c.embedding_mean) != 3 or len(c.embedding_std) != 3 or any(x <= 0 for x in c.embedding_std):
            raise ValueError(f"{c.code}: embedding_mean/std phải có 3 kênh, std > 0")

    def get(self, code):
        if code not in self.configs:
            raise ValueError(f"{code}: Chưa có model trong registry")
        return self.configs[code]

    def availability(self, code):
        try:
            c = self.get(code)
            for name in ("model", "embedding_model"):
                p = getattr(c, name)
                if p and not Path(p).is_file():
                    return f"Chưa có model: {p}"
            if c.mode == "shared" and not Path(c.ref_dir).is_dir():
                return f"Thiếu ảnh mẫu: {c.ref_dir}"
            return ""
        except ValueError as exc:
            return str(exc)
