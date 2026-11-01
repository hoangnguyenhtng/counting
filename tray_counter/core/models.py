from dataclasses import dataclass, field
from enum import Enum, IntEnum


class Status(str, Enum):
    WAITING = "Chờ"
    COUNTING = "Đang đếm"
    OK = "OK"
    MISMATCH = "Lệch"
    SKIPPED = "Bỏ qua (quản lý)"


class ErrorCode(IntEnum):
    NONE = 0
    NOT_READY = 10
    MODEL = 20
    CAMERA = 30
    STORAGE = 40
    TIMEOUT = 50
    PLC = 60


@dataclass
class Result:
    count: int
    n_foreign: int = 0
    n_lowconf: int = 0
    boxes: list = field(default_factory=list)
    model_version: str = ""
    infer_ms: float = 0.0

    def __post_init__(self):
        for value in (self.count, self.n_foreign, self.n_lowconf):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("Số đếm phải là số nguyên không âm")

    @property
    def ok_tray(self):
        return self.n_foreign == 0 and self.n_lowconf == 0 and self.count > 0


@dataclass(frozen=True)
class CodeConfig:
    code: str
    mode: str = "per_code"
    model: str = ""
    embedding_model: str = ""
    ref_dir: str = ""
    conf: float = 0.5
    conf_low: float = 0.25
    sim_thr: float = 0.82
    sim_margin: float = 0.03
    iou: float = 0.45
    version: str = ""
    tray_types: tuple = ()
    classes: tuple = ("target", "other")
    output_format: str = "yolo_raw"
    input_size: int = 640
    tray_mode: str = "contour"
    tray_min_area: float = 0.08
    embedding_size: int = 224
    embedding_mean: tuple = (0.485, 0.456, 0.406)
    embedding_std: tuple = (0.229, 0.224, 0.225)


@dataclass(frozen=True)
class CodeSummary:
    code: str
    qty_expected: int
    qty_counted: int
    n_foreign: int
    n_lowconf: int
    n_errors: int
    n_trays: int
    status: str

    @property
    def difference(self):
        return self.qty_counted - self.qty_expected


class ConfirmationRequired(ValueError):
    pass
