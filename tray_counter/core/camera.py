from abc import ABC, abstractmethod
from pathlib import Path
from threading import Condition, Event, Lock, Thread
from time import monotonic

from .storage import read_image


class Camera(ABC):
    @abstractmethod
    def grab(self):
        """Return a fresh, full-resolution BGR frame or raise an exception."""

    def latest(self):
        return None

    def close(self):
        pass


class SimCamera(Camera):
    """Replay images in filename order, advancing only on a counting trigger."""
    def __init__(self, folder, loop=False):
        self.folder, self.loop = Path(folder), loop
        self.files = sorted(p for p in self.folder.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}) if self.folder.is_dir() else []
        self.index, self._latest, self._lock = 0, None, Lock()

    def grab(self):
        with self._lock:
            if not self.files:
                raise IOError(f"Không có ảnh giả lập trong {self.folder}")
            if self.index >= len(self.files):
                if not self.loop:
                    raise IOError("Đã phát hết ảnh giả lập")
                self.index = 0
            frame = read_image(self.files[self.index])
            self.index += 1
            self._latest = frame.copy()
            return frame

    def latest(self):
        with self._lock:
            return None if self._latest is None else self._latest.copy()


class IndustrialCamera(Camera):
    """A single acquisition thread owns the existing SDK adapter.

    Counting waits for an acquisition started after the request. Recording reads
    cached full-resolution frames and never competes for SDK grabs. On failure,
    the pump reconnects in the background and invalidates the previous frame.
    """
    def __init__(self, kind, device_id="", feature="", exposure_us=10000, gain=0, timeout=2.0, on_error=None):
        self.kind, self.device_id, self.feature = kind, device_id, feature
        self.exposure_us, self.gain, self.timeout = exposure_us, gain, timeout
        self.on_error = on_error or (lambda message: None)
        self._condition, self._stop = Condition(), Event()
        self._frame, self._started, self._error = None, 0, "Camera đang kết nối"
        self._thread = Thread(target=self._pump, daemon=True, name="tray-camera")
        self._thread.start()

    def _open(self):
        # Import only the selected SDK; a missing HIK/Basler SDK cannot prevent simulation.
        if self.kind == "Basler":
            from hardware.cameras.soda import SODA as Driver
        elif self.kind == "HIK":
            from hardware.cameras.hik import HIK as Driver
        elif self.kind == "Webcam":
            from hardware.cameras.webcam import Webcam as Driver
        else:
            raise ValueError(f"Loại camera không hỗ trợ: {self.kind}")
        devices = Driver.get_devices()
        selected = str(self.device_id).strip()
        keys = {str(key) for key in devices}
        if selected and selected not in keys:
            raise IOError(f"Không tìm thấy camera ID {selected}; không tự chọn camera khác")
        if not selected:
            if len(keys) != 1:
                raise IOError("Cần chỉ định ID camera khi không có đúng một thiết bị")
            selected = next(iter(keys))
        camera = Driver(config={"id": selected, "feature": self.feature})
        try:
            if not camera.open():
                raise IOError(camera.get_error() or "Không mở được camera")
            if self.kind == "Basler":
                cap = camera._cap
                cap.ExposureAuto.SetValue("Off")
                cap.GainAuto.SetValue("Off")
                cap.ExposureTime.SetValue(float(self.exposure_us))
                cap.Gain.SetValue(float(self.gain))
            elif self.kind == "HIK":
                sdk = camera._cap.obj_cam
                for name in ("ExposureAuto", "GainAuto", "TriggerMode"):
                    if sdk.MV_CC_SetEnumValue(name, 0) != 0:
                        raise IOError(f"Không đặt được {name}=Off")
                for name, value in (("ExposureTime", self.exposure_us), ("Gain", self.gain)):
                    if sdk.MV_CC_SetFloatValue(name, float(value)) != 0:
                        raise IOError(f"Không đặt được {name}")
            else:
                # Webcam exposure units depend on its driver; use feature/driver setup.
                import cv2
                camera._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            if not camera.start_grabbing():
                raise IOError(camera.get_error() or "Không bắt đầu được acquisition")
            return camera
        except Exception:
            camera.close()
            raise

    def _pump(self):
        camera, last_error = None, None
        try:
            while not self._stop.is_set():
                try:
                    if camera is None:
                        camera = self._open()
                    started = monotonic()
                    error, frame = camera.grab()
                    if frame is None or not frame.size:
                        raise IOError(error or "Camera không trả ảnh")
                    # The existing HIK driver emits RGB, Basler/Webcam emit BGR.
                    if self.kind == "HIK":
                        import cv2
                        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR) if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    with self._condition:
                        self._frame, self._started, self._error = frame.copy(), started, ""
                        self._condition.notify_all()
                    last_error = None
                except Exception as exc:
                    with self._condition:
                        self._frame, self._error = None, str(exc)
                        self._condition.notify_all()
                    if str(exc) != last_error:
                        self.on_error(str(exc))
                        last_error = str(exc)
                    if camera:
                        camera.close()
                        camera = None
                    self._stop.wait(1.0)
        finally:
            if camera:
                camera.stop_grabbing()
                camera.close()

    def grab(self):
        requested = monotonic()
        with self._condition:
            while not self._stop.is_set():
                if self._frame is not None and self._started >= requested:
                    return self._frame.copy()
                remaining = self.timeout - (monotonic() - requested)
                if remaining <= 0:
                    raise TimeoutError(self._error or "Camera quá thời gian chụp")
                self._condition.wait(remaining)
        raise IOError("Camera đã đóng")

    def latest(self):
        with self._condition:
            return None if self._frame is None else self._frame.copy()

    def close(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        self._thread.join(timeout=self.timeout + 2)
        if self._thread.is_alive():
            raise TimeoutError("Camera SDK chưa dừng")
