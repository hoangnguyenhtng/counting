from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Any, Dict, List, Optional


class ModelAIConfig(BaseModel):
    model_name: str = ""
    conf: float = Field(0.25, ge=0.0, le=1.0)
    iou: float = Field(0.45, ge=0.0, le=1.0)
    max_det: int = Field(100, ge=1, le=10000)
    agnostic_nms: bool = False

    @field_validator("max_det", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        if v == "" or v is None:
            return 100
        try:
            return int(v)
        except (ValueError, TypeError):
            return 100

    @field_validator("conf", "iou", mode="before")
    @classmethod
    def coerce_float(cls, v: Any, info) -> float:
        if v == "" or v is None:
            return 0.25 if info.field_name == "conf" else 0.45
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.25 if info.field_name == "conf" else 0.45


class ServerConfig(BaseModel):
    ip: str = "127.0.0.1"
    port: int = 5000

    @field_validator("port", mode="before")
    @classmethod
    def coerce_port(cls, v: Any) -> int:
        if v == "" or v is None:
            return 5000
        try:
            return int(v)
        except (ValueError, TypeError):
            return 5000


class CameraConfig(BaseModel):
    id: str = ""
    feature: str = ""
    name: str = "Webcam"
    delay: int = 100

    @field_validator("delay", mode="before")
    @classmethod
    def coerce_delay(cls, v: Any) -> int:
        if v == "" or v is None:
            return 100
        try:
            return int(v)
        except (ValueError, TypeError):
            return 100


class LightingConfig(BaseModel):
    comport: str = "COM3"
    baudrate: int = 9600
    channel_value_0: int = 0
    channel_value_1: int = 0
    channel_value_2: int = 0
    channel_value_3: int = 0
    controller: str = "LCPController"
    delay: int = 200

    @field_validator("baudrate", "channel_value_0", "channel_value_1",
                     "channel_value_2", "channel_value_3", "delay", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        if v == "" or v is None:
            return 0
        try:
            return int(v)
        except (ValueError, TypeError):
            return 0


class ImageRetentionConfig(BaseModel):
    """Storage limits for inspection images saved below ``log_dir``."""

    enabled: bool = True
    max_size_gb: float = Field(100.0, gt=0)
    cleanup_start_gb: float = Field(90.0, gt=0)
    active_cleanup_start_gb: float = Field(97.0, gt=0)
    cleanup_target_gb: float = Field(80.0, ge=0)
    resume_saving_below_gb: float = Field(90.0, gt=0)
    idle_after_minutes: float = Field(30.0, gt=0)
    # Writes/deletes update an in-memory byte counter. A full disk scan is only
    # a periodic reconciliation for external file changes.
    scan_interval_seconds: float = Field(1800.0, gt=0)
    running_batch_files: int = Field(25, ge=1)
    running_work_time_ms: int = Field(200, ge=1)
    running_sleep_ms: int = Field(2500, ge=0)
    idle_batch_files: int = Field(300, ge=1)
    idle_work_time_ms: int = Field(2000, ge=1)
    idle_sleep_ms: int = Field(500, ge=0)
    protect_current_day: bool = True
    hard_limit_action: str = "stop_saving"

    @model_validator(mode="after")
    def validate_threshold_order(self):
        if not (
            self.cleanup_target_gb
            < self.cleanup_start_gb
            <= self.active_cleanup_start_gb
            < self.max_size_gb
        ):
            raise ValueError(
                "Image retention thresholds must satisfy "
                "cleanup_target < cleanup_start <= active_cleanup_start < max_size"
            )
        if not self.cleanup_target_gb < self.resume_saving_below_gb < self.max_size_gb:
            raise ValueError(
                "resume_saving_below_gb must be above cleanup_target_gb and below max_size_gb"
            )
        if self.hard_limit_action != "stop_saving":
            raise ValueError("Only hard_limit_action='stop_saving' is currently supported")
        return self


class SystemConfig(BaseModel):
    log_dir: str = "Log_Vision/LogImage"
    # Kept for compatibility with older model files. ImageRetentionConfig is
    # authoritative for inspection-image cleanup.
    log_size: float = 100.0 * 1024.0
    image_retention: ImageRetentionConfig = Field(default_factory=ImageRetentionConfig)
    database_path: str = "database.db"
    code_com_check: List[str] = ["L1"]
    code_tcp_check: List[str] = ["L1"]
    use_com_send_data: bool = False
    use_tcp_send_data: bool = True

    @field_validator("code_com_check", "code_tcp_check", mode="before")
    @classmethod
    def coerce_list_str(cls, v: Any) -> List[str]:
        if v is None:
            return ["L1"]
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [str(x).strip() for x in v]
        return ["L1"]

    @field_validator("log_size", mode="before")
    @classmethod
    def coerce_float(cls, v: Any) -> float:
        if v == "" or v is None:
            return 100.0 * 1024.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 100.0 * 1024.0


class SerialConfig(BaseModel):
    comport: str = "COM3"
    baudrate: int = 9600

    @field_validator("baudrate", mode="before")
    @classmethod
    def coerce_int(cls, v: Any) -> int:
        if v == "" or v is None:
            return 9600
        try:
            return int(v)
        except (ValueError, TypeError):
            return 9600


class HardwareConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    camera: CameraConfig = Field(default_factory=CameraConfig)
    lighting: LightingConfig = Field(default_factory=LightingConfig)
    system: SystemConfig = Field(default_factory=SystemConfig)
    send_data: SerialConfig = Field(default_factory=SerialConfig)


class AppConfig(BaseModel):
    name_model: str = "Default"
    shapes: Dict[str, List[float]] = Field(default_factory=dict)
    model_ai: ModelAIConfig = Field(default_factory=ModelAIConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
