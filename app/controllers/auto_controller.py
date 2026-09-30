import gc
import fnmatch
import threading
import time
from PyQt5.QtCore import QObject
from typing import Optional
import numpy as np

from app.signals import bus
from app.constants import Step, StepResult
from app.models.config import AppConfig
from app.models.result import DetectionResult
from app.core.detection import detect_tape, draw_result_badge
from app.core.model_pool import ModelPool


class AutoController(QObject):

    def __init__(self, hardware_ctrl, model_pool: ModelPool):
        super().__init__()
        self.hardware_ctrl = hardware_ctrl
        self.model_pool = model_pool

        self.state = Step.READ_TRIGGER
        self.config: Optional[AppConfig] = None
        self.is_running = False

        self._trigger_event = threading.Event()
        self.trigger_on = False
        self.msg_model = None
        self.loop_count = 0

        self.active_frame = None
        self.active_result: Optional[DetectionResult] = None
        self.active_error = None
        self.output_str = ""
        self.bypass_mode = False

        bus.trigger_received.connect(self.handle_trigger)

    def set_bypass_mode(self, enabled: bool):
        self.bypass_mode = bool(enabled)
        state = "ENABLED" if self.bypass_mode else "DISABLED"
        level = "WRN" if self.bypass_mode else "INF"
        bus.log_message.emit(f"BYPASS mode {state}", level)

    def _create_bypass_result(self, frame) -> DetectionResult:
        annotated_frame = frame.copy() if frame is not None else frame
        draw_result_badge(annotated_frame, "OK")
        return DetectionResult(
            model_name=self.config.name_model if self.config else "",
            src=frame,
            dst=annotated_frame,
            ret="PASS",
            label_counts="BYPASS_OK",
            timecheck=0.0,
        )

    def start(self, config: AppConfig):
        if not self.hardware_ctrl.stop_camera_acquisition():
            raise RuntimeError("Camera could not enter READY state")
        self.config = config
        self.state = Step.READ_TRIGGER
        self.is_running = True
        self._trigger_event.clear()
        self.trigger_on = False
        self.msg_model = None
        self.loop_count = 0
        self.active_frame = None
        self.active_result = None
        self.active_error = None

        bus.auto_started.emit(True)
        bus.log_message.emit("Auto Inspection started; camera READY and waiting for I/O trigger", "INF")

    def stop(self):
        self.is_running = False
        self._return_camera_to_ready_safely()
        self._turn_lights_off_safely()
        self._trigger_event.clear()
        self.trigger_on = False
        self.msg_model = None
        self.active_frame = None
        self.active_result = None

        bus.auto_started.emit(False)
        bus.log_message.emit("Auto Inspection stopped", "INF")

    def _return_camera_to_ready_safely(self):
        try:
            if not self.hardware_ctrl.stop_camera_acquisition():
                bus.log_message.emit("Camera failed to return to READY state", "ERR")
        except Exception as exc:
            bus.log_message.emit(f"Failed to return camera to READY: {exc}", "ERR")

    def _turn_lights_off_safely(self):
        if not getattr(self.hardware_ctrl, "is_open_light", False):
            return
        try:
            if not self.hardware_ctrl.set_light_values(0, 0, 0, 0, 0):
                bus.log_message.emit("Failed to turn off lights", "ERR")
        except Exception as exc:
            bus.log_message.emit(f"Failed to turn off lights: {exc}", "ERR")

    def _send_output_safely(self, value: str):
        """Send the inspection result on each enabled I/O channel."""
        failures = []
        if self.config.hardware.system.use_tcp_send_data and self.hardware_ctrl.is_connect_server:
            try:
                if not self.hardware_ctrl.server.send_data_to_all_clients(value):
                    failures.append("TCP: no connected client received the result")
            except Exception as exc:
                failures.append(f"TCP: {exc}")
        if self.config.hardware.system.use_com_send_data and self.hardware_ctrl.is_com_send_data:
            try:
                if not self.hardware_ctrl.serial_send.send_data(value):
                    failures.append("Serial: write returned False")
            except Exception as exc:
                failures.append(f"Serial: {exc}")
        if failures:
            bus.log_message.emit(f"I/O output '{value}' failed ({'; '.join(failures)})", "ERR")

    def handle_trigger(self, code: str):
        """Validates incoming serial/TCP trigger messages."""
        if not self.is_running:
            return
        if self.config is None:
            return
        if self.state != Step.READ_TRIGGER or self._trigger_event.is_set():
            bus.log_message.emit(f"Ignored trigger while busy: '{code}'", "WRN")
            return

        allowed_patterns = []
        for src in (self.config.hardware.system.code_com_check,
                    self.config.hardware.system.code_tcp_check):
            if isinstance(src, str):
                allowed_patterns.extend([p.strip() for p in src.split(",") if p.strip()])
            elif isinstance(src, list):
                allowed_patterns.extend(src)

        code_lower = code.lower().strip()
        matched = False
        for pattern in allowed_patterns:
            pattern_lower = pattern.lower().strip()
            if pattern_lower == "*" or fnmatch.fnmatch(code_lower, pattern_lower):
                matched = True
                break

        if matched:
            self.msg_model = code
            self._trigger_event.set()
            self.trigger_on = True
            bus.inspection_triggered.emit()
            bus.log_message.emit(f"Valid trigger received: '{code}'", "INF")
        else:
            bus.log_message.emit(f"Ignored invalid trigger: '{code}'", "WRN")

    def run_step(self):
        """Executes a single step in the state machine loop."""
        if not self.is_running or self.config is None:
            return

        try:
            if self.state == Step.READ_TRIGGER:
                if self._trigger_event.is_set():
                    self._trigger_event.clear()
                    if self.bypass_mode:
                        bus.log_message.emit(
                            "BYPASS trigger: capturing image; model inspection will be skipped",
                            "WRN",
                        )
                    else:
                        bus.log_message.emit("State: READ_TRIGGER -> ON_LIGHTING", "INF")
                    self.state = Step.ON_LIGHTING

            elif self.state == Step.ON_LIGHTING:
                bus.log_message.emit("State: ON_LIGHTING", "INF")
                bus.show_status.emit(StepResult.WAIT.value)

                if not getattr(self.hardware_ctrl, "is_open_light", False):
                    raise RuntimeError("Light controller disconnected before capture")
                l_cfg = self.config.hardware.lighting
                lighting_ok = self.hardware_ctrl.set_light_values(
                    l_cfg.channel_value_0,
                    l_cfg.channel_value_1,
                    l_cfg.channel_value_2,
                    l_cfg.channel_value_3,
                    l_cfg.delay
                )
                if not lighting_ok:
                    raise RuntimeError("Light controller did not apply channel values")
                if not self.hardware_ctrl.start_camera_acquisition():
                    raise RuntimeError("Camera could not start acquisition after trigger")
                bus.log_message.emit("Camera acquisition armed for triggered capture", "INF")
                self.state = Step.CAPTURE

            elif self.state == Step.CAPTURE:
                bus.log_message.emit("State: CAPTURE", "INF")
                time.sleep(self.config.hardware.camera.delay / 1000.0)
                try:
                    self.active_frame = self.hardware_ctrl.camera.get_frame()
                finally:
                    self._return_camera_to_ready_safely()
                if self.active_frame is None:
                    raise Exception('Failed to get frame from camera')
                bus.log_message.emit("Frame captured; camera returned to READY", "INF")
                self.state = Step.OFF_LIGHTING

            elif self.state == Step.OFF_LIGHTING:
                bus.log_message.emit("State: OFF_LIGHTING", "INF")
                if getattr(self.hardware_ctrl, "is_open_light", False):
                    if not self.hardware_ctrl.set_light_values(0, 0, 0, 0, 0):
                        raise RuntimeError("Light controller did not switch off")
                self.state = Step.DETECT

            elif self.state == Step.DETECT:
                bus.log_message.emit("State: DETECT", "INF")
                if self.bypass_mode:
                    self.active_result = self._create_bypass_result(self.active_frame)
                    bus.show_image.emit(self.active_result.dst)
                    self.output_str = "OK"
                    bus.log_message.emit(
                        "BYPASS: image captured; model inspection skipped and output forced to OK",
                        "WRN",
                    )
                else:
                    self.active_result = detect_tape(
                        self.active_frame,
                        self.config,
                        self.model_pool,
                    )
                    bus.show_image.emit(self.active_result.dst)
                    self.output_str = "OK" if self.active_result.ret == "PASS" else "NG"
                self.state = Step.OUTPUT

            elif self.state == Step.OUTPUT:
                bus.log_message.emit(f"State: OUTPUT -> {self.output_str}", "INF")
                bus.show_status.emit(self.active_result.ret)
                self._send_output_safely(self.output_str)
                self.state = Step.WRITE_LOG

            elif self.state == Step.WRITE_LOG:
                bus.log_message.emit("State: WRITE_LOG", "INF")
                try:
                    bus.write_log.emit(self.active_result)
                except Exception as log_ex:
                    bus.log_message.emit(f"Logging failed: {log_ex}", "ERR")
                self.state = Step.RELEASE

            elif self.state == Step.RELEASE:
                bus.log_message.emit("State: RELEASE", "INF")
                self.active_frame = None
                self.active_result = None
                self.output_str = ""
                self._trigger_event.clear()
                self.trigger_on = False
                self.msg_model = None
                self.loop_count += 1
                if self.loop_count % 10 == 0:
                    gc.collect()
                self.state = Step.READ_TRIGGER

            elif self.state == Step.ERROR:
                bus.log_message.emit(f"State: ERROR - {self.active_error}", "ERR")
                bus.show_status.emit(StepResult.FAIL.value)
                error_image = (
                    self.active_frame.copy()
                    if self.active_frame is not None
                    else np.zeros((100, 100, 3), dtype=np.uint8)
                )
                draw_result_badge(error_image, "NG")
                self.active_result = DetectionResult(
                    model_name=self.config.name_model if self.config else "",
                    src=self.active_frame if self.active_frame is not None else error_image,
                    dst=error_image,
                    ret="FAIL",
                    label_counts="NG",
                    error=self.active_error or "Unknown error"
                )
                bus.show_image.emit(error_image)
                self._send_output_safely("NG")
                self.state = Step.WRITE_LOG

        except Exception as e:
            self.active_error = str(e)
            bus.log_message.emit(f"Error in state {self.state.name}: {self.active_error}", "ERR")
            self._return_camera_to_ready_safely()
            self._turn_lights_off_safely()
            self.state = Step.ERROR
