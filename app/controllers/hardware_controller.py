import os
import time
import serial.tools.list_ports
from PyQt5.QtCore import QObject
from app.signals import bus

from hardware.camera_facade import Camera
from hardware.server import Server
from hardware.serial_receiver import SerialReceiver
from hardware.light_controller import LCPController, DCPController
from hardware.cameras import get_camera_devices


class HardwareController(QObject):
    """Manages lifecycle of all hardware devices (camera, light, TCP server, COM port)."""

    def __init__(self):
        super().__init__()
        self.camera = Camera()
        self.server = Server()
        self.serial_send = SerialReceiver()
        self.light_lcp = LCPController()
        self.light_dcp = DCPController()

        self.light = None

        self.is_open_camera = False
        self.is_connect_server = False
        self.is_com_send_data = False
        self.is_open_light = False
        self.is_light_on = False

        # Forward hardware signals to global EventBus
        self.server.sendData.connect(lambda msg: bus.trigger_received.emit(msg))
        self.serial_send.readData.connect(lambda msg: bus.trigger_received.emit(msg))

    @staticmethod
    def _validate_comport(comport: str, quiet: bool = False) -> bool:
        if not comport or not comport.strip():
            if not quiet:
                bus.log_message.emit("COM port name is empty.", "ERR")
            return False
        available = [p.device for p in serial.tools.list_ports.comports()]
        if comport not in available:
            if quiet:
                return False
            bus.log_message.emit(
                f"COM port '{comport}' not found. Available: {available or 'None'}. Trying anyway...",
                "WRN",
            )
        return True

    # ── Camera ──────────────────────────────────────────────────────────
    def open_camera(self, camera_type: str, camera_id: str, feature_file: str = "") -> bool:
        if self.is_open_camera:
            self.close_camera()
            time.sleep(0.2)

        feature_path = ""
        if feature_file and feature_file not in ("Default", ""):
            candidate = f"res/CameraFeature/{feature_file}"
            if os.path.isfile(candidate):
                feature_path = candidate

        try:
            success = self.camera.open_camera(camera_type, config={
                'id': camera_id,
                'feature': feature_path
            })
            if success:
                self.is_open_camera = True
                bus.hardware_status.emit("Camera", True)
                bus.log_message.emit("Camera connected and READY (acquisition stopped)", "INF")
                return True
            err = self.camera.get_last_error()
            bus.log_message.emit(f"Failed to open camera: {err or 'unknown'}", "ERR")
            return False
        except Exception as e:
            bus.log_message.emit(f"Error opening camera: {e}", "ERR")
            return False

    def close_camera(self):
        if not self.is_open_camera:
            return
        try:
            self.camera.close_camera()
            bus.log_message.emit("Camera closed", "INF")
        except Exception as e:
            bus.log_message.emit(f"Error closing camera: {e}", "ERR")
        finally:
            self.is_open_camera = False
            bus.hardware_status.emit("Camera", False)

    def start_camera_acquisition(self) -> bool:
        if not self.is_open_camera:
            bus.log_message.emit("Camera is not READY.", "ERR")
            return False
        return self.camera.start_grabbing()

    def stop_camera_acquisition(self) -> bool:
        return self.camera.stop_grabbing()

    def capture_single_frame(self):
        if not self.is_open_camera:
            bus.log_message.emit("Camera is not READY.", "ERR")
            return None
        return self.camera.capture_frame()

    # ── TCP Server ──────────────────────────────────────────────────────
    def open_server(self, ip: str, port: int) -> bool:
        if self.is_connect_server:
            return True
        try:
            self.server.start_server(ip, port)
            time.sleep(0.1)
            if self.server.is_connected:
                self.is_connect_server = True
                bus.hardware_status.emit("Server", True)
                bus.log_message.emit(f"TCP Server started at {ip}:{port}", "INF")
                return True
            return False
        except Exception as e:
            bus.log_message.emit(f"Error starting TCP server: {e}", "ERR")
            return False

    def close_server(self):
        if not self.is_connect_server:
            return
        try:
            self.server.stop_server()
            bus.log_message.emit("TCP Server stopped", "INF")
        except Exception as e:
            bus.log_message.emit(f"Error stopping TCP server: {e}", "ERR")
        finally:
            self.is_connect_server = False
            bus.hardware_status.emit("Server", False)

    # ── Light Controller ────────────────────────────────────────────────
    def open_light(self, controller_type: str, comport: str, baudrate: int,
                   optional: bool = False) -> bool:
        if self.is_open_light:
            self.close_light()
        if not self._validate_comport(comport, quiet=optional):
            return False
        if controller_type == "LCPController":
            self.light = self.light_lcp
        else:
            self.light = self.light_dcp
        try:
            if self.light.open(comport, baudrate):
                self.is_open_light = True
                self.is_light_on = False
                bus.hardware_status.emit("Light", True)
                bus.log_message.emit(f"Light controller ({controller_type}) opened on {comport}", "INF")
                return True
            if not optional:
                bus.log_message.emit(f"Light open returned False for {comport}", "WRN")
            return False
        except Exception as e:
            if not optional:
                bus.log_message.emit(f"Error opening light: {e}", "ERR")
            return False

    def close_light(self):
        if not self.is_open_light or not self.light:
            return
        try:
            self.light.close()
            bus.log_message.emit("Light controller closed", "INF")
        except Exception as e:
            bus.log_message.emit(f"Error closing light: {e}", "ERR")
        finally:
            self.is_open_light = False
            self.is_light_on = False
            self.light = None
            bus.hardware_status.emit("Light", False)

    def set_light_values(self, ch0: int, ch1: int, ch2: int, ch3: int, delay_ms: int):
        if not self.is_open_light or not self.light:
            return False
        try:
            results = (
                self.light.set_light_value(0, ch0),
                self.light.set_light_value(1, ch1),
                self.light.set_light_value(2, ch2),
                self.light.set_light_value(3, ch3),
            )
            if not all(results):
                bus.log_message.emit("One or more light channels rejected the command", "ERR")
                return False
            self.is_light_on = any(value > 0 for value in (ch0, ch1, ch2, ch3))
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)
            return True
        except Exception as e:
            bus.log_message.emit(f"Error setting light values: {e}", "ERR")
            return False

    # ── COM Serial (Send Data / Receive Trigger) ───────────────────────
    def open_send_data(self, comport: str, baudrate: int) -> bool:
        if self.is_com_send_data:
            return True
        if not self._validate_comport(comport):
            return False
        try:
            result = self.serial_send.connect(comport, baudrate)
            if result:
                self.is_com_send_data = True
                bus.hardware_status.emit("COM", True)
                bus.log_message.emit(f"COM Port opened on {comport} ({baudrate})", "INF")
                return True
            bus.log_message.emit(f"COM Port failed to open on {comport}", "WRN")
            return False
        except Exception as e:
            bus.log_message.emit(f"Error opening COM Port: {e}", "ERR")
            return False

    def close_send_data(self):
        if not self.is_com_send_data:
            return
        try:
            self.serial_send.disconnect()
            bus.log_message.emit("COM Port closed", "INF")
        except Exception as e:
            bus.log_message.emit(f"Error closing COM Port: {e}", "ERR")
        finally:
            self.is_com_send_data = False
            bus.hardware_status.emit("COM", False)

    # ── Cleanup ─────────────────────────────────────────────────────────
    def cleanup_all(self):
        self.close_camera()
        self.close_server()
        self.close_light()
        self.close_send_data()
