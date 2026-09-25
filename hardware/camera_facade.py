from data.logger import Logger
from hardware.cameras import BASLER_ALIASES, is_basler_camera_type
import threading


class Camera:
    """Facade for opening and managing different camera types (HIK, Basler, Webcam)."""

    def __init__(self):
        self.camera = None
        self.is_open = False
        self.is_grabbing = False
        self._acquisition_lock = threading.RLock()
        self.camera_logger = Logger("Camera")

    def open_camera(self, name, config=None):
        if config is None:
            config = {"id": "", "feature": ""}

        if self.is_open:
            self.close_camera()

        if name == "HIK":
            from hardware.cameras.hik import HIK

            self.camera = HIK(config=config)
        elif is_basler_camera_type(name):
            from hardware.cameras.soda import SODA

            self.camera = SODA(config=config)
        else:
            from hardware.cameras.webcam import Webcam

            self.camera = Webcam(config=config)

        opened = self.camera.open()
        if not opened:
            err = self.get_last_error()
            self.camera_logger.error(f"Failed to open camera: {err or 'unknown error'}")
            self.camera = None
            self.is_open = False
            return False

        self.is_open = True
        self.is_grabbing = False
        self.camera_logger.info(f"Camera ready ({name}); acquisition is stopped")
        return True

    def start_grabbing(self) -> bool:
        """Start acquisition only for preview or an active inspection cycle."""
        with self._acquisition_lock:
            if self.camera is None or not self.is_open:
                return False
            if self.is_grabbing:
                return True
            if not self.camera.start_grabbing():
                err = self.get_last_error()
                self.camera_logger.error(f"Failed to start acquisition: {err or 'unknown error'}")
                return False
            self.is_grabbing = True
            return True

    def stop_grabbing(self) -> bool:
        """Return an opened camera to the low-resource Ready state."""
        with self._acquisition_lock:
            if self.camera is None or not self.is_open:
                self.is_grabbing = False
                return True
            if not self.is_grabbing:
                return True
            stopped = bool(self.camera.stop_grabbing())
            if not stopped:
                self.camera_logger.error("Failed to stop camera acquisition")
                return False
            self.is_grabbing = False
            return True

    def capture_frame(self):
        """Acquire one frame and always return the camera to Ready."""
        if not self.start_grabbing():
            return None
        try:
            return self.get_frame()
        finally:
            self.stop_grabbing()

    def get_last_error(self) -> str:
        if self.camera is None:
            return ""
        return self.camera.get_error()

    def get_frame(self):
        with self._acquisition_lock:
            if self.camera is None or not self.is_open or not self.is_grabbing:
                return None
            err, frame = self.camera.grab()
            if frame is None and err and err != "":
                self.camera_logger.debug(f"Grab failed: {err}")
            return frame

    def close_camera(self):
        if self.camera is None:
            self.is_open = False
            return
        try:
            if self.is_open:
                self.stop_grabbing()
                self.camera.close()
                self.camera_logger.info("Camera was closed")
        except Exception as ex:
            self.camera_logger.error(f"Failed to close camera: {ex}")
        finally:
            self.is_open = False
            self.is_grabbing = False
            self.camera = None
