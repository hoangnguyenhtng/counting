from PyQt5.QtCore import QThread, pyqtSignal
import numpy as np

class CameraWorker(QThread):
    """Worker thread that continuously queries frames from the active camera."""
    frame_ready = pyqtSignal(np.ndarray, object)
    error_occurred = pyqtSignal(str)

    def __init__(self, camera_facade, delay_ms: int = 40):
        super().__init__()
        self.camera = camera_facade
        self.delay = delay_ms
        self._running = False
        self._empty_frame_streak = 0
        self._frame_pending = False

    def mark_frame_consumed(self):
        """Allow the worker to publish the next frame after the UI consumes one."""
        self._frame_pending = False

    def run(self):
        self._running = True
        self._empty_frame_streak = 0
        while self._running:
            try:
                frame = self.camera.get_frame()
                if frame is not None:
                    self._empty_frame_streak = 0
                    # Keep at most one queued frame. This prevents latency from
                    # growing when high-resolution conversion is slower than capture.
                    if not self._frame_pending:
                        self._frame_pending = True
                        self.frame_ready.emit(frame, self)
                else:
                    self._empty_frame_streak += 1
                    if self._empty_frame_streak in (1, 10, 30):
                        err = self.camera.get_last_error()
                        detail = f": {err}" if err else ""
                        self.error_occurred.emit(
                            f"No frame from camera{detail} "
                            f"(attempt {self._empty_frame_streak})"
                        )
            except Exception as e:
                self.error_occurred.emit(str(e))
            self.msleep(self.delay)

    def stop(self):
        self._running = False
        stopped = self.wait(3000)
        self._frame_pending = False
        return stopped
